from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.risk import api as risk_api


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
    return TestClient(app)


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
    client_ = TestClient(app_)
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
    client_ = TestClient(app)
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
    client_ = TestClient(app)
    r = client_.get("/api/risk/limits")
    assert r.status_code == 200
    assert r.json()["equity_usd"] == 42.0
