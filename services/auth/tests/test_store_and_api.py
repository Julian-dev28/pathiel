"""Session, nonce and route behaviour. Same rule as test_siwe: each test names
the thing that goes wrong without it."""
from __future__ import annotations

import hashlib
import time
from pathlib import Path

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.auth import api as auth_api
from services.auth import deps
from services.auth.store import AuthStore

ACCT = Account.from_key("0x" + "33" * 32)
SECOND = Account.from_key("0x" + "44" * 32)
DOMAIN = "pathia.test"


@pytest.fixture
def store(tmp_path):
    s = AuthStore(str(tmp_path / "auth.db"))
    yield s
    s.close()


@pytest.fixture
def client(store, monkeypatch):
    monkeypatch.setenv("PATHIA_AUTH_DOMAIN", DOMAIN)
    monkeypatch.setenv("PATHIA_INSECURE_COOKIES", "1")   # TestClient speaks http
    deps.reset_store_for_tests(store)
    auth_api._ATTEMPTS.clear()
    app = FastAPI()
    app.include_router(auth_api.router)
    yield TestClient(app)
    deps.reset_store_for_tests(None)


def login(client, acct=ACCT):
    r = client.get("/auth/nonce", params={"address": acct.address})
    msg = r.json()["message"]
    sig = acct.sign_message(encode_defunct(text=msg)).signature.hex()
    return msg, sig, client.post("/auth/verify", json={"message": msg, "signature": sig})


# ── nonces ──────────────────────────────────────────────────────────────────

def test_a_nonce_works_once(store):
    """Without single use, one captured signature is a permanent password."""
    n = store.issue_nonce()
    assert store.consume_nonce(n) is True
    assert store.consume_nonce(n) is False


def test_an_expired_nonce_is_refused(store):
    n = store.issue_nonce(now=time.time() - 10_000)
    assert store.consume_nonce(n) is False


def test_an_unknown_nonce_is_refused(store):
    assert store.consume_nonce("never-issued") is False


# ── sessions ────────────────────────────────────────────────────────────────

def test_the_session_token_is_never_stored_in_the_clear(store):
    """A leaked database file — a backup, a snapshot, a stray SELECT in a log —
    must not hand the reader a set of live sessions."""
    u = store.upsert_user(ACCT.address)
    token = store.create_session(u.id)
    # Every file a leak would carry, not just the main one: in WAL mode a
    # recent write lives in auth.db-wal until it is checkpointed, and a backup
    # or snapshot takes the whole set.
    raw = b"".join(Path(store.path + suffix).read_bytes()
                   for suffix in ("", "-wal", "-shm")
                   if Path(store.path + suffix).exists())
    assert token.encode() not in raw, "session token found verbatim on disk"
    assert hashlib.sha256(token.encode()).hexdigest().encode() in raw


def test_an_expired_session_stops_resolving(store):
    u = store.upsert_user(ACCT.address)
    token = store.create_session(u.id, now=time.time() - 10 ** 7)
    assert store.session_user(token) is None


def test_disabling_an_account_kills_its_live_sessions(store):
    """Revoking access must not depend on also finding every session row: the
    check is on the join, so the account flag is sufficient on its own."""
    u = store.upsert_user(ACCT.address)
    token = store.create_session(u.id)
    assert store.session_user(token) is not None
    store._db.execute("UPDATE users SET disabled = 1 WHERE id = ?", (u.id,))
    store._db.commit()
    assert store.session_user(token) is None


def test_logout_everywhere_drops_every_session(store):
    u = store.upsert_user(ACCT.address)
    tokens = [store.create_session(u.id) for _ in range(3)]
    assert store.revoke_all_for_user(u.id) == 3
    assert all(store.session_user(t) is None for t in tokens)


# ── identity ────────────────────────────────────────────────────────────────

def test_one_wallet_is_one_user_whatever_the_casing(store):
    """EIP-55 checksummed and lowercase forms are the same account. Two rows
    would be two identities with different balances attached."""
    a = store.upsert_user(ACCT.address)
    b = store.upsert_user(ACCT.address.lower())
    c = store.upsert_user(ACCT.address.upper().replace("0X", "0x"))
    assert a.id == b.id == c.id
    assert len(store.list_users()) == 1


def test_the_first_account_to_sign_in_owns_the_deployment(store):
    """A fresh box with no operator would let whoever finds the URL first claim
    the kill switch. Seeding from the installer's own login closes that without
    a bootstrap password to leak."""
    first = store.upsert_user(ACCT.address)
    second = store.upsert_user(SECOND.address)
    assert first.is_operator is True
    assert second.is_operator is False


# ── routes ──────────────────────────────────────────────────────────────────

def test_a_real_wallet_can_sign_in_and_is_remembered(client):
    _, _, r = login(client)
    assert r.status_code == 200, r.text
    assert r.json()["user"]["address"] == ACCT.address.lower()
    assert client.get("/auth/me").json()["user"]["address"] == ACCT.address.lower()


def test_the_same_signature_cannot_be_replayed(client):
    """The captured-signature attack, end to end through the routes."""
    msg, sig, first = login(client)
    assert first.status_code == 200
    client.cookies.clear()
    again = client.post("/auth/verify", json={"message": msg, "signature": sig})
    assert again.status_code == 401


def test_no_cookie_means_no_identity(client):
    assert client.get("/auth/me").status_code == 401


def test_logout_actually_invalidates_the_session(client):
    _, _, r = login(client)
    token = r.json()["session_token"]
    client.post("/auth/logout")
    client.cookies.clear()
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_a_cli_can_use_the_token_as_a_bearer(client):
    """Scripts cannot hold cookies; the same session must work either way."""
    _, _, r = login(client)
    token = r.json()["session_token"]
    client.cookies.clear()
    got = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert got.status_code == 200


def test_the_session_cookie_is_httponly_and_samesite(client):
    """httpOnly keeps XSS from reading it; SameSite is what stands in for CSRF
    tokens, since every mutating route here is a POST."""
    _, _, r = login(client)
    raw = r.headers["set-cookie"].lower()
    assert "httponly" in raw and "samesite=lax" in raw and "path=/" in raw


def test_a_failed_login_says_nothing_about_which_half_was_wrong(client):
    """Distinguishing a bad nonce from a bad signature hands an attacker an
    oracle for narrowing the search."""
    r = client.get("/auth/nonce", params={"address": ACCT.address})
    msg = r.json()["message"]
    bad_sig = SECOND.sign_message(encode_defunct(text=msg)).signature.hex()
    wrong = client.post("/auth/verify", json={"message": msg, "signature": bad_sig})
    stale = client.post("/auth/verify",
                        json={"message": msg.replace(r.json()["nonce"], "x" * 22),
                              "signature": bad_sig})
    assert wrong.status_code == stale.status_code == 401
    assert wrong.json()["detail"] == stale.json()["detail"] == "signature rejected"


def test_a_malformed_address_never_reaches_the_database(client):
    for bad in ("", "0x", "nope", "0x" + "z" * 40):
        assert client.get("/auth/nonce", params={"address": bad}).status_code in (400, 422)


def test_login_is_rate_limited(client):
    """Signature recovery is real CPU work, and this route is anonymous by
    definition, so it is the cheapest way to burn the box."""
    codes = [client.get("/auth/nonce", params={"address": ACCT.address}).status_code
             for _ in range(auth_api._MAX_ATTEMPTS + 5)]
    assert 429 in codes


def test_the_profile_is_editable_and_scoped_to_the_caller(client):
    login(client)
    r = client.patch("/auth/profile", json={"display_name": "Julien", "email": "j@example.com"})
    assert r.status_code == 200
    assert r.json()["user"]["display_name"] == "Julien"
    assert client.get("/auth/me").json()["user"]["display_name"] == "Julien"


def test_the_signed_statement_promises_no_transaction(client):
    """A wallet popup that looks like it might move funds trains users to
    approve things they should not. The text has to say what it is not."""
    msg = client.get("/auth/nonce", params={"address": ACCT.address}).json()["message"]
    assert "does not approve any transaction" in msg
