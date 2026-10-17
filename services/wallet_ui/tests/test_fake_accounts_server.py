"""Gate tests for fake_accounts_server.py itself.

Not browser tests — plain FastAPI TestClient calls, <1s. This is the test
infrastructure the browser suite (test_wallet_account_flow_browser.py) relies
on to prove the bundle signs exactly what it was given; if THIS file's
signature verification or expiry check were wrong, the browser suite could
pass for the wrong reasons. Cheap enough to just test directly.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from eth_account import Account as EthAccount
from eth_account.messages import encode_typed_data
from fastapi.testclient import TestClient

from services.wallet_ui.tests.fake_accounts_server import build_app


def _sign(prepare_body: dict, key) -> str:
    sm = encode_typed_data(full_message=prepare_body["typed_data"])
    signed = EthAccount.sign_message(sm, private_key=key.key)
    return "0x" + signed.signature.hex()


@pytest.fixture
def client():
    return TestClient(build_app())


def test_session_starts_empty(client):
    body = client.get("/api/session").json()
    assert body == {
        "local_mode": True, "address": None, "active_account_id": None,
        "accounts_count": 0, "can_trade": False, "needs_onboarding": True,
    }


def test_prepare_returns_the_approve_agent_shape(client):
    key = EthAccount.create()
    res = client.post("/api/accounts/agent/prepare", json={"main_address": key.address, "label": "Main"})
    assert res.status_code == 200
    body = res.json()
    assert body["typed_data"]["primaryType"] == "HyperliquidTransaction:ApproveAgent"
    assert body["typed_data"]["domain"]["chainId"] == 42161
    assert body["typed_data"]["message"]["hyperliquidChain"] == "Mainnet"


def test_confirm_with_a_correct_signature_creates_and_activates_the_account(client):
    key = EthAccount.create()
    prep = client.post("/api/accounts/agent/prepare", json={"main_address": key.address}).json()
    sig = _sign(prep, key)
    res = client.post("/api/accounts/agent/confirm", json={"request_id": prep["request_id"], "signature": sig})
    assert res.status_code == 200
    account = res.json()
    assert account["main_address"].lower() == key.address.lower()
    assert account["mode"] == "trade"
    assert account["source"] == "wallet_sign"
    assert account["is_active"] is True  # first account ever: auto-activated
    assert account["verified_at"] is not None

    session = client.get("/api/session").json()
    assert session["can_trade"] is True
    assert session["needs_onboarding"] is False


def test_confirm_rejects_a_signature_from_the_wrong_key(client):
    key = EthAccount.create()
    impostor = EthAccount.create()
    prep = client.post("/api/accounts/agent/prepare", json={"main_address": key.address}).json()
    bad_sig = _sign(prep, impostor)  # valid signature, wrong signer
    res = client.post("/api/accounts/agent/confirm", json={"request_id": prep["request_id"], "signature": bad_sig})
    assert res.status_code == 400
    assert "does not match" in res.json()["detail"]


def test_confirm_rejects_a_mutated_payload(client):
    """Proves this harness actually binds the signature to the ORIGINAL
    stored typed_data, not whatever the caller claims it signed — the same
    property the browser suite relies on to catch a bundle that tampers with
    the payload before signing it."""
    key = EthAccount.create()
    prep = client.post("/api/accounts/agent/prepare", json={"main_address": key.address}).json()
    tampered = dict(prep)
    tampered["typed_data"] = dict(prep["typed_data"])
    tampered["typed_data"]["message"] = dict(prep["typed_data"]["message"])
    tampered["typed_data"]["message"]["agentName"] = "evil"
    sig = _sign(tampered, key)  # signs the TAMPERED message...
    # ...but confirm recovers against the record stored at prepare time, not
    # whatever the client sends back, so this must fail.
    res = client.post("/api/accounts/agent/confirm", json={"request_id": prep["request_id"], "signature": sig})
    assert res.status_code == 400


def test_confirm_rejects_unknown_request_id(client):
    res = client.post("/api/accounts/agent/confirm", json={"request_id": "nope", "signature": "0x" + "11" * 65})
    assert res.status_code == 404


def test_confirm_rejects_a_reused_request_id(client):
    key = EthAccount.create()
    prep = client.post("/api/accounts/agent/prepare", json={"main_address": key.address}).json()
    sig = _sign(prep, key)
    first = client.post("/api/accounts/agent/confirm", json={"request_id": prep["request_id"], "signature": sig})
    assert first.status_code == 200
    second = client.post("/api/accounts/agent/confirm", json={"request_id": prep["request_id"], "signature": sig})
    assert second.status_code == 400
    assert "already used" in second.json()["detail"]


def test_confirm_rejects_an_expired_request_using_an_injected_clock():
    """Exercises the real server-side expiry branch deterministically — no
    sleeping 10 minutes, no monkeypatching datetime globally."""
    t = {"now": datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)}
    app = build_app(clock=lambda: t["now"])
    client = TestClient(app)
    key = EthAccount.create()
    prep = client.post("/api/accounts/agent/prepare", json={"main_address": key.address}).json()
    sig = _sign(prep, key)

    t["now"] = t["now"] + timedelta(minutes=11)  # past the 10-minute ttl
    res = client.post("/api/accounts/agent/confirm", json={"request_id": prep["request_id"], "signature": sig})
    assert res.status_code == 400
    assert "expired" in res.json()["detail"].lower()


def test_import_derives_a_real_address_from_the_key(client):
    key = EthAccount.create()
    agent_key = EthAccount.create()
    res = client.post("/api/accounts/import", json={
        "main_address": key.address, "agent_private_key": agent_key.key.hex(),
    })
    assert res.status_code == 200
    account = res.json()
    assert account["agent_address"].lower() == agent_key.address.lower()
    assert account["source"] == "api_key"
    assert account["is_active"] is True  # first account


def test_watch_creates_a_readonly_account_that_does_not_steal_activation(client):
    trader = EthAccount.create()
    agent_key = EthAccount.create()
    client.post("/api/accounts/import", json={
        "main_address": trader.address, "agent_private_key": agent_key.key.hex(),
    })
    watched = EthAccount.create()
    res = client.post("/api/accounts/watch", json={"address": watched.address, "label": "Whale"})
    account = res.json()
    assert account["mode"] == "watch"
    assert account["is_active"] is False  # an account already existed

    summary = client.get("/api/accounts/active/summary").json()
    assert summary["account"]["main_address"].lower() == trader.address.lower()


def test_activate_switches_exactly_one_account(client):
    a = client.post("/api/accounts/watch", json={"address": EthAccount.create().address}).json()
    b = client.post("/api/accounts/watch", json={"address": EthAccount.create().address}).json()
    assert a["is_active"] is True and b["is_active"] is False

    client.post(f"/api/accounts/{b['id']}/activate")
    accounts = {acc["id"]: acc for acc in client.get("/api/accounts").json()}
    assert accounts[a["id"]]["is_active"] is False
    assert accounts[b["id"]]["is_active"] is True


def test_patch_renames_without_touching_other_fields(client):
    a = client.post("/api/accounts/watch", json={"address": EthAccount.create().address, "label": "Old"}).json()
    res = client.patch(f"/api/accounts/{a['id']}", json={"label": "New name"})
    assert res.status_code == 200
    assert res.json()["label"] == "New name"
    assert res.json()["main_address"] == a["main_address"]


def test_remove_reassigns_activation_to_a_remaining_account(client):
    a = client.post("/api/accounts/watch", json={"address": EthAccount.create().address}).json()
    b = client.post("/api/accounts/watch", json={"address": EthAccount.create().address}).json()
    assert a["is_active"] is True

    res = client.delete(f"/api/accounts/{a['id']}")
    assert res.status_code == 200 and res.json() == {"ok": True}

    remaining = client.get("/api/accounts").json()
    assert len(remaining) == 1
    assert remaining[0]["id"] == b["id"]
    assert remaining[0]["is_active"] is True


def test_remove_unknown_account_is_404(client):
    assert client.delete("/api/accounts/does-not-exist").status_code == 404


def test_summary_with_no_accounts_blocks_trading(client):
    body = client.get("/api/accounts/active/summary").json()
    assert body["account"] is None
    assert body["can_trade"] is False
    assert body["blockers"]
