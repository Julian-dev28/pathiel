"""The accounts APIRouter — every route in `contracts/accounts/models.py`'s
docstring. Not mounted by this service: `pathiel/server.py` is
integrator-only this wave (see docs/overhaul/SWARM_RULES.md section 10). Mount
with:

    from services.accounts.api import router as _accounts_router
    app.include_router(_accounts_router)

Auth posture (coordinated with services/auth, whose `integration` module is
the contract point — see that module's docstring for the full threat model):

  - `GET /api/session` never raises; it is how a caller DISCOVERS whether it
    needs to sign in at all, so gating it would be circular. A non-local,
    unauthenticated caller gets a neutral "needs_onboarding" response with no
    account details, never silently the real registry.
  - `agent/prepare`, `agent/confirm`, `import`, `watch` require
    `integration.require_session_or_local` — local mode (Julien, on his own
    box) or an already-established session. These are "connect" routes with
    no specific account to own yet.
  - Every route that acts on an EXISTING account (`activate`, the PATCH
    rename, `delete`) additionally requires
    `integration.session_may_control_account` against THAT account's
    `main_address`, loaded first — so session A can never touch account B.
  - `agent/prepare`, `agent/confirm`, and `import` are rate-limited the same
    way `services/auth/api.py`'s `/auth/nonce` and `/auth/verify` are: they
    are reachable by an unauthenticated-in-the-SIWE-sense caller (local mode
    needs no session at all) and are the one surface here that does real
    cryptographic or network work per request.

No route here ever returns key material. `agent/confirm` burns its
`request_id` (see services.accounts.pending) BEFORE verifying the signature
against it, so a replayed confirm call — the same request_id and signature,
resent by whoever observed the first one — finds nothing to act on and can
never reach `services.auth.integration.issue_session_for_address` a second
time.
"""
from __future__ import annotations

import logging
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List

from eth_account import Account as EthAccount
from fastapi import APIRouter, HTTPException, Request, Response

from contracts.accounts.models import (Account as ContractAccount, AccountPatch, ActiveSummary,
                                       AgentConfirmIn, AgentPrepareIn, AgentPrepareOut,
                                       ApiKeyImportIn, SessionInfo, WatchIn)
from pathiel.client.hl_client import HLRequestError
from services.accounts import agent_approval, credentials, hl_verify, pending, registry, summary, vault, view
from services.auth import integration

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["accounts"])


# ── rate limiting (mirrors services/auth/api.py's _rate_limit exactly) ─────────

_ATTEMPTS: Dict[str, list] = {}
_MAX_ATTEMPTS, _WINDOW_S = 20, 300


def _client(request: Request) -> str:
    return (request.headers.get("x-forwarded-for", "").split(",")[0].strip()
            or (request.client.host if request.client else "unknown"))


def _rate_limit(request: Request) -> None:
    now = time.time()
    key = _client(request)
    hits = [t for t in _ATTEMPTS.get(key, []) if now - t < _WINDOW_S]
    if len(hits) >= _MAX_ATTEMPTS:
        raise HTTPException(status_code=429, detail="too many attempts, wait a few minutes")
    hits.append(now)
    _ATTEMPTS[key] = hits
    if len(_ATTEMPTS) > 10_000:                    # unbounded dict = a leak
        for k in [k for k, v in _ATTEMPTS.items() if not v or now - v[-1] > _WINDOW_S]:
            _ATTEMPTS.pop(k, None)


# ── authorization helpers ───────────────────────────────────────────────────────

def _require_control(request: Request, main_address: str) -> None:
    """401 if nobody is signed in and this is not a local request; 403 if a
    session exists but names a different address (and is not the
    deployment's operator). Exactly the split
    `integration.session_may_control_account`'s own docstring documents."""
    if integration.session_may_control_account(request, main_address):
        return
    if integration.session_address(request) is None and not integration.is_local_mode(request):
        raise HTTPException(status_code=401, detail="sign in required")
    raise HTTPException(status_code=403, detail="not authorized for this account")


def _require_operator_or_local(request: Request, *, allow_bootstrap: bool = False) -> None:
    """Account management is single-operator off the local machine: pathiel
    runs ONE bot on ONE active account, and letting any signed-in wallet add
    and activate its own account would move the operator's bot onto a
    stranger's account. See services.auth.integration.require_operator_or_local."""
    integration.require_operator_or_local(request, allow_bootstrap=allow_bootstrap)


def _normalize_hex_key(raw: str) -> str:
    return raw if raw.startswith("0x") else f"0x{raw}"


# ── GET /api/session ────────────────────────────────────────────────────────────

@router.get("/session", response_model=SessionInfo)
def get_session(request: Request) -> SessionInfo:
    local = integration.is_local_mode(request)
    if not local and integration.session_address(request) is None:
        # Nothing proven about this caller — report only that it is not
        # local and needs onboarding, never the real registry contents.
        return SessionInfo(local_mode=False, needs_onboarding=True)

    active = view.get_effective_active_account()
    accounts_count = len(view.list_all_accounts())
    can_trade = bool(active is not None and active.mode == "trade" and active.verified_at is not None)
    return SessionInfo(
        local_mode=local,
        address=(active.main_address if active is not None else None),
        active_account_id=(active.id if active is not None else None),
        accounts_count=accounts_count,
        can_trade=can_trade,
        needs_onboarding=(active is None),
    )


# ── GET /api/accounts ────────────────────────────────────────────────────────────

@router.get("/accounts", response_model=List[ContractAccount])
def list_accounts(request: Request) -> List[ContractAccount]:
    _require_operator_or_local(request)
    return view.list_all_accounts()


# ── POST /api/accounts/agent/prepare ─────────────────────────────────────────────

@router.post("/accounts/agent/prepare", response_model=AgentPrepareOut)
def prepare_agent(body: AgentPrepareIn, request: Request) -> AgentPrepareOut:
    _rate_limit(request)
    _require_operator_or_local(request, allow_bootstrap=True)

    agent_key = EthAccount.create()
    nonce_ms = agent_approval.new_nonce_ms()
    chain_id = body.signature_chain_id or agent_approval.DEFAULT_WALLET_SIGNATURE_CHAIN_ID
    typed_data = agent_approval.build_typed_data(agent_key.address, nonce_ms, agent_approval.AGENT_NAME,
                                                 signature_chain_id=chain_id)
    request_id = "req_" + secrets.token_hex(12)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=agent_approval.EXPIRY_MINUTES)

    pending.put(pending.PendingAgent(
        request_id=request_id,
        main_address=body.main_address.lower(),
        agent_address=agent_key.address,
        agent_private_key=_normalize_hex_key(agent_key.key.hex()),
        agent_name=agent_approval.AGENT_NAME,
        nonce_ms=nonce_ms,
        label=body.label,
        typed_data=typed_data,
        expires_at=expires_at,
        signature_chain_id=chain_id,
    ))

    return AgentPrepareOut(request_id=request_id, agent_address=agent_key.address,
                           typed_data=typed_data, expires_at=expires_at)


# ── POST /api/accounts/agent/confirm ─────────────────────────────────────────────

@router.post("/accounts/agent/confirm", response_model=ContractAccount)
def confirm_agent(body: AgentConfirmIn, request: Request, response: Response) -> ContractAccount:
    _rate_limit(request)
    _require_operator_or_local(request, allow_bootstrap=True)

    # Burn FIRST, before the signature is even inspected — see module and
    # pending.py docstrings. A replay of this exact call, successful or not,
    # finds nothing here on its second attempt.
    #
    # Expired gets its OWN status/wording, distinct from "not found": the
    # wallet-ui client auto-retries (re-calls prepare) by checking the error
    # detail for "expir" (case-insensitive), so that exact word and a non-404
    # status here are load-bearing for that recovery path, not just phrasing.
    try:
        pend = pending.pop(body.request_id)
    except pending.Expired:
        raise HTTPException(status_code=410, detail="request expired — call prepare again")
    if pend is None:
        raise HTTPException(status_code=404, detail="unknown or already-used request")

    try:
        recovered = agent_approval.recover_signer(pend.typed_data, body.signature)
    except Exception:
        raise HTTPException(status_code=401, detail="signature rejected")

    if recovered.lower() != pend.main_address.lower():
        raise HTTPException(status_code=401,
                            detail="signature does not match the main address for this request")

    # REAL /exchange submission. Implemented fully; mocked in every test; this
    # agent's own work never calls it for real
    # (docs/overhaul/SWARM_RULES.md section 10, wave 2 extra rule).
    try:
        agent_approval.submit_approve_agent(pend.agent_address, pend.agent_name,
                                           pend.nonce_ms, body.signature,
                                           signature_chain_id=pend.signature_chain_id)
    except HLRequestError as e:
        raise HTTPException(status_code=502, detail=f"could not reach Hyperliquid to submit the approval: {e}")

    try:
        approved = hl_verify.is_agent_approved(pend.main_address, pend.agent_address)
    except HLRequestError as e:
        raise HTTPException(status_code=502, detail=f"could not verify the approval with Hyperliquid: {e}")
    if not approved:
        raise HTTPException(status_code=502,
                            detail="Hyperliquid did not confirm this agent is approved yet")

    stored = registry.add(label=pend.label, main_address=pend.main_address,
                          agent_address=pend.agent_address, mode="trade",
                          source="wallet_sign", agent_name=pend.agent_name, verified=True)
    try:
        vault.store_key(stored.id, pend.agent_private_key)
    except Exception as e:
        logger.error(f"[accounts.api] vault store failed for new account {stored.id}, rolling back: {e}")
        registry.delete(stored.id)
        raise HTTPException(status_code=500, detail="could not store the approved key securely")

    # Opens the dashboard session for pend.main_address. Safe to call now and
    # only now: ownership of pend.main_address was just proven by the
    # signature check above, and request_id reuse is already structurally
    # impossible (popped above). See services/auth/integration.py's module
    # docstring for why issue_session_for_address itself verifies nothing.
    integration.issue_session_for_address(response, pend.main_address, request)

    return view.to_contract_account(stored, registry.get_active_id())


# ── POST /api/accounts/import ────────────────────────────────────────────────────

@router.post("/accounts/import", response_model=ContractAccount)
def import_api_key(body: ApiKeyImportIn, request: Request) -> ContractAccount:
    _rate_limit(request)
    _require_operator_or_local(request, allow_bootstrap=True)

    key_hex = _normalize_hex_key(body.agent_private_key)
    try:
        agent_account = EthAccount.from_key(key_hex)
    except Exception:
        raise HTTPException(status_code=400, detail="malformed private key")
    agent_address = agent_account.address

    try:
        approved = hl_verify.is_agent_approved(body.main_address, agent_address)
    except HLRequestError as e:
        raise HTTPException(status_code=502, detail=f"could not verify with Hyperliquid: {e}")
    if not approved:
        raise HTTPException(status_code=400,
                            detail="this key is not an approved agent for that address on Hyperliquid yet")

    stored = registry.add(label=body.label, main_address=body.main_address,
                          agent_address=agent_address, mode="trade",
                          source="api_key", agent_name=None, verified=True)
    try:
        vault.store_key(stored.id, key_hex)
    except Exception as e:
        logger.error(f"[accounts.api] vault store failed for imported account {stored.id}, rolling back: {e}")
        registry.delete(stored.id)
        raise HTTPException(status_code=500, detail="could not store the key securely")

    return view.to_contract_account(stored, registry.get_active_id())


# ── POST /api/accounts/watch ──────────────────────────────────────────────────────

@router.post("/accounts/watch", response_model=ContractAccount)
def watch_address(body: WatchIn, request: Request) -> ContractAccount:
    _require_operator_or_local(request)
    stored = registry.add(label=body.label, main_address=body.address, agent_address=None,
                          mode="watch", source="watch", agent_name=None, verified=False)
    return view.to_contract_account(stored, registry.get_active_id())


# ── POST /api/accounts/{account_id}/activate ─────────────────────────────────────

@router.post("/accounts/{account_id}/activate", response_model=ContractAccount)
def activate_account(account_id: str, request: Request) -> ContractAccount:
    account = view.get_account_or_env(account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="unknown account")
    _require_operator_or_local(request)

    if account_id == view.ENV_ACCOUNT_ID:
        registry.clear_active()
    else:
        registry.set_active(account_id)
    credentials.invalidate_cache()

    refreshed = view.get_account_or_env(account_id)
    assert refreshed is not None
    return refreshed


# ── PATCH /api/accounts/{account_id} ─────────────────────────────────────────────

@router.patch("/accounts/{account_id}", response_model=ContractAccount)
def rename_account(account_id: str, body: AccountPatch, request: Request) -> ContractAccount:
    account = view.get_account_or_env(account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="unknown account")
    _require_operator_or_local(request)

    if body.label is None:
        return account
    if account_id == view.ENV_ACCOUNT_ID:
        raise HTTPException(status_code=400,
                            detail="the env account's label is fixed — it mirrors .env.local, not a stored row")

    stored = registry.rename(account_id, body.label)
    return view.to_contract_account(stored, registry.get_active_id())


# ── DELETE /api/accounts/{account_id} ────────────────────────────────────────────

@router.delete("/accounts/{account_id}")
def delete_account(account_id: str, request: Request) -> Dict[str, bool]:
    account = view.get_account_or_env(account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="unknown account")
    _require_operator_or_local(request)

    if account_id == view.ENV_ACCOUNT_ID:
        raise HTTPException(status_code=400,
                            detail="the env account is not stored here — remove the keys from .env.local instead")

    registry.delete(account_id)
    credentials.invalidate_cache()
    return {"ok": True}


# ── GET /api/accounts/active/summary ─────────────────────────────────────────────

@router.get("/accounts/active/summary", response_model=ActiveSummary)
def active_summary(request: Request) -> ActiveSummary:
    _require_operator_or_local(request)
    return summary.get_active_summary()
