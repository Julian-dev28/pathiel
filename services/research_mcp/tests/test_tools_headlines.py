from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from contracts.strategies.models import Headline
from services.research_mcp import tools

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


@pytest.fixture(autouse=True)
def _frozen_now(monkeypatch):
    """Every test here reasons about headline ages relative to NOW, not
    whatever today's real wall-clock date happens to be."""
    monkeypatch.setattr(tools, "datetime", _FixedDatetime)


def _headline(id_, title, published_at, relevance=0.5):
    return Headline(id=id_, title=title, url="https://example.com/" + id_, source="Test",
                    published_at=published_at, relevance=relevance)


def test_headlines_tool_needs_coin_or_topic():
    with pytest.raises(ValueError):
        tools.headlines_tool()


def test_headlines_tool_by_coin(monkeypatch):
    rows = [_headline("a", "Brent news", NOW - timedelta(hours=1))]
    monkeypatch.setattr(tools, "fetch_headlines_fast", lambda spec, now, **kw: (rows, []))
    out = tools.headlines_tool(coin="BRENTOIL", hours=24)
    assert out["query"] == "xyz:BRENTOIL"
    assert out["headlines"][0]["id"] == "a"


def test_headlines_tool_by_topic_builds_an_adhoc_spec(monkeypatch):
    seen = {}

    def fake(spec, now, **kw):
        seen["queries"] = list(spec.news_queries)
        return [], []
    monkeypatch.setattr(tools, "fetch_headlines_fast", fake)
    out = tools.headlines_tool(topic="OPEC+ meeting")
    assert out["query"] == "OPEC+ meeting"
    assert seen["queries"] == ["OPEC+ meeting"]


def test_headlines_tool_filters_by_hours(monkeypatch):
    rows = [
        _headline("fresh", "fresh", NOW - timedelta(hours=1)),
        _headline("stale", "stale", NOW - timedelta(hours=48)),
        _headline("undated", "undated", None),
    ]
    monkeypatch.setattr(tools, "fetch_headlines_fast", lambda spec, now, **kw: (rows, []))
    out = tools.headlines_tool(coin="BTC", hours=24)
    ids = {h["id"] for h in out["headlines"]}
    assert ids == {"fresh", "undated"}  # stale dropped, undated kept (can't rule it out)


def test_headlines_tool_caps_at_25_and_reports_errors(monkeypatch):
    rows = [_headline(str(i), f"h{i}", NOW) for i in range(40)]
    monkeypatch.setattr(tools, "fetch_headlines_fast", lambda spec, now, **kw: (rows, ["news:x:timeout"]))
    out = tools.headlines_tool(coin="HYPE")
    assert len(out["headlines"]) == 25
    assert out["sources_failed"] == ["news:x:timeout"]


def test_headlines_tool_resolves_coin_aliases(monkeypatch):
    monkeypatch.setattr(tools, "fetch_headlines_fast", lambda spec, now, **kw: ([], []))
    out = tools.headlines_tool(coin="bitcoin")
    assert out["query"] == "BTC"
