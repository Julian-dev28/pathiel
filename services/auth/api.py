"""The SIWE login routes.

    GET  /auth/nonce     mint a single-use nonce + the exact message to sign
    POST /auth/verify    check the signature, open a session, set the cookie
    GET  /auth/me        who am I
    PATCH /auth/profile  display name / notification email
    POST /auth/logout    drop this session
    POST /auth/logout-all  drop every session for this user

The server builds the message in `GET /auth/nonce` rather than accepting
whatever the client assembled. If the client composed it, every security field
inside — domain, nonce, expiry — would be attacker-chosen, and verifying them
afterwards would be checking the attacker's homework against itself.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from services.auth import siwe
from services.auth.deps import (SESSION_COOKIE, cookie_kwargs, get_store,
                                require_user)
from services.auth.store import SESSION_TTL_S, User

router = APIRouter(prefix="/auth", tags=["auth"])

STATEMENT = ("Sign in to Pathia. This proves you control this wallet. "
             "It does not approve any transaction, transfer or trade.")

# How long a minted message stays signable. Long enough to find the wallet
# popup, short enough that a signature captured off a screen share is stale.
# Mirrored into the message as `Expiration Time` and enforced by siwe.verify.
MESSAGE_TTL_S = 600

# Login is unauthenticated by definition, so it is the one surface an anonymous
# caller can hammer. Signature recovery is CPU work (~1ms of secp256k1), which
# makes an unbounded verify endpoint a cheap way to burn the box.
_ATTEMPTS: Dict[str, list] = {}
_MAX_ATTEMPTS, _WINDOW_S = 20, 300


def expected_domain() -> str:
    """The domain that must appear inside the signed message.

    Deliberately from config, never from the request's own Host header: an
    attacker controls Host, so deriving it from the request would make the
    domain check assert nothing at all.
    """
    return os.environ.get("PATHIA_AUTH_DOMAIN", "localhost:8000")


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


class VerifyBody(BaseModel):
    message: str = Field(max_length=4000)
    signature: str = Field(max_length=500)


class ProfileBody(BaseModel):
    display_name: Optional[str] = Field(default=None, max_length=64)
    email: Optional[str] = Field(default=None, max_length=254)


@router.get("/nonce")
def nonce(request: Request, address: str) -> Dict[str, Any]:
    """Mint a nonce and return the exact string the wallet should sign."""
    _rate_limit(request)
    if not siwe._ADDRESS_RE.match(address or ""):
        raise HTTPException(status_code=400, detail="malformed address")
    store = get_store()
    store.purge_expired()
    value = store.issue_nonce()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    message = (
        f"{expected_domain()} wants you to sign in with your Ethereum account:\n"
        f"{address}\n"
        f"\n{STATEMENT}\n\n"
        f"URI: {os.environ.get('PATHIA_AUTH_URI', 'https://' + expected_domain())}\n"
        f"Version: 1\n"
        f"Chain ID: {os.environ.get('PATHIA_AUTH_CHAIN_ID', '42161')}\n"
        f"Nonce: {value}\n"
        f"Issued At: {now.isoformat().replace('+00:00', 'Z')}\n"
        f"Expiration Time: "
        f"{(now + timedelta(seconds=MESSAGE_TTL_S)).isoformat().replace('+00:00', 'Z')}"
    )
    return {"nonce": value, "message": message, "domain": expected_domain()}


@router.post("/verify")
def verify(request: Request, response: Response, body: VerifyBody) -> Dict[str, Any]:
    """Check the signature and open a session."""
    _rate_limit(request)
    store = get_store()
    try:
        parsed = siwe.verify(body.message, body.signature,
                             expected_domain=expected_domain())
    except siwe.SiweError:
        # One message for every failure. Distinguishing "bad nonce" from "bad
        # signature" would tell an attacker which half to keep working on.
        raise HTTPException(status_code=401, detail="signature rejected")

    # Burned only after the signature checks out, so a valid-looking replay
    # cannot be used to exhaust a legitimate user's pending nonce.
    if not store.consume_nonce(parsed.nonce):
        raise HTTPException(status_code=401, detail="signature rejected")

    user = store.upsert_user(parsed.address)
    if user.disabled:
        raise HTTPException(status_code=403, detail="account disabled")

    token = store.create_session(user.id, user_agent=request.headers.get("user-agent", ""))
    response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_TTL_S, **cookie_kwargs())
    # Returned as well as set, so a CLI that cannot hold cookies can use Bearer.
    return {"user": user.public(), "session_token": token}


@router.get("/me")
def me(user: User = Depends(require_user)) -> Dict[str, Any]:
    return {"user": user.public()}


@router.patch("/profile")
def profile(body: ProfileBody, user: User = Depends(require_user)) -> Dict[str, Any]:
    updated = get_store().update_profile(user.id, display_name=body.display_name,
                                         email=body.email)
    return {"user": updated.public() if updated else user.public()}


@router.post("/logout")
def logout(request: Request, response: Response) -> Dict[str, Any]:
    token = request.cookies.get(SESSION_COOKIE) or ""
    if token:
        get_store().revoke_session(token)
    # Cleared with the same flags it was set with, or the browser keeps it.
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.post("/logout-all")
def logout_all(response: Response, user: User = Depends(require_user)) -> Dict[str, Any]:
    n = get_store().revoke_all_for_user(user.id)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True, "revoked": n}


# ── API keys ────────────────────────────────────────────────────────────────
#
# This is the join between a wallet and the data API. `services/pathia_data_api`
# already authenticated requests against an `api_keys` table; it just had no
# notion of who a key belonged to. These four routes give a signed-in wallet the
# only three operations that matter: see mine, make one, kill one.

class MintKeyBody(BaseModel):
    label: Optional[str] = Field(default=None, max_length=48)


@router.get("/keys")
def list_keys(user: User = Depends(require_user)) -> Dict[str, Any]:
    from services.auth import api_keys
    api_keys.ensure_schema()
    return {"keys": api_keys.list_for(user.address),
            "max": api_keys.MAX_KEYS_PER_OWNER}


@router.post("/keys")
def mint_key(body: MintKeyBody, user: User = Depends(require_user)) -> Dict[str, Any]:
    """Mint a key. The raw token is in this response and nowhere else, ever.

    Only its SHA-256 is stored, so a database leak yields no usable credential —
    and so "show it to me again" is not a feature that can exist. The caller has
    to say that at the moment of minting, not leave it to be discovered.
    """
    from services.auth import api_keys
    api_keys.ensure_schema()
    try:
        minted = api_keys.mint(user.address, label=body.label)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {
        "key_id": minted.key_id,
        "token": minted.token,
        "shown_once": True,
        "note": "Copy this now. Only its hash is stored, so it cannot be shown again.",
    }


@router.delete("/keys/{key_id}")
def revoke_key(key_id: str, user: User = Depends(require_user)) -> Dict[str, Any]:
    """Revoke one of your own keys.

    404, not 403, when the key belongs to someone else: telling a caller that a
    key_id exists but is not theirs confirms the id is real, which is an
    enumeration oracle for free.
    """
    from services.auth import api_keys
    api_keys.ensure_schema()
    if not api_keys.revoke(user.address, key_id):
        raise HTTPException(status_code=404, detail="no such key")
    return {"ok": True, "key_id": key_id}
