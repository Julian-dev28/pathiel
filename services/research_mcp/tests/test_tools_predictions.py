from __future__ import annotations

import pytest

from contracts.strategies.models import MarketOdds
from services.research_mcp import tools


def _odds(id_, prob, vol):
    return MarketOdds(id=id_, venue="polymarket", question=f"q {id_}", probability=prob,
                      volume_usd=vol, url="https://polymarket.com/x")


def test_prediction_odds_tool_needs_coin_or_query():
    with pytest.raises(ValueError):
        tools.prediction_odds_tool()


def test_prediction_odds_tool_by_coin_uses_the_desks_own_queries(monkeypatch):
    seen = {}

    def fake_for_spec(spec, **kw):
        seen["queries"] = list(spec.prediction_market_queries)
        return [_odds("a", 0.6, 100.0)], []
    monkeypatch.setattr(tools, "prediction_odds_for_spec", fake_for_spec)
    out = tools.prediction_odds_tool(coin="HYPE")
    assert out["query"] == "HYPE"
    assert out["odds"][0]["id"] == "a"
    assert seen["queries"], "the hype desk's own prediction_market_queries must be used"


def test_prediction_odds_tool_by_free_text_query(monkeypatch):
    seen = {}

    def fake_for_spec(spec, **kw):
        seen["queries"] = list(spec.prediction_market_queries)
        seen["instrument_label"] = spec.instruments[0].label
        return [], []
    monkeypatch.setattr(tools, "prediction_odds_for_spec", fake_for_spec)
    out = tools.prediction_odds_tool(query="Fed rate cut")
    assert out["query"] == "Fed rate cut"
    assert seen["queries"] == ["Fed rate cut"]
    assert seen["instrument_label"] == "Fed rate cut"


def test_prediction_odds_for_spec_dedups_and_sorts_by_volume(monkeypatch):
    from contracts.strategies.models import DeskSpec, Instrument
    spec = DeskSpec(id="tt", name="t", tagline="t",
                    instruments=[Instrument(coin="BTC", label="BTC", role="primary")])
    poly = [_odds("x", 0.5, 10.0), _odds("y", 0.4, 500.0)]
    kalshi = [_odds("x", 0.5, 10.0)]  # same id as poly's "x" -- must dedup, not double count
    monkeypatch.setattr(tools.predictions_src, "fetch_polymarket", lambda s, **kw: (poly, None))
    monkeypatch.setattr(tools.predictions_src, "fetch_kalshi", lambda s, **kw: (kalshi, None))
    ranked, errors = tools.prediction_odds_for_spec(spec)
    assert [o.id for o in ranked] == ["y", "x"]  # higher volume first
    assert errors == []


def test_prediction_odds_for_spec_collects_source_errors(monkeypatch):
    from contracts.strategies.models import DeskSpec, Instrument
    spec = DeskSpec(id="tt", name="t", tagline="t",
                    instruments=[Instrument(coin="BTC", label="BTC", role="primary")])
    monkeypatch.setattr(tools.predictions_src, "fetch_polymarket", lambda s, **kw: ([], "predictions:polymarket:timeout"))
    monkeypatch.setattr(tools.predictions_src, "fetch_kalshi", lambda s, **kw: ([_odds("z", 0.3, 1.0)], None))
    ranked, errors = tools.prediction_odds_for_spec(spec)
    assert [o.id for o in ranked] == ["z"]
    assert errors == ["predictions:polymarket:timeout"]
