"""API-key authentication — Bearer token, hashed at rest, resolved to a Principal.

Clean-room of UW's `Authorization: Bearer <token>` scheme. Keys live in our own DB
(see db.py), never in code. A Principal carries the plan/scopes used by rate limiting
and endpoint gating.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, status

from .db import ApiKeyStore


@dataclass(frozen=True)
class Principal:
    key_id: str
    plan: str          # e.g. "free" | "standard" | "elite"
    scopes: frozenset[str]
    rate_per_min: int


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def require_principal(authorization: str = Header(default="")) -> Principal:
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            detail="missing bearer token", headers={"WWW-Authenticate": "Bearer"})
    token = authorization.split(" ", 1)[1].strip()
    rec = ApiKeyStore.lookup(hash_token(token))
    if not rec or not rec.get("active", False):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="invalid or inactive API key")
    return Principal(key_id=rec["key_id"], plan=rec["plan"],
                     scopes=frozenset(rec.get("scopes", [])), rate_per_min=int(rec["rate_per_min"]))


def require_scope(scope: str):
    async def _dep(principal: Principal = Depends(require_principal)) -> Principal:
        if scope not in principal.scopes and "*" not in principal.scopes:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail=f"plan lacks scope: {scope}")
        return principal
    return _dep
