"""FastAPI dependencies. The only way a route learns who is calling.

Two audiences, one door:

  a browser        carries an httpOnly session cookie set by the SIWE flow
  a script or CLI  carries `Authorization: Bearer <session token>`

Both resolve to the same `User`, so no route has to care which it got.

The legacy `PATHIA_OPERATOR_TOKEN` still works and is still checked in
`pathia.dashboard._require_operator`. It is a shared, static, per-deployment
secret: fine for one operator on one box, useless the moment there is more than
one human, because it cannot say who acted. It is retained for the machine paths
(the scheduler, the supervisor, smoke checks) and is not a login.
"""
from __future__ import annotations

import os
from typing import Optional

from fastapi import Depends, HTTPException, Request

from services.auth.store import AuthStore, User

SESSION_COOKIE = "pathia_session"

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


def current_user(request: Request) -> Optional[User]:
    """Who is calling, or None. Never raises — for routes that serve both."""
    token = _token_from(request)
    if not token:
        return None
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
    in both places and there is no PATHIA_INSECURE_COOKIES to forget to unset in
    production. The test is deliberately narrow: http only, and only for a host
    that is unambiguously this machine.
    """
    if request is None:
        return False
    if request.url.scheme == "https":
        return False
    host = (request.url.hostname or "").lower()
    return host in ("localhost", "127.0.0.1", "::1", "0.0.0.0")


def cookie_kwargs(request: Optional[Request] = None) -> dict:
    """Cookie flags for the session.

    `secure` is on everywhere except plain HTTP to localhost, where a Secure
    cookie would be dropped by the browser and sign-in would fail silently.
    PATHIA_INSECURE_COOKIES still forces it off for a test client, which speaks
    http to a non-local host.

    SameSite=Lax, not None: the session is only ever used by our own pages, and
    Lax means a cross-site POST cannot ride the cookie. That, plus the fact that
    every mutating route is a POST, is what stands in for CSRF tokens here.
    """
    insecure = bool(os.environ.get("PATHIA_INSECURE_COOKIES")) or _is_local(request)
    return {
        "httponly": True,
        "secure": not insecure,
        "samesite": "lax",
        "path": "/",
    }
