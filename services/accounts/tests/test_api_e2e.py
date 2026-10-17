"""End-to-end tests through FastAPI's TestClient — the full wallet_sign flow
with a fresh key signing the server-returned typed_data and a mocked
/exchange, plus the api_key and watch flows, auth gating, rate limiting, and
the replay attack the confirm route must refuse.

`clients.loopback()` / `clients.remote()` (services/accounts/tests/conftest.py)
simulate a request whose TCP peer is 127.0.0.1 or a real remote address,
mirroring services/auth/tests/test_integration.py's own fixture shape so the
two services' tests read the same way.
"""
from __future__ import annotations

from eth_account import Account as EthAccount
from eth_account.messages import encode_typed_data

from services.accounts import agent_approval, hl_verify, registry, vault
from services.accounts.tests.conftest import login_via_siwe


def _sign_prepare(prepare_body: dict, wallet: EthAccount) -> str:
    structured = encode_typed_data(full_message=prepare_body["typed_data"])
    signed = wallet.sign_message(structured)
    return "0x" + signed.signature.hex()


def _approve_agent_mocks(monkeypatch, approved: bool = True):
    monkeypatch.setattr(agent_approval, "submit_approve_agent",
                        lambda *a, **kw: {"status": "ok"})
    monkeypatch.setattr(hl_verify, "is_agent_approved", lambda *a, **kw: approved)


# ── GET /api/session ──────────────────────────────────────────────────────────

def test_session_local_mode_needs_onboarding_with_no_accounts(clients, monkeypatch):
    monkeypatch.setattr("pathiel.client.exchange.PRIVATE_KEY_HEX", "")
    r = clients.loopback().get("/api/session")
    assert r.status_code == 200
    body = r.json()
    assert body == {
        "local_mode": True, "address": None, "active_account_id": None,
        "accounts_count": 0, "can_trade": False, "needs_onboarding": True,
    }


def test_session_remote_unauthenticated_reports_neutral_state(clients, monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    monkeypatch.setattr("pathiel.client.exchange.PRIVATE_KEY_HEX", "")
    # Even with real accounts present, an unauthenticated remote caller must
    # learn nothing about them.
    registry.add(label="Secret", main_address="0x" + "1" * 40, agent_address=None,
                mode="watch", source="watch")
    r = clients.remote().get("/api/session")
    assert r.status_code == 200
    assert r.json() == {
        "local_mode": False, "address": None, "active_account_id": None,
        "accounts_count": 0, "can_trade": False, "needs_onboarding": True,
    }


def test_session_local_mode_reports_active_account_and_can_trade(clients, tmp_path, monkeypatch):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    registry.add(label="Main", main_address="0x" + "a1" * 20, agent_address="0x" + "b2" * 20,
                mode="trade", source="wallet_sign", agent_name="pathiel", verified=True)
    r = clients.loopback().get("/api/session")
    body = r.json()
    assert body["local_mode"] is True
    assert body["address"] == "0x" + "a1" * 20
    assert body["can_trade"] is True
    assert body["needs_onboarding"] is False
    assert body["accounts_count"] == 1


# ── GET /api/accounts gating ────────────────────────────────────────────────────

def test_list_accounts_local_mode_allowed_with_no_session(clients, tmp_path, monkeypatch):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    r = clients.loopback().get("/api/accounts")
    assert r.status_code == 200
    assert r.json() == []


def test_list_accounts_remote_without_session_is_401(clients):
    r = clients.remote().get("/api/accounts")
    assert r.status_code == 401


# ── wallet_sign: the full prepare -> confirm flow ───────────────────────────────

def test_wallet_sign_flow_end_to_end(clients, monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    _approve_agent_mocks(monkeypatch)
    main_wallet = EthAccount.create()
    c = clients.loopback()

    prep = c.post("/api/accounts/agent/prepare",
                  json={"main_address": main_wallet.address, "label": "My wallet"})
    assert prep.status_code == 200, prep.text
    prep_body = prep.json()
    assert set(prep_body.keys()) == {"request_id", "agent_address", "typed_data", "expires_at"}

    signature = _sign_prepare(prep_body, main_wallet)
    conf = c.post("/api/accounts/agent/confirm",
                  json={"request_id": prep_body["request_id"], "signature": signature})
    assert conf.status_code == 200, conf.text
    account = conf.json()
    assert account["main_address"] == main_wallet.address.lower()
    assert account["agent_address"] == prep_body["agent_address"].lower()
    assert account["mode"] == "trade"
    assert account["source"] == "wallet_sign"
    assert account["is_active"] is True
    assert account["verified_at"] is not None
    assert "private" not in account and "key" not in str(account).lower().replace("mode", "")

    # The key actually landed in the vault, encrypted.
    stored = registry.get(account["id"])
    assert stored is not None
    loaded_key = vault.load_key(account["id"])
    assert loaded_key is not None


def test_wallet_sign_confirm_cannot_be_replayed(clients, monkeypatch, tmp_path):
    """The exact scenario the auth agent flagged: resending a SUCCESSFUL
    (request_id, signature) pair must never succeed twice, because a second
    success would call issue_session_for_address a second time for whoever
    replayed it."""
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    _approve_agent_mocks(monkeypatch)
    main_wallet = EthAccount.create()
    c = clients.loopback()

    prep = c.post("/api/accounts/agent/prepare", json={"main_address": main_wallet.address, "label": "A"})
    prep_body = prep.json()
    signature = _sign_prepare(prep_body, main_wallet)
    body = {"request_id": prep_body["request_id"], "signature": signature}

    first = c.post("/api/accounts/agent/confirm", json=body)
    assert first.status_code == 200

    accounts_after_first = len(registry.list_accounts())

    replay = c.post("/api/accounts/agent/confirm", json=body)
    assert replay.status_code == 404
    assert len(registry.list_accounts()) == accounts_after_first, "a replay must never create a second account"


def test_wallet_sign_confirm_rejects_wrong_signer(clients, monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    _approve_agent_mocks(monkeypatch)
    real_wallet = EthAccount.create()
    impostor = EthAccount.create()
    c = clients.loopback()

    prep = c.post("/api/accounts/agent/prepare", json={"main_address": real_wallet.address, "label": "A"})
    prep_body = prep.json()
    wrong_signature = _sign_prepare(prep_body, impostor)

    conf = c.post("/api/accounts/agent/confirm",
                  json={"request_id": prep_body["request_id"], "signature": wrong_signature})
    assert conf.status_code == 401
    assert registry.list_accounts() == []

    # The request_id is burned even on a failed attempt (pop-before-verify)
    # -- a correct second attempt against the same id must also fail.
    correct_signature = _sign_prepare(prep_body, real_wallet)
    retry = c.post("/api/accounts/agent/confirm",
                   json={"request_id": prep_body["request_id"], "signature": correct_signature})
    assert retry.status_code == 404


def test_wallet_sign_confirm_rejects_unknown_request_id(clients):
    c = clients.loopback()
    conf = c.post("/api/accounts/agent/confirm",
                  json={"request_id": "req_doesnotexist", "signature": "0x" + "11" * 65})
    assert conf.status_code == 404


def test_wallet_sign_confirm_rejects_expired_request(clients, monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    _approve_agent_mocks(monkeypatch)
    wallet = EthAccount.create()
    c = clients.loopback()

    prep = c.post("/api/accounts/agent/prepare", json={"main_address": wallet.address, "label": "A"})
    prep_body = prep.json()
    signature = _sign_prepare(prep_body, wallet)

    from services.accounts import pending as pending_mod
    import dataclasses
    from datetime import datetime, timedelta, timezone
    current = pending_mod._STORE[prep_body["request_id"]]
    pending_mod._STORE[prep_body["request_id"]] = dataclasses.replace(
        current, expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))

    conf = c.post("/api/accounts/agent/confirm",
                  json={"request_id": prep_body["request_id"], "signature": signature})
    # Pins the exact contract wallet-ui's bundle relies on for its auto-retry
    # (re-call prepare): a non-404 status, and "expir" (case-insensitive) in
    # the error detail. Both are load-bearing, not cosmetic.
    assert conf.status_code in (400, 410)
    assert conf.status_code != 404
    assert "expir" in conf.json()["detail"].lower()
    assert "request expired" in conf.json()["detail"].lower()
    # A second attempt against the same (now-doubly-dead) id is the ordinary
    # "not found" case, not expired again -- single-use either way.
    retry = c.post("/api/accounts/agent/confirm",
                   json={"request_id": prep_body["request_id"], "signature": signature})
    assert retry.status_code == 404
    assert "expir" not in retry.json()["detail"].lower()


def test_wallet_sign_confirm_502s_when_hyperliquid_never_confirms_the_approval(clients, monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    _approve_agent_mocks(monkeypatch, approved=False)
    wallet = EthAccount.create()
    c = clients.loopback()

    prep = c.post("/api/accounts/agent/prepare", json={"main_address": wallet.address, "label": "A"})
    prep_body = prep.json()
    signature = _sign_prepare(prep_body, wallet)

    conf = c.post("/api/accounts/agent/confirm",
                  json={"request_id": prep_body["request_id"], "signature": signature})
    assert conf.status_code == 502
    assert registry.list_accounts() == []


# ── api_key import ───────────────────────────────────────────────────────────────

def test_import_api_key_success(clients, monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(hl_verify, "is_agent_approved", lambda *a, **kw: True)
    main = EthAccount.create()
    agent = EthAccount.create()
    c = clients.loopback()

    r = c.post("/api/accounts/import", json={
        "main_address": main.address, "agent_private_key": agent.key.hex(), "label": "Imported",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["main_address"] == main.address.lower()
    assert body["agent_address"] == agent.address.lower()
    assert body["source"] == "api_key"
    assert vault.load_key(body["id"]) is not None


def test_import_api_key_rejected_when_not_approved(clients, monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(hl_verify, "is_agent_approved", lambda *a, **kw: False)
    main = EthAccount.create()
    agent = EthAccount.create()
    c = clients.loopback()

    r = c.post("/api/accounts/import", json={
        "main_address": main.address, "agent_private_key": agent.key.hex(), "label": "Imported",
    })
    assert r.status_code == 400
    assert registry.list_accounts() == []


def test_second_connected_account_never_steals_activation(clients, monkeypatch, tmp_path):
    """wallet-ui's bundle auto-activates a freshly connected account only
    when it is the very first one; the server must enforce the identical
    rule so the two can never disagree about which account actually trades."""
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    _approve_agent_mocks(monkeypatch)
    c = clients.loopback()

    first_wallet = EthAccount.create()
    prep = c.post("/api/accounts/agent/prepare",
                  json={"main_address": first_wallet.address, "label": "First"}).json()
    sig = _sign_prepare(prep, first_wallet)
    first = c.post("/api/accounts/agent/confirm",
                   json={"request_id": prep["request_id"], "signature": sig}).json()
    assert first["is_active"] is True

    second = c.post("/api/accounts/watch", json={"address": "0x" + "c3" * 20, "label": "Second"}).json()
    assert second["is_active"] is False

    session = c.get("/api/session").json()
    assert session["active_account_id"] == first["id"]


# ── watch ──────────────────────────────────────────────────────────────────────

def test_watch_flow(clients, tmp_path, monkeypatch):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    c = clients.loopback()
    r = c.post("/api/accounts/watch", json={"address": "0x" + "c3" * 20, "label": "Whale"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "watch"
    assert body["source"] == "watch"
    assert body["agent_address"] is None


# ── activate / rename / delete + authorization gating ───────────────────────────

def test_activate_rename_delete_allowed_locally_with_no_session(clients, tmp_path, monkeypatch):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    c = clients.loopback()
    a = c.post("/api/accounts/watch", json={"address": "0x" + "1" * 40, "label": "A"}).json()
    b = c.post("/api/accounts/watch", json={"address": "0x" + "2" * 40, "label": "B"}).json()

    act = c.post(f"/api/accounts/{a['id']}/activate")
    assert act.status_code == 200
    assert act.json()["is_active"] is True

    ren = c.patch(f"/api/accounts/{b['id']}", json={"label": "B renamed"})
    assert ren.status_code == 200
    assert ren.json()["label"] == "B renamed"

    dele = c.delete(f"/api/accounts/{b['id']}")
    assert dele.status_code == 200
    assert dele.json() == {"ok": True}
    assert registry.get(b["id"]) is None


def test_mutating_routes_401_for_a_remote_caller_with_no_session(clients, tmp_path, monkeypatch):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    stored = registry.add(label="A", main_address="0x" + "1" * 40, agent_address=None,
                          mode="watch", source="watch")
    remote = clients.remote()
    assert remote.post(f"/api/accounts/{stored.id}/activate").status_code == 401
    assert remote.patch(f"/api/accounts/{stored.id}", json={"label": "x"}).status_code == 401
    assert remote.delete(f"/api/accounts/{stored.id}").status_code == 401


def test_mutating_routes_403_for_a_session_that_owns_a_different_address(clients, monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    owner_wallet = EthAccount.create()
    other_wallet = EthAccount.create()

    # services/auth bootstraps the FIRST wallet ever to sign in as this
    # deployment's operator, and an operator may control any account by
    # design (session_may_control_account's own documented override). A
    # throwaway third wallet burns that bootstrap slot first so `other_wallet`
    # below is an ordinary, non-operator user -- the actual case being tested.
    login_via_siwe(clients.remote(addr="203.0.113.1"), EthAccount.create())

    # `other_wallet` establishes its OWN session via SIWE (the only way a
    # remote caller gets a first session at all -- see
    # services.auth.integration.require_session_or_local).
    other_client = clients.remote(addr="203.0.113.9")
    login_via_siwe(other_client, other_wallet)

    # A pre-existing account belonging to a DIFFERENT address.
    owned = registry.add(label="Owner's", main_address=owner_wallet.address, agent_address=None,
                         mode="watch", source="watch", activate=False)

    r = other_client.post(f"/api/accounts/{owned.id}/activate")
    assert r.status_code == 403


def test_mutating_routes_200_for_the_session_that_owns_the_account(clients, monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    _approve_agent_mocks(monkeypatch)
    wallet = EthAccount.create()

    remote = clients.remote(addr="203.0.113.10")
    login_via_siwe(remote, wallet)  # establishes the session agent/prepare now requires

    prep = remote.post("/api/accounts/agent/prepare",
                       json={"main_address": wallet.address, "label": "Mine"}).json()
    sig = _sign_prepare(prep, wallet)
    confirmed = remote.post("/api/accounts/agent/confirm",
                            json={"request_id": prep["request_id"], "signature": sig}).json()

    r = remote.patch(f"/api/accounts/{confirmed['id']}", json={"label": "Renamed by owner"})
    assert r.status_code == 200
    assert r.json()["label"] == "Renamed by owner"


def test_activate_unknown_account_is_404(clients):
    r = clients.loopback().post("/api/accounts/acc_doesnotexist/activate")
    assert r.status_code == 404


def test_env_account_cannot_be_deleted_or_renamed(clients, monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    monkeypatch.setattr("pathiel.client.exchange.PRIVATE_KEY_HEX", "deadbeef")
    monkeypatch.setattr("pathiel.client.exchange.HL_WALLET", "0x" + "9" * 40)
    monkeypatch.setattr("pathiel.client.exchange.HL_MASTER", "")
    monkeypatch.setattr("pathiel.client.exchange.IS_AGENT", False)
    monkeypatch.setattr("pathiel.client.exchange.HL_ACCOUNT", "0x" + "9" * 40)
    c = clients.loopback()

    assert c.delete("/api/accounts/env").status_code == 400
    assert c.patch("/api/accounts/env", json={"label": "x"}).status_code == 400
    assert c.post("/api/accounts/env/activate").status_code == 200


# ── rate limiting ────────────────────────────────────────────────────────────────

def test_prepare_is_rate_limited(clients, monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    from services.accounts import api as accounts_api
    monkeypatch.setattr(accounts_api, "_MAX_ATTEMPTS", 3)
    c = clients.loopback()
    wallet = EthAccount.create()
    for _ in range(3):
        r = c.post("/api/accounts/agent/prepare", json={"main_address": wallet.address, "label": "A"})
        assert r.status_code == 200
    blocked = c.post("/api/accounts/agent/prepare", json={"main_address": wallet.address, "label": "A"})
    assert blocked.status_code == 429


def test_rate_limit_is_keyed_per_client_not_global(clients, monkeypatch, tmp_path):
    """Two logical clients, kept in local mode (no session plumbing needed)
    via a real loopback TCP peer, distinguished only by X-Forwarded-For —
    exactly the signal _client() in services/accounts/api.py keys on."""
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    from services.accounts import api as accounts_api
    monkeypatch.setattr(accounts_api, "_MAX_ATTEMPTS", 1)
    wallet = EthAccount.create()
    c = clients.loopback()
    headers_a = {"x-forwarded-for": "203.0.113.20"}
    headers_b = {"x-forwarded-for": "203.0.113.21"}
    body = {"main_address": wallet.address, "label": "A"}

    assert c.post("/api/accounts/agent/prepare", json=body, headers=headers_a).status_code == 200
    assert c.post("/api/accounts/agent/prepare", json=body, headers=headers_a).status_code == 429
    assert c.post("/api/accounts/agent/prepare", json=body, headers=headers_b).status_code == 200


# ── public mode is single-operator (integrator, 2026-10-17) ───────────────────

def test_a_second_wallet_cannot_hijack_the_bot_in_public_mode(clients, monkeypatch, tmp_path):
    """pathiel runs ONE bot on ONE active account. Before this rule, any wallet
    that signed in remotely could connect its own account and activate it,
    switching the operator's bot onto a stranger's account."""
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    _approve_agent_mocks(monkeypatch)
    operator, stranger = EthAccount.create(), EthAccount.create()

    op = clients.remote(addr="203.0.113.20")
    login_via_siwe(op, operator)                     # first wallet ever: operator
    prep = op.post("/api/accounts/agent/prepare",
                   json={"main_address": operator.address, "label": "Op"}).json()
    op_acc = op.post("/api/accounts/agent/confirm",
                     json={"request_id": prep["request_id"],
                           "signature": _sign_prepare(prep, operator)}).json()

    st = clients.remote(addr="203.0.113.21")
    login_via_siwe(st, stranger)                     # second wallet: plain user
    r = st.post("/api/accounts/agent/prepare",
                json={"main_address": stranger.address, "label": "Mine"})
    assert r.status_code == 403
    assert st.post(f"/api/accounts/{op_acc['id']}/activate").status_code == 403
    assert st.post("/api/accounts/watch", json={"address": stranger.address}).status_code == 403
    assert st.get("/api/accounts").status_code == 403


def test_fresh_deployment_lets_the_first_connect_through_then_closes(clients, monkeypatch, tmp_path):
    """No wallet has ever held a session: the first remote connect may start
    without one (it becomes the operator). After that, a session is required."""
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("PATHIEL_AUTH_NO_BOOTSTRAP_OPERATOR", raising=False)
    _approve_agent_mocks(monkeypatch)
    first = EthAccount.create()
    c = clients.remote(addr="203.0.113.30")
    prep = c.post("/api/accounts/agent/prepare", json={"main_address": first.address})
    assert prep.status_code == 200
    ok = c.post("/api/accounts/agent/confirm",
                json={"request_id": prep.json()["request_id"],
                      "signature": _sign_prepare(prep.json(), first)})
    assert ok.status_code == 200
    late = clients.remote(addr="203.0.113.31")
    assert late.post("/api/accounts/agent/prepare",
                     json={"main_address": EthAccount.create().address}).status_code == 401


def test_bootstrap_can_be_disabled(clients, monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("PATHIEL_AUTH_NO_BOOTSTRAP_OPERATOR", "1")
    c = clients.remote(addr="203.0.113.40")
    assert c.post("/api/accounts/agent/prepare",
                  json={"main_address": EthAccount.create().address}).status_code == 401
