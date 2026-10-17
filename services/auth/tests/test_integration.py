"""services/auth/integration.py: the contract services/accounts calls.

Same rule as test_siwe and test_store_and_api: each test names the thing that
goes wrong without it. The two things that matter most here are the two most
dangerous to get wrong silently —

  is_local_mode              a False here means "ask for a session" (a minor
                              annoyance if wrong). A True here means SKIP
                              AUTHENTICATION ENTIRELY. Every test on it is
                              written assuming the second failure mode, not
                              the first.
  issue_session_for_address  does zero verification of its own (by design —
                              see its docstring). The tests here prove what IT
                              guarantees (a real, correctly-flagged session),
                              never that the caller's proof-of-ownership was
                              checked, because this module cannot check that.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.testclient import TestClient

from services.auth import deps, integration
from services.auth.store import AuthStore

ADDR_A = "0x" + "a1" * 20
ADDR_B = "0x" + "b2" * 20
ADDR_C = "0x" + "c3" * 20


# ── a fake Request, for the pure functions that only read request.client ───

class _Client:
    def __init__(self, host):
        self.host = host


class _BareRequest:
    """Enough of a Request for is_loopback_peer / is_local_mode / current_user:
    a `.client`, a `.headers.get`, and `.cookies.get`. Real routes use a real
    Request; this is for testing the decision in isolation from any HTTP
    stack."""

    def __init__(self, client_host, headers=None, cookies=None):
        self.client = _Client(client_host) if client_host is not None else None
        self.headers = headers or {}
        self.cookies = cookies or {}


# ── is_loopback_peer: the non-spoofable signal ──────────────────────────────

def test_is_loopback_peer_true_for_both_loopback_forms():
    assert deps.is_loopback_peer(_BareRequest("127.0.0.1")) is True
    assert deps.is_loopback_peer(_BareRequest("::1")) is True


def test_is_loopback_peer_false_for_a_real_remote_address():
    assert deps.is_loopback_peer(_BareRequest("203.0.113.5")) is False


def test_is_loopback_peer_false_for_a_bind_address_not_a_peer_address():
    """0.0.0.0 is something a socket binds to, never something a connection
    arrives FROM. Accepting it as loopback would only ever mask a bug."""
    assert deps.is_loopback_peer(_BareRequest("0.0.0.0")) is False


def test_is_loopback_peer_fails_closed_with_no_client_info():
    assert deps.is_loopback_peer(_BareRequest(None)) is False
    assert deps.is_loopback_peer(None) is False


def test_the_host_header_is_attacker_text_but_the_peer_address_is_not():
    """The entire reason is_local_mode is built on request.client and not on
    request.url / the Host header. A caller can set Host to anything it
    likes and request.url follows it verbatim; request.client stays the real
    transport-layer peer regardless of what headers arrive. Proven against a
    real FastAPI Request, not a stub, because this is the property the whole
    design leans on.
    """
    app = FastAPI()

    @app.get("/x")
    def x(request: Request):
        return {"hostname": request.url.hostname,
                "is_loopback_peer": deps.is_loopback_peer(request)}

    remote = TestClient(app, client=("203.0.113.5", 4433))
    r = remote.get("/x", headers={"Host": "localhost"})
    body = r.json()
    assert body["hostname"] == "localhost", "the attacker's Host: text, taken at face value"
    assert body["is_loopback_peer"] is False, "the real peer, which the header cannot touch"


# ── _origin_is_loopback: the CSRF-against-localhost guard ───────────────────

@pytest.mark.parametrize("origin", [
    "http://localhost:5173",
    "http://127.0.0.1:8820",
    "http://[::1]:9000",
    "https://localhost",
])
def test_origin_is_loopback_accepts_any_scheme_or_port_on_a_loopback_host(origin):
    assert integration._origin_is_loopback(origin) is True


@pytest.mark.parametrize("origin", [
    "https://evil.example",
    "null",
    "",
    "http://localhost.evil.example",   # suffix trick: NOT the same host
    "http://evil-localhost.example",
])
def test_origin_is_loopback_rejects_everything_else(origin):
    assert integration._origin_is_loopback(origin) is False


# ── is_local_mode: loopback peer is necessary, not sufficient ──────────────

def test_is_local_mode_true_for_a_loopback_peer_with_no_origin_at_all():
    """curl, a script, the scheduler: not a browser, sends no Origin, and is
    exactly the caller local mode exists for."""
    assert integration.is_local_mode(_BareRequest("127.0.0.1")) is True


def test_is_local_mode_true_for_a_loopback_peer_whose_own_page_calls_it():
    """Julien's own dashboard, served from this box, fetching its own API."""
    req = _BareRequest("127.0.0.1", headers={"origin": "http://127.0.0.1:8820"})
    assert integration.is_local_mode(req) is True


def test_is_local_mode_false_for_a_remote_peer_regardless_of_origin():
    assert integration.is_local_mode(_BareRequest("203.0.113.5")) is False


def test_is_local_mode_false_for_a_loopback_peer_with_a_cross_site_origin():
    """THE regression test. A malicious page open in Julien's OWN browser,
    on Julien's OWN machine, can make Julien's browser fire a request to
    127.0.0.1 — the TCP peer really is loopback, because the browser is
    local, even though the page that triggered the call is not. Without the
    Origin check this would be a full, unauthenticated bypass of every
    trading route, reachable by getting a victim to open any web page while
    pathiel is running on their box.
    """
    req = _BareRequest("127.0.0.1", headers={"origin": "https://evil.example"})
    assert integration.is_local_mode(req) is False


def test_is_local_mode_false_for_a_forged_host_header_over_a_real_remote_connection():
    """Belt and suspenders on the same point as the deps.py test above, at
    the level callers actually use: a remote attacker cannot buy local mode
    merely by sending Host: localhost."""
    app = FastAPI()

    @app.get("/x")
    def x(request: Request):
        return {"is_local_mode": integration.is_local_mode(request)}

    remote = TestClient(app, client=("203.0.113.5", 4433))
    r = remote.get("/x", headers={"Host": "localhost"})
    assert r.json()["is_local_mode"] is False


# ── e2e: a demo app wired the way services/accounts and pathiel/server.py
#    are expected to wire it ─────────────────────────────────────────────────
#
# services/auth does not own any dashboard or accounts route, so this is a
# miniature stand-in proving the CONTRACT those routes will be built on, end
# to end through a real ASGI stack and real cookies — not just the pure
# functions above.

def _demo_app() -> FastAPI:
    app = FastAPI()

    @app.get("/demo/protected")
    def protected(request: Request):
        if integration.is_local_mode(request):
            return {"ok": True, "as": "local"}
        address = integration.session_address(request)
        if address is None:
            raise HTTPException(status_code=401, detail="sign in required")
        return {"ok": True, "as": address}

    @app.post("/demo/connect/{address}")
    def connect(address: str, request: Request, response: Response):
        # Stands in for services/accounts AFTER it has already verified
        # ownership (approveAgent signature / verified API-key import). This
        # demo performs no such check — that is the whole point of the
        # function's contract, see integration.py's module docstring.
        integration.issue_session_for_address(response, address, request)
        return {"ok": True}

    @app.post("/demo/accounts/{main_address}/activate")
    def activate(main_address: str, request: Request):
        if integration.session_may_control_account(request, main_address):
            return {"ok": True, "activated": main_address}
        status = (401 if integration.session_address(request) is None
                   and not integration.is_local_mode(request) else 403)
        raise HTTPException(status_code=status, detail="not yours")

    return app


@pytest.fixture
def store(tmp_path):
    s = AuthStore(str(tmp_path / "auth.db"))
    yield s
    s.close()


@pytest.fixture
def demo(store, monkeypatch):
    # TestClient's cookiejar enforces Secure like a real browser would; the
    # transport here is plain http, so without this the session cookie never
    # round-trips and every "then succeeds" assertion below would fail for a
    # reason that has nothing to do with the thing being tested. The real
    # /auth/verify tests set the same flag for the same reason.
    monkeypatch.setenv("PATHIEL_INSECURE_COOKIES", "1")
    deps.reset_store_for_tests(store)
    app = _demo_app()
    yield SimpleNamespace(
        app=app,
        loopback=lambda: TestClient(app, client=("127.0.0.1", 11000)),
        remote=lambda addr="203.0.113.5": TestClient(app, client=(addr, 22000)),
    )
    deps.reset_store_for_tests(None)


def test_a_loopback_request_needs_no_auth_on_any_route(demo):
    c = demo.loopback()
    assert c.get("/demo/protected").json() == {"ok": True, "as": "local"}
    # Including a route keyed to someone else's address: local mode is a full
    # bypass by design, not a "you must own this one account" exemption.
    r = c.post(f"/demo/accounts/{ADDR_B}/activate")
    assert r.status_code == 200
    assert r.json()["activated"] == ADDR_B


def test_a_non_loopback_request_is_refused_until_a_session_is_issued(demo):
    c = demo.remote()
    assert c.get("/demo/protected").status_code == 401

    opened = c.post(f"/demo/connect/{ADDR_A}")
    assert opened.status_code == 200

    ok = c.get("/demo/protected")
    assert ok.status_code == 200
    assert ok.json()["as"] == ADDR_A.lower()


def test_the_cookie_issue_session_for_address_sets_matches_auth_verify(demo):
    c = demo.remote()
    r = c.post(f"/demo/connect/{ADDR_A}")
    raw = r.headers["set-cookie"].lower()
    assert "httponly" in raw
    assert "samesite=lax" in raw
    assert "path=/" in raw
    assert deps.SESSION_COOKIE in raw


def test_a_malformed_address_is_refused_before_anything_is_written(demo):
    c = demo.remote()
    r = c.post("/demo/connect/not-an-address")
    assert r.status_code == 400
    assert c.get("/demo/protected").status_code == 401, \
        "a rejected connect must not have opened a session for anybody"


def test_a_disabled_account_cannot_get_a_session(demo, store):
    store.upsert_user(ADDR_A)
    row = store.get_user_by_address(ADDR_A)
    store._db.execute("UPDATE users SET disabled = 1 WHERE id = ?", (row.id,))
    store._db.commit()
    c = demo.remote()
    r = c.post(f"/demo/connect/{ADDR_A}")
    assert r.status_code == 403


def test_every_connect_mints_a_brand_new_token_never_a_reused_one(demo):
    """issue_session_for_address never accepts a token from the caller — only
    ever mints a fresh one via AuthStore.create_session / stateless.issue_session
    — so there is no code path where a pre-chosen session id could be fixed by
    an attacker and later "upgraded" by a victim's login."""
    c1 = demo.remote("203.0.113.5")
    c2 = demo.remote("203.0.113.6")
    t1 = c1.post(f"/demo/connect/{ADDR_A}").headers["set-cookie"]
    t2 = c2.post(f"/demo/connect/{ADDR_A}").headers["set-cookie"]
    assert t1 != t2, "two connects for the same address must not share a token"


def test_a_session_for_address_a_cannot_manage_address_bs_account(demo):
    # Burn the operator bootstrap on a bystander first — this test is about
    # the address check, not the "first ever" rule covered separately below,
    # and A must be a PLAIN user for "cannot manage B" to mean anything.
    demo.remote("198.51.100.1").post(f"/demo/connect/{ADDR_C}")

    a = demo.remote("203.0.113.5")
    a.post(f"/demo/connect/{ADDR_A}")

    own = a.post(f"/demo/accounts/{ADDR_A}/activate")
    assert own.status_code == 200

    other = a.post(f"/demo/accounts/{ADDR_B}/activate")
    assert other.status_code == 403


def test_the_operator_override_can_manage_any_account_but_a_plain_user_cannot(demo):
    """"Keep the operator gating intact in spirit": the first address ever to
    open a session — through either door now — still becomes operator, and
    operator still means "may act on the house / any connected account",
    exactly like the SIWE-only world before this module existed.
    """
    operator = demo.remote("203.0.113.5")
    operator.post(f"/demo/connect/{ADDR_A}")           # first ever -> operator
    plain_user = demo.remote("203.0.113.6")
    plain_user.post(f"/demo/connect/{ADDR_C}")          # second -> plain user

    r_operator = operator.post(f"/demo/accounts/{ADDR_B}/activate")
    assert r_operator.status_code == 200, "operator may act on an account it does not own"

    r_plain = plain_user.post(f"/demo/accounts/{ADDR_B}/activate")
    assert r_plain.status_code == 403, "a plain user may not act on someone else's account"


def test_no_session_at_all_gets_401_not_403_on_an_account_route(demo):
    """401 (no identity) and 403 (wrong identity) must stay distinguishable:
    collapsing them would make a signed-out caller and a signed-in-but-wrong
    caller look the same to the UI."""
    c = demo.remote()
    r = c.post(f"/demo/accounts/{ADDR_A}/activate")
    assert r.status_code == 401


# ── session_address / session_may_control_account as pure functions ────────

def test_session_address_is_none_with_nothing_presented():
    assert integration.session_address(_BareRequest("203.0.113.5")) is None


def test_require_session_or_local_allows_local_with_no_address(demo):
    req = _BareRequest("127.0.0.1")
    assert integration.require_session_or_local(req) is None


def test_require_session_or_local_401s_a_remote_caller_with_no_session(demo):
    req = _BareRequest("203.0.113.5")
    with pytest.raises(HTTPException) as exc:
        integration.require_session_or_local(req)
    assert exc.value.status_code == 401


def test_session_may_control_account_true_for_local_mode_unconditionally():
    req = _BareRequest("127.0.0.1")
    assert integration.session_may_control_account(req, ADDR_B) is True


def test_session_may_control_account_false_with_no_session_and_not_local():
    req = _BareRequest("203.0.113.5")
    assert integration.session_may_control_account(req, ADDR_B) is False


# ── the operator bootstrap still fires through the new door ────────────────

def test_the_first_address_to_ever_connect_becomes_operator(demo, store):
    first = demo.remote("203.0.113.5")
    first.post(f"/demo/connect/{ADDR_A}")
    second = demo.remote("203.0.113.6")
    second.post(f"/demo/connect/{ADDR_B}")

    assert store.get_user_by_address(ADDR_A).is_operator is True
    assert store.get_user_by_address(ADDR_B).is_operator is False


def test_a_stateless_session_from_issue_session_for_address_can_never_be_operator(
        demo, store, monkeypatch):
    """deps.py already decided a stateless session must always resolve to
    role="user", because minting an operator from a token that only needs to
    be guessed or leaked is worse than one more sign-in prompt. This module
    adds no second way to become operator, so that restriction applies here
    unchanged: session_may_control_account's operator override can never
    fire for a stateless caller acting on someone else's account.
    """
    monkeypatch.setenv("PATHIEL_AUTH_NONCE_SECRET", "s" * 48)
    monkeypatch.setenv("PATHIEL_AUTH_STATELESS_SESSION", "1")
    c = demo.remote("203.0.113.5")
    c.post(f"/demo/connect/{ADDR_A}")   # first ever -> operator row in the DB
    r = c.post(f"/demo/accounts/{ADDR_B}/activate")
    assert r.status_code == 403, \
        "a stateless session must not inherit the DB row's operator role"
