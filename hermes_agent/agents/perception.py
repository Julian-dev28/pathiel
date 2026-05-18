"""Perception scan engine — sweeps Hyperliquid markets for trigger signals.

Fetches candles, runs trigger detection, and returns candidates that meet the
composite-score threshold. Scans fan out across threads; volume pre-filtering
limits the sweep to the top-N markets by 24h volume to stay within HL's
1200 weight/minute rate limit (candle fetch = 20 weight each).
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

from hermes_agent.agents.config import get_config
from hermes_agent.client.daemon import check_daemon_state, producer_daemon
from hermes_agent.client.hl_client import fetch_all_mids, fetch_hl_candles, start_ws_mids
from hermes_agent.client.universe import get_universe
from hermes_agent.indicators import triggers as trigger_mod
from hermes_agent.models.types import Candle

logger = logging.getLogger(__name__)

# ── Candle cache (module-level, shared across ticks) ──────────────────────────

_candle_cache: Dict[str, Dict[str, Any]] = {}


def _make_cache_key(coin: str, interval: str, count: int) -> str:
    return f"{coin}:{interval}:{count}"


def _fetch_candles_sync(
    coin: str,
    interval: str,
    count: int,
    cache_ttl_ms: int,
    max_retries: int = 3,
    backoff_base: float = 2.0,
) -> Optional[List[Candle]]:
    """Fetch candles from the SDK with in-memory caching and retry on 429."""
    key = _make_cache_key(coin, interval, count)
    cached = _candle_cache.get(key)
    if cached and (time.time() * 1000 - cached["cached_at"]) < cache_ttl_ms:
        return cached["candles"]

    for attempt in range(max_retries):
        try:
            candles = fetch_hl_candles(coin, interval, count)
            if not candles:
                return None
            _candle_cache[key] = {"candles": candles, "cached_at": time.time() * 1000}
            return candles
        except Exception as e:
            err_str = str(e).lower()
            if attempt < max_retries - 1 and ("429" in err_str or "rate" in err_str or "connection" in err_str or "timeout" in err_str):
                wait = backoff_base ** attempt
                logger.warning(f"[candles] rate-limited/connection error for {coin} {interval}, retry {attempt+1}/{max_retries} in {wait:.1f}s")
                time.sleep(wait)
            else:
                logger.error(f"[candles] failed for {coin} {interval}: {e}")
                return None

    return None


# ── Scan single market (returns result or (False, error)) ────────────────────

def _scan_single_market(
    market: Dict[str, Any],
    mid: float,
    config: Dict[str, Any],
    min_score: float,
) -> Tuple[bool, Dict[str, Any] | str | None]:
    """Run all triggers on a single market's candles.

    Returns (success, perception_dict | None) on success, or (False, error_string).
    Designed to run inside a ThreadPoolExecutor worker.
    """
    try:
        candles = _fetch_candles_sync(
            market["coin"],
            config["scan"]["candleInterval"],
            config["scan"]["candleCount"],
            config["scan"]["cacheTtlMs"],
        )

        if not candles or len(candles) < 50:
            return (True, None)  # Not an error, just no triggers

        thresholds = config["thresholds"]
        hits = [
            trigger_mod.pct_move_spike(candles, thresholds["sigmaThreshold"]),
            trigger_mod.volume_spike(candles, thresholds["sigmaThreshold"]),
            trigger_mod.breakout(candles, thresholds["breakoutLookback"]),
            trigger_mod.range_compression(candles, thresholds["bbLength"], thresholds["bbStdDev"]),
            trigger_mod.trend_strength(candles, thresholds["adxPeriod"]),
            trigger_mod.momentum_burst(candles, thresholds["momentumLookback"], thresholds["momentumPct"]),
        ]

        # At least one trigger must fire.
        fired_count = sum(1 for h in hits if h.get("fired"))
        if fired_count < 1:
            return (True, None)

        score = trigger_mod.composite_score(hits, config["weights"])
        # A confirmed momentum burst is always surfaced — a large, fast move is
        # exactly the signal the composite gate must never filter out.
        burst_fired = any(h["name"] == "momentumBurst" and h["fired"] for h in hits)
        if score < min_score and not burst_fired:
            return (True, None)

        return (True, {
            "id": f"{market['coin']}-{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}",
            "coin": market["coin"],
            "type": market["type"],
            "fired_at": int(time.time() * 1000),
            "mid": mid,
            "triggers": hits,
            "composite_score": score,
        })
    except Exception as e:
        return (False, str(e))


# ── Main scan entry point ───────────────────────────────────────────────────

def scan_once(
    universe: Optional[List[Dict[str, Any]]] = None,
    min_score: float = 20,
    config: Optional[Dict[str, Any]] = None,
    parallel_workers: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Scan Hyperliquid markets for trigger signals.

    Returns perception dicts sorted by composite score descending. Markets are
    scanned in parallel; the only shared state is the candle cache.

    Args:
        universe: pre-fetched market list. Defaults to get_universe().
        min_score: minimum composite score to include a result.
        config: config dict. Defaults to get_config().
        parallel_workers: max concurrent market scans. Defaults to 32.
    """
    started = time.time()
    cfg = config or get_config()
    min_score = cfg["scan"]["minCompositeScore"] if min_score == 20 else min_score
    workers = parallel_workers or cfg["scan"].get("parallelWorkers", 32)

    # ── Step 1: Fetch mids (HTTP POST, ~150ms) ──────────────────────────
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

    # ── Step 2: Get universe & pre-filter by volume ─────────────────────
    # HL rate limit: 1200 weight/minute. Candle fetch = 20 weight each.
    # Fetching all 500+ markets would need 10,000+ weight → instant 429.
    # Pre-filter to top-N markets by 24h notional volume to stay under limit.
    if universe is None:
        universe = get_universe()

    # Filter: must have valid mid, exclude spot (@ or type=spot), then top-N by volume
    eligible = [m for m in universe 
                if mids.get(m["coin"], 0) > 0 
                and not m["coin"].startswith("@")
                and m.get("type") != "spot"]
    max_markets = int(os.environ.get("HERMES_MAX_MARKETS", "60"))
    markets = sorted(eligible, key=lambda m: m.get("dayNtlVlm", 0), reverse=True)[:max_markets]
    if not markets:
        return []

    # ── Step 3: Parallel scan with rate-limiting ───────────────────────
    # Batch markets into groups of `batch_size` and sleep between batches
    # to stay under the HL rate limit. Within each batch, fan out with
    # `workers` threads.
    batch_size = int(os.environ.get("HERMES_BATCH_SIZE", "20"))
    batch_sleep = float(os.environ.get("HERMES_BATCH_SLEEP", "0.3"))

    # Build per-market scan callables
    callables = []
    for m in markets:
        mid = mids.get(m["coin"], 0)
        if mid <= 0:
            continue
        callables.append((m, mid))

    total = len(callables)
    logger.info(f"[scan] scanning {total} markets in batches of {batch_size} ({workers} workers/batch)...")

    results: List[Dict[str, Any]] = []
    errors = 0
    completed = 0

    for batch_start in range(0, total, batch_size):
        batch_end = min(batch_start + batch_size, total)
        batch = callables[batch_start:batch_end]

        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="hermes-scan") as pool:
            futures = [pool.submit(_scan_single_market, m, md, cfg, min_score) for m, md in batch]
            for i, future in enumerate(futures):
                idx = batch_start + i
                try:
                    success, result = future.result(timeout=60)
                    if success and isinstance(result, dict):
                        results.append(result)
                    elif not success:
                        errors += 1
                        if errors <= 5:
                            logger.warning(f"[scan] market scan #{idx} failed: {result}")
                except Exception as e:
                    errors += 1
                    if errors <= 5:
                        logger.error(f"[scan] market scan #{idx} exception: {e}")

        completed += len(batch)
        if completed % 100 == 0 or completed == total:
            logger.info(f"[scan] progress: {completed}/{total} ({completed/total*100:.0f}%), {len(results)} triggers so far")

        if batch_end < total:
            time.sleep(batch_sleep)

    # ── Step 4: Sort by composite score descending ──────────────────────
    elapsed = (time.time() - started) * 1000
    logger.info(f"[scan] scanned {len(markets)} markets, {len(results)} triggers in {elapsed:.0f}ms ({errors} errors)")
    return sorted(results, key=lambda r: r["composite_score"], reverse=True)


def clear_candle_cache() -> None:
    """Clear the in-memory candle cache."""
    _candle_cache.clear()


def get_candle_cache_stats() -> Dict[str, int]:
    """Return cache size."""
    return {"size": len(_candle_cache)}


def start_scan_daemon(interval_seconds: int = 180, name: str = "hermes-scanner") -> None:
    """Start the autonomous scanning daemon loop.
    
    This wraps scan_once in a producer_daemon that:
    - Runs every `interval_seconds` seconds
    - Uses scanner_lock to prevent overlapping scans
    - Enforces per-tick timeout (3 min default)
    - Writes PID/heartbeat state files for monitoring
    - Handles SIGTERM gracefully
    """
    # Start WebSocket for real-time mids (one persistent connection)
    ws = start_ws_mids()
    if ws:
        logger.info("[daemon] WebSocket started for real-time mids")

    def scan_fn() -> List[Dict[str, Any]]:
        return scan_once(
            config=None,
            min_score=75,
            parallel_workers=32,
        )

    producer_daemon(
        scan_fn=scan_fn,
        interval_seconds=interval_seconds,
        name=name,
        tick_timeout=180.0,
    )


def check_scan_daemon_state(name: str = "hermes-scanner") -> Dict[str, Any]:
    """Check the state of the scanning daemon."""
    return check_daemon_state(name)
