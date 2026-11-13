"""The Finlight stream inside scripts/trigger_watch.py: a background thread
that owns the provider, a queue the watcher drains every pass, and the
enrichment of each Stage 1 hit (Stage 2 score, book guard, event log).

    runner = start_runner()                  # None without FINLIGHT_API_KEY
    hits = finlight_pass(runner, targets, now_s, state)   # (desk, alert) pairs
    runner.wait(60)                          # the watcher's sleep, cut short by a hit

Why a thread: the watcher is synchronous and exits on every fire; the
provider is async and must keep its socket open between passes. The thread
is a daemon, so the watcher's exit (one fire line, return 0) ends it; the
next watcher start reconnects. The headline state file carries `seen`, so a
headline pushed before the exit and again after it alerts once.

How a hit reaches the desk: the thread runs Stage 1 on arrival
(headline_watch.match_headline(triggers=True), the <5 ms check) and wakes
the watcher when it matches; the watcher's pass drains the queue into
headline_watch.news_alerts (dedup, age, trusted source, direction), the
SAME function the RSS items go through, so the alert is recorded, wakes the
tick, makes the thesis due and holds the blackout exactly like any other
headline. Every alert from here carries "provider": "finlight" and
"stage2": the event_score JSON; the direction the blackout reads is still
text_direction's, unchanged.

Priming: the first pass after the source is new to the state file learns
the queue (the REST fallback's first page is the last 45 minutes, already
old news) and alerts on nothing, like the posts and official feeds.

Nothing here raises to the watcher: start_runner returns None on any
failure, finlight_pass returns [] on any failure, and the thread's own
errors stay in runner.status().
"""
from __future__ import annotations

import asyncio
import logging
import queue
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from services.intel import book_guard, event_log, event_score
from services.intel import headline_watch as HW
from services.intel.sources.finlight import FinlightProvider, api_key, redact
from services.intel.sources.providers import NewsItem

logger = logging.getLogger(__name__)

SOURCE_NAME = "finlight"
GROUP = "oil"
MAX_QUEUE = 2000


class FinlightRunner:
    """Owns the provider's thread and loop. `provider` is injectable (the
    tests pass a fake that pushes fixture items)."""

    def __init__(self, provider: Optional[FinlightProvider] = None, *, wake_on: Callable[[str], bool] = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.provider = provider or FinlightProvider()
        self._q: "queue.Queue[NewsItem]" = queue.Queue(maxsize=MAX_QUEUE)
        self._wake = threading.Event()
        self._stop: Optional[asyncio.Event] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._wake_on = wake_on or (lambda title: bool(HW.match_headline(GROUP, title, triggers=True)))
        self._clock = clock
        self.started_at: Optional[float] = None
        self.last_item_at: Optional[float] = None
        self.newest_published_s: Optional[float] = None    # the plan's data delay, visible in the status line
        self.dropped = 0

    # ── lifecycle ───────────────────────────────────────────────────────

    def start(self) -> "FinlightRunner":
        if self._thread is not None:
            return self
        self.started_at = self._clock()
        self._thread = threading.Thread(target=self._thread_main, name="finlight-provider", daemon=True)
        self._thread.start()
        return self

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        self._stop = asyncio.Event()
        try:
            loop.run_until_complete(self.provider.run(self.on_item, self._stop))
        except Exception as exc:  # noqa: BLE001 - the thread never takes the watcher down
            self.provider.last_error = redact(f"{type(exc).__name__}: {exc}")[:200]
            self.provider.mode = "down"
            logger.warning("[finlight] provider thread ended: %s", self.provider.last_error)
        finally:
            try:
                loop.close()
            except Exception:  # noqa: BLE001
                pass

    def stop(self, timeout_s: float = 5.0) -> None:
        if self._loop and self._stop and not self._stop.is_set():
            try:
                self._loop.call_soon_threadsafe(self._stop.set)
            except RuntimeError:
                pass
        if self._thread:
            self._thread.join(timeout_s)

    # ── the queue ───────────────────────────────────────────────────────

    def on_item(self, item: NewsItem) -> None:
        """The provider's callback (its thread). Queue the item; wake the
        watcher when Stage 1 matches the title."""
        self.last_item_at = self._clock()
        pub = item.get("published_at")
        if pub is not None:
            try:
                self.newest_published_s = max(self.newest_published_s or 0.0, pub.timestamp())
            except (AttributeError, TypeError, ValueError):
                pass
        try:
            self._q.put_nowait(item)
        except queue.Full:
            self.dropped += 1
            return
        try:
            if self._wake_on(item.get("title") or ""):
                self._wake.set()
        except Exception:  # noqa: BLE001 - a matcher bug must not kill the stream
            self._wake.set()

    def drain(self) -> List[NewsItem]:
        out: List[NewsItem] = []
        while True:
            try:
                out.append(self._q.get_nowait())
            except queue.Empty:
                return out

    def wait(self, timeout_s: float) -> bool:
        """Block up to `timeout_s`; True when a Stage 1 hit cut it short."""
        hit = self._wake.wait(timeout_s)
        self._wake.clear()
        return hit

    def status(self) -> Dict[str, Any]:
        s = dict(self.provider.status())
        s.update({"queued": self._q.qsize(), "dropped": self.dropped, "last_item_at": self.last_item_at,
                  "newest_published_s": self.newest_published_s,
                  "alive": bool(self._thread and self._thread.is_alive())})
        return s

    def status_line(self) -> str:
        s = self.status()
        age = f", last item {self._clock() - s['last_item_at']:.0f}s ago" if s.get("last_item_at") else ""
        if s.get("newest_published_s"):
            lag_min = (self._clock() - s["newest_published_s"]) / 60
            age += f", newest published {lag_min:.0f} min old" + (" (DELAYED plan)" if lag_min > 120 else "")
        err = f", err: {s['last_error']}" if s.get("last_error") else ""
        return f"finlight: {s['mode']} ({s['received']} received{age}{err})"


def start_runner(provider: Optional[FinlightProvider] = None) -> Optional[FinlightRunner]:
    """A started runner, or None (no key, or anything failed). Never raises."""
    try:
        if provider is None and not api_key():
            return None
        return FinlightRunner(provider).start()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[finlight] not started: %s", redact(f"{type(exc).__name__}: {exc}")[:160])
        return None


def enrich(alert: Dict[str, Any], item: Optional[NewsItem], *, mode: str,
           score: Optional[Callable[..., Dict[str, Any]]] = None,
           book: Optional[Callable[..., Dict[str, Dict[str, Any]]]] = None,
           log_path: Optional[str] = None, webhook: Optional[Callable[..., bool]] = None,
           printer: Callable[[str], None] = print) -> Dict[str, Any]:
    """Stage 2 + the book guard + the event log for one Stage 1 alert.
    Mutates `alert` (adds "stage2", "event_log"); returns the log row.
    Any failure inside leaves the alert as it was (the tick still wakes).
    The collaborators resolve at call time so a test can patch the modules."""
    score = score or event_score.score_event_sync
    book = book or book_guard.book_snapshot
    webhook = webhook or event_log.post_webhook
    item = item or {}
    try:
        s2 = score(alert["title"], source=alert.get("source"), sentiment=item.get("sentiment"),
                   sentiment_confidence=item.get("sentiment_confidence"), stage1_label=alert.get("label"),
                   entities=item.get("entities"))
    except Exception as exc:  # noqa: BLE001
        s2 = event_score.rule_score(alert["title"], source=alert.get("source"))
        s2["rationale"] = f"scorer failed ({type(exc).__name__}); " + s2["rationale"]
    scored_ms = event_log.now_ms()
    snap = None
    if float(s2.get("confidence_score") or 0) >= book_guard.GUARD_CONFIDENCE:
        try:
            snap = book(book_guard.GUARD_COINS)
        except Exception as exc:  # noqa: BLE001
            snap = {c: {"coin": c, "error": f"book failed ({type(exc).__name__})"} for c in book_guard.GUARD_COINS}
    row = event_log.build_event(alert, mode=mode, stage2=s2, scored_ms=scored_ms, book=snap,
                                entities=item.get("entities"))
    alert["stage2"] = {k: s2.get(k) for k in ("event_type", "expected_oil_direction", "confidence_score",
                                              "is_confirmed", "scorer")}
    try:
        alert["event_log"] = event_log.append_event(row, log_path)
    except OSError as exc:
        logger.warning("[finlight] event log write failed: %s", exc)
    try:
        printer(event_log.console_line(row))
    except Exception:  # noqa: BLE001
        pass
    try:
        webhook(row)
    except Exception:  # noqa: BLE001
        pass
    return row


def finlight_pass(runner: Optional[FinlightRunner], targets: Sequence[Tuple[str, str, str]], now_s: float,
                  state: Dict[str, Any], *, prime: bool = False, **enrich_kw: Any) -> List[Tuple[str, Dict[str, Any]]]:
    """Drain the runner into news_alerts for the oil group's desks; enrich
    each hit. Mutates `state` (seen, primed_sources). Returns (desk, alert)
    pairs the watcher records and fires on. Never raises."""
    if runner is None:
        return []
    try:
        items = runner.drain()
    except Exception:  # noqa: BLE001
        return []
    if not items:
        return []
    out: List[Tuple[str, Dict[str, Any]]] = []
    try:
        primed = set(state.get("primed_sources") or [])
        learn_only = prime or SOURCE_NAME not in primed
        seen = set(state.get("seen") or [])
        by_title = {(i.get("title") or "").strip()[:200]: i for i in items}
        mode = runner.provider.mode
        for desk, _coin, group in targets:
            if group != GROUP:
                continue
            hits = HW.news_alerts(group, items, seen, int(now_s * 1000), triggers=True)
            if learn_only:
                continue
            for a in hits:
                enrich(a, by_title.get(a["title"]), mode=mode, **enrich_kw)
                out.append((desk, a))
        primed.add(SOURCE_NAME)
        state["primed_sources"] = sorted(primed)
        state["seen"] = list(seen)
        state["finlight"] = {"mode": mode, "received": runner.provider.received, "last_drain_s": now_s,
                             "drained": len(items)}
    except Exception as exc:  # noqa: BLE001 - the watcher keeps running on RSS
        logger.warning("[finlight] pass failed: %s", redact(f"{type(exc).__name__}: {exc}")[:160])
        return out
    return out


__all__ = ["FinlightRunner", "GROUP", "SOURCE_NAME", "enrich", "finlight_pass", "start_runner"]
