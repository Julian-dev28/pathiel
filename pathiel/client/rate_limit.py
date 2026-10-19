"""Token-bucket rate limiter for the Hyperliquid /info + /exchange endpoints.

HL allows ~1200 request-weight per minute per IP. Different endpoints cost
different weight (candleSnapshot=20, allMids=2, etc.). Previously the scan
loop paced itself with a crude fixed `time.sleep(0.3)` between batches, which
either left throughput on the table or — during backtests / dense scans —
fired straight into 429s (200+ "no candles" skips observed).

A single shared bucket meters every outbound request by its weight, so bursts
are smoothed against the real per-minute budget regardless of which code path
(live scan, dashboard, backtest, treasury) is making the call.
"""
from __future__ import annotations

import contextlib
import functools
import os
import threading
import time
from collections import Counter
from typing import Any, Callable, Dict, Iterator, TypeVar

# Per-endpoint weights from HL docs. Default 20 (the expensive bucket) for
# anything unknown so we never under-count and trip a 429.
_ENDPOINT_WEIGHT = {
    "candleSnapshot": 20,
    "metaAndAssetCtxs": 20,
    "spotMetaAndAssetCtxs": 20,
    "meta": 20,
    "spotMeta": 20,
    "allMids": 2,
    "clearinghouseState": 2,
    "spotClearinghouseState": 2,
    "l2Book": 2,
    "userNonFundingLedgerUpdates": 20,
    "perpDexs": 20,
    "portfolio": 20,
    "userFills": 20,
    "orderStatus": 2,
}


def endpoint_weight(req_type: str | None) -> int:
    return _ENDPOINT_WEIGHT.get(req_type or "", 20)


_F = TypeVar("_F", bound=Callable[..., Any])

# Background work (desk rebuilds, account re-reads nobody is waiting on) runs
# inside background_priority(). Thread-local, so a thread pool does not inherit
# it: wrap the callable with carry_priority() before handing it to one.
_PRIORITY = threading.local()


def in_background() -> bool:
    return bool(getattr(_PRIORITY, "background", False))


@contextlib.contextmanager
def background_priority() -> Iterator[None]:
    """Requests made in this block yield to everything else: they may only
    spend tokens above the bucket's background reserve, so a page someone is
    looking at, or a refresh someone clicked, never queues behind a
    background rebuild (2026-10-19: the BTC desk's background rebuild took
    213 s and a clicked refresh sat behind it)."""
    prev = in_background()
    _PRIORITY.background = True
    try:
        yield
    finally:
        _PRIORITY.background = prev


def carry_priority(fn: _F) -> _F:
    """fn, set to run at the CALLER's priority on whatever thread runs it."""
    background = in_background()

    @functools.wraps(fn)
    def run(*args: Any, **kwargs: Any) -> Any:
        prev = in_background()
        _PRIORITY.background = background
        try:
            return fn(*args, **kwargs)
        finally:
            _PRIORITY.background = prev
    return run  # type: ignore[return-value]


class TokenBucket:
    """Thread-safe token bucket. `acquire(weight)` blocks (up to max_wait)
    until enough tokens have refilled, then deducts them.

    Two priorities. Foreground (the default) may spend the whole bucket.
    Background (see background_priority) must leave `background_reserve`
    tokens in it, so foreground callers always find headroom; once nothing
    is in the foreground the reserve simply stays full and background runs at
    the full refill rate."""

    def __init__(self, capacity: int, refill_per_sec: float,
                 background_reserve: float | None = None):
        self._capacity = float(capacity)
        self._tokens = float(capacity)
        self._refill = float(refill_per_sec)
        self._reserve = (float(capacity) * 0.5 if background_reserve is None
                         else max(0.0, float(background_reserve)))
        self._last = time.monotonic()
        self._lock = threading.Lock()
        self._granted: Counter = Counter()   # (label, priority) -> weight granted
        self._denied: Counter = Counter()    # (label, priority) -> requests refused

    def acquire(self, weight: int = 20, max_wait: float = 10.0, *,
                background: bool = False, label: str = "") -> bool:
        """Block until `weight` tokens are available. Returns False if the
        wait would exceed `max_wait` (caller should back off / skip)."""
        # A reserve so big that weight + reserve exceeds the bucket would lock
        # background out for good; it may always use what the bucket can hold.
        floor = min(self._reserve, max(0.0, self._capacity - weight)) if background else 0.0
        key = (label or "unknown", "background" if background else "foreground")
        deadline = time.monotonic() + max_wait
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(
                    self._capacity,
                    self._tokens + (now - self._last) * self._refill,
                )
                self._last = now
                if self._tokens - weight >= floor:
                    self._tokens -= weight
                    self._granted[key] += weight
                    return True
                if self._refill <= 0:
                    self._denied[key] += 1
                    return False  # no refill → will never recover
                deficit = weight + floor - self._tokens
                sleep_for = deficit / self._refill
                if time.monotonic() + sleep_for > deadline:
                    self._denied[key] += 1
                    return False
            time.sleep(min(sleep_for, 0.5))

    def stats(self) -> Dict[str, Any]:
        """Budget state plus weight granted / requests refused per request
        type and priority since the process started (GET /api/health/system)."""
        with self._lock:
            now = time.monotonic()
            tokens = min(self._capacity, self._tokens + (now - self._last) * self._refill)

            def table(c: Counter) -> Dict[str, Dict[str, int]]:
                out: Dict[str, Dict[str, int]] = {}
                for (label, prio), n in sorted(c.items()):
                    out.setdefault(prio, {})[label] = int(n)
                return out
            return {
                "capacity": self._capacity, "refill_per_sec": self._refill,
                "background_reserve": self._reserve, "available_weight": round(tokens, 1),
                "granted_weight": table(self._granted), "refused_requests": table(self._denied),
            }


# NOTE: this bucket is PER-PROCESS, but HL limits per-IP. The live loop and
# dashboard server are separate processes sharing one IP, so neither sees the
# other's usage. Still, each process must respect the documented budget. A live
# scan legitimately bursts ~50 candleSnapshot calls (weight ~= 1000) then sleeps;
# the refill stays at 1200 weight/min, while the burst cap is large enough that
# those workers queue instead of timing out and producing fake candle gaps.
# Sized so loop + server FIT the per-IP budget (audit 2026-07-10: the old
# 600cap+20/s loop plus 200cap+5/s server allowed 2,300 weight/min against
# HL's 1,200 — the limiter never braked while the exchange returned 522 real
# 429s/14d). Loop: 300 burst + 15/s = 900/min sustained; server (restart.sh
# env) gets 60 burst + 2/s = 120/min. Combined ~1,020/min, margin for the SDK
# calls that bypass this bucket.
#
# Background reserve: half the bucket by default (server: 30 of 60), so a
# clicked refresh or a cold page read can always take ~30 weight at once.
# PATHIEL_HL_BACKGROUND_RESERVE overrides it in weight.
_reserve_env = os.environ.get("PATHIEL_HL_BACKGROUND_RESERVE", "").strip()
HL_LIMITER = TokenBucket(
    capacity=int(os.environ.get("PATHIEL_HL_RATE_CAPACITY", "300")),
    refill_per_sec=float(os.environ.get("PATHIEL_HL_RATE_REFILL_PER_SEC", "15")),
    background_reserve=float(_reserve_env) if _reserve_env else None,
)
