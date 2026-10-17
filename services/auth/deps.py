"""FastAPI dependencies. The only way a route learns who is calling.

Two audiences, one door:

  a browser        carries an httpOnly session cookie set by the SIWE flow
  a script or CLI  carries `Authorization: Bearer <session token>`

Both resolve to the same `User`, so no route has to care which it got.

The legacy `PATHIEL_OPERATOR_TOKEN` still works and is still checked in
`pathiel.dashboard._require_operator`. It is a shared, static, per-deployment
secret: fine for one operator on one box, useless the moment there is more than
one human, because it cannot say who acted. It is retained for the machine paths
(the scheduler, the supervisor, smoke checks) and is not a login.

A third audience exists now, with no door at all: `is_loopback_peer` below
identifies a caller whose request itself arrived from this machine, so
`services.auth.integration.is_local_mode` can grant it full access with no
credential checked, ever. `services/auth/README.md` (Local mode) has the
threat model for why that is safe and the one way it would not be.
"""
from __future__ import annotations

import os
import time
from typing import Optional

from fastapi import Depends, HTTPException, Request

from services.auth import stateless
from services.auth.store import AuthStore, User

SESSION_COOKIE = "pathiel_session"

_STORE: Optional[AuthStore] = None


def get_store() -> AuthStore:
    """Process-wide store. Opened lazily so importing this module is free."""
    global _STORE
    if _STORE is None:
        _STORE = AuthStore()
    return _STORE


def reset_store_for_tests(store: Optional[AuthStore] = None) -> None:
    """Swap the singleton. Tests only — nothing in the app should call this."""
    global _STORE
    _STORE = store


def _token_from(request: Request) -> Optional[str]:
    cookie = request.cookies.get(SESSION_COOKIE)
    if cookie:
        return cookie
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip() or None
    return None


def _user_from_stateless(token: str) -> Optional[User]:
    """Rebuild the caller from a signed token, with no database read at all.

    On a host with no durable disk the users table is as ephemeral as the
    sessions table, so looking the address up would fail for exactly the same
    reason the session lookup did. Everything here comes out of the token.

    Role is always "user". The operator bootstrap is off wherever this is on
    (see PATHIEL_AUTH_NO_BOOTSTRAP_OPERATOR), and a token that could mint an
    operator would be a much worse thing to leak.
    """
    address = stateless.read_session(token)
    if address is None:
        return None
    now = time.time()
    return User(id=0, address=address, display_name=None, email=None,
                role="user", created_at=now, last_seen_at=now, disabled=False)


def current_user(request: Request) -> Optional[User]:
    """Who is calling, or None. Never raises — for routes that serve both."""
    token = _token_from(request)
    if not token:
        return None
    if stateless.sessions_enabled():
        user = _user_from_stateless(token)
        if user is not None:
            return user
        # Fall through: a deployment that just turned this on still has stored
        # sessions in flight, and logging those users out for the switch would
        # be a worse first impression than one extra lookup.
    try:
        return get_store().session_user(token)
    except Exception:
        # A broken auth database must not turn every page into a 500. Callers
        # that need a user will 401; callers that don't carry on anonymous.
        return None


def require_user(request: Request) -> User:
    user = current_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="sign in required")
    return user


def require_operator_user(user: User = Depends(require_user)) -> User:
    """Operator-only. 403, not 404: the caller is authenticated, so hiding the
    route's existence protects nothing and only makes the failure harder to
    diagnose."""
    if not user.is_operator:
        raise HTTPException(status_code=403, detail="operator role required")
    return user


def _is_local(request: Optional[Request]) -> bool:
    """Is this a plain-HTTP request to the operator's own machine?

    Browsers silently DROP a Secure cookie sent over http://. On the local
    server that turns sign-in into the worst kind of failure: the signature
    verifies, the response is 200, and the user is still logged out with nothing
    anywhere saying why. Reported 2026-09-04 as "the connect button does
    nothing".

    Deciding from the request rather than an env var means the default is right
    in both places and there is no PATHIEL_INSECURE_COOKIES to forget to unset in
    production. The test is deliberately narrow: http only, and only for a host
    that is unambiguously this machine.
    """
    if request is None:
        return False
    if request.url.scheme == "https":
        return False
    host = (request.url.hostname or "").lower()
    return host in ("localhost", "127.0.0.1", "::1", "0.0.0.0")


def is_loopback_peer(request: Optional[Request]) -> bool:
    """Did this request's own TCP connection originate from this machine?

    Used for the local-mode auth bypass in `services.auth.integration`,
    which is a much higher-stakes decision than the cookie flag `_is_local`
    above exists for — getting this one wrong does not drop a cookie
    attribute, it skips authentication entirely. The two must never be
    merged into one function.

    Deliberately reads `request.client`, never `request.url` / the `Host`
    header, and never `X-Forwarded-For`. All three are text the CALLER wrote
    and a remote attacker controls completely — see the probe in this
    service's test suite showing `request.url.hostname` following an
    attacker-supplied `Host:` header verbatim while `request.client` stays
    the real transport-layer peer regardless of what headers arrive.
    `request.client` is set by the ASGI server from the actual socket
    peername; the only way to make it say "127.0.0.1" is to actually hold a
    connection from 127.0.0.1.

    This degrades to "not local" (fails CLOSED) when `request.client` is
    absent, e.g. some non-HTTP ASGI transports. It also fails closed if the
    app is ever run behind a reverse proxy configured to honour forwarded
    headers (uvicorn's `--proxy-headers`) — that proxy, not this function,
    would then own the true peer address, and nothing here is in a position
    to verify whether it is honest. Nothing in this repo's entrypoints sets
    that flag as of this writing (see `pathiel/server.py`'s `uvicorn.run`
    call); if a future deployment adds it, the proxy's trust boundary must
    be re-audited alongside this function.

    "0.0.0.0" is deliberately not treated as loopback here, unlike the Host
    allowlist in `_is_local`: that is a bind address, never a real peer
    address, so accepting it as one would not relax anything an attacker
    could actually trigger — it would only mask the bug if `request.client`
    were ever wrongly populated with it.
    """
    if request is None or request.client is None:
        return False
    return request.client.host in ("127.0.0.1", "::1")


def cookie_kwargs(request: Optional[Request] = None) -> dict:
    """Cookie flags for the session.

    `secure` is on everywhere except plain HTTP to localhost, where a Secure
    cookie would be dropped by the browser and sign-in would fail silently.
    PATHIEL_INSECURE_COOKIES still forces it off for a test client, which speaks
    http to a non-local host.

    SameSite=Lax, not None: the session is only ever used by our own pages, and
    Lax means a cross-site POST cannot ride the cookie. That, plus the fact that
    every mutating route is a POST, is what stands in for CSRF tokens here.
    """
    insecure = bool(os.environ.get("PATHIEL_INSECURE_COOKIES")) or _is_local(request)
    return {
        "httponly": True,
        "secure": not insecure,
        "samesite": "lax",
        "path": "/",
    }
