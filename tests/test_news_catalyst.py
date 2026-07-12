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


def test_google_news_search_parses_and_caches(monkeypatch):
    from hermes_trader.agents import news_catalyst as nc
    xml = """<rss><channel>
      <item><title>Bitcoin ETF sees record inflow</title><link>https://a.example/1</link>
        <pubDate>Fri, 11 Jul 2026 01:00:00 +0000</pubDate></item>
      <item><title>Exchange hacked for $40M</title><link>https://b.example/2</link>
        <pubDate>Fri, 11 Jul 2026 00:30:00 +0000</pubDate></item>
    </channel></rss>"""
    calls = []
    monkeypatch.setattr(nc, "_get_text", lambda url, timeout=25.0: calls.append(url) or xml)
    nc._cache.clear()
    arts = nc.google_news_search("bitcoin", when="1h")
    assert len(arts) == 2 and arts[0].title.startswith("Bitcoin ETF")
    assert "when%3A1h" in calls[0] or "when:1h" in calls[0]
    # cached: second call makes no fetch
    nc.google_news_search("bitcoin", when="1h")
    assert len(calls) == 1


def test_coin_catalyst_surge_math(monkeypatch):
    from hermes_trader.agents import news_catalyst as nc
    def fake_search(query, when="1d", limit=25, ttl=0):
        n = 6 if when == "1h" else 24        # 6/h vs 1/h baseline -> surge 6x
        # titles carry the ALL-CAPS ticker so the relevance guard keeps them
        return [nc.Article(title=f"VIRTUAL story {i}", url="u", domain="d",
                           seen=None, source="s")
                for i in range(min(n, limit))]
    monkeypatch.setattr(nc, "google_news_search", fake_search)
    rep = nc.coin_catalyst("VIRTUAL")
    assert rep.breaking is True and rep.surge_x == 6.0
    def quiet(query, when="1d", limit=25, ttl=0):
        return [] if when == "1h" else [nc.Article("t","u","d",None,"s")] * 12
    monkeypatch.setattr(nc, "google_news_search", quiet)
    rep = nc.coin_catalyst("QUIET")
    assert rep.breaking is False and rep.n_recent == 0


def test_fetch_news_prefers_google(monkeypatch):
    from hermes_trader.agents import research, news_catalyst as nc
    monkeypatch.setattr(nc, "google_news_search",
                        lambda q, when="1d", limit=5, ttl=0: [
                            nc.Article("SOL upgrade ships", "u", "d", None, "s")])
    out = research._fetch_news("SOL")
    assert "SOL upgrade ships" in out


def test_title_relevance_rejects_homonym_noise():
    from hermes_trader.agents.news_catalyst import _title_relevant
    # the live GRASS false positive (2026-07-12)
    assert _title_relevant(
        "GRASS",
        "After years in labs, 12 beagles reach Canada foster homes and feel "
        "grass, sunlight, and safety - The Cool Down") is False
    assert _title_relevant("PUMP", "New pump track opens at city bike park") is False
    assert _title_relevant("TRUMP", "Trump comments on trade tariffs") is False


def test_title_relevance_accepts_crypto_context_and_tickers():
    from hermes_trader.agents.news_catalyst import _title_relevant
    assert _title_relevant("GRASS", "$GRASS jumps 20% after exchange listing") is True
    assert _title_relevant("GRASS", "GRASS token rallies on DePIN news") is True
    assert _title_relevant("GRASS", "Grass airdrop checker goes live") is True   # crypto term
    assert _title_relevant("PUMP", "PUMP hits all-time high after Binance listing") is True


def test_coin_catalyst_counts_only_relevant(monkeypatch):
    from hermes_trader.agents import news_catalyst as nc

    def fake_search(query, when="1d", limit=25, ttl=0):
        mk = lambda t: nc.Article(title=t, url="u", domain="d", seen=None)
        if when == "1h":
            return [mk("Beagles feel grass and sunlight"),
                    mk("$GRASS surges on listing"),
                    mk("Lawn care tips for summer grass")]
        return [mk("Beagles feel grass and sunlight")] * 24

    monkeypatch.setattr(nc, "google_news_search", fake_search)
    rep = nc.coin_catalyst("GRASS", ttl=0)
    assert rep.n_recent == 1                       # only the cashtag story
    assert all("Beagles" not in a.title for a in rep.headlines)
    assert rep.breaking is False                   # 1 < min 3 articles


def test_macro_headlines_dedup_and_tags(monkeypatch):
    from hermes_trader.agents import news_catalyst as nc

    def fake_search(query, when="1d", limit=10, ttl=0):
        mk = lambda t: nc.Article(title=t, url="u", domain="d", seen=None)
        if "Federal Reserve" in query:
            return [mk("Fed holds rates steady"), mk("Fed holds rates steady"), mk("Inflation cools")]
        if "Trump" in query:
            return [mk("Trump signals new tariffs on chips")]
        return [mk("Bitcoin steadies near $64k")]

    monkeypatch.setattr(nc, "google_news_search", fake_search)
    tape = nc.macro_headlines(per_query=2, ttl=0)
    assert any(t.startswith("[fed]") for t in tape)
    assert any(t.startswith("[politics]") for t in tape)
    # dedup: the doubled Fed headline appears once
    assert sum("Fed holds rates steady" in t for t in tape) == 1


def test_user_message_carries_date_and_macro(monkeypatch):
    from hermes_trader.agents import research
    msg = research._build_user_message(
        "ARB", {"mid": 0.1, "composite_score": 50, "triggers": []},
        {}, {}, {}, "0.00%", "no news", 100.0, [], "LIVE",
        macro_tape="[fed] Fed holds | [politics] tariffs",
    )
    assert "Today (UTC): 20" in msg
    assert "NOT a catalyst" in msg
    assert "Macro tape" in msg and "tariffs" in msg
