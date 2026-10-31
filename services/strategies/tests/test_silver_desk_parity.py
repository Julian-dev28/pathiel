"""2026-10-31: silver (desk id "metals") joins oil as a live desk, each on
its own slice of equity (desk_equity_share). These tests cover the Desk page
contract added for it:

  - GET /api/desks/active carries desk_share.desk_shares as a `desks` list,
    not just the raw active_desks/default_desk strings an older cached page
    reads (test_oil_dashboard_routes.py covers that route in full).
  - GET /api/desks/{id}/next answers correctly for a non-Brent desk: its
    stop buffer is ATR-derived, not Brent's fixed 0.75, and the response
    carries that number so the page never hardcodes it.
  - The loop log, levels and line routes are desk_id-parameterized already
    (test_oil_dashboard_routes.py's genericity checks); this file only adds
    the metals/silver-specific case the operator actually runs.
  - Static asserts on oil_desk.js/oil_desk.html: the switcher label reads
    name+share from the server (never a raw desk id), the "sizes off X% of
    equity" note exists and is wired into the refresh loop, and the stop
    buffer in the next-trade sentence is the server's own number, not a
    "0.75" literal baked in for every desk.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.strategies import api as strategies_api
from services.strategies import journal, pipeline
from services.strategies.next_trade import stop_buffer_for
from services.strategies.tests.conftest import make_thesis, minimal_analytics

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def client(stub_live_analytics):
    analytics = minimal_analytics("metals", mid=56.9, coin="xyz:SILVER")
    stub_live_analytics(analytics)
    pipeline.save_analytics("metals", analytics)
    strategies_api._JOBS.clear()
    app = FastAPI()
    app.include_router(strategies_api.router)
    return TestClient(app, client=("127.0.0.1", 50000))


# ── GET /api/desks/metals/next ──────────────────────────────────────────────

def test_next_route_for_silver_uses_the_atr_stop_buffer_not_brents(client, monkeypatch):
    journal.save_thesis(make_thesis(desk_id="metals", side="long", instrument="xyz:SILVER",
                                    entry_px=57.5, stop_px=55.0, targets=[60.0], leverage=10))
    monkeypatch.setattr("pathiel.client.exchange.get_max_leverage", lambda coin: 25)
    monkeypatch.setattr(strategies_api.levels, "summarize_coin", lambda coin: {
        "mid": 56.9, "range_24h": {"low": 55.0, "high": 59.0}, "atr14_1h": 0.5})

    r = client.get("/api/desks/metals/next")
    assert r.status_code == 200
    body = r.json()
    assert body["coin"] == "xyz:SILVER"
    expected_buf = stop_buffer_for("xyz:SILVER", 0.5)
    assert expected_buf != 0.75          # sanity: this is NOT Brent's fixed buffer
    assert body["stop_buffer"] == pytest.approx(expected_buf)


def test_next_route_for_silver_has_no_atr_means_no_line(client, monkeypatch):
    journal.save_thesis(make_thesis(desk_id="metals", side="long", instrument="xyz:SILVER"))
    monkeypatch.setattr("pathiel.client.exchange.get_max_leverage", lambda coin: 25)
    monkeypatch.setattr(strategies_api.levels, "summarize_coin", lambda coin: {
        "mid": 56.9, "range_24h": {"low": 55.0, "high": 59.0}, "atr14_1h": None})
    r = client.get("/api/desks/metals/next")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False and "no ATR" in body["reason"]


# ── GET /api/desks/active, with silver live next to oil ────────────────────

def test_active_desks_with_oil_and_silver_both_live(client, monkeypatch):
    from pathiel.agents import config_store
    monkeypatch.setattr(config_store, "read_agent_config", lambda: {
        "active_desks": ["oil", "metals"],
        "desk_equity_share": {"oil": 0.95, "metals": 0.05},
    })
    r = client.get("/api/desks/active")
    body = r.json()
    assert body["active_desks"] == ["oil", "metals"]
    by_id = {d["id"]: d for d in body["desks"]}
    assert by_id["metals"]["name"] == "Silver"
    assert by_id["metals"]["primary_coin"] == "xyz:SILVER"
    assert by_id["metals"]["equity_share"] == pytest.approx(0.05)
    assert by_id["oil"]["equity_share"] == pytest.approx(0.95)


# ── static asserts: oil_desk.js / oil_desk.html ─────────────────────────────

def test_the_page_never_hardcodes_brents_stop_buffer():
    js = (ROOT / "pathiel" / "static" / "oil_desk.js").read_text()
    assert "n.stop_buffer" in js
    # the old literal this replaces must be gone, not just supplemented
    assert "(0.75 ${long" not in js


def test_the_switcher_label_reads_name_and_share_from_the_server():
    js = (ROOT / "pathiel" / "static" / "oil_desk.js").read_text()
    assert "function deskSwitcherLabel" in js
    assert "meta.equity_share" in js
    assert "deskSwitcherLabel(id)" in js


def test_the_equity_slice_note_is_wired_into_the_refresh_loop():
    js = (ROOT / "pathiel" / "static" / "oil_desk.js").read_text()
    html = (ROOT / "pathiel" / "templates" / "oil_desk.html").read_text()
    assert "function loadEquitySlice" in js
    assert "loadEquitySlice()" in js   # actually called from loadAll(), not just defined
    assert "Sizes off" in js
    assert 'id="line-equity-share"' in html
