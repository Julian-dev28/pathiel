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
    whale_signals: Optional[Dict[str, Dict[str, Any]]] = None,
    whale_scan_bypass: bool = False,
    trend_surface_enabled: bool = True,
) -> Tuple[bool, Dict[str, Any] | str | None]:
    """Run all triggers on a single market's candles.

    `whale_signals` is the per-scan whale_accumulation_map() result, keyed by
    coin. When present and the coin matches, the perception's `whale_signal`
    field carries the signal dict for downstream gating.

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

        # 1h candles for slow-burn / accumulation triggers. Cached far longer
        # than 5m (1h bars don't change intra-hour). Failure here doesn't
        # block the scan — slow-burn triggers just won't fire.
        candles_1h = _fetch_candles_sync(
            market["coin"], "1h", 48,
            config["scan"].get("cacheTtlMs1h", 600_000),
        ) or []

        thresholds = config["thresholds"]
        hits = [
            trigger_mod.pct_move_spike(candles, thresholds["sigmaThreshold"]),
            trigger_mod.volume_spike(candles, thresholds["sigmaThreshold"]),
            trigger_mod.breakout(candles, thresholds["breakoutLookback"]),
            trigger_mod.range_compression(candles, thresholds["bbLength"], thresholds["bbStdDev"]),
            trigger_mod.trend_strength(candles, thresholds["adxPeriod"]),
            trigger_mod.momentum_burst(candles, thresholds["momentumLookback"], thresholds["momentumPct"]),
            trigger_mod.volume_buildup_1h(candles_1h, thresholds.get("volBuildupRatio", 2.5)),
            trigger_mod.trend_flip_1h(candles_1h, thresholds.get("trendFlipBars", 3)),
            trigger_mod.higher_lows_1h(candles_1h, thresholds.get("higherLowsRequired", 4)),
            # Symmetric directional surfacing (weight 0 → no composite-denominator
            # impact). uptrend/downtrend momentum surface a coin in a sustained
            # intraday trend for research REGARDLESS of the bullish-biased composite
            # gate — the down side is what lets us short selloffs (the weighted
            # triggers are all long-structured, so down-movers scored ~0 and never
            # reached the AI). Acts as a bypass below; the AI + aligned-conf bar +
            # short floor + counter-regime gate adjudicate direction/execution.
            trigger_mod.uptrend_momentum(candles, thresholds.get("trendMomentumLookback", 72),
                                         thresholds.get("trendMomentumPct", 3.0)),
            trigger_mod.downtrend_momentum(candles, thresholds.get("trendMomentumLookback", 72),
                                           thresholds.get("trendMomentumPct", 3.0)),
        ]

        # Momentum-continuation trigger (LEAK #2) — OFF by default. Catches a coin
        # in a sustained multi-hour uptrend that is now consolidating (already-
        # extended movers that print no fresh 5m spike, so the other triggers miss
        # them). Gated so it has ZERO scoring effect when off: only when enabled is
        # the hit appended AND its weight added to the denominator. LONG-biased —
        # enable only when the macro regime is up/neutral (counter-trend gate backs it up).
        _mc = config.get("momentum_continuation", {}) or {}
        _score_weights = config["weights"]
        if _mc.get("enabled"):
            hits.append(trigger_mod.momentum_continuation_1h(
                candles_1h,
                _mc.get("min_trend_pct", 8.0),
                _mc.get("max_pullback_pct", 6.0),
            ))
            _score_weights = {**config["weights"],
                              "momentumContinuation1h": _mc.get("weight", 0.4)}

        # Candlestick reversal patterns — OFF by default. Shooting-star / bearish-
        # engulfing (top of an advance → SHORT) and hammer / bullish-engulfing
        # (bottom of a decline → LONG). The momentum/breakout triggers are weak at
        # calling tops & bottoms; these catch exhaustion/reversal. Surfacing bypass
        # (weight 0, like uptrend/downtrend) — the AI (which now also sees raw OHLC)
        # adjudicates direction/execution. Gated so it's reversible without code.
        _cp = config.get("candlestick_patterns", {}) or {}
        if _cp.get("enabled"):
            _wbr = _cp.get("wick_body_ratio", 2.0)
            _ctx_lb = int(_cp.get("context_lookback", 6))
            _ctx_pct = _cp.get("context_pct", 1.5)
            hits.append(trigger_mod.bearish_reversal_candle(candles, _wbr, _ctx_lb, _ctx_pct))
            hits.append(trigger_mod.bullish_reversal_candle(candles, _wbr, _ctx_lb, _ctx_pct))

        # At least one trigger must fire.
        fired_count = sum(1 for h in hits if h.get("fired"))
        if fired_count < 1:
            return (True, None)

        score = trigger_mod.composite_score(hits, _score_weights)
        # A confirmed momentum burst is always surfaced — a large, fast move is
        # exactly the signal the composite gate must never filter out.
        burst_fired = any(h["name"] == "momentumBurst" and h["fired"] for h in hits)
        # Whale-accumulation bypass (gated by whale_scan_bypass, default OFF).
        # oi_funding_anomaly / oi_surge_accumulation fire on FLAT price (smart
        # money loading vs crowded shorts), which by definition scores low on the
        # momentum/breakout triggers — so without this the coin is dropped here
        # and the executor's whale override (whale_force_execute / regime bypass)
        # never sees it. When enabled, surface the coin so the downstream whale
        # gates can decide; they still apply min_ai_confidence + all risk gates.
        whale_bypass = whale_scan_bypass and bool((whale_signals or {}).get(market["coin"]))
        # Directional-trend bypass: a sustained intraday up/down trend surfaces the
        # coin for research even below the composite gate (the gate is calibrated
        # for bullish multi-trigger setups; a lone trend signal can't clear it).
        # This is what unblocks shorting downtrends. Gated by trend_surface_enabled
        # (default ON) so it's reversible without a code change.
        trend_bypass = trend_surface_enabled and any(
            h["name"] in ("uptrendMomentum", "downtrendMomentum") and h["fired"] for h in hits)
        # Candlestick reversal bypass: a fired shooting-star/hammer/engulfing surfaces
        # the coin for AI research even below the composite gate (the gate is tuned for
        # momentum, not reversals). Gated by candlestick_patterns.enabled.
        pattern_bypass = bool(_cp.get("enabled")) and any(
            h["name"] in ("bearishReversalCandle", "bullishReversalCandle") and h["fired"] for h in hits)
        if (score < min_score and not burst_fired and not whale_bypass
                and not trend_bypass and not pattern_bypass):
            # Near-miss logging (LEAK #2 observability) — OFF by default. Surfaces
            # coins that scored just below the gate so we can see whether extended
            # movers land just-under (tune threshold) or far-under (need the
            # continuation trigger). Pure logging; no effect on what trades.
            if _mc.get("log_near_miss") and score >= min_score * 0.5:
                try:
                    logger.info(
                        f"[near-miss] {market['coin']} composite {score:.1f} "
                        f"(gate {min_score}) fired={[h['name'] for h in hits if h.get('fired')]}"
                    )
                except Exception:
                    pass
            return (True, None)

        whale = (whale_signals or {}).get(market["coin"])
        return (True, {
            "id": f"{market['coin']}-{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}",
            "coin": market["coin"],
            "type": market["type"],
            "fired_at": int(time.time() * 1000),
            "mid": mid,
            "triggers": hits,
            "composite_score": score,
            "whale_signal": whale,  # None unless coin is in oi_funding_anomaly hits
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
        whale_scan_bypass = bool(_cfg.get("whale_scan_bypass", False))
        trend_surface_enabled = bool(_cfg.get("trend_surface_enabled", True))
    except Exception:
        include_crypto = True
        include_hip3 = False
        whale_scan_bypass = False
        trend_surface_enabled = True

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
    # HIP-3 dex mute: focus scanning on specific HIP-3 venues without disabling
    # HIP-3 entirely. `hip3_dex_allowlist` (e.g. ["xyz"]) = scan ONLY those dexes;
    # `hip3_dex_blocklist` = scan all but those. Crypto/main-dex markets (no
    # `dex`) are never affected. Stops wasted research on unfunded/uninteresting
    # dexes (km, hyna, cash, ...). Both read fresh each scan (hot-reload).
    if include_hip3:
        allow = {d for d in (_cfg.get("hip3_dex_allowlist") or []) if d}
        block = {d for d in (_cfg.get("hip3_dex_blocklist") or []) if d}
        if allow:
            eligible = [m for m in eligible if not m.get("dex") or m.get("dex") in allow]
        if block:
            eligible = [m for m in eligible if not m.get("dex") or m.get("dex") not in block]
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
    movers_vol_floor = float(os.environ.get("HERMES_MOVERS_VOL_FLOOR_USD", "300000"))
    # Half the HIP-3 budget goes to top-by-volume (clean liquid markets),
    # half to top-by-|24h%| above a tiny floor (catches xyz:DKNG-style
    # low-volume HIP-3 pumpers that would never make a vol cut). The HIP-3
    # universe is bounded so this doesn't expose us to crypto-microcap noise.
    hip3_movers_floor = float(os.environ.get("HERMES_HIP3_MOVERS_FLOOR_USD", "50000"))

    def _abs_pct_24h(m):
        prev = float(m.get("prevDayPx") or 0)
        # Current price MUST come from this cycle's fresh mids — the universe
        # dict's midPx is from the (up-to-24h-cached) metaAndAssetCtxs snapshot
        # and freezes at loop-start, so ranking off it selects YESTERDAY's
        # movers and misses a coin ripping right now. Fall back to the cached
        # mid/mark only if the live mid is missing.
        cur = float(mids.get(m["coin"]) or m.get("midPx") or m.get("markPx") or 0)
        if prev <= 0 or cur <= 0:
            return 0.0
        return abs((cur - prev) / prev * 100)

    def _pick_with_movers(pool, vol_budget, movers_budget, mv_floor):
        """Top-N by 24h volume + top-M by |24h%|, deduped, in that priority.

        Movers slot guarantees a budget for sub-top-volume big movers
        regardless of their volume rank; the floor filters out pico-cap
        noise where a $200 trade can print a 50% move.
        """
        by_vol = sorted(pool, key=lambda m: m.get("dayNtlVlm", 0), reverse=True)
        vol_pick = by_vol[:vol_budget]
        chosen = {m["coin"] for m in vol_pick}
        candidates = [m for m in pool
                      if m["coin"] not in chosen
                      and m.get("dayNtlVlm", 0) >= mv_floor]
        by_pct = sorted(candidates, key=_abs_pct_24h, reverse=True)
        movers_pick = [m for m in by_pct if _abs_pct_24h(m) >= 1.0][:movers_budget]
        return vol_pick, movers_pick

    if include_crypto and include_hip3:
        crypto_budget = max(0, max_markets - max_markets_hip3)
        crypto_vol_budget = max(0, crypto_budget - max_markets_movers)
        crypto = [m for m in eligible if not m.get("dex")]
        hip3 = [m for m in eligible if m.get("dex")]
        crypto_top, crypto_movers = _pick_with_movers(crypto, crypto_vol_budget,
                                                     max_markets_movers, movers_vol_floor)
        # Split HIP-3: half by volume, half by |24h%| above the tiny floor.
        hip3_vol_budget = max_markets_hip3 // 2
        hip3_mover_budget = max_markets_hip3 - hip3_vol_budget
        hip3_top, hip3_movers = _pick_with_movers(hip3, hip3_vol_budget,
                                                  hip3_mover_budget, hip3_movers_floor)
        markets = crypto_top + crypto_movers + hip3_top + hip3_movers
        logger.info(
            f"[scan] budget split: {len(crypto_top)} crypto-vol + {len(crypto_movers)} crypto-movers "
            f"+ {len(hip3_top)} HIP-3-vol + {len(hip3_movers)} HIP-3-movers "
            f"(of {len(crypto)} crypto + {len(hip3)} HIP-3 eligible)"
        )
        if crypto_movers:
            sample = ", ".join(f"{m['coin']} {_abs_pct_24h(m):+.1f}%" for m in crypto_movers[:5])
            logger.info(f"[scan] crypto-movers: {sample}")
        if hip3_movers:
            sample = ", ".join(f"{m['coin']} {_abs_pct_24h(m):+.1f}%" for m in hip3_movers[:5])
            logger.info(f"[scan] HIP-3-movers: {sample}")
    else:
        pool = eligible
        vol_budget = max(0, max_markets - max_markets_movers)
        # Use the appropriate floor for the single-class mode.
        floor = hip3_movers_floor if include_hip3 else movers_vol_floor
        chosen, movers = _pick_with_movers(pool, vol_budget, max_markets_movers, floor)
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

    # Fetch the whale-accumulation map ONCE per scan (it's universe-derived
    # so all markets share the same snapshot). If the call fails, perceptions
    # just won't have whale signals attached — no other effect.
    try:
        from hermes_trader.agents.whale_index import whale_accumulation_map
        whale_signals = whale_accumulation_map()
        if whale_signals:
            logger.info(
                f"[scan] whale accumulation: {len(whale_signals)} coins flagged "
                f"({', '.join(list(whale_signals.keys())[:5])})"
            )
    except Exception as e:
        logger.warning(f"[scan] whale_accumulation_map failed: {e}")
        whale_signals = {}

    results: List[Dict[str, Any]] = []
    errors = 0
    completed = 0

    for batch_start in range(0, total, batch_size):
        batch_end = min(batch_start + batch_size, total)
        batch = callables[batch_start:batch_end]

        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="hermes-scan") as pool:
            futures = [pool.submit(_scan_single_market, m, md, cfg, min_score, whale_signals, whale_scan_bypass, trend_surface_enabled) for m, md in batch]
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


