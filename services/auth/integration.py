"""The contract point between `services/accounts` and `services/auth`.

`contracts/accounts/models.py` describes the flow this module exists for:
connecting a Hyperliquid account IS the identity now. There is no separate
"Sign in" step — the user proves ownership of an address while authorising a
trading key (`wallet_sign`), or while importing a verified API key
(`api_key`), and that proof is exactly as good as a SIWE signature, reached by
a different door. This module is that second door's hinge: three functions
`services/accounts` calls directly, by the exact names and signatures below,
plus the authorization decision for "who may control this account" that both
services need to agree on.

THE ONE RULE THAT MATTERS MOST
-------------------------------
`issue_session_for_address` performs ZERO verification of its own. It does
not check a signature, does not check a nonce, does not know what
`approveAgent` is. It trusts the address it is given completely and opens a
session for it. That is correct ONLY because `/auth/verify` has exactly the
same property — it also just calls the shared session-opening code once
`siwe.verify()` and the nonce burn have already succeeded — and the contract
for every caller of this module is identical: prove ownership FIRST, the same
way SIWE does (a signature over a single-use, expiring, domain-bound
challenge), and call this function SECOND. Calling it on an unverified
address is a complete account takeover, for whichever address was named.

TWO KINDS OF "WHO MAY" THIS MODULE ANSWERS
-------------------------------------------
  is_local_mode           "does this request need to prove who it is at all?"
  session_may_control_account   "given a session, may it act on THIS account?"

Local mode answers yes to nothing needing a session, ever, for a request
that genuinely arrived from this machine — see `is_local_mode`'s docstring
for exactly what "genuinely" has to mean. Public mode has no such escape: a
session must exist, and `session_may_control_account` is the one further
question — this product supports more than one connected Hyperliquid account
per deployment, so a session proves an ADDRESS, not a blanket license to
every account on the box.
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, Response

from services.auth import deps, stateless
from services.auth.deps import SESSION_COOKIE, cookie_kwargs, get_store
from services.auth.store import SESSION_TTL_S, User

_ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")

# Hostnames a same-machine page's own Origin may legitimately carry. Port is
# deliberately not checked: a local dev frontend proxying to a different local
# API port (e.g. a Vite server on :5173 calling an API on :8820) is still
# "this machine", and the attack this exists to stop is a DIFFERENT origin
# entirely (https://evil.example), not a different local port.
_LOOPBACK_HOSTNAMES = {"localhost", "127.0.0.1", "::1"}


# Environment markers that mean "this process serves the internet". Behind a
# same-host reverse proxy or a service-mesh sidecar (k8s/ ships manifests),
# EVERY request reaches the app from 127.0.0.1, so a loopback peer proves
# nothing there. Local mode is therefore off on any deployment, whatever the
# peer address says. PATHIEL_DISABLE_LOCAL_MODE=1 forces it off anywhere.
_DEPLOYMENT_MARKERS = ("KUBERNETES_SERVICE_HOST", "FLY_APP_NAME", "VERCEL", "RENDER",
                       "RAILWAY_ENVIRONMENT", "PATHIEL_PUBLIC_DASHBOARD",
                       "PATHIEL_DISABLE_LOCAL_MODE")


def deployed_environment() -> bool:
    import os
    return any(os.environ.get(k) for k in _DEPLOYMENT_MARKERS)


def _origin_is_loopback(origin: str) -> bool:
    try:
        host = urlsplit(origin).hostname or ""
    except ValueError:
        return False
    return host.lower() in _LOOPBACK_HOSTNAMES


def _local_hostnames() -> set:
    """Hostnames the request's own Host header may carry in local mode: the
    loopback names, plus any listed in PATHIEL_LOCAL_HOSTNAMES (comma
    separated; the test session adds Starlette TestClient's "testserver")."""
    import os
    extra = {h.strip().lower() for h in os.environ.get("PATHIEL_LOCAL_HOSTNAMES", "").split(",")
             if h.strip()}
    return _LOOPBACK_HOSTNAMES | extra


def _host_is_local(request: Request) -> bool:
    """Does the Host the browser addressed name this machine?

    This is the DNS-rebinding check (security review 2026-10-25). A page on
    https://evil.example can re-point its own hostname at 127.0.0.1 after it
    loads. The browser then treats requests to it as SAME-origin, so a GET
    carries no Origin header at all and the Origin check below has nothing to
    reject, while the TCP peer really is 127.0.0.1. The one thing the attacker
    cannot change is the Host header: the browser sends the name it resolved,
    evil.example. Host alone proves nothing (curl can write any Host), which
    is why it is an ADDITIONAL necessary condition here, never a sufficient
    one."""
    return (request.url.hostname or "").lower() in _local_hostnames()


def client_key(request: Request) -> str:
    """The key a per-client rate limit buckets on.

    The TCP peer, unless this process sits behind an edge proxy (any
    deployment marker, or PATHIEL_TRUST_FORWARDED_FOR=1), in which case the
    RIGHTMOST X-Forwarded-For entry: the one that proxy appended itself.
    Every entry to its left was written by the caller. Keying on the leftmost
    entry, as both login rate limits did until 2026-10-25, let any caller
    reset their own bucket by sending a fresh made-up address per request."""
    import os
    peer = (request.client.host if request.client else "") or "unknown"
    if deployed_environment() or os.environ.get("PATHIEL_TRUST_FORWARDED_FOR"):
        hops = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",") if h.strip()]
        if hops:
            return hops[-1]
    return peer


def _open_session(address: str, request: Request, response: Response) -> tuple[User, str]:
    """Shared by `/auth/verify` and `issue_session_for_address` so the two
    doors can never drift apart: a wallet proven by SIWE and a wallet proven
    by `approveAgent` must open IDENTICAL sessions — same TTL, same cookie
    flags, same stateless-vs-stored branch — or a user who happened to come
    through one door would silently get a different security posture than
    the same wallet coming through the other.

    Returns the `User` row too (not part of the public `-> None` contract of
    `issue_session_for_address`) because `/auth/verify`'s response body
    still needs it and re-reading it from the store a second time would be
    wasted work for no benefit.
    """
    if not _ADDR_RE.match(address or ""):
        raise HTTPException(status_code=400, detail="malformed address")
    store = get_store()
    user = store.upsert_user(address)
    if user.disabled:
        raise HTTPException(status_code=403, detail="account disabled")

    # Mirrors /auth/verify exactly: a stored session is a row on a disk the
    # next request may not share on an ephemeral host, so a deployment that
    # says so (PATHIEL_AUTH_STATELESS_SESSION) gets a self-contained token
    # instead. See services/auth/stateless.py for the trade.
    if stateless.sessions_enabled():
        token = stateless.issue_session(user.address)
        max_age = stateless.STATELESS_SESSION_TTL_S
    else:
        token = store.create_session(user.id, user_agent=request.headers.get("user-agent", ""))
        max_age = SESSION_TTL_S
    response.set_cookie(SESSION_COOKIE, token, max_age=max_age, **cookie_kwargs(request))
    return user, token


def issue_session_for_address(response: Response, address: str, request: Request) -> None:
    """Open a session for `address` and set the cookie — exactly what
    `/auth/verify` does after a signature checks out, reached from a
    different front door.

    Call this ONLY after you have already proven the caller owns `address`,
    with proof as strong as SIWE's: a signature over a single-use, expiring,
    domain-bound challenge, burned so it cannot be replayed. The accounts
    connect flow has two such proofs — `approveAgent`'s EIP-712 signature
    (`AgentConfirmIn` in `contracts/accounts/models.py`, checked against the
    single-use `request_id` from `AgentPrepareOut` the same way a SIWE nonce
    is burned) and a verified API-key import (checked against Hyperliquid
    itself before this is called). Neither check happens here — see the
    module docstring.

    Raises 400 for a malformed address and 403 for a disabled account,
    matching `/auth/verify`. Returns None: the caller gets a cookie on
    `response`, nothing else, because the accounts service's own route
    response is the `Account` or `SessionInfo` the caller actually wants, not
    a raw session token (unlike `/auth/verify`, which exists to serve API
    clients that cannot hold cookies — see services/auth/README.md).
    """
    _open_session(address, request, response)


def session_address(request: Request) -> Optional[str]:
    """The lowercase address behind the caller's session, or None.

    None covers every case with nothing to name: local mode (see
    `is_local_mode` — call that first if the distinction matters to you), no
    cookie or bearer token, an expired or revoked session, and a disabled
    account. Deliberately one value for all of them, for the same reason
    `/auth/verify` returns one message for every login failure: a caller that
    could branch on *why* there is no address would be handed a probe for
    enumerating sessions and accounts it has no business learning about.
    """
    user = deps.current_user(request)
    return user.address if user is not None else None


def is_local_mode(request: Request) -> bool:
    """True when this request itself arrived over loopback: Julien, on his
    own box, before or after connecting any Hyperliquid account. Full
    access, no session, no sign-in, ever — this is the normal case, and
    nothing above this function decides that; it only decides whether the
    request qualifies.

    Built on `deps.is_loopback_peer`, which reads the ASGI-reported TCP peer
    address rather than any header — see that function for why a
    Host-header-based check (which is what this service already had, for a
    much lower-stakes decision) would be a complete authentication bypass
    here, not a convenience.

    A loopback peer is necessary but not sufficient. A browser tab open on
    ANY website, on Julien's own machine, can make Julien's OWN browser issue
    a request to `http://127.0.0.1:<port>/...` — the TCP connection really is
    local, because the browser is local, even though the page that triggered
    it is `https://evil.example`. This is a known, named attack class against
    local dev servers and tools that trust "it came from loopback" alone
    (Ollama's and Docker Desktop's APIs have both shipped exactly this bug).
    `pathiel/server.py` ran CORS wide open (`allow_origins=["*"]`) until
    2026-10-25 and now sends no CORS headers unless PATHIEL_CORS_ORIGINS
    names an origin, but this check must not depend on that: a CORS policy
    decides who may READ a response, not who may send the request.

    So: if `Origin` is present on the request at all, it must itself name a
    loopback host (`localhost` / `127.0.0.1` / `::1`, any port). `Origin` is
    not a header page script can set — the browser sets it to the calling
    page's own origin and will not lie about it — so `evil.example`'s page
    cannot forge an Origin that passes this check; it can only send the one
    that damns it. A request with no `Origin` at all (curl, a script, the
    scheduler, anything that is not a browser enforcing CORS) is unaffected,
    which is exactly right: that is Julien's own tooling, the case this mode
    exists for.
    """
    if deployed_environment():
        return False
    if not deps.is_loopback_peer(request):
        return False
    if not _host_is_local(request):
        return False
    origin = request.headers.get("origin", "")
    if origin and not _origin_is_loopback(origin):
        return False
    return True


def _is_operator(request: Request) -> bool:
    user = deps.current_user(request)
    return user is not None and user.is_operator


def session_may_control_account(request: Request, main_address: str) -> bool:
    """May this caller mutate / trade the account whose main wallet is
    `main_address`?

    Local mode: yes, unconditionally — see `is_local_mode`.

    Public mode: yes iff the session's own address is `main_address`
    (case-insensitive), OR the session belongs to the deployment's operator.
    No session at all: no.

    The operator override is "keep the operator gating intact in spirit":
    this product already has exactly one configured-operator concept — the
    `role` column `AuthStore` bootstraps to the first wallet ever to
    establish a session (now reachable through either door) and that
    `scripts/grant_operator.py` can hand to someone else. Re-using it here
    rather than inventing a second operator list means there is exactly one
    place that answers "who runs this box", and it inherits that config's
    existing restriction for free: a stateless session (`current_user` /
    `_user_from_stateless` in deps.py) is HARD-CODED to `role="user"` and can
    never be an operator, because deps.py already decided that minting an
    operator from a token that merely needs to be guessed or leaked — rather
    than read from a database row a server operator actually controls — is a
    much worse thing to hand out than one more "sign in again" prompt. That
    restriction is not duplicated here; it falls out of calling
    `current_user` at all.

    `services/accounts` is expected to call this inside a mutating route AFTER
    loading the account row (this module does not know what an Account is):

        account = registry.get(account_id)
        if not integration.session_may_control_account(request, account.main_address):
            raise HTTPException(401 if integration.session_address(request) is None
                                 and not integration.is_local_mode(request) else 403, ...)
    """
    if is_local_mode(request):
        return True
    address = session_address(request)
    if address is None:
        return False
    if address.lower() == (main_address or "").lower():
        return True
    return _is_operator(request)


def require_session_or_local(request: Request) -> Optional[str]:
    """FastAPI dependency: who may call a protected, mutating route that has
    no specific account to check yet (`contracts/accounts/models.py`'s
    "initial connect routes" — `agent/prepare`, `agent/confirm`,
    `import`, `watch`: there is no account to own until these finish).

    Returns the caller's address, or None for local mode (nothing to name,
    and nothing more to check). Raises 401 when neither a local request nor a
    session is present.

    Not used for a route that mutates a SPECIFIC existing account — that
    needs `session_may_control_account` instead, called after the account is
    loaded, so address-A-cannot-touch-address-B's-account is enforced.
    """
    if is_local_mode(request):
        return None
    address = session_address(request)
    if address is None:
        raise HTTPException(status_code=401, detail="sign in required")
    return address



def require_operator_or_local(request: Request, *, allow_bootstrap: bool = False) -> None:
    """Gate for anything that changes or reveals what the ONE trading bot does
    (accounts, risk profile, live limits).

    Local mode: always allowed. Public mode: only the deployment's operator.
    allow_bootstrap: on a fresh deployment (no wallet has ever held a session,
    bootstrap not disabled) the first caller is let through; the session it
    then opens becomes the operator under store.upsert_user's first-login rule.
    401 when nobody is signed in, 403 for a signed-in non-operator.
    """
    import os
    if is_local_mode(request):
        return
    user = deps.current_user(request)
    if user is not None and user.is_operator:
        return
    if (allow_bootstrap and user is None
            and not os.environ.get("PATHIEL_AUTH_NO_BOOTSTRAP_OPERATOR")
            and get_store().user_count() == 0):
        return
    if user is None:
        raise HTTPException(status_code=401, detail="connect the operator wallet first")
    raise HTTPException(status_code=403,
                        detail="this deployment has one operator; only the operator can do this")
