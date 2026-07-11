"""Tests for the free news-catalyst engine (pure parsers; no network)."""

from datetime import datetime, timezone

from hermes_trader.agents.news_catalyst import (
    parse_gdelt_artlist, detect_surge, parse_gdelt_timeline,
    parse_rss, filter_keywords, _parse_gdelt_date,
)


def test_gdelt_date():
    d = _parse_gdelt_date("20260615T143000Z")
    assert d == datetime(2026, 6, 15, 14, 30, 0, tzinfo=timezone.utc)
    assert _parse_gdelt_date("garbage") is None


def test_parse_artlist_sorts_newest_first():
    payload = {"articles": [
        {"title": "Older", "url": "u1", "domain": "reuters.com", "seendate": "20260615T120000Z"},
        {"title": "Newer", "url": "u2", "domain": "ap.org", "seendate": "20260615T143000Z"},
    ]}
    arts = parse_gdelt_artlist(payload)
    assert [a.title for a in arts] == ["Newer", "Older"]
    assert arts[0].source == "gdelt"


def test_detect_surge():
    # flat baseline ~10, latest spikes to 40 -> 4x -> breaking
    breaking, x = detect_surge([10, 9, 11, 10, 40])
    assert breaking and x == 4.0
    # no spike
    assert detect_surge([10, 11, 9, 10, 12]) == (False, 1.2)
    # too few points -> safe
    assert detect_surge([5]) == (False, 1.0)


def test_parse_timeline():
    payload = {"timeline": [{"data": [{"date": "x", "value": 3}, {"date": "y", "value": 7}]}]}
    assert parse_gdelt_timeline(payload) == [3.0, 7.0]
    assert parse_gdelt_timeline({}) == []


_RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item><title>Iran peace deal signed</title><link>https://reuters.com/a</link>
        <pubDate>Mon, 15 Jun 2026 14:30:00 GMT</pubDate></item>
  <item><title>Some sports result</title><link>https://espn.com/b</link>
        <pubDate>Mon, 15 Jun 2026 14:00:00 GMT</pubDate></item>
</channel></rss>"""


def test_parse_rss_and_filter():
    arts = parse_rss(_RSS, source="reuters.com")
    assert len(arts) == 2
    assert arts[0].title == "Iran peace deal signed"
    assert arts[0].seen.tzinfo is not None
    hits = filter_keywords(arts, ["iran", "peace"])
    assert len(hits) == 1 and "Iran" in hits[0].title


def test_parse_rss_malformed_safe():
    assert parse_rss("not xml at all") == []


def test_filter_no_keywords_passthrough():
    arts = parse_rss(_RSS)
    assert filter_keywords(arts, []) == arts
