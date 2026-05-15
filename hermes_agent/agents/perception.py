"""Perception scan engine — sweeps all Hyperliquid markets for trigger signals.

Translation of lib/agent/perception.ts.
Sweeps every market, fetches candles, runs trigger detection, returns
Perception objects for candidates that meet the composite score threshold.

All functions are SYNC — no async/await needed.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional

from hermes_agent.agents.config import get_config
from hermes_agent.client.hl_client import fetch_hl_candles, fetch_all_mids
from hermes_agent.client.universe import get_universe
from hermes_agent.indicators import triggers as trigger_mod

logger = logging.getLogger(__name__)

# ── Candle cache ───────────────────────────────────────────────────────────────

_candle_cache: Dict[str, Dict[str, Any]] = {}


def _make_cache_key(coin: str, interval: str, count: int) -> str:
    return f"{coin}:{interval}:{count}"


def _fetch_candles_sync(
    coin: str,
    interval: str,
    count: int,
    cache_ttl_ms: int,
) -> Optional[List[Dict[str, Any]]]:
    """Fetch candles from the SDK with in-memory caching."""
    key = _make_cache_key(coin, interval, count)
    cached = _candle_cache.get(key)
    if cached and (time.time() * 1000 - cached["cached_at"]) < cache_ttl_ms:
        return cached["candles"]

    candles = fetch_hl_candles(coin, interval, count)
    if not candles or len(candles) == 0:
        return None

    # Convert Candle objects to dicts
    candle_dicts = [
        {"t": c.t, "o": c.o, "h": c.h,
         "l": c.l, "c": c.c, "v": c.v}
        for c in candles
    ]

    _candle_cache[key] = {"candles": candle_dicts, "cached_at": time.time() * 1000}
    return candle_dicts


# ── Scan single market ─────────────────────────────────────────────────────────

def _scan_market(
    market: Dict[str, Any],
    mid: float,
    config: Dict[str, Any],
    min_score: float,
) -> Optional[Dict[str, Any]]:
    """Run all triggers on a single market's candles."""
    candles = _fetch_candles_sync(
        market["coin"],
        config["scan"]["candleInterval"],
        config["scan"]["candleCount"],
        config["scan"]["cacheTtlMs"],
    )

    if not candles or len(candles) < 50:
        return None

    thresholds = config["thresholds"]
    hits = [
        trigger_mod.pct_move_spike(candles, thresholds["sigmaThreshold"]),
        trigger_mod.volume_spike(candles, thresholds["sigmaThreshold"]),
        trigger_mod.breakout(candles, thresholds["breakoutLookback"]),
        trigger_mod.range_compression(candles, thresholds["bbLength"], thresholds["bbStdDev"]),
        trigger_mod.trend_strength(candles, thresholds["adxPeriod"]),
    ]

    # Require at least 2 triggers to co-fire
    fired_count = sum(1 for h in hits if h.get("fired"))
    if fired_count < 2:
        return None

    score = trigger_mod.composite_score(hits, config["weights"])
    if score < min_score:
        return None

    return {
        "id": f"{market['coin']}-{int(time.time() * 1000)}-{__import__('uuid').uuid4().hex[:6]}",
        "coin": market["coin"],
        "type": market["type"],
        "fired_at": int(time.time() * 1000),
        "mid": mid,
        "triggers": hits,
        "composite_score": score,
    }


# ── Main scan entry point ─────────────────────────────────────────────────────

def scan_once(
    universe: Optional[List[Dict[str, Any]]] = None,
    min_score: float = 20,
    config: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Scan all Hyperliquid markets for trigger signals.

    Translation of scanOnce() from lib/agent/perception.ts.
    Returns list of Perception dicts sorted by composite score descending.

    NOTE: This is SYNC. No async needed — all fetches are synchronous.
    """
    started = time.time()
    cfg = config or get_config()
    min_score = cfg["scan"]["minCompositeScore"] if min_score == 20 else min_score

    # Step 1: Fetch all mids directly from SDK
    raw_mids = fetch_all_mids()
    mids: Dict[str, float] = {}
    for coin, val in raw_mids.items():
        if isinstance(val, str):
            try:
                mids[coin] = float(val)
            except ValueError:
                pass
        elif isinstance(val, (int, float)):
            mids[coin] = val

    # Step 2: Get universe
    if universe is None:
        universe = get_universe()

    # Filter to markets with valid mid prices, exclude spot (@)
    markets = [m for m in universe if mids.get(m["coin"], 0) > 0 and not m["coin"].startswith("@")]
    if not markets:
        return []

    # Step 3: Sequential scan (no async needed, fast enough)
    results = []
    for m in markets:
        mid = mids.get(m["coin"], 0)
        if mid <= 0:
            continue
        perception = _scan_market(m, mid, cfg, min_score)
        if perception:
            results.append(perception)

    # Step 4: Sort by composite score descending
    elapsed = (time.time() - started) * 1000
    logger.info(f"[scan] scanned {len(markets)} markets, {len(results)} triggers fired in {elapsed:.0f}ms")
    return sorted(results, key=lambda r: r["composite_score"], reverse=True)


def clear_candle_cache() -> None:
    """Clear the in-memory candle cache."""
    _candle_cache.clear()


def get_candle_cache_stats() -> Dict[str, int]:
    """Return cache size."""
    return {"size": len(_candle_cache)}
