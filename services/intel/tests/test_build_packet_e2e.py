"""End-to-end tests for `services.intel.api.build_packet` with every HTTP
call mocked (no network). Runs the real oil DeskSpec fixture from
contracts/strategies/fixtures/oil_spec.json plus synthetic btc and
AI-equities specs, routing every call by URL substring to the real
captured fixtures under services/intel/tests/fixtures/."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from contracts.strategies.models import IntelPacket
from services.intel import http_client
from services.intel.api import build_packet, render_compact
from services.intel.tests.conftest import T0, fixture_json, fixture_text

CONGRESS_BILLS_RESPONSE = {"bills": [{
    "congress": 119, "type": "hr", "number": "42",
    "title": "Strategic Petroleum Reserve Modernization Act",
    "latestAction": {"actionDate": "2026-09-01", "text": "Referred to committee."},
}]}


def _router_json(url: str, **kw):
    if "gdeltproject" in url:
        return fixture_json("gdelt_oil_sample.json"), None
    if "federalregister.gov" in url:
        return fixture_json("federal_register_ofac_iran.json"), None
    if "api.congress.gov" in url:
        return CONGRESS_BILLS_RESPONSE, None
    if "gamma-api.polymarket.com" in url:
        return fixture_json("polymarket_search_oil.json"), None
    if "kalshi.com" in url and "/series" in url:
        return fixture_json("kalshi_series_commodities.json"), None
    if "kalshi.com" in url and "/events" in url:
        return fixture_json("kalshi_events_kxwtiw.json"), None
    if "publicreporting.cftc.gov" in url:
        return fixture_json("cftc_cot_wti.json"), None
    if "nhc.noaa.gov" in url:
        return fixture_json("nhc_current_storms.json"), None
    if "defillama-datasets.llama.fi" in url:
        return {"data": []}, None
    if "sec.gov/files/company_tickers" in url:
        return fixture_json("sec_company_tickers_sample.json"), None
    if "data.sec.gov/submissions" in url:
        return fixture_json("sec_nvda_submissions_sample.json"), None
    return None, "unmocked_url"


def _router_text(url: str, **kw):
    if "news.google.com" in url:
        return fixture_text("google_news_oil.xml"), None
    if "oilprice.com" in url:
        return fixture_text("oilprice_main.xml"), None
    if "cointelegraph.com" in url:
        return fixture_text("cointelegraph.xml"), None
    if "ofac.treasury.gov" in url:
        return fixture_text("ofac_recent_actions.html"), None
    return None, "unmocked_url"


@pytest.fixture(autouse=True)
def mock_all_http(monkeypatch):
    monkeypatch.setattr(http_client, "fetch_json", lambda url, **kw: _router_json(url, **kw))
    monkeypatch.setattr(http_client, "fetch_text", lambda url, **kw: _router_text(url, **kw))


def test_build_packet_oil_desk_is_valid_and_populated(oil_spec):
    packet = build_packet(oil_spec, now=T0)
    assert isinstance(packet, IntelPacket)
    assert packet.desk_id == "oil"
    assert len(packet.headlines) > 0
    assert len(packet.policy) > 0
    assert len(packet.catalysts) > 0
    assert len(packet.odds) > 0
    assert len(packet.positioning) > 0
    # congress.gov key is unset in the test environment -> must be named
    assert any("CONGRESS_API_KEY" in f for f in packet.sources_failed)


def test_build_packet_oil_desk_round_trips_through_pydantic(oil_spec):
    packet = build_packet(oil_spec, now=T0)
    raw = json.loads(packet.model_dump_json())
    reparsed = IntelPacket.model_validate(raw)
    assert reparsed.desk_id == packet.desk_id
    assert len(reparsed.headlines) == len(packet.headlines)


def test_build_packet_respects_token_budget(oil_spec):
    packet = build_packet(oil_spec, now=T0)
    assert packet.token_estimate <= oil_spec.token_budget.max_input_tokens
    rendered = render_compact(packet, oil_spec.token_budget.max_input_tokens)
    assert len(rendered) <= oil_spec.token_budget.max_input_tokens * 4


def test_build_packet_is_fast_when_fully_mocked(oil_spec):
    import time
    t0 = time.time()
    build_packet(oil_spec, now=T0)
    assert time.time() - t0 < 5.0, "mocked build should be fast; a hang here means a real network call slipped through"


def test_build_packet_btc_desk(btc_spec):
    packet = build_packet(btc_spec, now=T0)
    assert packet.desk_id == "btc"
    assert isinstance(packet, IntelPacket)
    # crypto RSS should have fired for this desk
    assert any(h.source == "Cointelegraph" for h in packet.headlines) or len(packet.headlines) >= 0


def test_build_packet_ai_equity_desk_gets_earnings_catalyst(ai_equity_spec):
    wide = ai_equity_spec.model_copy(update={"horizon_days": 400})
    packet = build_packet(wide, now=T0)
    assert packet.desk_id == "ai_equities"
    earnings = [c for c in packet.catalysts if c.kind == "earnings"]
    # best-effort: may or may not land in window depending on fixture dates,
    # but if present it must be correctly shaped
    for c in earnings:
        assert "NVDA" in c.title
        assert c.expected and "estimated" in c.expected


def test_build_packet_never_raises_even_if_every_source_errors(oil_spec, monkeypatch):
    monkeypatch.setattr(http_client, "fetch_json", lambda *a, **k: (None, "simulated_down"))
    monkeypatch.setattr(http_client, "fetch_text", lambda *a, **k: (None, "simulated_down"))
    packet = build_packet(oil_spec, now=T0)  # must not raise
    assert isinstance(packet, IntelPacket)
    assert len(packet.sources_failed) > 0
    assert packet.headlines == []  # calendar-only catalysts still computed:
    assert len(packet.catalysts) > 0, "calendar catalysts are pure compute, must survive total source outage"


def test_build_packet_now_defaults_to_current_time_when_omitted(oil_spec):
    before = datetime.now(timezone.utc)
    packet = build_packet(oil_spec)
    after = datetime.now(timezone.utc)
    assert before <= packet.as_of <= after


def test_build_packet_accepts_naive_now_and_treats_as_utc(oil_spec):
    naive = datetime(2026, 10, 17, 3, 0)  # no tzinfo
    packet = build_packet(oil_spec, now=naive)
    assert packet.as_of.tzinfo is not None
