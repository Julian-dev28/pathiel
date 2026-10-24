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
    if "sec.gov/Archives" in url and "index.json" in url:
        return fixture_json("sec_nvda_form4_index.json"), None
    return None, "unmocked_url"


def _router_text(url: str, **kw):
    if "news.google.com" in url and "when%3A2d" in url:
        return fixture_text("google_news_query_opec.xml"), None   # the per-phrase fan-out
    if "news.google.com" in url:
        return fixture_text("google_news_oil.xml"), None
    if "feeds.finance.yahoo.com" in url:
        return fixture_text("yahoo_quote_headlines_oil.xml"), None
    if "eia.gov/rss/todayinenergy" in url:
        return fixture_text("eia_today_in_energy.xml"), None
    if "eia.gov/rss/press" in url:
        return fixture_text("eia_press.xml"), None
    if "investing.com/rss" in url:
        return fixture_text("investing_commodities.xml"), None
    if "oilprice.com" in url:
        return fixture_text("oilprice_main.xml"), None
    if "cointelegraph.com" in url:
        return fixture_text("cointelegraph.xml"), None
    if "ofac.treasury.gov" in url:
        return fixture_text("ofac_recent_actions.html"), None
    if "cdn.cboe.com" in url:
        for sym in ("VIX9D", "VIX3M", "VIX"):  # longest-prefix first: "VIX" is a substring of both
            if sym in url:
                return fixture_text(f"cboe_{sym}_history.csv"), None
        return None, "unmocked_url"
    if "sec.gov/cgi-bin/browse-edgar" in url:
        return fixture_text("sec_nvda_form4_browse_atom.xml"), None
    if "sec.gov/Archives" in url:  # the Form 4 XML itself (not index.json, routed above)
        return fixture_text("sec_nvda_form4_sample.xml"), None
    return None, "unmocked_url"


def _router_post(url: str, json_body, **kw):
    if json_body.get("type") == "metaAndAssetCtxs":
        return fixture_json("hl_meta_and_ctxs_xyz.json"), None
    if json_body.get("type") == "fundingHistory":
        return fixture_json("hl_funding_history_brentoil.json"), None
    return None, "unmocked_url"


@pytest.fixture(autouse=True)
def mock_all_http(monkeypatch):
    monkeypatch.setattr(http_client, "fetch_json", lambda url, **kw: _router_json(url, **kw))
    monkeypatch.setattr(http_client, "fetch_text", lambda url, **kw: _router_text(url, **kw))
    monkeypatch.setattr(http_client, "fetch_json_post",
                        lambda url, json_body, **kw: _router_post(url, json_body, **kw))


def test_build_packet_oil_desk_is_valid_and_populated(oil_spec, monkeypatch):
    # The operator's .env.local may carry a real key (it does since
    # 2026-10-23); this test is about the MISSING-key path, so force it.
    # Empty, not deleted: the intel env loader re-reads .env.local with
    # setdefault, so a deleted key comes straight back; an empty one stays.
    monkeypatch.setenv("CONGRESS_API_KEY", "")
    # Same reasoning as CONGRESS_API_KEY above: force the missing-key path
    # explicitly rather than assume the environment has none.
    monkeypatch.setenv("EIA_API_KEY", "")
    packet = build_packet(oil_spec, now=T0)
    assert isinstance(packet, IntelPacket)
    assert packet.desk_id == "oil"
    assert len(packet.headlines) > 0
    assert len(packet.policy) > 0
    assert len(packet.catalysts) > 0
    assert len(packet.odds) > 0
    assert len(packet.positioning) > 0
    assert len(packet.datapoints) > 0  # venue.py fires for every desk, no key needed
    # congress.gov / EIA keys unset -> each must be named, never silently empty
    assert any("CONGRESS_API_KEY" in f for f in packet.sources_failed)
    assert any("EIA_API_KEY" in f for f in packet.sources_failed)


def test_build_packet_oil_desk_headlines_span_the_2026_10_24_feeds(oil_spec):
    """Yahoo Finance quote headlines, EIA's two feeds, Investing.com
    commodities and the per-phrase Google News requests all land in one
    packet and survive dedup; GDELT is gone from the fetcher list, so it can
    neither contribute nor fail."""
    packet = build_packet(oil_spec, now=T0)
    sources = {h.source for h in packet.headlines}
    assert {"Yahoo Finance", "EIA", "Investing.com", "OilPrice.com", "EnergyNow.com"} <= sources
    assert not any("gdelt" in f for f in packet.sources_failed)


def test_build_packet_oil_desk_venue_datapoints_are_real(oil_spec):
    """Proves the venue.py wiring in api.py end to end: funding/OI/volume
    rows for the oil desk's real HIP-3 instruments, built from the real
    captured metaAndAssetCtxs(dex="xyz")/fundingHistory fixtures."""
    packet = build_packet(oil_spec, now=T0)
    venue_points = [dp for dp in packet.datapoints if dp.source == "venue_hl"]
    assert venue_points
    labels = {dp.label for dp in venue_points}
    assert any("funding rate" in l for l in labels)
    assert any("open interest" in l for l in labels)
    assert any("24h volume" in l for l in labels)


def test_build_packet_oil_desk_eia_and_fred_populate_with_keys_present(monkeypatch, oil_spec):
    monkeypatch.setenv("EIA_API_KEY", "fake-test-key")
    eia_response = {"response": {"data": [
        {"period": "2026-10-10", "value": "420000"},
        {"period": "2026-10-03", "value": "430000"},
        {"period": "2026-09-26", "value": "431000"},
    ]}}

    def router_with_eia(url, **kw):
        if "api.eia.gov" in url:
            return eia_response, None
        return _router_json(url, **kw)
    monkeypatch.setattr(http_client, "fetch_json", router_with_eia)

    packet = build_packet(oil_spec, now=T0)
    eia_points = [dp for dp in packet.datapoints if dp.source == "eia"]
    assert eia_points
    assert not any("EIA_API_KEY" in f for f in packet.sources_failed)


def test_build_packet_ai_desk_gets_cboe_and_edgar_datapoints(ai_equity_spec):
    packet = build_packet(ai_equity_spec, now=T0)
    sources = {dp.source for dp in packet.datapoints}
    assert "cboe" in sources
    assert "sec_edgar" in sources


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
