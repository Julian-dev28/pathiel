"""Shared fixtures for services/accounts' own test suite.

Every test gets: a fresh throwaway PATHIEL_STATE_DIR (so registry.json and
the vault never touch another test or the real state dir), a cheap vault KDF
(PATHIEL_VAULT_FAST_KDF — see services/accounts/vault.py's module docstring
for why this is safe: nothing in production ever sets it), a fresh
tmp_path-backed AuthStore wired into services.auth.deps exactly like
services/auth's own test suite does, and a clean slate for the two
module-level stores this service keeps in process memory
(services.accounts.pending's pending-request dict and
services.accounts.api's rate-limit dict).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.accounts import credentials, pending
from services.accounts import api as accounts_api
from services.accounts.api import router as accounts_router
from services.auth import deps as auth_deps
from services.auth.api import router as auth_router
from services.auth.store import AuthStore


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("PATHIEL_VAULT_FAST_KDF", "1")
    monkeypatch.delenv("PATHIEL_VAULT_PASSPHRASE", raising=False)
    pending.clear_for_tests()
    credentials.invalidate_cache()
    accounts_api._ATTEMPTS.clear()
    yield
    pending.clear_for_tests()
    credentials.invalidate_cache()
    accounts_api._ATTEMPTS.clear()


@pytest.fixture
def auth_store(tmp_path):
    store = AuthStore(str(tmp_path / "auth.db"))
    auth_deps.reset_store_for_tests(store)
    yield store
    auth_deps.reset_store_for_tests(None)
    store.close()


@pytest.fixture
def app(auth_store):
    """Mounts BOTH routers, matching how pathiel/server.py will mount them
    for real. services.auth's SIWE login is how a REMOTE caller gets its
    FIRST session at all — services.accounts' own "connect" routes
    (agent/prepare, agent/confirm, import, watch) deliberately require one
    to already exist remotely (services.auth.integration.require_session_or_local),
    local mode being the only front door with no session requirement. See
    login_via_siwe() below for how tests establish one."""
    a = FastAPI()
    a.include_router(accounts_router)
    a.include_router(auth_router)
    return a


@pytest.fixture
def clients(app, monkeypatch):
    """TestClient factories for the two request shapes every gated route
    cares about — mirrors services/auth/tests/test_integration.py's own
    `demo` fixture so the two services' tests read the same way.

    PATHIEL_INSECURE_COOKIES=1: TestClient speaks plain http, and a browser
    (and TestClient's own cookiejar) silently drops a Secure cookie there —
    without this, every "then the session cookie round-trips" assertion
    would fail for a reason unrelated to what is being tested.
    """
    monkeypatch.setenv("PATHIEL_INSECURE_COOKIES", "1")
    return SimpleNamespace(
        loopback=lambda: TestClient(app, client=("127.0.0.1", 11000)),
        remote=lambda addr="203.0.113.5": TestClient(app, client=(addr, 22000)),
    )


def login_via_siwe(client: TestClient, wallet) -> None:
    """Establish a real SIWE session for `wallet` on `client` — the one way
    a REMOTE (non-local) caller gets a session at all. Mirrors
    services/auth/tests/test_store_and_api.py's own login() helper so a
    services/accounts test reads the same way a services/auth test would.
    Sets the session cookie on `client`; callers then act on it with that
    same TestClient instance."""
    from eth_account.messages import encode_defunct

    nonce_resp = client.get("/auth/nonce", params={"address": wallet.address})
    message = nonce_resp.json()["message"]
    signature = wallet.sign_message(encode_defunct(text=message)).signature.hex()
    verify_resp = client.post("/auth/verify", json={"message": message, "signature": signature})
    assert verify_resp.status_code == 200, verify_resp.text
