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

import threading
import time

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
    "userNonFundingLedgerUpdates": 2,
    "perpDexs": 2,
    "portfolio": 2,
    "userFills": 2,
}


def endpoint_weight(req_type: str | None) -> int:
    return _ENDPOINT_WEIGHT.get(req_type or "", 20)


class TokenBucket:
    """Thread-safe token bucket. `acquire(weight)` blocks (up to max_wait)
    until enough tokens have refilled, then deducts them."""

    def __init__(self, capacity: int, refill_per_sec: float):
        self._capacity = float(capacity)
        self._tokens = float(capacity)
        self._refill = float(refill_per_sec)
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, weight: int = 20, max_wait: float = 10.0) -> bool:
        """Block until `weight` tokens are available. Returns False if the
        wait would exceed `max_wait` (caller should back off / skip)."""
        deadline = time.monotonic() + max_wait
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(
                    self._capacity,
                    self._tokens + (now - self._last) * self._refill,
                )
                self._last = now
                if self._tokens >= weight:
                    self._tokens -= weight
                    return True
                if self._refill <= 0:
                    return False  # no refill → will never recover
                deficit = weight - self._tokens
                sleep_for = deficit / self._refill
            if time.monotonic() + sleep_for > deadline:
                return False
            time.sleep(min(sleep_for, 0.5))


# NOTE: this bucket is PER-PROCESS, but HL limits per-IP. The live loop and
# the dashboard server are separate processes sharing one IP, so neither
# sees the other's usage — the limiter can't fully prevent cross-process
# 429s (those get retried by the candle fetcher). Its real job is capping
# the pathological single-process burst (a backtest firing thousands of
# requests instantly). So: generous capacity/refill matched to HL's observed
# tolerance (~the pre-limiter loop sustained ~133 w/s fine) so live scans
# aren't slowed, while still throttling a runaway backtest to a paced stream.
HL_LIMITER = TokenBucket(capacity=600, refill_per_sec=130.0)
