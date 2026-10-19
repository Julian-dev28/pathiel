from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.risk import api as risk_api


@pytest.fixture(autouse=True)
def _local_mode_possible(monkeypatch):
    """These tests exercise the routes as Julien uses them: local mode. A
    deployment marker in the environment would switch local mode off."""
    from services.auth import integration
    for m in integration._DEPLOYMENT_MARKERS:
        monkeypatch.delenv(m, raising=False)


@pytest.fixture
def app():
    a = FastAPI()
    a.include_router(risk_api.router)
    return a


@pytest.fixture
def _client(app, tmp_path, monkeypatch):
    cfg_path = tmp_path / "agent-config.json"
    cfg_path.write_text("{}")
    import pathiel.agents.config_store as cs
    monkeypatch.setattr(cs, "CONFIG_PATH", str(cfg_path))
    monkeypatch.setattr(risk_api, "active_equity_usd", lambda: 15.19)
    return TestClient(app, client=("127.0.0.1", 41000))


def test_get_profile_default_is_auto(_client):
    r = _client.get("/api/risk/profile")
    assert r.status_code == 200
    body = r.json()
    assert body["preset"] == "auto"


def test_put_then_get_round_trips(_client):
    payload = {"preset": "small", "risk_per_trade_pct": 1.5, "daily_loss_limit_pct": None,
              "max_leverage": None, "max_open_positions": None,
              "allow_min_order_bump": None, "max_bumped_risk_pct": None}
    put = _client.put("/api/risk/profile", json=payload)
    assert put.status_code == 200
    assert put.json()["preset"] == "small"
    assert put.json()["risk_per_trade_pct"] == 1.5

    got = _client.get("/api/risk/profile")
    assert got.json() == put.json()


def test_put_writes_the_configured_file_atomically(_client, tmp_path):
    cfg_path = tmp_path / "agent-config.json"
    payload = {"preset": "pro", "risk_per_trade_pct": None, "daily_loss_limit_pct": None,
              "max_leverage": None, "max_open_positions": None,
              "allow_min_order_bump": None, "max_bumped_risk_pct": None}
    _client.put("/api/risk/profile", json=payload)
    on_disk = json.loads(cfg_path.read_text())
    assert on_disk["risk_profile"]["preset"] == "pro"


def test_put_rejects_an_invalid_profile():
    app_ = FastAPI()
    app_.include_router(risk_api.router)
    client_ = TestClient(app_, client=("127.0.0.1", 41000))
    r = client_.put("/api/risk/profile", json={"preset": "auto", "risk_per_trade_pct": 999})
    assert r.status_code == 422


def test_get_limits_uses_the_mocked_active_equity(_client):
    r = _client.get("/api/risk/limits")
    assert r.status_code == 200
    body = r.json()
    assert body["equity_usd"] == 15.19
    assert len(body["books"]) == 7
    assert all("reason" in b and b["reason"] for b in body["books"])


def test_get_limits_503s_when_equity_is_unreadable(app, tmp_path, monkeypatch):
    cfg_path = tmp_path / "agent-config.json"
    cfg_path.write_text("{}")
    import pathiel.agents.config_store as cs
    monkeypatch.setattr(cs, "CONFIG_PATH", str(cfg_path))
    monkeypatch.setattr(risk_api, "_equity_from_accounts_service", lambda: None)
    monkeypatch.setattr(risk_api, "_equity_from_env_account", lambda: None)
    client_ = TestClient(app, client=("127.0.0.1", 41000))
    r = client_.get("/api/risk/limits")
    assert r.status_code == 503


def test_preview_any_equity_any_profile_no_live_dependency(_client):
    r = _client.post("/api/risk/preview", json={"equity_usd": 100000, "profile": {"preset": "pro"}})
    assert r.status_code == 200
    body = r.json()
    assert body["equity_usd"] == 100000
    assert body["preset_applied"] == "pro"
    assert body["can_trade"] is True


def test_preview_rejects_negative_equity(_client):
    r = _client.post("/api/risk/preview", json={"equity_usd": -5, "profile": {}})
    assert r.status_code == 422


def test_preview_matches_a_15_dollar_account_directly(_client):
    """The exact scenario docs/overhaul/PROFIT_AUDIT.md section 1 describes:
    $15.19 equity, previously blocked outright by min_tradable_equity_usd=26."""
    r = _client.post("/api/risk/preview", json={"equity_usd": 15.19, "profile": {"preset": "starter"}})
    body = r.json()
    assert body["can_trade"] is True
    feasible = [b for b in body["books"] if b["can_trade"]]
    assert feasible, f"expected at least one tradeable book at $15.19, got: {body['books']}"


def test_accounts_service_preferred_over_env_fallback(app, tmp_path, monkeypatch):
    cfg_path = tmp_path / "agent-config.json"
    cfg_path.write_text("{}")
    import pathiel.agents.config_store as cs
    monkeypatch.setattr(cs, "CONFIG_PATH", str(cfg_path))
    monkeypatch.setattr(risk_api, "_equity_from_accounts_service", lambda: 42.0)
    monkeypatch.setattr(risk_api, "_equity_from_env_account",
                        lambda: (_ for _ in ()).throw(AssertionError("should not be called")))
    client_ = TestClient(app, client=("127.0.0.1", 41000))
    r = client_.get("/api/risk/limits")
    assert r.status_code == 200
    assert r.json()["equity_usd"] == 42.0


def test_limits_equity_comes_from_the_real_accounts_summary(monkeypatch):
    """Integration (2026-10-17): the accounts service exposes
    services.accounts.summary.get_active_summary; the risk API must read the
    ACTIVE account's equity from it, not fall back to the env account."""
    from contracts.accounts.models import ActiveSummary
    from services.accounts import summary as acc_summary
    from services.risk import api as risk_api
    monkeypatch.setattr(acc_summary, "get_active_summary",
                        lambda: ActiveSummary(equity_usd=432.1, can_trade=True))
    monkeypatch.setattr(risk_api, "_equity_from_env_account",
                        lambda: (_ for _ in ()).throw(AssertionError("must not fall back")))
    assert risk_api._equity_from_accounts_service() == 432.1


def test_profile_and_limits_are_operator_only_off_the_local_machine(monkeypatch, tmp_path):
    """PUT /profile changes live sizing and /limits reveals equity: a remote
    caller without the operator session gets 401; /preview stays open."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from services.auth import deps as auth_deps, integration
    from services.auth.store import AuthStore
    from services.risk.api import router
    for m in integration._DEPLOYMENT_MARKERS:
        monkeypatch.delenv(m, raising=False)
    store = AuthStore(str(tmp_path / "auth.db"))
    store.upsert_user("0x" + "9" * 40)                 # an operator exists already
    auth_deps.reset_store_for_tests(store)
    try:
        app = FastAPI(); app.include_router(router)
        remote = TestClient(app, client=("203.0.113.7", 40000))
        assert remote.get("/api/risk/profile").status_code == 401
        assert remote.put("/api/risk/profile", json={"preset": "pro"}).status_code == 401
        assert remote.get("/api/risk/limits").status_code == 401
        r = remote.post("/api/risk/preview", json={"equity_usd": 100, "profile": {"preset": "auto"}})
        assert r.status_code == 200
    finally:
        auth_deps.reset_store_for_tests(None)
        store.close()


# ── POST /api/risk/size: the desk page's "Your size" line ──────────────────

@pytest.fixture
def _sized(_client, monkeypatch):
    monkeypatch.setattr(risk_api, "_coin_specs", lambda coin: (20.0, 3))
    return _client


def test_size_sizes_one_trade_for_the_active_account(_sized):
    r = _sized.post("/api/risk/size", json={"coin": "xyz:CL", "entry_px": 98.87,
                                            "stop_px": 88.22, "leverage": 5,
                                            "targets": [101.19, 105.55]})
    assert r.status_code == 200
    body = r.json()
    assert body["can_trade"] is True and body["side"] == "long"
    assert body["notional_usd"] >= 10.5 and body["size"] == round(body["size"], 3)
    assert any("R:" in w for w in body["warnings"])          # 0.6R best target


def test_size_rejects_a_non_positive_price(_sized):
    r = _sized.post("/api/risk/size", json={"coin": "xyz:CL", "entry_px": 0, "stop_px": 88.22})
    assert r.status_code == 422


def test_size_is_gated_like_the_other_account_reads(app, monkeypatch, tmp_path):
    """Reads the account's equity: a non-local caller with no session is refused."""
    monkeypatch.setenv("PATHIEL_PUBLIC_DASHBOARD", "1")       # deployment marker: no local mode
    monkeypatch.setattr(risk_api, "active_equity_usd", lambda: 15.19)
    monkeypatch.setattr(risk_api, "_coin_specs", lambda coin: (20.0, 3))
    c = TestClient(app, client=("203.0.113.9", 41000))
    r = c.post("/api/risk/size", json={"coin": "xyz:CL", "entry_px": 98.87, "stop_px": 88.22})
    assert r.status_code in (401, 403)


def test_active_equity_is_reused_within_its_ttl(monkeypatch):
    """The desk page sizes the call on every render; each fresh equity read
    is ~12 venue requests on the server's small budget."""
    calls = []
    monkeypatch.setattr(risk_api, "_equity_from_accounts_service", lambda: calls.append(1) or 15.21)
    monkeypatch.setattr(risk_api, "_EQUITY_CACHE", {"ts": 0.0, "eq": None})
    monkeypatch.setattr(risk_api, "EQUITY_TTL_S", 30.0)
    assert risk_api.active_equity_usd() == 15.21
    assert risk_api.active_equity_usd() == 15.21
    assert len(calls) == 1
    monkeypatch.setattr(risk_api, "EQUITY_TTL_S", 0.0)          # 0 disables the cache
    risk_api.active_equity_usd()
    assert len(calls) == 2


def test_prewarm_reads_each_coins_size_metadata_once(monkeypatch):
    """The server warms "Your size" at startup (pathiel/server.py
    _prewarm_size_specs): before 2026-10-19 the first size after a restart
    built the SDK client and read every dex's meta inside the request, 2.8 s."""
    import pathiel.client.exchange as exchange
    seen = []

    def max_lev(coin):
        seen.append(coin)
        if coin == "xyz:GONE":
            raise ValueError("Unknown coin: xyz:GONE")
        return 20
    monkeypatch.setattr(exchange, "get_max_leverage", max_lev)
    monkeypatch.setattr(exchange, "get_coin_index", lambda coin: (0, 3, 2))
    assert risk_api.prewarm_coin_specs(["BTC", "xyz:BRENTOIL", "xyz:GONE"]) == 2
    assert seen == ["BTC", "xyz:BRENTOIL", "xyz:GONE"]
