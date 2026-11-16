"""Unit tests for services/intel/sources/predictions.py against real
captured Polymarket public-search and Kalshi series/events responses."""
from __future__ import annotations


from contracts.strategies.models import DeskSpec, Instrument
from services.intel import http_client
from services.intel.sources import predictions
from services.intel.tests.conftest import fixture_json


def _oil_prediction_spec() -> DeskSpec:
    return DeskSpec(id="oil", name="Oil", tagline="t",
                     instruments=[Instrument(coin="xyz:CL", label="WTI")],
                     prediction_market_queries=["Iran deal", "oil price"])


# ── Polymarket ───────────────────────────────────────────────────────

def test_polymarket_real_fixture_extracts_wti_markets(monkeypatch):
    from datetime import datetime, timezone
    fixture = fixture_json("polymarket_search_oil.json")
    monkeypatch.setattr(http_client, "fetch_json", lambda *a, **k: (fixture, None))
    captured = datetime(2026, 10, 17, tzinfo=timezone.utc)   # its WTI markets end 2026-11-01
    odds, err = predictions.fetch_polymarket(_oil_prediction_spec(), now=captured)
    assert err is None
    assert len(odds) > 0
    assert all(o.venue == "polymarket" for o in odds)
    assert all(0.0 <= o.probability <= 1.0 for o in odds)
    assert any("WTI" in o.question for o in odds)
    assert all(o.url.startswith("https://polymarket.com/event/") for o in odds)


def test_polymarket_skips_closed_or_inactive_events(monkeypatch):
    data = {"events": [{
        "active": False, "closed": True, "slug": "dead-event",
        "markets": [{"active": True, "closed": False, "outcomes": '["Yes","No"]',
                      "outcomePrices": '["0.5","0.5"]', "volumeNum": 1000, "id": "m1"}],
    }]}
    monkeypatch.setattr(http_client, "fetch_json", lambda *a, **k: (data, None))
    odds, err = predictions.fetch_polymarket(_oil_prediction_spec())
    assert odds == [] and err is None


def test_polymarket_filters_zero_volume_markets(monkeypatch):
    data = {"events": [{
        "active": True, "closed": False, "slug": "e1",
        "markets": [{"active": True, "closed": False, "outcomes": '["Yes","No"]',
                      "outcomePrices": '["0.3","0.7"]', "volumeNum": 0, "id": "m1"}],
    }]}
    monkeypatch.setattr(http_client, "fetch_json", lambda *a, **k: (data, None))
    odds, err = predictions.fetch_polymarket(_oil_prediction_spec())
    assert odds == []


def test_polymarket_no_queries_is_clean_noop():
    spec = DeskSpec(id="xx", name="X", tagline="t",
                     instruments=[Instrument(coin="BTC", label="BTC")])
    odds, err = predictions.fetch_polymarket(spec)
    assert odds == [] and err is None


def test_polymarket_malformed_outcome_json_is_skipped_not_fatal(monkeypatch):
    data = {"events": [{
        "active": True, "closed": False, "slug": "e1",
        "markets": [{"active": True, "closed": False, "outcomes": "not json",
                      "outcomePrices": '["0.3","0.7"]', "volumeNum": 500, "id": "m1"}],
    }]}
    monkeypatch.setattr(http_client, "fetch_json", lambda *a, **k: (data, None))
    odds, err = predictions.fetch_polymarket(_oil_prediction_spec())
    assert odds == [] and err is None


def test_polymarket_error_propagates_with_prefix(monkeypatch):
    monkeypatch.setattr(http_client, "fetch_json", lambda *a, **k: (None, "timeout"))
    odds, err = predictions.fetch_polymarket(_oil_prediction_spec())
    assert odds == [] and "predictions:polymarket" in err and "timeout" in err


# ── Kalshi ───────────────────────────────────────────────────────────

def test_kalshi_matches_series_catalog_against_queries(monkeypatch):
    series_fixture = fixture_json("kalshi_series_commodities.json")
    events_fixture = fixture_json("kalshi_events_kxwtiw.json")

    def fake(url, **kw):
        if "series" in url:
            return series_fixture, None
        return events_fixture, None

    monkeypatch.setattr(http_client, "fetch_json", fake)
    odds, err = predictions.fetch_kalshi(_oil_prediction_spec())
    assert err is None or "kalshi_events" not in (err or "")
    assert len(odds) > 0
    assert all(o.venue == "kalshi" for o in odds)
    assert all(0.0 <= o.probability <= 1.0 for o in odds)


def test_kalshi_no_queries_is_clean_noop():
    spec = DeskSpec(id="xx", name="X", tagline="t", instruments=[Instrument(coin="BTC", label="BTC")])
    odds, err = predictions.fetch_kalshi(spec)
    assert odds == [] and err is None


def test_kalshi_no_matching_series_returns_empty_not_error(monkeypatch):
    monkeypatch.setattr(http_client, "fetch_json",
                         lambda url, **kw: ({"series": [{"title": "Unrelated thing", "ticker": "XYZ"}]},
                                            None))
    spec = DeskSpec(id="xx", name="X", tagline="t", instruments=[Instrument(coin="BTC", label="BTC")],
                     prediction_market_queries=["completely unrelated topic"])
    odds, err = predictions.fetch_kalshi(spec)
    assert odds == [] and err is None


def test_kalshi_series_catalog_failure_propagates(monkeypatch):
    monkeypatch.setattr(http_client, "fetch_json", lambda *a, **k: (None, "timeout"))
    odds, err = predictions.fetch_kalshi(_oil_prediction_spec())
    assert odds == []
    assert err is not None and "timeout" in err


def test_kalshi_filters_illiquid_wide_spread_markets():
    series = [{"title": "WTI oil weekly range", "ticker": "KXWTIW"}]
    tickers = predictions._matching_series_tickers(series, ["oil price"])
    assert "KXWTIW" in tickers


# 2026-11-16 regression: Polymarket still listed "US x Iran Effective Ceasefire
# by November 8?" as active and open eight days after its endDate. Its drift
# woke the oil desk and started an entry blackout.

def _ceasefire_events():
    def mkt(mid, q, end, price):
        return {"active": True, "closed": False, "acceptingOrders": True, "id": mid,
                "question": q, "endDate": end, "outcomes": '["Yes","No"]',
                "outcomePrices": f'["{price}","{1 - price:.2f}"]', "volumeNum": 250000}
    return {"events": [{"active": True, "closed": False, "slug": "us-x-iran-ceasefire", "markets": [
        mkt("nov8", "US x Iran Effective Ceasefire by November 8?", "2026-11-08T20:29:00Z", 0.32),
        mkt("nov22", "US x Iran Effective Ceasefire by November 22?", "2026-11-22T20:29:00Z", 0.18),
        mkt("noend", "US x Iran Effective Ceasefire ever?", None, 0.40),
    ]}]}


def test_polymarket_drops_markets_past_their_end_date(monkeypatch):
    from datetime import datetime, timezone
    monkeypatch.setattr(http_client, "fetch_json", lambda *a, **k: (_ceasefire_events(), None))
    now = datetime(2026, 11, 16, 0, 43, tzinfo=timezone.utc)
    odds, err = predictions.fetch_polymarket(_oil_prediction_spec(), now=now)
    qs = sorted(o.question for o in odds)
    assert err is None
    assert qs == ["US x Iran Effective Ceasefire by November 22?", "US x Iran Effective Ceasefire ever?"]
    nov22 = next(o for o in odds if "22" in o.question)
    assert nov22.closes_at == datetime(2026, 11, 22, 20, 29, tzinfo=timezone.utc)


def test_polymarket_keeps_a_market_until_its_end_minute(monkeypatch):
    from datetime import datetime, timezone
    monkeypatch.setattr(http_client, "fetch_json", lambda *a, **k: (_ceasefire_events(), None))
    before = datetime(2026, 11, 8, 20, 28, tzinfo=timezone.utc)
    at_end = datetime(2026, 11, 8, 20, 29, tzinfo=timezone.utc)
    assert any("November 8" in o.question
               for o in predictions.fetch_polymarket(_oil_prediction_spec(), now=before)[0])
    assert not any("November 8" in o.question
                   for o in predictions.fetch_polymarket(_oil_prediction_spec(), now=at_end)[0])
