"""E2E tests for the three Oil Desk dashboard routes added to
services/strategies/api.py: GET /api/desks/{id}/line, GET /api/desks/oil/levels,
GET /api/desks/oil/log. Every other /api/desks/* route is covered by
test_api.py; this file is scoped to the three new ones."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.strategies import api as strategies_api
from services.strategies import journal
from services.strategies.tests.conftest import make_thesis, minimal_analytics


@pytest.fixture
def client(stub_live_analytics):
    stub_live_analytics(minimal_analytics("oil", mid=107.5, coin="xyz:BRENTOIL"))
    strategies_api._JOBS.clear()
    app = FastAPI()
    app.include_router(strategies_api.router)
    return TestClient(app)


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
