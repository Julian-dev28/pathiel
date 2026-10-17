"""A real FastAPI app implementing contracts/accounts/models.py's routes,
for services/wallet_ui's own tests only.

This is NOT a preview of services/accounts (owned by the "accounts" agent,
built in the same wave, in parallel). That service talks to Hyperliquid for
real agent generation and key verification; this one exists so the bundle's
request/response shapes and its EIP-712 signing can be proven end to end
against something that actually routes HTTP with FastAPI, actually validates
with the contract's Pydantic models, and actually recovers the signer from
an EIP-712 payload with eth_account — not a dict of canned JSON responses.

build_app() takes an optional `clock` so tests can exercise the real expiry
check deterministically (see test_fake_accounts_server.py) without sleeping
10 minutes or monkeypatching datetime globally.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional

from eth_account import Account as EthAccount
from eth_account.messages import encode_typed_data
from fastapi import FastAPI, HTTPException

from contracts.accounts.models import (
    Account,
    AccountPatch,
    ActiveSummary,
    AgentConfirmIn,
    AgentPrepareIn,
    AgentPrepareOut,
    ApiKeyImportIn,
    SessionInfo,
    WatchIn,
)

# Hyperliquid's ApproveAgent EIP-712 domain signs against Arbitrum mainnet,
# independent of which chain perps trade on (see contracts/accounts/
# fixtures/agent_prepare.json and App.tsx's chains comment).
DOMAIN_CHAIN_ID = 42161

Clock = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class _PrepareRecord:
    main_address: str
    agent_address: str
    typed_data: dict
    expires_at: datetime
    used: bool = False


def build_app(clock: Optional[Clock] = None) -> FastAPI:
    """A fresh app with a fresh in-memory store — call once per test."""
    now = clock or _utcnow
    app = FastAPI()
    accounts: Dict[str, Account] = {}
    prepares: Dict[str, _PrepareRecord] = {}
    seq = {"n": 0}

    def next_id(prefix: str) -> str:
        seq["n"] += 1
        return f"{prefix}_{seq['n']}"

    def active() -> Optional[Account]:
        return next((a for a in accounts.values() if a.is_active), None)

    def store_activating_if_first(acc: Account) -> Account:
        """New accounts become active only if there was nothing before them —
        adding a second account should never silently switch you off the one
        you were just trading on."""
        if not accounts:
            acc = acc.model_copy(update={"is_active": True})
        accounts[acc.id] = acc
        return acc

    @app.get("/api/session")
    def get_session() -> SessionInfo:
        a = active()
        return SessionInfo(
            local_mode=True,
            address=a.main_address if a else None,
            active_account_id=a.id if a else None,
            accounts_count=len(accounts),
            can_trade=bool(a and a.mode == "trade" and a.verified_at is not None),
            needs_onboarding=len(accounts) == 0,
        )

    @app.get("/api/accounts")
    def list_accounts() -> List[Account]:
        return list(accounts.values())

    @app.post("/api/accounts/agent/prepare")
    def agent_prepare(body: AgentPrepareIn) -> AgentPrepareOut:
        agent_key = EthAccount.create()
        request_id = next_id("req")
        typed_data = {
            "domain": {
                "name": "HyperliquidSignTransaction",
                "version": "1",
                "chainId": DOMAIN_CHAIN_ID,
                "verifyingContract": "0x0000000000000000000000000000000000000000",
            },
            "types": {
                "EIP712Domain": [
                    {"name": "name", "type": "string"},
                    {"name": "version", "type": "string"},
                    {"name": "chainId", "type": "uint256"},
                    {"name": "verifyingContract", "type": "address"},
                ],
                "HyperliquidTransaction:ApproveAgent": [
                    {"name": "hyperliquidChain", "type": "string"},
                    {"name": "agentAddress", "type": "address"},
                    {"name": "agentName", "type": "string"},
                    {"name": "nonce", "type": "uint64"},
                ],
            },
            "primaryType": "HyperliquidTransaction:ApproveAgent",
            "message": {
                "hyperliquidChain": "Mainnet",
                "agentAddress": agent_key.address,
                "agentName": body.label or "pathiel",
                "nonce": int(time.time() * 1000),
            },
        }
        expires_at = now() + timedelta(minutes=10)
        prepares[request_id] = _PrepareRecord(
            main_address=body.main_address,
            agent_address=agent_key.address,
            typed_data=typed_data,
            expires_at=expires_at,
        )
        return AgentPrepareOut(
            request_id=request_id,
            agent_address=agent_key.address,
            typed_data=typed_data,
            expires_at=expires_at,
        )

    @app.post("/api/accounts/agent/confirm")
    def agent_confirm(body: AgentConfirmIn) -> Account:
        record = prepares.get(body.request_id)
        if record is None:
            raise HTTPException(404, "Unknown or already-used signing request.")
        if record.used:
            raise HTTPException(400, "This request was already used.")
        if now() >= record.expires_at:
            raise HTTPException(400, "This signing request expired. Generate a new one.")

        signable = encode_typed_data(full_message=record.typed_data)
        sig_hex = body.signature[2:] if body.signature.startswith("0x") else body.signature
        try:
            recovered = EthAccount.recover_message(signable, signature=bytes.fromhex(sig_hex))
        except Exception as exc:  # noqa: BLE001 - surfaced to the caller as a 400, not a 500
            raise HTTPException(400, f"Could not verify that signature: {exc}") from None
        if recovered.lower() != record.main_address.lower():
            raise HTTPException(400, "That signature does not match the connected wallet.")

        record.used = True
        existing = next(
            (a for a in accounts.values() if a.main_address.lower() == record.main_address.lower()), None
        )
        acc = Account(
            id=existing.id if existing else next_id("acc"),
            label=existing.label if existing else "Main",
            main_address=record.main_address,
            agent_address=record.agent_address,
            mode="trade",
            source="wallet_sign",
            is_active=existing.is_active if existing else False,
            created_at=existing.created_at if existing else now(),
            verified_at=now(),
            agent_name="pathiel",
        )
        return store_activating_if_first(acc)

    @app.post("/api/accounts/import")
    def import_api_key(body: ApiKeyImportIn) -> Account:
        # A real server verifies the key against Hyperliquid; this fake one
        # accepts any well-formed key (Pydantic already enforced the shape)
        # and derives a real address from it so the UI has something
        # meaningful to display.
        agent_address = EthAccount.from_key(body.agent_private_key).address
        acc = Account(
            id=next_id("acc"),
            label=body.label or "Main",
            main_address=body.main_address,
            agent_address=agent_address,
            mode="trade",
            source="api_key",
            is_active=False,
            created_at=now(),
            verified_at=now(),
            agent_name=None,
        )
        return store_activating_if_first(acc)

    @app.post("/api/accounts/watch")
    def watch(body: WatchIn) -> Account:
        acc = Account(
            id=next_id("acc"),
            label=body.label or "Watching",
            main_address=body.address,
            agent_address=None,
            mode="watch",
            source="watch",
            is_active=False,
            created_at=now(),
            verified_at=None,
            agent_name=None,
        )
        return store_activating_if_first(acc)

    @app.post("/api/accounts/{account_id}/activate")
    def activate(account_id: str) -> Account:
        if account_id not in accounts:
            raise HTTPException(404, "No such account.")
        for aid, a in list(accounts.items()):
            if a.is_active != (aid == account_id):
                accounts[aid] = a.model_copy(update={"is_active": aid == account_id})
        return accounts[account_id]

    @app.patch("/api/accounts/{account_id}")
    def patch_account(account_id: str, body: AccountPatch) -> Account:
        if account_id not in accounts:
            raise HTTPException(404, "No such account.")
        updates = body.model_dump(exclude_unset=True)
        accounts[account_id] = accounts[account_id].model_copy(update=updates)
        return accounts[account_id]

    @app.delete("/api/accounts/{account_id}")
    def remove(account_id: str) -> dict:
        if account_id not in accounts:
            raise HTTPException(404, "No such account.")
        was_active = accounts[account_id].is_active
        del accounts[account_id]
        if was_active and accounts:
            first_id = next(iter(accounts))
            accounts[first_id] = accounts[first_id].model_copy(update={"is_active": True})
        return {"ok": True}

    @app.get("/api/accounts/active/summary")
    def summary() -> ActiveSummary:
        a = active()
        if a is None:
            return ActiveSummary(
                account=None, equity_usd=None, withdrawable_usd=None,
                open_positions=0, can_trade=False, blockers=["Connect a wallet to trade."],
            )
        can_trade = a.mode == "trade" and a.verified_at is not None
        return ActiveSummary(
            account=a, equity_usd=15.21, withdrawable_usd=15.21, open_positions=0,
            can_trade=can_trade,
            blockers=[] if can_trade else ["Watch-only account: connect a wallet to trade."],
        )

    return app
