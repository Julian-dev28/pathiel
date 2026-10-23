"""Unit tests for services/intel/sources/news.py against REAL captured
responses (services/intel/tests/fixtures/), with http_client monkeypatched
so no test here touches the network."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from contracts.strategies.models import DeskSpec, Instrument
from services.intel import http_client
from services.intel.sources import news
from services.intel.tests.conftest import fixture_text

NOW = datetime(2026, 10, 17, 3, 0, tzinfo=timezone.utc)


def _btc_spec() -> DeskSpec:
    return DeskSpec(id="btc", name="Bitcoin", tagline="t",
                     instruments=[Instrument(coin="BTC", label="BTC")],
                     news_queries=["bitcoin price", "BTC"])


# ── RSS parsing against real fixtures ────────────────────────────────

def test_parse_google_news_real_fixture_strips_source_suffix():
    items = news.parse_rss_items(fixture_text("google_news_oil.xml"), split_source_from_title=True)
    assert len(items) == 15
    first = items[0]
    assert not first["title"].endswith("CNBC")
    assert first["source"]  # non-empty, split out of the title
    assert first["published_at"] is not None
    assert first["published_at"].tzinfo is not None


def test_parse_oilprice_real_fixture_default_source():
    items = news.parse_rss_items(fixture_text("oilprice_main.xml"), default_source="OilPrice.com")
    assert len(items) == 12
    assert all(i["source"] == "OilPrice.com" for i in items)
    assert items[0]["url"].startswith("https://oilprice.com/")


def test_parse_cointelegraph_real_fixture():
    items = news.parse_rss_items(fixture_text("cointelegraph.xml"), default_source="Cointelegraph")
    assert len(items) == 12
    assert all(i["title"] for i in items)


def test_parse_rss_items_malformed_xml_raises_for_caller_to_catch():
    with pytest.raises(Exception):
        news.parse_rss_items("<rss><item><title>unterminated")


def test_parse_rss_items_skips_items_without_title():
    xml = ("<rss><channel><item><link>https://x.com</link></item>"
           "<item><title>Real story</title><link>https://y.com</link></item></channel></rss>")
    items = news.parse_rss_items(xml)
    assert len(items) == 1 and items[0]["title"] == "Real story"


# ── GDELT parsing against a real (multilingual) capture ─────────────

def test_gdelt_ascii_filter_drops_non_english_real_titles():
    """The real captured response has Vietnamese/Chinese/Finnish titles
    mixed with the one English one — exactly the leak the module
    docstring describes. Exercised through fetch_gdelt with http_client
    mocked to return this fixture verbatim."""
    import json
    raw = json.loads(fixture_text("gdelt_oil_sample.json"))
    assert len(raw["articles"]) == 10  # sanity: fixture has the mixed-language set

    def fake_fetch_json(url, **kw):
        return raw, None

    orig = http_client.fetch_json
    http_client.fetch_json = fake_fetch_json
    try:
        items, err = news.fetch_gdelt(_oil_spec())
    finally:
        http_client.fetch_json = orig
    assert err is None
    titles = [i["title"] for i in items]
    assert any("soaring oil shipping costs" in t for t in titles)
    assert not any("Giá xăng dầu" in t or "油价" in t for t in titles)


def test_fetch_gdelt_propagates_http_error(monkeypatch):
    monkeypatch.setattr(http_client, "fetch_json", lambda *a, **k: (None, "http_429"))
    items, err = news.fetch_gdelt(_oil_spec())
    assert items == [] and err == "news:gdelt:http_429"


def test_fetch_gdelt_does_not_retry_and_keeps_timeout_under_the_packet_budget(monkeypatch):
    """Regression for the real `news.fetch_gdelt:timeout_budget_exceeded`
    failure: GDELT's own 429 rate limit (one request per 5s, see module
    docstring) can never clear within http_client's 1.0s retry backoff, so
    retrying wastes the whole packet's 12s FETCH_DEADLINE_S. fetch_gdelt
    must call with retries=0 and a timeout well under that deadline."""
    captured = {}

    def fake(url, params=None, **kw):
        captured.update(kw)
        return {"articles": []}, None
    monkeypatch.setattr(http_client, "fetch_json", fake)
    news.fetch_gdelt(_oil_spec())
    assert captured["retries"] == 0
    assert captured["timeout_s"] <= 6.0


def test_fetch_gdelt_falls_back_to_instrument_tokens_when_no_news_queries(monkeypatch):
    """A DeskSpec always has at least one primary instrument (the contract
    enforces it), so `_gdelt_query` can never truly have nothing to
    search for — confirm it falls back to the instrument's label/coin
    rather than skipping the call outright."""
    captured = {}

    def fake_fetch_json(url, params=None, **kw):
        captured["query"] = (params or {}).get("query")
        return {"articles": []}, None

    monkeypatch.setattr(http_client, "fetch_json", fake_fetch_json)
    spec = DeskSpec(id="xx", name="X", tagline="t",
                     instruments=[Instrument(coin="xyz:ZZZ", label="SomeLabel")], news_queries=[])
    items, err = news.fetch_gdelt(spec)
    assert items == [] and err is None
    assert "SomeLabel" in captured["query"] and "ZZZ" in captured["query"]


# ── desk-type gating (oilprice / crypto RSS only fire for the right desk) ─

def test_fetch_oilprice_skipped_for_non_oil_desk(monkeypatch):
    called = []
    monkeypatch.setattr(http_client, "fetch_text", lambda *a, **k: called.append(1))
    items, err = news.fetch_oilprice(_btc_spec())
    assert items == [] and err is None
    assert not called, "oilprice.com must not be fetched for a non-oil desk"


def test_fetch_oilprice_fires_for_oil_desk(monkeypatch):
    monkeypatch.setattr(http_client, "fetch_text",
                         lambda *a, **k: (fixture_text("oilprice_main.xml"), None))
    items, err = news.fetch_oilprice(_oil_spec())
    assert err is None and len(items) == 12


def test_fetch_crypto_rss_skipped_for_oil_desk(monkeypatch):
    called = []
    monkeypatch.setattr(http_client, "fetch_text", lambda *a, **k: called.append(1))
    items, err = news.fetch_crypto_rss(_oil_spec())
    assert items == [] and err is None
    assert not called


def test_fetch_crypto_rss_fires_for_btc_desk(monkeypatch):
    monkeypatch.setattr(http_client, "fetch_text",
                         lambda *a, **k: (fixture_text("cointelegraph.xml"), None))
    items, err = news.fetch_crypto_rss(_btc_spec())
    assert err is None and len(items) == 12


# ── score_and_dedup: the pure merge/score/sort step ──────────────────

def test_score_and_dedup_sorts_by_relevance_descending():
    raw = [
        {"title": "Shell Rotella revs up its heavy-duty oil lineup", "url": "u1",
         "source": "Land Line Media", "published_at": NOW},
        {"title": "Brent oil touches $110 as Iran escalates Hormuz attacks", "url": "u2",
         "source": "CNBC", "published_at": NOW},
    ]
    out = news.score_and_dedup(raw, _oil_spec(), NOW)
    assert len(out) == 2
    assert out[0].title.startswith("Brent oil touches")
    assert out[0].relevance > out[1].relevance


def test_score_and_dedup_deduplicates_cross_source_reprints():
    raw = [
        {"title": "WTI gains 6.5% as US crude inventories drop 8.6 million barrels - Reuters",
         "url": "u1", "source": "Reuters", "published_at": NOW},
        {"title": "WTI gains 6.5% as US crude inventories drop 8.6 million barrels - Coinpaper",
         "url": "u2", "source": "Coinpaper", "published_at": NOW - timedelta(hours=1)},
    ]
    out = news.score_and_dedup(raw, _oil_spec(), NOW)
    assert len(out) == 1


def test_score_and_dedup_ids_are_stable_across_calls():
    raw = [{"title": "Oil rises", "url": "u1", "source": "Reuters", "published_at": NOW}]
    out1 = news.score_and_dedup(raw, _oil_spec(), NOW)
    out2 = news.score_and_dedup(raw, _oil_spec(), NOW)
    assert out1[0].id == out2[0].id


def test_score_and_dedup_empty_input():
    assert news.score_and_dedup([], _oil_spec(), NOW) == []


def test_query_phrases_includes_instrument_labels_and_coins():
    phrases = news.query_phrases(_oil_spec())
    assert "WTI" in phrases and "Brent" in phrases
    assert "crude oil price" in phrases


def _oil_spec() -> DeskSpec:
    import json
    from pathlib import Path
    p = (Path(__file__).parent.parent.parent.parent / "contracts" / "strategies"
         / "fixtures" / "oil_spec.json")
    return DeskSpec.model_validate(json.loads(p.read_text()))


def test_score_and_dedup_sinks_a_bare_instrument_token_headline(oil_spec):
    """2026-10-24: a Guardian piece about David Brent (The Office) ranked
    among the oil desk's headlines on the single token "Brent". With the
    instrument-only penalty it scores a quarter of a real match and sorts
    last; the real Brent crude story keeps a full score."""
    from datetime import datetime, timedelta, timezone
    now = datetime(2026, 10, 24, 4, 0, tzinfo=timezone.utc)
    fresh = now - timedelta(hours=2)
    raw = [
        {"title": "Is this Prince Harry or David Brent in The Office? Either way, we're watching a masterclass in awkwardness",
         "url": "https://g/1", "source": "The Guardian", "published_at": fresh},
        {"title": "Brent crude tops $113, highest level since May, after Trump hints at action on Iran",
         "url": "https://c/1", "source": "CNBC", "published_at": fresh},
        {"title": "Brent falls as Iran's president discusses mediation with Pakistani army chief",
         "url": "https://r/1", "source": "Reuters", "published_at": fresh},
    ]
    heads = news.score_and_dedup(raw, oil_spec, now)
    by_title = {h.title[:20]: h for h in heads}
    sitcom = by_title["Is this Prince Harry"]
    assert sitcom.relevance <= 0.25
    assert by_title["Brent crude tops $11"].relevance >= 0.9
    assert by_title["Brent falls as Iran'"].relevance >= 0.8
    assert heads[-1] is sitcom
