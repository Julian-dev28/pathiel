"""API keys belong to a wallet, and to nobody else.

The property that matters most here is isolation: with two customers on one
deployment, a bug that lets one see or revoke the other's credentials is the
whole product's credibility. Every test below is about ownership or about the
key never being recoverable after minting.
"""
from __future__ import annotations

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.auth import api as auth_api
from services.auth import deps
from services.auth.api_keys import hash_token as api_keys_hash
from services.auth.store import AuthStore

ALICE = Account.from_key("0x" + "a1" * 32)
BOB = Account.from_key("0x" + "b2" * 32)
DOMAIN = "pathia.test"


@pytest.fixture
def app_db(tmp_path, monkeypatch):
    """A throwaway data-API database, so key rows never touch the real one.

    Points services/auth at a temp sqlite file through the same env var the data
    API reads, which is the whole coupling: one file, one schema, no imports.
    """
    path = tmp_path / "data.db"
    monkeypatch.setenv("PATHIA_DATABASE_URL", f"sqlite:///{path}")
    from services.auth import api_keys
    api_keys.ensure_schema()
    return path


@pytest.fixture
def client(tmp_path, monkeypatch, app_db):
    monkeypatch.setenv("PATHIA_AUTH_DOMAIN", DOMAIN)
    monkeypatch.setenv("PATHIA_INSECURE_COOKIES", "1")
    store = AuthStore(str(tmp_path / "auth.db"))
    deps.reset_store_for_tests(store)
    auth_api._ATTEMPTS.clear()
    app = FastAPI()
    app.include_router(auth_api.router)
    yield TestClient(app)
    deps.reset_store_for_tests(None)
    store.close()


def sign_in(client, acct):
    msg = client.get("/auth/nonce", params={"address": acct.address}).json()["message"]
    sig = acct.sign_message(encode_defunct(text=msg)).signature.hex()
    r = client.post("/auth/verify", json={"message": msg, "signature": sig})
    assert r.status_code == 200, r.text
    return r.json()["session_token"]


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_a_signed_in_wallet_can_mint_a_key(client):
    tok = sign_in(client, ALICE)
    r = client.post("/auth/keys", json={"label": "backtests"}, headers=auth(tok))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token"].startswith("pk_live_")
    assert body["shown_once"] is True


def test_the_raw_key_is_never_recoverable_after_minting(client, app_db):
    """Only the SHA-256 is stored, so a database leak yields no usable
    credential, and "show it to me again" cannot be built."""
    import sqlite3
    tok = sign_in(client, ALICE)
    raw = client.post("/auth/keys", json={}, headers=auth(tok)).json()["token"]
    assert raw.encode() not in app_db.read_bytes(), "raw key found on disk"
    with sqlite3.connect(app_db) as conn:
        stored = [r[0] for r in conn.execute("SELECT token_hash FROM api_keys")]
    assert raw not in stored
    assert api_keys_hash(raw) in stored
    # and it is never echoed back by any listing
    listed = client.get("/auth/keys", headers=auth(tok)).json()
    assert raw not in str(listed)
    assert "token_hash" not in str(listed)


def test_the_two_services_still_hash_a_token_the_same_way(client):
    """The only contract between them is the table plus sha256(raw). If the two
    ever drift, a customer mints a key that opens nothing and neither service
    logs a thing, so the agreement is asserted rather than assumed.

    Read as source, not imported: the data API is a separate deploy unit whose
    dependencies are not installed here.
    """
    import pathlib
    from services.auth import api_keys
    src = (pathlib.Path(__file__).resolve().parents[2]
           / "pathia_data_api" / "app" / "auth.py").read_text()
    body = src[src.index("def hash_token("):]
    assert "sha256" in body and ".hexdigest()" in body, body[:200]
    assert api_keys.hash_token("abc") == __import__("hashlib").sha256(b"abc").hexdigest()


def test_one_customer_cannot_see_another_customers_keys(client):
    a_tok = sign_in(client, ALICE)
    client.post("/auth/keys", json={"label": "alice"}, headers=auth(a_tok))
    client.cookies.clear()
    b_tok = sign_in(client, BOB)
    assert client.get("/auth/keys", headers=auth(b_tok)).json()["keys"] == []
    client.cookies.clear()
    assert len(client.get("/auth/keys", headers=auth(a_tok)).json()["keys"]) == 1


def test_one_customer_cannot_revoke_another_customers_key(client):
    """Ownership is in the WHERE clause, not a Python check after the row is
    loaded: a test that runs after the fetch is one early return from skipped."""
    a_tok = sign_in(client, ALICE)
    key_id = client.post("/auth/keys", json={}, headers=auth(a_tok)).json()["key_id"]
    client.cookies.clear()
    b_tok = sign_in(client, BOB)
    r = client.delete(f"/auth/keys/{key_id}", headers=auth(b_tok))
    # 404 not 403: confirming the id exists but is not yours is an enumeration
    # oracle for free.
    assert r.status_code == 404
    client.cookies.clear()
    assert client.get("/auth/keys", headers=auth(a_tok)).json()["keys"][0]["active"] is True


def test_revoking_your_own_key_deactivates_it(client):
    tok = sign_in(client, ALICE)
    key_id = client.post("/auth/keys", json={}, headers=auth(tok)).json()["key_id"]
    assert client.delete(f"/auth/keys/{key_id}", headers=auth(tok)).status_code == 200
    assert client.get("/auth/keys", headers=auth(tok)).json()["keys"][0]["active"] is False


def test_keys_are_capped_per_account(client):
    """Unbounded minting is a free way to fill the table and to spread credentials
    past anyone's ability to rotate them."""
    from services.auth import api_keys
    tok = sign_in(client, ALICE)
    for _ in range(api_keys.MAX_KEYS_PER_OWNER):
        assert client.post("/auth/keys", json={}, headers=auth(tok)).status_code == 200
    assert client.post("/auth/keys", json={}, headers=auth(tok)).status_code == 409


def test_a_new_key_does_not_inherit_the_wildcard_scope(client):
    """The demo seed key carries "*". A customer key inheriting that would hand
    out every scope this API ever grows, including ones added years later."""
    tok = sign_in(client, ALICE)
    client.post("/auth/keys", json={}, headers=auth(tok))
    scopes = client.get("/auth/keys", headers=auth(tok)).json()["keys"][0]["scopes"]
    assert "*" not in scopes
    assert "signals:read" in scopes


def test_signing_out_locks_the_keys(client):
    tok = sign_in(client, ALICE)
    client.post("/auth/logout")
    client.cookies.clear()
    assert client.get("/auth/keys", headers=auth(tok)).status_code == 401
    assert client.post("/auth/keys", json={}, headers=auth(tok)).status_code == 401


def test_anonymous_callers_get_nothing(client):
    assert client.get("/auth/keys").status_code == 401
    assert client.post("/auth/keys", json={}).status_code == 401
    assert client.delete("/auth/keys/key_whatever").status_code == 401


def test_the_owner_column_is_added_to_a_table_that_predates_it(app_db):
    """create_all builds missing tables and never alters an existing one, so a
    deployment already holding pathia_data.db would keep a table with no owner
    column and raise on every ownership query. Idempotent, because it runs on
    every request path."""
    import sqlite3
    from services.auth import api_keys
    with sqlite3.connect(app_db) as conn:          # the pre-ownership shape
        conn.execute("DROP TABLE api_keys")
        conn.execute("CREATE TABLE api_keys (id INTEGER PRIMARY KEY, key_id TEXT, "
                     "token_hash TEXT, plan TEXT, scopes TEXT, rate_per_min INT, "
                     "active BOOLEAN, created_at TEXT)")
    api_keys.ensure_schema()
    api_keys.ensure_schema()
    with sqlite3.connect(app_db) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(api_keys)")}
    assert "owner_address" in cols


# ── the house account is not your account ───────────────────────────────────

def test_a_signed_in_customer_never_sees_the_house_balance(client, monkeypatch, tmp_path):
    """Reported by the operator 2026-09-04: sign in with any wallet and the
    dashboard showed $12.94 — the deployment's balance, presented as yours.

    Gating these routes behind a session (4c14aba) stopped strangers reading
    them and did nothing about this: every authenticated user still read the one
    house account. A number a user believes is theirs is worse than a number
    they cannot see.
    """
    from fastapi.testclient import TestClient
    from pathia.server import app

    monkeypatch.delenv("PATHIA_PUBLIC_DASHBOARD", raising=False)
    monkeypatch.setenv("PATHIA_INSECURE_COOKIES", "1")
    c = TestClient(app)

    # BOB signs in second, so he is a plain user, not the operator.
    for acct in (ALICE, BOB):
        msg = c.get("/auth/nonce", params={"address": acct.address}).json()["message"]
        sig = acct.sign_message(encode_defunct(text=msg)).signature.hex()
        c.post("/auth/verify", json={"message": msg, "signature": sig})

    for house in ("/api/dashboard/summary", "/api/dashboard/risk",
                  "/api/dashboard/positions", "/api/dashboard/closed-trades",
                  "/api/dashboard/equity-curve", "/api/dashboard/funnel",
                  "/api/dashboard/book_league"):
        r = c.get(house)
        assert r.status_code == 403, f"{house} showed the house book to a customer"
        assert "equity" not in r.json(), f"{house} leaked a balance inside its 403"


def test_the_account_route_reads_the_caller_not_the_deployment(client, monkeypatch):
    """A wallet's own balance comes from its own address. No key is stored and
    none is needed: /info clearinghouseState takes a plain address, so the
    product cannot trade on a customer's behalf even by accident."""
    import pathia.dashboard as db
    seen = {}

    def fake_state(user, include_hip3=False):
        seen["addr"] = user
        return {"equity": 4321.0, "available": 1234.0, "asset_positions": []}

    import pathia.client.hl_client as hl
    monkeypatch.setattr(hl, "fetch_account_state", fake_state)
    db._ACCOUNT_CACHE.clear()
    out = db._viewer_account_payload(BOB.address)
    assert seen["addr"] == BOB.address.lower(), "read somebody else's address"
    assert out["equity"] == 4321.0 and out["funded"] is True


def test_an_empty_wallet_reads_as_unfunded_not_as_a_loss(monkeypatch):
    """Somebody who just connected a wallet has no Hyperliquid account. That is
    the ordinary starting state, and rendering it as a row of zeros looks like
    a drawdown rather than an empty account."""
    import pathia.dashboard as db
    import pathia.client.hl_client as hl
    monkeypatch.setattr(hl, "fetch_account_state",
                        lambda user, include_hip3=False: {"equity": 0.0, "asset_positions": []})
    db._ACCOUNT_CACHE.clear()
    out = db._viewer_account_payload("0x" + "9" * 40)
    assert out["funded"] is False and out["equity"] == 0.0
    assert out["status"] == "ok"


def test_a_failing_venue_read_does_not_render_as_a_zero_balance(monkeypatch):
    """Zero is a number a user will believe. A rate-limited read has to say it
    could not answer."""
    import pathia.dashboard as db
    import pathia.client.hl_client as hl

    def boom(user, include_hip3=False):
        raise RuntimeError("429 rate limited")

    monkeypatch.setattr(hl, "fetch_account_state", boom)
    db._ACCOUNT_CACHE.clear()
    out = db._viewer_account_payload("0x" + "8" * 40)
    assert out["status"] == "unavailable" and out["funded"] is False
