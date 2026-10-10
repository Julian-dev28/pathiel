"""Perception scan engine — sweeps Hyperliquid markets for trigger signals.

Fetches candles, runs trigger detection, and returns candidates that meet the
composite-score threshold. Scans fan out across threads; volume pre-filtering
limits the sweep to the top-N markets by 24h volume to stay within HL's
1200 weight/minute rate limit (candle fetch = 20 weight each).
"""

from __future__ import annotations

import logging
import os
import threading
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

# ── Feed freshness: data-gap accounting (Phase-0 hardening) ──────────────────
# A coin whose candle fetch comes back empty/short is treated as "no trigger"
# (perception returns (True, None)) — which is INDISTINGUISHABLE from a real
# no-signal. So a big mover we simply failed to read looks identical to one we
# evaluated and skipped. We now count those per scan (thread-safe; scan fans out
# over a ThreadPoolExecutor) and surface the count in the scan summary so a
# silent data gap is visible, not invisible. Observability only — no behavior
# change to what gets traded.
_data_gap_lock = threading.Lock()
_data_gap_count = 0


def _reset_data_gaps() -> None:
    global _data_gap_count
    with _data_gap_lock:
        _data_gap_count = 0


def _note_data_gap() -> None:
    global _data_gap_count
    with _data_gap_lock:
        _data_gap_count += 1


def _get_data_gaps() -> int:
    with _data_gap_lock:
        return _data_gap_count


# HIP-3 prefilter drop counter (same lock pattern as data gaps — the scan
# fans out across threads). Per-coin drop lines are DEBUG (invisible at the
# prod log level), so this aggregate on the INFO scan summary is the ONLY
# production visibility into how much research the prefilter saved.
_prefilter_lock = threading.Lock()
_prefilter_drop_count = 0


def _reset_prefilter_drops() -> None:
    global _prefilter_drop_count
    with _prefilter_lock:
        _prefilter_drop_count = 0


def _note_prefilter_drop() -> None:
    global _prefilter_drop_count
    with _prefilter_lock:
        _prefilter_drop_count += 1


def _get_prefilter_drops() -> int:
    with _prefilter_lock:
        return _prefilter_drop_count

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


# ── HIP-3 pre-research composite pre-filter ──────────────────────────────────

def _hip3_prefilter_verdict(score: float, hits: List[Any], market: Dict[str, Any],
                            config: Dict[str, Any]) -> Tuple[bool, bool]:
    """Decide whether a HIP-3 candidate dies at the executor's composite floor.

    Pure — no logging, no I/O (decision-function purity rule). Returns
    ``(drop, shadow)``:
      * ``drop=True, shadow=False`` — hard-drop this candidate pre-research;
      * ``drop=True, shadow=True``  — would drop; caller logs the accrual and
        lets it through (shadow counterfactual);
      * ``drop=False``              — no opinion (not HIP-3, feature off, or
        short lane open).

    Rationale (2026-09-22): while HIP-3 was on, the executor's long-side floor
    ``runner_entry_gate.min_hip3_composite`` sat AFTER full LLM research — 2,176
    research cycles completed and then died there (observed composite p50 25 /
    p90 38 / p99 43 vs bar 50; only 2/1908 blocks were at/over 50). Pre-applying
    the same rule in perception stops paying research for doomed candidates.

    The bar is read from the SAME key the executor uses, so scan and gate can
    never disagree. Fail-safe: no ``dex`` on the market or feature off ⇒ no-op.

    Short lane preserved: the executor's floor only guards LONGS (the short
    branch returns before it), so a candidate with a fired down-side surfacing
    trigger (downtrendMomentum / bearishReversalCandle) is NOT dropped — it can
    still reach research and be judged SHORT without ever facing the floor.
    """
    if not market.get("dex"):
        return (False, False)
    pf = config.get("hip3_scan_prefilter") or {}
    if not bool(pf.get("enabled", False)):
        return (False, False)
    bar = float((config.get("runner_entry_gate") or {}).get("min_hip3_composite", 50.0))
    short_lane = any(
        h["name"] in ("downtrendMomentum", "bearishReversalCandle") and h.get("fired")
        for h in hits)
    if score >= bar or short_lane:
        return (False, False)
    return (True, bool(pf.get("shadow_mode", False)))


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
            # Empty/short can mean genuine thin history OR a fetch failure that
            # survived retries (429/timeout). Either way we evaluated nothing
            # here — count it as a data gap so the scan summary distinguishes
            # "read it, no signal" from "couldn't read it". (Still returns the
            # same (True, None) — no behavior change.)
            _note_data_gap()
            return (True, None)  # Not an error, just no triggers

        # 1h candles for slow-burn / accumulation triggers. Cached far longer
        # than 5m (1h bars don't change intra-hour). 100 bars: the band
        # snapback (when on the 1h interval) needs 2*band_span+2 (34 for the
        # default span-16) for its band edges + drift reference. Failure here
        # doesn't block the scan — slow-burn triggers just won't fire.
        candles_1h = _fetch_candles_sync(
            market["coin"], "1h", 100,
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

        # Band snapback — OFF by default. MA-band poke-out + snapback in CHOP:
        # the band edges are the rolling `ma_type` average of the HIGHs (upper)
        # and LOWs (lower) over `band_span` bars — a CURVED band that hugs
        # price. A wick that pierces a band edge by >= min_poke_atr * ATR and
        # price back inside proposes a mean-reversion (lower poke -> LONG,
        # upper poke -> SHORT). The drift/direction verdict ("trending") is
        # measured over the SAME band_span window — no separate longer
        # lookback. Runs on its OWN interval (15m/1h are the primary
        # timeframes; 5m works too) so the timeframe is configurable,
        # independent of the 5m scan.
        # Surfacing bypass (weight 0) — the AI + risk gates adjudicate.
        _bs = config.get("band_snapback", {}) or {}
        if _bs.get("enabled"):
            # Per-coin overrides (asset-class separation): `overrides` is a
            # map of coin -> {interval, band_span, min_poke_atr, ...}.
            # The grid found 1h suits majors and 15m suits the choppy meme
            # coins, so each coin can run its own interval/params here. An
            # omitted key falls back to the base block.
            _ov = (_bs.get("overrides") or {}).get(market["coin"]) or {}
            _bs = {**_bs, **{k: v for k, v in _ov.items() if v is not None}}
            if _bs.get("enabled"):
                # (A per-coin override with enabled=false falls through here
                # and skips the fetch — that's the opt-out path.)
                _bs_interval = str(_bs.get("interval", "15m"))
                _bs_span = max(2, int(_bs.get("band_span", 16)))
                # Fetch the band interval's candles (TTL-cached, so a stable
                # interval costs one fetch per coin per scan window). We need
                # 2*band_span bars: the drift reference sits one full span
                # back in the fit, so the MA must be warm there. Reuse the 1h
                # fetch when the interval is 1h (100 bars fetched above covers
                # span up to 49).
                _band_candles = candles_1h if _bs_interval == "1h" else None
                if _band_candles is None:
                    _band_candles = _fetch_candles_sync(
                        market["coin"], _bs_interval, 2 * _bs_span + 4,
                        config["scan"].get("cacheTtlMs1h", 600_000),
                    )
                if _band_candles and len(_band_candles) >= 2 * _bs_span + 2:
                    _bs_hit = trigger_mod.band_snapback(
                        _band_candles,
                        band_span=_bs_span,
                        max_drift_pct=float(_bs.get("max_drift_pct", 1.5)),
                        min_poke_atr=float(_bs.get("min_poke_atr", 0.5)),
                        ma_type=str(_bs.get("ma_type", "ema")),
                        # Overshoot guard for the de-lagged band edge: clamp the
                        # 1-bar projection to this many ATR (0.25 = measured p99
                        # overshoot on 15m/1h). None/0 disables the cap.
                        max_project_atr=_bs.get("max_project_atr", 0.25),
                    )
                    hits.append(_bs_hit)
                    # P3 shadow accrual: with band_snapback.shadow_mode the
                    # prompt block and the surfacing bypass are BOTH suppressed,
                    # so this is the only visible trace of a FIRED snapback —
                    # the counterfactual accrual record the owner greps later.
                    # Emitted only when the umbrella flag is on AND the trigger
                    # fired; a trending/no-snapback band has no fired accrual.
                    # The trigger compute above is untouched (enabled still
                    # gates it) so `fired`/`reason` keep populating either way.
                    if _bs.get("shadow_mode") and _bs_hit.get("fired"):
                        _bs_reason = _bs_hit.get("reason", "")
                        _bs_side = _bs_reason.split(" ", 1)[0] if _bs_reason else ""
                        logger.warning(
                            f"[band-snapback][SHADOW] {market['coin']} {_bs_side} "
                            f"— {_bs_reason} (shadow: prompt+surfacing suppressed, "
                            f"gate log-only)"
                        )

        # RSI divergence — OFF by default. Price prints a new extreme (lower
        # low / higher high beyond min_extreme_atr x ATR) while RSI prints the
        # opposite — momentum fading into the push. The candlestick-pattern
        # triggers catch the SHAPE of a reversal bar; this catches the
        # momentum CONFIRMATION behind it. Surfacing bypass (weight 0) — the
        # AI + risk gates adjudicate. shadow_mode: trigger keeps computing and
        # accrues a [SHADOW] line, but the surfacing bypass is suppressed and
        # research.py filters the hit out of the prompt.
        _rd = config.get("rsi_divergence", {}) or {}
        _rd_hit = None
        if _rd.get("enabled"):
            _rd_hit = trigger_mod.rsi_divergence(
                candles,
                rsi_period=int(_rd.get("rsi_period", 14)),
                lookback=int(_rd.get("lookback", 48)),
                min_rsi_gap=float(_rd.get("min_rsi_gap", 3.0)),
                min_extreme_atr=float(_rd.get("min_extreme_atr", 0.25)),
                bull_rsi_max=float(_rd.get("bull_rsi_max", 45.0)),
                bear_rsi_min=float(_rd.get("bear_rsi_min", 55.0)),
            )
            hits.append(_rd_hit)
            if _rd.get("shadow_mode") and _rd_hit.get("fired"):
                # Shadow accrual record — the only visible trace while
                # shadow_mode suppresses surfacing + prompt.
                logger.warning(
                    f"[rsi-divergence][SHADOW] {market['coin']} "
                    f"{_rd_hit.get('reason', '')} "
                    f"(shadow: surfacing+prompt suppressed, log-only)"
                )

        # Daily mover surfacing: the scan already reserves slots for top 24h
        # movers, but the trigger gate can still drop an orderly runner once the
        # fresh spike/breakout bar has passed. Surface large liquid movers to AI
        # as a weight-0 trigger so SHADOW can tell us whether they are late-chase
        # junk or real continuation setups. Execution is still governed by the
        # runner gate, liquidity gate, AI confidence, and SHADOW/LIVE mode.
        _rms = config.get("runner_mover_surface") or {}
        daily_mover_fired = False
        daily_mover_reason = ""
        if bool(_rms.get("enabled", False)):
            prev = float(market.get("prevDayPx") or 0)
            cur = float(mid or market.get("midPx") or market.get("markPx") or 0)
            vol = float(market.get("dayNtlVlm") or 0)
            if prev > 0 and cur > 0:
                move_pct = (cur - prev) / prev * 100
                is_hip3 = bool(market.get("dex"))
                min_move = float(_rms.get(
                    "min_hip3_24h_pct" if is_hip3 else "min_crypto_24h_pct",
                    8.0 if is_hip3 else 10.0,
                ))
                min_vol = float(_rms.get("min_volume_usd", 3_000_000))
                daily_mover_fired = move_pct >= min_move and vol >= min_vol
                daily_mover_reason = (
                    f"{move_pct:+.1f}% 24h mover, vol ${vol/1e6:.2f}M"
                    if daily_mover_fired
                    else f"{move_pct:+.1f}% 24h / vol ${vol/1e6:.2f}M"
                )
        hits.append({
            "name": "dailyMover",
            "score": 10 if daily_mover_fired else 0,
            "reason": daily_mover_reason or "not a configured 24h mover",
            "fired": daily_mover_fired,
        })

        # Signed 24h move (cur vs prevDayPx) — attached to the perception dict
        # whenever the universe row carries both prices, INDEPENDENT of the
        # mover-surfacing gate above. The executor's falling-knife guard for the
        # long sidestep (upstream 51bc23b) reads it to block a PASS->LONG upgrade
        # on a violently negative 24h move; a missing value degrades that clause
        # to a no-op, leaving the downtrend clause as the guaranteed protection.
        daily_move_pct: Optional[float] = None
        _prev = float(market.get("prevDayPx") or 0)
        _cur = float(mid or market.get("midPx") or market.get("markPx") or 0)
        if _prev > 0 and _cur > 0:
            daily_move_pct = (_cur - _prev) / _prev * 100

        # At least one trigger must fire.
        fired_count = sum(1 for h in hits if h.get("fired"))
        if fired_count < 1:
            return (True, None)

        score = trigger_mod.composite_score(hits, _score_weights)
        # HIP-3 pre-research drop (2026-09-22): apply the executor's long-side
        # composite floor HERE so doomed candidates never pay a research cycle
        # (measured: 2,176 full research runs ended at that one gate). Short
        # lane + shadow mode + fail-safes in the verdict helper. Runs BEFORE
        # every surfacing bypass — daily-mover/trend-bypass admits were exactly
        # the population that died downstream (composite p50 25 vs bar).
        _pf_drop, _pf_shadow = _hip3_prefilter_verdict(score, hits, market, config)
        if _pf_drop:
            if _pf_shadow:
                logger.warning(
                    f"[gate][SHADOW] hip3_scan_prefilter WOULD DROP {market['coin']} "
                    f"composite {score:.1f} < bar "
                    f"{float((config.get('runner_entry_gate') or {}).get('min_hip3_composite', 50.0)):.0f} "
                    f"— letting through to research (shadow accrual)")
            else:
                _note_prefilter_drop()
                logger.debug(
                    f"[scan] hip3_scan_prefilter dropped {market['coin']} "
                    f"composite {score:.1f} pre-research")
                return (True, None)
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
        # Band-snapback bypass: a fired poke+snapback surfaces the coin for AI
        # research even below the composite gate (the gate is tuned for momentum
        # bursts, not chop mean-reversion). Gated by band_snapback.enabled; the
        # P3 umbrella band_snapback.shadow_mode additionally suppresses the
        # surfacing (the trigger still computes and accrues the [SHADOW] line).
        snapback_bypass = (bool(_bs.get("enabled"))
                           and not bool(_bs.get("shadow_mode"))
                           and any(
                               h["name"] == "bandSnapback" and h["fired"] for h in hits))
        daily_mover_bypass = any(h["name"] == "dailyMover" and h["fired"] for h in hits)
        # RSI-divergence bypass: a fired divergence surfaces the coin for AI
        # research even below the composite gate (the gate is tuned for momentum
        # bursts, not fading-momentum reversals). Gated by rsi_divergence.enabled;
        # rsi_divergence.shadow_mode suppresses the surfacing (the trigger still
        # computes and accrues the [SHADOW] line).
        divergence_bypass = (bool(_rd.get("enabled"))
                             and not bool(_rd.get("shadow_mode"))
                             and _rd_hit is not None and _rd_hit.get("fired"))
        if (score < min_score and not burst_fired and not whale_bypass
                and not trend_bypass and not pattern_bypass and not snapback_bypass
                and not daily_mover_bypass and not divergence_bypass):
            # Near-miss logging (LEAK #2 observability) — OFF by default. Surfaces
            # coins that scored just below the gate so we can see whether extended
            # movers land just-under (tune threshold) or far-under (need the
            # continuation trigger). Pure logging; no effect on what trades.
            if _mc.get("enabled") and _mc.get("log_near_miss") and score >= min_score * 0.5:
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
            "daily_move_pct": daily_move_pct,
            "whale_signal": whale,  # None unless coin is in oi_funding_anomaly hits
        })
    except Exception as e:
        return (False, str(e))


# ── Main scan entry point ───────────────────────────────────────────────────

# Rotating universe-sweep cursor — persists across scan_once calls in the running
# process so successive cycles walk the FULL universe (see HERMES_UNIVERSE_SWEEP).
_sweep_offset = 0


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
    _reset_data_gaps()
    _reset_prefilter_drops()
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
        trend_surface_enabled = bool(_cfg.get("trend_surface_enabled", False))
    except Exception:
        _cfg = {}
        include_crypto = True
        include_hip3 = False
        whale_scan_bypass = False
        trend_surface_enabled = False

    # `get_config()` owns static trigger weights/thresholds; `.agent-config.json`
    # owns hot strategy toggles. Merge root-level live keys so optional scan
    # features such as momentum_continuation / candlestick_patterns actually
    # follow the live config instead of being dead knobs.
    scan_cfg = {**cfg, **_cfg}

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
    # Eligibility falls back to the cached midPx/markPx when the live WS mid is
    # missing — the live feed only covers a subset of HIP-3 coins, and requiring
    # it silently shrank the HIP-3 scan pool to ~3 names against a 25-slot
    # budget (xyz:QNT +9.0% / xyz:NBIS +8.2% / xyz:PURRDAT +10.3% / xyz:ARM
    # +9.3% all absent from perceptions on 2026-06-12 while only CBRS/SKHX/SMSN
    # scanned). _abs_pct_24h already uses this exact fallback for ranking.
    eligible = [m for m in universe
                if (mids.get(m["coin"], 0)
                    or float(m.get("midPx") or m.get("markPx") or 0)) > 0
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
    crypto_sweep_floor = float(_cfg.get("min_market_volume_usd", movers_vol_floor) or movers_vol_floor)
    hip3_sweep_floor = float(_cfg.get("min_hip3_volume_usd", hip3_movers_floor) or hip3_movers_floor)

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
    # ── Rotating universe sweep ─────────────────────────────────────────
    # Cover the FULL universe over successive cycles, not just top-vol+movers.
    # Each scan adds the next `sweep_n` eligible markets, advancing a persistent
    # offset that wraps around — so every market is seen within
    # ceil(len(eligible)/sweep_n) cycles, while top-vol+movers are ALWAYS scanned
    # (never miss a live ripper). Pacing (HERMES_BATCH_SLEEP) keeps us under HL's
    # ~1200 weight/min budget. HERMES_UNIVERSE_SWEEP=0 disables (default).
    sweep_n = int(os.environ.get("HERMES_UNIVERSE_SWEEP", "0"))
    if sweep_n > 0 and eligible:
        global _sweep_offset
        sweep_pool = [
            m for m in eligible
            if float(m.get("dayNtlVlm") or 0) >= (
                hip3_sweep_floor if m.get("dex") else crypto_sweep_floor
            )
        ]
        ordered = sorted(sweep_pool, key=lambda m: m.get("coin", ""))
        if not ordered:
            logger.info("[scan] universe sweep: no markets above liquidity floor")
            ordered = []
    if sweep_n > 0 and eligible and ordered:
        n = len(ordered)
        off = _sweep_offset % n
        window = ordered[off:off + sweep_n]
        if len(window) < sweep_n:                      # wrap-around
            window += ordered[: sweep_n - len(window)]
        have = {m["coin"] for m in markets}
        added = [m for m in window if m.get("coin") not in have]
        markets = markets + added
        _sweep_offset = (off + sweep_n) % n
        logger.info(f"[scan] universe sweep: +{len(added)} new (offset {off}/{n}, "
                    f"full coverage ~{(n + sweep_n - 1) // sweep_n} cycles)")

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

    whale_enabled = any([
        whale_scan_bypass,
        bool(_cfg.get("whale_force_execute", False)),
        bool(_cfg.get("whale_regime_bypass", False)),
        float(_cfg.get("whale_size_multiplier", 1.0) or 1.0) != 1.0,
    ])
    # Fetch the whale-accumulation map only when a downstream whale feature can
    # actually use it. Keeping disabled signals out of the research prompt avoids
    # stale "shadow" context nudging verdicts while force/regime/size paths are off.
    if whale_enabled:
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
    else:
        whale_signals = {}

    results: List[Dict[str, Any]] = []
    errors = 0
    completed = 0

    for batch_start in range(0, total, batch_size):
        batch_end = min(batch_start + batch_size, total)
        batch = callables[batch_start:batch_end]

        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="hermes-scan") as pool:
            futures = [pool.submit(_scan_single_market, m, md, scan_cfg, min_score, whale_signals, whale_scan_bypass, trend_surface_enabled) for m, md in batch]
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
    data_gaps = _get_data_gaps()
    pf_drops = _get_prefilter_drops()
    logger.info(f"[scan] scanned {len(markets)} markets, {len(results)} triggers in {elapsed:.0f}ms "
                f"({errors} errors, {data_gaps} data-gaps"
                + (f", hip3-prefilter saved {pf_drops} research" if pf_drops else "") + ")")
    if data_gaps > 0 and len(markets) > 0 and data_gaps / len(markets) > 0.25:
        # >25% of the universe unreadable this scan = a degraded data feed, not a
        # quiet market. Surface loudly so a silent miss-the-move window is visible.
        logger.warning(
            f"[scan] FEED-FRESHNESS: {data_gaps}/{len(markets)} markets had empty/short "
            f"candles ({data_gaps/len(markets)*100:.0f}%) — possible degraded candle feed; "
            f"signals may be silently missed this scan")
    return sorted(results, key=lambda r: r["composite_score"], reverse=True)
