"""GET /api/accounts/{account_id}/positions: gating + the live-fetch leg,
through FastAPI's TestClient. The pure assessment itself is covered by
tests/test_watch_wallet.py; the dex-discovery/per-dex-fetch leg by
services/accounts/tests/test_positions.py. This file only covers the route:
account lookup (404 on unknown), operator/local gating, and the happy path
end to end."""
from __future__ import annotations

from services.accounts import registry


def _stub_positions(monkeypatch, result):
    import services.accounts.api as accounts_api
    monkeypatch.setattr(accounts_api.positions, "fetch_positions_for_account", lambda addr: result)


def test_unknown_account_is_404(clients, tmp_path, monkeypatch):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    r = clients.loopback().get("/api/accounts/does-not-exist/positions")
    assert r.status_code == 404


def test_local_mode_may_read_a_watch_only_account(clients, tmp_path, monkeypatch):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    acct = registry.add(label="Julien", main_address="0x" + "7" * 40, agent_address=None,
                        mode="watch", source="watch")
    stub = {"user": acct.main_address, "accounts": [
        {"dex": "xyz", "equity_usd": 34.58, "notional_usd": 627.16,
         "positions": [{"coin": "xyz:BRENTOIL", "naked": True}], "any_naked": True}],
        "any_naked": True}
    _stub_positions(monkeypatch, stub)

    r = clients.loopback().get(f"/api/accounts/{acct.id}/positions")
    assert r.status_code == 200
    body = r.json()
    assert body["user"] == acct.main_address
    assert body["any_naked"] is True
    assert body["accounts"][0]["positions"][0]["coin"] == "xyz:BRENTOIL"


def test_remote_unauthenticated_is_refused(clients, tmp_path, monkeypatch):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    acct = registry.add(label="Julien", main_address="0x" + "7" * 40, agent_address=None,
                        mode="watch", source="watch")
    _stub_positions(monkeypatch, {"user": acct.main_address, "accounts": [], "any_naked": False})

    r = clients.remote().get(f"/api/accounts/{acct.id}/positions")
    assert r.status_code == 401


def test_the_env_account_id_resolves_too(clients, tmp_path, monkeypatch):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    from pathiel.client import exchange as ex
    monkeypatch.setattr(ex, "PRIVATE_KEY_HEX", "0x" + "1" * 64, raising=False)
    monkeypatch.setattr(ex, "HL_ACCOUNT", "0x" + "2" * 40, raising=False)
    monkeypatch.setattr(ex, "HL_WALLET", "0x" + "2" * 40, raising=False)
    monkeypatch.setattr(ex, "IS_AGENT", False, raising=False)

    _stub_positions(monkeypatch, {"user": "0x" + "2" * 40, "accounts": [], "any_naked": False})
    r = clients.loopback().get("/api/accounts/env/positions")
    assert r.status_code == 200
    assert r.json()["user"] == "0x" + "2" * 40
