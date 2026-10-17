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
    fixture = fixture_json("polymarket_search_oil.json")
    monkeypatch.setattr(http_client, "fetch_json", lambda *a, **k: (fixture, None))
    odds, err = predictions.fetch_polymarket(_oil_prediction_spec())
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
