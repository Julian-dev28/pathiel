"""E2E tests for the Desk dashboard routes added to services/strategies/api.py:
GET /api/desks/active, GET /api/desks/{id}/line, GET /api/desks/{id}/levels,
GET /api/desks/{id}/log. Every other /api/desks/* route is covered by
test_api.py; this file is scoped to these new ones, mostly exercised against
the "oil" desk id (today's only active one) plus a couple of genericity
checks against another desk id."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.strategies import api as strategies_api
from services.strategies import journal, pipeline
from services.strategies.tests.conftest import make_thesis, minimal_analytics


@pytest.fixture
def client(stub_live_analytics):
    analytics = minimal_analytics("oil", mid=107.5, coin="xyz:BRENTOIL")
    stub_live_analytics(analytics)
    # Warm cache — see test_api.py's client fixture for why this is needed
    # now that a cold desk no longer builds inline on wait=False
    # (docs/overhaul/DESK_UI_2026-10-23.md, Polish section).
    pipeline.save_analytics("oil", analytics)
    strategies_api._JOBS.clear()
    app = FastAPI()
    app.include_router(strategies_api.router)
    return TestClient(app, client=("127.0.0.1", 50000))   # local mode: the operator on his box


# ── GET /api/desks/{desk_id}/line ───────────────────────────────────────────

def test_line_unknown_desk_404(client):
    r = client.get("/api/desks/not-a-real-desk/line")
    assert r.status_code == 404


def test_line_with_no_thesis_yet_reports_plainly(client):
    r = client.get("/api/desks/oil/line")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False and "no thesis yet" in body["reason"]


def test_line_with_a_thesis_computes_the_20x_line(client, monkeypatch):
    journal.save_thesis(make_thesis(
        desk_id="oil", entry_px=107.16, stop_px=106.70, targets=[108.45], leverage=20))
    monkeypatch.setattr("pathiel.client.exchange.get_max_leverage", lambda coin: 20)

    r = client.get("/api/desks/oil/line")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["verdict"] == "20x OK"
    assert body["liq_px"] == pytest.approx(104.41, abs=0.03)


def test_line_selector_recomputes_at_a_different_leverage(client, monkeypatch):
    journal.save_thesis(make_thesis(
        desk_id="oil", entry_px=107.0, stop_px=101.65, targets=[115.0], leverage=20))
    monkeypatch.setattr("pathiel.client.exchange.get_max_leverage", lambda coin: 20)

    r20 = client.get("/api/desks/oil/line", params={"leverage": 20})
    assert r20.json()["ok"] is False
    r5 = client.get("/api/desks/oil/line", params={"leverage": 5})
    assert r5.json()["ok"] is True and r5.json()["verdict"] == "5x OK"


def test_line_degrades_plainly_when_venue_max_leverage_is_unreadable(client, monkeypatch):
    journal.save_thesis(make_thesis(desk_id="oil", entry_px=107.16, stop_px=106.70, targets=[108.45]))

    def boom(coin):
        raise RuntimeError("venue metadata timeout")

    monkeypatch.setattr("pathiel.client.exchange.get_max_leverage", boom)
    r = client.get("/api/desks/oil/line")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False and "venue max leverage" in body["reason"]


# ── GET /api/desks/oil/levels ────────────────────────────────────────────────

def test_oil_levels_route_returns_summarize_coin(client, monkeypatch):
    import services.strategies.api as api_mod

    def fake_summarize_coin(coin):
        assert coin == "xyz:BRENTOIL"
        return {"coin": coin, "mid": 107.5, "range_24h": {"high": 109.0, "low": 106.0},
               "mid_pos_in_24h": 0.5}

    monkeypatch.setattr(api_mod.levels, "summarize_coin", fake_summarize_coin)
    r = client.get("/api/desks/oil/levels")
    assert r.status_code == 200
    assert r.json()["coin"] == "xyz:BRENTOIL" and r.json()["mid"] == 107.5


def test_oil_levels_route_502s_plainly_on_a_read_failure(client, monkeypatch):
    import services.strategies.api as api_mod

    def boom(coin):
        raise RuntimeError("no live mid")

    monkeypatch.setattr(api_mod.levels, "summarize_coin", boom)
    r = client.get("/api/desks/oil/levels")
    assert r.status_code == 502
    assert "xyz:BRENTOIL" in r.json()["detail"]


# ── GET /api/desks/oil/log ───────────────────────────────────────────────────

def test_oil_log_route_reads_the_env_configured_file(client, monkeypatch, tmp_path):
    p = tmp_path / "progress.log"
    p.write_text("OIL-TICK 2026-10-23T00:00 mid=107.5 pos=flat action=NONE no setup\n")
    monkeypatch.setenv("PATHIEL_OIL_LOG", str(p))

    r = client.get("/api/desks/oil/log")
    assert r.status_code == 200
    assert r.json()["lines"] == ["OIL-TICK 2026-10-23T00:00 mid=107.5 pos=flat action=NONE no setup"]


def test_oil_log_route_missing_file_is_an_empty_list(client, monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_OIL_LOG", str(tmp_path / "does-not-exist.log"))
    r = client.get("/api/desks/oil/log")
    assert r.status_code == 200 and r.json()["lines"] == []


def test_oil_log_route_respects_n(client, monkeypatch, tmp_path):
    p = tmp_path / "progress.log"
    p.write_text("\n".join(f"OIL-TICK line {i}" for i in range(5)) + "\n")
    monkeypatch.setenv("PATHIEL_OIL_LOG", str(p))
    r = client.get("/api/desks/oil/log", params={"n": 2})
    assert r.json()["lines"] == ["OIL-TICK line 3", "OIL-TICK line 4"]


# ── GET /api/desks/{desk_id} quick_markdown ──────────────────────────────────

def test_desk_brief_carries_quick_markdown_once_there_is_a_thesis(client):
    journal.save_thesis(make_thesis(desk_id="oil", entry_px=107.16, stop_px=106.70, targets=[108.45]))
    r = client.get("/api/desks/oil")
    assert r.status_code == 200
    body = r.json()
    assert body["quick_markdown"] is not None
    assert "test thesis" in body["quick_markdown"]
    assert "Decision:" in body["quick_markdown"]


def test_desk_brief_quick_markdown_is_none_with_no_thesis(client):
    r = client.get("/api/desks/oil")
    assert r.status_code == 200
    assert r.json()["quick_markdown"] is None


# ── genericity: /levels and /log are desk_id-parameterized, not oil-only ────

def test_levels_route_works_for_a_non_oil_desk_id(client, monkeypatch):
    import services.strategies.api as api_mod

    def fake_summarize_coin(coin):
        assert coin == "BTC"
        return {"coin": coin, "mid": 90000.0}

    monkeypatch.setattr(api_mod.levels, "summarize_coin", fake_summarize_coin)
    r = client.get("/api/desks/btc/levels")
    assert r.status_code == 200 and r.json()["coin"] == "BTC"


def test_levels_route_404s_for_an_unknown_desk(client):
    r = client.get("/api/desks/not-a-real-desk/levels")
    assert r.status_code == 404


def test_log_route_resolves_the_desk_specific_env_var(client, monkeypatch, tmp_path):
    p = tmp_path / "btc.log"
    p.write_text("BTC-TICK one\n")
    monkeypatch.setenv("PATHIEL_BTC_LOG", str(p))
    r = client.get("/api/desks/btc/log")
    assert r.status_code == 200 and r.json()["lines"] == ["BTC-TICK one"]


def test_log_route_404s_for_an_unknown_desk(client):
    r = client.get("/api/desks/not-a-real-desk/log")
    assert r.status_code == 404


# ── GET /api/desks/active ────────────────────────────────────────────────────

def test_active_desks_defaults_to_oil_with_no_config(client, monkeypatch):
    from pathiel.agents import config_store
    monkeypatch.setattr(config_store, "read_agent_config", lambda: {})
    r = client.get("/api/desks/active")
    assert r.status_code == 200
    assert r.json() == {"active_desks": ["oil"], "default_desk": "oil"}


def test_active_desks_reads_the_configured_list(client, monkeypatch):
    from pathiel.agents import config_store
    monkeypatch.setattr(config_store, "read_agent_config", lambda: {"active_desks": ["btc", "oil"]})
    r = client.get("/api/desks/active")
    assert r.json() == {"active_desks": ["btc", "oil"], "default_desk": "btc"}


def test_active_desks_drops_unknown_ids_and_falls_back_to_oil(client, monkeypatch):
    from pathiel.agents import config_store
    monkeypatch.setattr(config_store, "read_agent_config", lambda: {"active_desks": ["not-a-real-desk"]})
    r = client.get("/api/desks/active")
    assert r.json() == {"active_desks": ["oil"], "default_desk": "oil"}


def test_active_desks_degrades_plainly_on_a_config_read_failure(client, monkeypatch):
    from pathiel.agents import config_store

    def boom():
        raise RuntimeError("config file corrupt")

    monkeypatch.setattr(config_store, "read_agent_config", boom)
    r = client.get("/api/desks/active")
    assert r.status_code == 200
    assert r.json() == {"active_desks": ["oil"], "default_desk": "oil"}
