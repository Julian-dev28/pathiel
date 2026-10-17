"""In-memory store bridging `POST /api/accounts/agent/prepare` to
`POST /api/accounts/agent/confirm`.

Deliberately NOT persisted to disk: a pending request is a short-lived
(`EXPIRY_MINUTES`), single-use handshake, not account state. Losing it on a
restart just means the caller calls `prepare` again — exactly like losing an
in-flight SIWE nonce would (see services/auth/stateless.py's own framing).

`pop()` is the ONLY way to read a request, and it ATOMICALLY removes it
before any signature is even inspected. That one property is what makes a
replayed `confirm` call (the same request_id and signature, resent by
whoever captured the first one off the wire or a log) harmless: the second
lookup sees nothing. There is no separate "mark it used" step to forget, and
`services.accounts.api.confirm_agent` never calls
`services.auth.integration.issue_session_for_address` — which trusts its
address argument completely, by design — until AFTER this pop has already
succeeded and the signature has verified against what was popped.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

_LOCK = threading.Lock()
_STORE: Dict[str, "PendingAgent"] = {}


@dataclass(frozen=True)
class PendingAgent:
    request_id: str
    main_address: str
    agent_address: str
    agent_private_key: str
    agent_name: str
    nonce_ms: int
    label: str
    typed_data: Dict[str, Any]
    expires_at: datetime


def _prune_locked() -> None:
    now = datetime.now(timezone.utc)
    for rid in [rid for rid, p in _STORE.items() if now >= p.expires_at]:
        _STORE.pop(rid, None)


def put(pending: PendingAgent) -> None:
    with _LOCK:
        _prune_locked()
        _STORE[pending.request_id] = pending


class Expired(Exception):
    """Raised by pop() when request_id existed but its expiry had already
    passed. Kept distinct from "returns None" (never existed, or already
    consumed by an earlier pop — see pop()'s docstring for why those two stay
    indistinguishable): "expired" is not a secret and the wallet-ui client
    auto-retries (re-calls prepare) specifically on that signal, so
    services.accounts.api surfaces it as its own clear, stable response
    rather than folding it into the generic "unknown request" case."""


def pop(request_id: str) -> Optional[PendingAgent]:
    """Remove and return the request.

    None if it never existed, or was already consumed by an earlier pop (a
    prior confirm attempt — success OR failure, see module docstring): a
    caller cannot distinguish "wrong id" from "already used" — nothing
    useful to an attacker either way.

    Raises Expired if it existed but its expiry had already passed, after
    removing it the same way (still single-use; a second pop for the same
    expired id returns None, not another Expired)."""
    with _LOCK:
        pending = _STORE.pop(request_id, None)
    if pending is None:
        return None
    if datetime.now(timezone.utc) >= pending.expires_at:
        raise Expired(request_id)
    return pending


def peek_count() -> int:
    """Tests only."""
    with _LOCK:
        return len(_STORE)


def clear_for_tests() -> None:
    with _LOCK:
        _STORE.clear()
