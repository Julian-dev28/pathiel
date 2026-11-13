"""services/intel/finlight_watch.py and event_log.py, and the Finlight step
of scripts/trigger_watch.py: a pushed headline fires the same
{"fire": "oil", "reason": "headline"} line; a Finlight failure never stops
the watcher; the event log and the webhook. Every file is under tmp_path."""
from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.intel import book_guard as BG
from services.intel import event_log as EL
from services.intel import finlight_watch as FW
from services.intel import headline_watch as HW
from services.intel.sources import finlight as FL
from services.intel.sources.providers import BaseNewsProvider, normalize_item

ROOT = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location("trigger_watch_fl", ROOT / "scripts" / "trigger_watch.py")
W = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(W)

KEY = "fl_test_secret_key_0123456789abcdef"
NOW_S = 1000.0
NOW = datetime.fromtimestamp(NOW_S, tz=timezone.utc)
BRENT_LV = {"range_24h": {"low": 112.00, "high": 114.23}, "atr14_1h": 0.17}
TARGETS = [("oil", "xyz:BRENTOIL", "oil")]
BOOK = {"xyz:BRENTOIL": BG.snapshot_from_book("xyz:BRENTOIL", {"bids": [(63.49, 20.0)], "asks": [(63.51, 20.0)]}, 500.0, 1),
        "xyz:CL": {"coin": "xyz:CL", "error": "no book (budget refused or venue failed)"}}


def _item(title, seconds_ago=20, source="reuters.com", sentiment="negative"):
    return normalize_item(title=title, url="https://r/" + str(abs(hash(title))), source=source,
                          published_at=NOW - timedelta(seconds=seconds_ago), provider="finlight",
                          sentiment=sentiment, sentiment_confidence=0.9, received_at=NOW - timedelta(seconds=2))


class FakeProvider(BaseNewsProvider):
    """Pushes `items` once the loop runs, then waits for stop."""
    name = "finlight"

    def __init__(self, items=(), mode="ws", raise_on_run=None):
        super().__init__()
        self.items = list(items)
        self._mode = mode
        self.raise_on_run = raise_on_run

    def status(self):
        return {"provider": "finlight", "mode": self.mode, "received": self.received, "last_error": self.last_error}

    async def run(self, on_item, stop):
        if self.raise_on_run:
            raise self.raise_on_run
        self.mode = self._mode
        for it in self.items:
            self.received += 1
            on_item(it)
        await stop.wait()
        self.mode = "idle"


def _started(items=(), **kw):
    r = FW.FinlightRunner(FakeProvider(items, **kw), clock=lambda: NOW_S).start()
    for _ in range(200):
        if r.provider.received >= len(items) and (r.status()["queued"] >= len(items) or r.provider.mode != "idle"):
            break
        time.sleep(0.01)
    time.sleep(0.02)
    return r


def _no_book(coins):
    return BOOK


def _no_webhook(row):
    return False


# ── the runner ──────────────────────────────────────────────────────────────

def test_the_runner_queues_items_and_wakes_only_on_a_stage1_match():
    r = _started([_item("Apple launches new iPhone"), _item("Houthis seize vessel in Red Sea")])
    try:
        assert r.status()["queued"] == 2 and r.status()["alive"] and r.status()["mode"] == "ws"
        assert r.wait(0.05) is True            # the seizure matched on arrival
        assert r.wait(0.01) is False           # cleared; nothing new
        items = r.drain()
        assert [i["title"] for i in items] == ["Apple launches new iPhone", "Houthis seize vessel in Red Sea"]
        assert r.drain() == [] and "finlight: ws (2 received" in r.status_line()
    finally:
        r.stop()
    assert not r.status()["alive"]


def test_a_provider_that_raises_leaves_the_runner_down_not_the_process(caplog):
    with caplog.at_level(logging.DEBUG):
        r = _started([], raise_on_run=RuntimeError(f"boom with {KEY}"))
        for _ in range(100):
            if not r.status()["alive"]:
                break
            time.sleep(0.01)
    assert r.status()["mode"] == "down" and "boom" in r.status()["last_error"]
    assert r.drain() == [] and r.wait(0.01) is False


def test_start_runner_is_none_without_a_key_and_on_failure(monkeypatch):
    monkeypatch.delenv(FL.KEY_ENV, raising=False)
    assert FW.start_runner() is None
    monkeypatch.setenv(FL.KEY_ENV, KEY)

    class Boom:
        def __init__(self, *a, **k):
            raise RuntimeError(KEY)
    monkeypatch.setattr(FW, "FinlightRunner", Boom)
    assert FW.start_runner() is None
    monkeypatch.delenv(FL.KEY_ENV, raising=False)


# ── the pass ────────────────────────────────────────────────────────────────

def test_finlight_pass_learns_first_then_alerts_scores_logs_and_dedups(tmp_path, capsys):
    log = str(tmp_path / "finlight_events.jsonl")
    state = {"seen": [], "primed_sources": ["posts", "official"]}
    r = _started([_item("Houthis seize vessel in Red Sea")])
    try:
        # first drain: the source is new to the state file, learn only
        assert FW.finlight_pass(r, TARGETS, NOW_S, state, log_path=log, book=_no_book, webhook=_no_webhook) == []
        assert "finlight" in state["primed_sources"] and len(state["seen"]) == 1
        assert state["finlight"]["mode"] == "ws" and state["finlight"]["drained"] == 1
        # the same title again (the WebSocket repeated it): seen, no alert
        r.on_item(_item("Houthis seize vessel in Red Sea"))
        assert FW.finlight_pass(r, TARGETS, NOW_S, state, log_path=log, book=_no_book, webhook=_no_webhook) == []
        # a new confirmed strike: alert, Stage 2, book (confidence over the guard), log line
        r.on_item(_item("Tanker struck by missile in Strait of Hormuz, UKMTO confirms"))
        r.on_item(_item("Fed holds rates steady"))
        hits = FW.finlight_pass(r, TARGETS, NOW_S, state, log_path=log, book=_no_book, webhook=_no_webhook)
    finally:
        r.stop()
    assert len(hits) == 1
    desk, a = hits[0]
    assert desk == "oil" and a["kind"] == "news" and a["label"] == "iran/hormuz" and a["direction"] == 1
    assert a["provider"] == "finlight" and a["at_ms"] == 1_000_000 and a["received_ms"] == 998_000
    assert a["stage2"]["event_type"] == "chokepoint_disruption" and a["stage2"]["expected_oil_direction"] == "bullish"
    assert a["stage2"]["is_confirmed"] is True and a["stage2"]["confidence_score"] >= 0.75
    assert a["event_log"] == log
    rows = [json.loads(l) for l in open(log)]
    assert len(rows) == 1
    row = rows[0]
    assert row["provider"] == "finlight" and row["mode"] == "ws" and row["stage1"] == "iran/hormuz"
    assert row["sentiment"] == "negative" and row["published"] == "1970-01-01T00:16:20Z"
    assert row["latency_ms"]["published_to_received"] == 18_000 and row["latency_ms"]["received_to_scored"] > 0
    assert row["book"]["xyz:BRENTOIL"]["spread_bps"] and "error" in row["book"]["xyz:CL"]
    assert row["stage2"]["scorer"] == "rule"
    out = capsys.readouterr().out
    assert out.startswith("FINLIGHT ws ") and "chokepoint_disruption bullish" in out and "confirmed lag 18s" in out
    assert "xyz:BRENTOIL spread" in out
    # the alert goes through the live record path like any other headline
    HW.record([a], str(tmp_path / "alerts.json"))
    assert HW.headline_blackout("xyz:BRENTOIL", 1_000_000 + 60_000, str(tmp_path / "alerts.json"), side="short")
    assert HW.headline_blackout("xyz:BRENTOIL", 1_000_000 + 60_000, str(tmp_path / "alerts.json"), side="long") is None


def test_a_low_confidence_hit_takes_no_book_snapshot_and_a_failing_scorer_still_alerts(tmp_path, capsys):
    log = str(tmp_path / "ev.jsonl")
    state = {"seen": [], "primed_sources": ["finlight"]}
    books = []

    def book(coins):
        books.append(coins)
        return BOOK

    def bad_scorer(*a, **k):
        raise RuntimeError("scorer down")
    r = _started([_item("Reports say Houthis may seize vessel in Red Sea, sources say")])
    try:
        hits = FW.finlight_pass(r, TARGETS, NOW_S, state, log_path=log, book=book, webhook=_no_webhook)
        assert len(hits) == 1 and hits[0][1]["stage2"]["event_type"] == "unconfirmed_rumor" and books == []
        r.on_item(_item("CENTCOM: strikes on Houthi missile sites"))
        hits = FW.finlight_pass(r, TARGETS, NOW_S, state, log_path=log, book=book, webhook=_no_webhook,
                                score=bad_scorer)
    finally:
        r.stop()
    assert len(hits) == 1 and hits[0][1]["stage2"]["scorer"] == "rule"
    rows = [json.loads(l) for l in open(log)]
    assert rows[0]["book"] is None and "scorer failed" in rows[1]["stage2"]["rationale"]


def test_finlight_pass_never_raises(monkeypatch):
    class Broken:
        provider = SimpleNamespace(mode="ws", received=0)

        def drain(self):
            raise RuntimeError("queue gone")
    assert FW.finlight_pass(Broken(), TARGETS, NOW_S, {}) == []
    assert FW.finlight_pass(None, TARGETS, NOW_S, {}) == []
    r = _started([_item("Houthis seize vessel in Red Sea")])
    try:
        monkeypatch.setattr(HW, "news_alerts", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
        assert FW.finlight_pass(r, TARGETS, NOW_S, {"primed_sources": ["finlight"]}) == []
    finally:
        r.stop()


# ── event_log.py ────────────────────────────────────────────────────────────

def test_the_event_log_path_is_under_the_state_dir_and_the_webhook_is_off_by_default(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    assert EL.events_path() == str(tmp_path / "intel" / "finlight_events.jsonl")
    monkeypatch.delenv(EL.WEBHOOK_ENV, raising=False)
    sent = []
    assert EL.post_webhook({"title": "x"}, post=lambda u, b: sent.append((u, b))) is False and sent == []
    monkeypatch.setenv(EL.WEBHOOK_ENV, "https://discord.com/api/webhooks/1/abc")
    row = EL.build_event({"title": "Tanker struck", "source": "reuters.com", "label": "iran/hormuz", "at_ms": 5_000,
                          "published_ms": 1_000, "received_ms": 4_000, "provider": "finlight"},
                         mode="rest", stage2={"event_type": "chokepoint_disruption", "expected_oil_direction": "bullish",
                                              "confidence_score": 0.9, "is_confirmed": True}, scored_ms=4_500)
    assert row["latency_ms"] == {"published_to_received": 3_000, "received_to_scored": 500}
    assert EL.post_webhook(row, post=lambda u, b: sent.append((u, b))) is True
    assert sent[0][0].startswith("https://discord.com") and sent[0][1]["content"].startswith("FINLIGHT rest")
    assert sent[0][1]["event"]["stage2"]["confidence_score"] == 0.9
    tg = EL.webhook_body("https://api.telegram.org/bot123:abc/sendMessage?chat_id=42", "line", row)
    assert tg == {"chat_id": "42", "text": "line"}

    def boom(u, b):
        raise ConnectionError("down")
    assert EL.post_webhook(row, post=boom) is False
    path = EL.append_event(row, str(tmp_path / "x" / "ev.jsonl"))
    assert json.loads(open(path).read())["mode"] == "rest"


# ── the watcher ─────────────────────────────────────────────────────────────

def _stub(monkeypatch, tmp_path, mids, runner, held=""):
    import _state_env
    import pathiel.agents.config_store as cs
    import pathiel.client.exchange as ex
    from services.strategies import journal, levels
    monkeypatch.setattr(W, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(HW, "STATE_PATH", str(tmp_path / "headline_state.json"))
    monkeypatch.setattr(HW, "ALERTS_PATH", str(tmp_path / "headline_alerts.json"))
    monkeypatch.setattr(W, "fetch_headlines", lambda group: [])
    monkeypatch.setattr(W, "fetch_odds", lambda desk: [])
    monkeypatch.setattr(W, "fetch_posts", lambda: [])
    monkeypatch.setattr(W, "fetch_official", lambda: [])
    monkeypatch.setattr(W, "_start_finlight", lambda: runner)
    monkeypatch.setattr(_state_env, "load_env_local", lambda root: None)
    monkeypatch.setattr(cs, "read_agent_config", lambda: {
        "active_desks": ["oil"], "desk_equity_share": {"oil": 1.0}, "risk_profile": {"max_leverage": 20}})
    call = SimpleNamespace(side="long", entry_px=112.20, instrument="xyz:BRENTOIL")
    monkeypatch.setattr(journal, "latest_thesis", lambda desk: SimpleNamespace(call=call))
    monkeypatch.setattr(levels, "summarize_coin", lambda coin: BRENT_LV)
    it = iter(mids)
    monkeypatch.setattr(levels, "fetch_mid", lambda coin: next(it))
    monkeypatch.setattr(ex, "get_max_leverage", lambda coin: 20)
    monkeypatch.setattr(W, "_slot", lambda mod, desk: held)
    from services.accounts import signer_status
    monkeypatch.setattr(signer_status, "live", lambda max_age_s=0: {"ok": True})
    monkeypatch.setattr(BG, "book_snapshot", _no_book)
    monkeypatch.setattr(EL, "post_webhook", _no_webhook)
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))


def test_a_finlight_headline_fires_the_same_headline_line_and_cuts_the_sleep(monkeypatch, tmp_path, capsys):
    HW.save_state({"seen": [], "last_news_s": NOW_S, "last_posts_s": NOW_S, "odds": {},
                   "primed_sources": ["posts", "official", "finlight"]}, str(tmp_path / "headline_state.json"))
    runner = _started([_item("Tanker struck by missile in Strait of Hormuz, UKMTO confirms")])
    _stub(monkeypatch, tmp_path, [112.30, 112.30], runner, held="holds position on xyz:BRENTOIL")
    try:
        assert W.watch(once=False, cooldown_min=30, clock=lambda: NOW_S) == 0
    finally:
        runner.stop()
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines[0].startswith("FINLIGHT ws ")
    line = json.loads(lines[-1])
    assert line == {"fire": "oil", "also": [], "reason": "headline", "utc": line["utc"], "coin": "xyz:BRENTOIL",
                    "label": "iran/hormuz", "headline": "Tanker struck by missile in Strait of Hormuz, UKMTO confirms"}
    alerts = HW.read_alerts(str(tmp_path / "headline_alerts.json"))
    assert len(alerts) == 1 and alerts[0]["provider"] == "finlight" and alerts[0]["stage2"]["is_confirmed"] is True
    assert alerts[0]["direction"] == 1
    events = [json.loads(l) for l in open(tmp_path / "intel" / "finlight_events.jsonl")]
    assert events[0]["title"] == alerts[0]["title"] and events[0]["book"]["xyz:BRENTOIL"]["spread_bps"]
    # the live blackout reads the alert the same way as an RSS one: a bullish alert holds a short, not the long
    from services.strategies.event_blackout import event_blackout
    assert event_blackout("xyz:BRENTOIL", 1_000_000 + 60_000, side="short")["kind"] == "headline"
    assert event_blackout("xyz:BRENTOIL", 1_000_000 + 60_000, side="long") is None
    # a quiet pass after the fire: the status line names the mode and the count
    runner2 = _started([])
    _stub(monkeypatch, tmp_path, [112.30], runner2, held="holds position on xyz:BRENTOIL")
    try:
        W.watch(once=True, cooldown_min=30, clock=lambda: NOW_S + 120)
    finally:
        runner2.stop()
    assert "finlight: ws (0 received" in capsys.readouterr().out


def test_the_watcher_keeps_running_when_finlight_raises_everywhere(monkeypatch, tmp_path, capsys):
    class Broken:
        provider = SimpleNamespace(mode="ws", received=0)

        def drain(self):
            raise RuntimeError("queue gone")

        def wait(self, s):
            raise RuntimeError("event gone")

        def status_line(self):
            raise RuntimeError("status gone")
    HW.save_state({"seen": [], "last_news_s": NOW_S, "last_posts_s": NOW_S, "odds": {}},
                  str(tmp_path / "headline_state.json"))
    _stub(monkeypatch, tmp_path, [112.30, 112.20, 112.04], Broken())
    sleeps = []
    assert W.watch(once=False, cooldown_min=30, sleep=sleeps.append, clock=lambda: NOW_S) == 0
    out = json.loads(capsys.readouterr().out.strip())
    assert out["fire"] == "oil" and out["mid"] == 112.04 and sleeps == [60, 60]   # the plain sleep took over
    assert "finlight: status unavailable" in (tmp_path / "watch.log").read_text()


def test_the_watcher_without_a_runner_is_exactly_the_old_watcher(monkeypatch, tmp_path, capsys):
    _stub(monkeypatch, tmp_path, [112.30, 112.04], None)
    sleeps = []
    assert W.watch(once=False, cooldown_min=30, sleep=sleeps.append, clock=lambda: NOW_S) == 0
    assert json.loads(capsys.readouterr().out.strip())["mid"] == 112.04 and sleeps == [60]
    assert "finlight" not in (tmp_path / "watch.log").read_text()


def test_wait_uses_the_runner_and_falls_back_to_sleep():
    sleeps = []
    W._wait(None, sleeps.append, 7)
    assert sleeps == [7]
    r = FW.FinlightRunner(FakeProvider([]), clock=lambda: NOW_S)
    r._wake.set()
    t0 = time.perf_counter()
    W._wait(r, sleeps.append, 5)
    assert time.perf_counter() - t0 < 1.0 and sleeps == [7]


def test_the_key_never_reaches_a_log_line_from_the_runner(monkeypatch, caplog):
    monkeypatch.setenv(FL.KEY_ENV, KEY)

    async def connect(key, query, on_article, stop, probe_s, on_admit):
        raise RuntimeError(f"handshake failed, header x-api-key: {key}")
    prov = FL.FinlightProvider(ws_connect=connect, rest_fetch=None)
    with caplog.at_level(logging.DEBUG):
        r = FW.FinlightRunner(prov, clock=lambda: NOW_S).start()
        time.sleep(0.3)
        r.stop()
    assert "handshake failed" in caplog.text and KEY not in caplog.text and KEY[:8] not in caplog.text
    assert KEY not in json.dumps(r.status()) and KEY not in r.status_line()
    monkeypatch.delenv(FL.KEY_ENV, raising=False)


def test_asyncio_loop_in_the_runner_thread_does_not_leak_into_the_caller():
    r = _started([])
    r.stop()
    with pytest.raises(RuntimeError):
        asyncio.get_running_loop()
