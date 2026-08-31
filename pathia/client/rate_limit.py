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

import os
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
    "userNonFundingLedgerUpdates": 20,
    "perpDexs": 20,
    "portfolio": 20,
    "userFills": 20,
    "orderStatus": 2,
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
HL_LIMITER = TokenBucket(
    capacity=int(os.environ.get("PATHIA_HL_RATE_CAPACITY", "300")),
    refill_per_sec=float(os.environ.get("PATHIA_HL_RATE_REFILL_PER_SEC", "15")),
)
