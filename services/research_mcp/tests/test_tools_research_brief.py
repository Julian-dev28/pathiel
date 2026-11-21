"""services/research_mcp/tools.py: research_brief -- the one-call summary.

Every data source is mocked at tools.py's own seam (summarize_levels,
upcoming_catalysts, fetch_headlines_fast, prediction_odds_for_spec,
latest_thesis_json); each tool's own happy/error path is covered in its own
test_tools_*.py file. These tests cover research_brief's COMPOSITION: it
must survive any one source failing, serve the stored thesis without
writing a new one, and flag the ETH/top5 special case.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from contracts.strategies.models import Catalyst, Headline, MarketOdds
from services.research_mcp import coins, tools

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


def _mock_all_sources(monkeypatch, *, levels=None, catalysts=None, headlines=None, odds=None,
                       thesis=None):
    monkeypatch.setattr(tools, "summarize_levels", lambda coin, **kw: levels if levels is not None
                        else {"coin": coin, "mid": 1.0})
    monkeypatch.setattr(tools, "upcoming_catalysts", lambda spec, now, **kw: catalysts or [])
    monkeypatch.setattr(tools, "fetch_headlines_fast", lambda spec, now, **kw: (headlines or [], []))
    monkeypatch.setattr(tools, "prediction_odds_for_spec", lambda spec, **kw: (odds or [], []))
    monkeypatch.setattr(tools, "latest_thesis_json", lambda desk_id: thesis)


def test_research_brief_happy_path(monkeypatch):
    cat = Catalyst(id="c1", kind="fomc", title="FOMC", at=NOW, importance=3, source="Fed")
    hl = Headline(id="h1", title="Brent news", url="https://x", source="Test", relevance=0.9)
    odd = MarketOdds(id="o1", venue="polymarket", question="q", probability=0.5,
                     volume_usd=10.0, url="https://x")
    _mock_all_sources(monkeypatch, catalysts=[cat], headlines=[hl], odds=[odd],
                      thesis={"id": "t1", "desk_id": "oil"})
    out = tools.research_brief("BRENTOIL", now=NOW)
    assert out["coin"] == "xyz:BRENTOIL"
    assert out["desk_id"] == "oil"
    assert out["levels"]["mid"] == 1.0
    assert out["catalysts"][0]["id"] == "c1"
    assert out["headlines"][0]["id"] == "h1"
    assert out["odds"][0]["id"] == "o1"
    assert out["thesis"] == {"id": "t1", "desk_id": "oil"}
    assert out["note"] is None
    assert out["sources_failed"] == []


def test_research_brief_with_no_stored_thesis_is_null_not_an_error(monkeypatch):
    _mock_all_sources(monkeypatch, thesis=None)
    out = tools.research_brief("HYPE", now=NOW)
    assert out["thesis"] is None
    assert out["sources_failed"] == []


def test_research_brief_survives_every_source_failing(monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("dead source")
    monkeypatch.setattr(tools, "summarize_levels", boom)
    monkeypatch.setattr(tools, "upcoming_catalysts", boom)
    monkeypatch.setattr(tools, "fetch_headlines_fast", boom)
    monkeypatch.setattr(tools, "prediction_odds_for_spec", boom)
    monkeypatch.setattr(tools, "latest_thesis_json", boom)
    out = tools.research_brief("BTC", now=NOW)
    assert out["levels"] is None
    assert out["catalysts"] == []
    assert out["headlines"] == []
    assert out["odds"] == []
    assert out["thesis"] is None
    assert len(out["sources_failed"]) == 5
    assert any("levels:" in e for e in out["sources_failed"])
    assert any("thesis:" in e for e in out["sources_failed"])


def test_research_brief_one_dead_source_does_not_sink_the_others(monkeypatch):
    hl = Headline(id="h1", title="t", url="https://x", source="s", relevance=0.5)

    def boom(*a, **kw):
        raise RuntimeError("odds API down")
    _mock_all_sources(monkeypatch, headlines=[hl])
    monkeypatch.setattr(tools, "prediction_odds_for_spec", boom)
    out = tools.research_brief("SP500", now=NOW)
    assert out["headlines"][0]["id"] == "h1"
    assert out["odds"] == []
    assert out["sources_failed"] == ["odds:odds API down"]


def test_research_brief_eth_notes_the_top5_basket_desk(monkeypatch):
    _mock_all_sources(monkeypatch)
    out = tools.research_brief("ETH", now=NOW)
    assert out["desk_id"] == "top5"
    assert out["note"] is not None and "top5" in out["note"] and "ETH" in out["note"]


def test_research_brief_other_coins_get_no_note(monkeypatch):
    _mock_all_sources(monkeypatch)
    for coin in ("BRENTOIL", "HYPE", "BTC", "SP500"):
        assert tools.research_brief(coin, now=NOW)["note"] is None


def test_research_brief_rejects_an_unsupported_coin(monkeypatch):
    _mock_all_sources(monkeypatch)
    with pytest.raises(coins.UnsupportedCoinError):
        tools.research_brief("DOGE", now=NOW)
