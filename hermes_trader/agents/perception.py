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

from hermes_trader.agents.config import get_config
from hermes_trader.client.hl_client import fetch_all_mids, fetch_hl_candles
from hermes_trader.client.universe import get_universe
from hermes_trader.indicators import triggers as trigger_mod
from hermes_trader.models.types import Candle

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

    # Asset-class toggles read fresh per scan so operator flips take effect
    # without restart. `enable_hip3` adds per-dex POSTs (cost) so it's opt-in.
    try:
        from hermes_trader.agents.config_store import read_agent_config
        _cfg = read_agent_config()
        include_crypto = bool(_cfg.get("enable_crypto", True))
        include_hip3 = bool(_cfg.get("enable_hip3", False))
    except Exception:
        include_crypto = True
        include_hip3 = False

    if not include_crypto and not include_hip3:
        logger.warning("[scan] both enable_crypto and enable_hip3 are False — nothing to scan")
        return []

    # ── Step 1: Fetch mids (HTTP POST, ~150ms; +~8 per-dex POSTs if HIP-3 on) ─
    raw_mids = fetch_all_mids(include_hip3=include_hip3)
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
        universe = get_universe(include_hip3=include_hip3)

    # Filter: must have valid mid, exclude spot (@ or type=spot), then apply
    # asset-class gates + budget split.
    eligible = [m for m in universe
                if mids.get(m["coin"], 0) > 0
                and not m["coin"].startswith("@")
                and m.get("type") != "spot"]
    if not include_crypto:
        eligible = [m for m in eligible if m.get("dex")]
    if not include_hip3:
        eligible = [m for m in eligible if not m.get("dex")]
    # Bucketed budget so HIP-3 markets and low-volume big-movers each get
    # candle fetches instead of being crowded out by crypto majors. Crypto
    # gets `max_markets - max_markets_hip3` slots, further split between
    # top-by-volume and top-by-|24h%| (movers); HIP-3 gets a flat
    # top-by-volume slice. Single-class runs hand the entire budget to
    # that class. Total candle fetches stay at `max_markets` to keep
    # the scanner inside HL's 1200 weight/minute rate budget.
    max_markets = int(os.environ.get("HERMES_MAX_MARKETS", "60"))
    max_markets_hip3 = int(os.environ.get("HERMES_MAX_MARKETS_HIP3", "25"))
    max_markets_movers = int(os.environ.get("HERMES_MAX_MARKETS_MOVERS", "10"))
    movers_vol_floor = float(os.environ.get("HERMES_MOVERS_VOL_FLOOR_USD", "1000000"))

    def _abs_pct_24h(m):
        prev = float(m.get("prevDayPx") or 0)
        cur = float(m.get("midPx") or m.get("markPx") or 0)
        if prev <= 0 or cur <= 0:
            return 0.0
        return abs((cur - prev) / prev * 100)

    def _pick_with_movers(pool, vol_budget, movers_budget):
        """Top-N by 24h volume + top-M by |24h%|, deduped, in that priority.

        The movers slot guarantees a budget for sub-top-volume big movers
        regardless of their volume rank; the floor filters out pico-cap
        noise where a $200 trade can print a 50% move.
        """
        by_vol = sorted(pool, key=lambda m: m.get("dayNtlVlm", 0), reverse=True)
        vol_pick = by_vol[:vol_budget]
        chosen = {m["coin"] for m in vol_pick}
        candidates = [m for m in pool
                      if m["coin"] not in chosen
                      and m.get("dayNtlVlm", 0) >= movers_vol_floor]
        by_pct = sorted(candidates, key=_abs_pct_24h, reverse=True)
        movers_pick = [m for m in by_pct if _abs_pct_24h(m) >= 1.0][:movers_budget]
        return vol_pick, movers_pick

    if include_crypto and include_hip3:
        crypto_budget = max(0, max_markets - max_markets_hip3)
        crypto_vol_budget = max(0, crypto_budget - max_markets_movers)
        crypto = [m for m in eligible if not m.get("dex")]
        hip3 = [m for m in eligible if m.get("dex")]
        crypto_top, crypto_movers = _pick_with_movers(crypto, crypto_vol_budget, max_markets_movers)
        hip3_top = sorted(hip3, key=lambda m: m.get("dayNtlVlm", 0), reverse=True)[:max_markets_hip3]
        markets = crypto_top + crypto_movers + hip3_top
        logger.info(
            f"[scan] budget split: {len(crypto_top)} crypto-vol + {len(crypto_movers)} crypto-movers "
            f"+ {len(hip3_top)} HIP-3 (of {len(crypto)} crypto + {len(hip3)} HIP-3 eligible)"
        )
        if crypto_movers:
            sample = ", ".join(f"{m['coin']} {_abs_pct_24h(m):+.1f}%" for m in crypto_movers[:5])
            logger.info(f"[scan] movers picked: {sample}")
    else:
        pool = eligible
        vol_budget = max(0, max_markets - max_markets_movers)
        chosen, movers = _pick_with_movers(pool, vol_budget, max_markets_movers)
        markets = chosen + movers
        cls = "crypto-only" if include_crypto else "HIP-3-only"
        logger.info(
            f"[scan] {cls} mode: {len(chosen)} by-volume + {len(movers)} by-momentum "
            f"(of {len(eligible)} eligible)"
        )
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


