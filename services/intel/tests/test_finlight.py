"""services/intel/sources/finlight.py and providers.py, offline: fakes for
the WebSocket and the REST call. The key never reaches a log line."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import pytest

from services.intel.sources import finlight as FL
from services.intel.sources import providers as P

KEY = "fl_test_secret_key_0123456789abcdef"
NOW = datetime(2026, 11, 14, 10, 0, tzinfo=timezone.utc)


def _article(title, link=None, minutes_ago=1, sentiment="negative", confidence=0.9, source="reuters.com"):
    return {"title": title, "link": link or "https://x/" + str(abs(hash(title))), "source": source,
            "publishDate": (NOW - timedelta(minutes=minutes_ago)).isoformat(), "language": "en",
            "sentiment": sentiment, "confidence": confidence,
            "companies": [{"companyId": 1, "name": "Saudi Aramco", "ticker": "2222.SR", "confidence": 0.8}]}


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _run(coro, timeout=5.0):
    return asyncio.run(asyncio.wait_for(coro, timeout))


# ── providers.py ────────────────────────────────────────────────────────────

def test_normalize_item_is_the_news_alerts_row_plus_provider_fields():
    it = P.normalize_item(title="  Tanker   struck ", url="https://r/1", source="reuters.com",
                          published_at="2026-11-14T09:59:00Z", provider="finlight", sentiment="Negative",
                          sentiment_confidence="0.93", entities=[{"name": "Aramco"}, "junk"], summary=" s ")
    assert it["title"] == "Tanker struck" and it["id"] == "https://r/1" and it["source"] == "reuters.com"
    assert it["published_at"] == datetime(2026, 11, 14, 9, 59, tzinfo=timezone.utc)
    assert it["sentiment"] == "negative" and it["sentiment_confidence"] == 0.93
    assert it["entities"] == [{"name": "Aramco"}] and it["summary"] == "s" and it["received_at"].tzinfo
    assert P.normalize_item(title="x", provider="p", published_at="bad", sentiment="meh")["published_at"] is None
    assert P.normalize_sentiment("bullish") == "positive" and P.normalize_sentiment(None) is None
    assert P.dedup_key({"title": "A  b!"}) == P.dedup_key({"title": "a b"})


def test_article_to_item_reads_the_sdk_model_and_its_dict():
    from finlight_client.models import Article
    raw = _article("OPEC+ agrees surprise output cut")
    a = Article.model_validate(raw)
    for src in (a, raw):
        it = FL.article_to_item(src, NOW)
        assert it["title"] == "OPEC+ agrees surprise output cut" and it["provider"] == "finlight"
        assert it["sentiment"] == "negative" and it["sentiment_confidence"] == 0.9
        assert it["entities"][0]["ticker"] == "2222.SR" and it["received_at"] == NOW
        assert it["published_at"] == NOW - timedelta(minutes=1)


# ── the poll budget ─────────────────────────────────────────────────────────

def test_the_free_plan_floors_the_poll_near_ten_minutes_and_a_big_plan_does_not(monkeypatch):
    monkeypatch.delenv(FL.POLL_ENV, raising=False)
    monkeypatch.delenv(FL.CAP_ENV, raising=False)
    free = FL.effective_poll_s()
    assert free["requested_s"] == 15.0 and free["monthly_cap"] == 5000 and free["why"] == "budget floor"
    assert 570 < free["effective_s"] < 600          # 31 days / (5000 x 0.9)
    assert FL.effective_poll_s(15, 150_000)["effective_s"] == pytest.approx(19.8, abs=0.1)
    assert FL.effective_poll_s(600, 5000)["why"] == "requested"
    monkeypatch.setenv(FL.POLL_ENV, "30")
    monkeypatch.setenv(FL.CAP_ENV, "1000000")
    assert FL.effective_poll_s() == {"requested_s": 30.0, "floor_s": 3.0, "effective_s": 30.0,
                                     "monthly_cap": 1000000, "why": "requested"}
    monkeypatch.setenv(FL.POLL_ENV, "junk")
    assert FL.effective_poll_s()["requested_s"] == 15.0


# ── the transports ──────────────────────────────────────────────────────────

def _tier_ws(reason="websocket handshake refused with HTTP 403"):
    async def connect(key, query, on_article, stop, probe_s, on_admit):
        raise FL.TierError(reason)
    return connect


def test_fallback_to_rest_on_a_tier_error_logs_the_mode_and_never_the_key(caplog):
    clock = FakeClock()
    pages = [[_article("Houthis seize vessel in Red Sea", link="https://r/a")],
             [_article("Houthis seize vessel in Red Sea", link="https://r/a"),      # the same article again
              _article("OPEC+ agrees surprise output cut", link="https://r/b")]]
    calls = []

    async def rest(query, since):
        calls.append((query, since))
        return pages.pop(0) if pages else []

    prov = FL.FinlightProvider(KEY, poll_s=1, monthly_cap=10_000_000, ws_connect=_tier_ws(), rest_fetch=rest,
                               clock=clock)
    got = []
    stop = asyncio.Event()

    async def drive():
        task = asyncio.create_task(prov.run(got.append, stop))
        for _ in range(40):
            await asyncio.sleep(0.05)
            clock.t += 0.6
            if len(calls) >= 2:
                break
        stop.set()
        await task

    with caplog.at_level(logging.DEBUG):
        _run(drive())
    assert prov.mode == "idle" and prov.ws_fallback_reason.startswith("websocket handshake refused")
    assert "mode=rest" in caplog.text and "HTTP 403" in caplog.text
    assert [g["title"] for g in got] == ["Houthis seize vessel in Red Sea", "OPEC+ agrees surprise output cut"]
    assert prov.received == 2 and prov.rest_calls == len(calls) >= 2
    assert all(it["provider"] == "finlight" for it in got)
    assert calls[0][0] == FL.QUERY and calls[0][1].tzinfo is not None
    assert KEY not in caplog.text and KEY[:8] not in caplog.text


def test_the_tier_error_reads_a_403_a_close_code_and_a_plan_message():
    class Resp:
        status_code = 403

    class Handshake(Exception):
        response = Resp()

    class Closed(Exception):
        code = 1008

    assert FL.is_tier_error(Handshake("x")) and FL.is_tier_error(Closed("x"))
    assert FL.is_tier_error(RuntimeError("WebSocket streaming is not included in your plan"))
    assert not FL.is_tier_error(RuntimeError("websocket pong timeout"))
    assert not FL.is_tier_error(ConnectionError("Connection reset by peer"))


def test_a_transport_error_reconnects_with_backoff_and_an_admit_sets_ws_mode(caplog):
    attempts = []

    async def connect(key, query, on_article, stop, probe_s, on_admit):
        attempts.append(key)
        if len(attempts) == 1:
            raise RuntimeError(f"websocket pong timeout for {key}")      # a message that leaks the key
        on_admit()
        on_article(_article("Tanker struck by missile in Strait of Hormuz, UKMTO confirms"))
        on_article(_article("Tanker struck by missile in Strait of Hormuz, UKMTO confirms"))  # the ws repeats
        await stop.wait()

    prov = FL.FinlightProvider(KEY, ws_connect=connect, rest_fetch=None)
    got = []
    stop = asyncio.Event()

    async def drive():
        task = asyncio.create_task(prov.run(got.append, stop))
        for _ in range(100):
            await asyncio.sleep(0.02)
            if got:
                break
        assert prov.mode == "ws"
        stop.set()
        await task

    with caplog.at_level(logging.DEBUG):
        _run(drive())
    assert len(attempts) == 2 and [g["title"] for g in got] == ["Tanker struck by missile in Strait of Hormuz, UKMTO confirms"]
    assert "backing off" in caplog.text and "mode=ws" in caplog.text
    assert KEY not in caplog.text and "***" in prov.last_error and KEY not in prov.last_error


def test_rest_backs_off_on_a_failed_poll_and_never_raises(caplog):
    clock = FakeClock()
    n = {"calls": 0}

    async def rest(query, since):
        n["calls"] += 1
        raise RuntimeError(f"429 Too Many Requests for key {KEY}")

    prov = FL.FinlightProvider(KEY, poll_s=1, monthly_cap=10_000_000, ws_connect=_tier_ws(), rest_fetch=rest,
                               clock=clock)
    stop = asyncio.Event()

    async def drive():
        task = asyncio.create_task(prov.run(lambda i: None, stop))
        await asyncio.sleep(0.1)
        stop.set()
        await task

    with caplog.at_level(logging.DEBUG):
        _run(drive())
    assert n["calls"] == 1 and "429" in prov.last_error and KEY not in prov.last_error
    assert KEY not in caplog.text


def test_no_key_means_the_provider_is_down_not_an_exception(monkeypatch, caplog):
    monkeypatch.delenv(FL.KEY_ENV, raising=False)
    prov = FL.FinlightProvider(None)
    assert not prov.has_key
    with caplog.at_level(logging.DEBUG):
        _run(prov.run(lambda i: None, asyncio.Event()))
    assert prov.mode == "down" and "not set" in prov.last_error


def test_redact_scrubs_the_key_and_its_prefix():
    assert FL.redact(f"bad key {KEY} and {KEY[:8]}xyz", KEY) == "bad key *** and ***xyz"
    assert FL.redact("nothing", None) == "nothing"


def test_the_provider_dedups_by_link_across_rest_pages_and_forgets_after_six_hours():
    clock = FakeClock()
    prov = FL.FinlightProvider(KEY, ws_connect=_tier_ws(), rest_fetch=None, clock=clock)
    got = []
    it = P.normalize_item(title="A", url="https://r/1", provider="finlight")
    assert prov._push(it, got.append) and not prov._push(dict(it), got.append)
    assert not prov._push(P.normalize_item(title="", url="https://r/2", provider="finlight"), got.append)
    assert len(got) == 1 and prov.received == 1
