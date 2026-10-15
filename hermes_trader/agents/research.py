"""Deep-analysis pipeline: perception -> multi-timeframe indicators -> AI verdict -> persist."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import httpx

from hermes_trader.agents.config_store import read_agent_config
from hermes_trader.agents.duel_store import (
    call_duelist,
    duelist_config,
    duelist_enabled,
    effective_chat_template_kwargs,
    effective_primary_max_tokens,
    effective_primary_model,
    record_duel,
    server_processing_ms,
    slot_get,
)
from hermes_trader.agents import prompt_log
from hermes_trader.agents.decision_voice import (
    decision_voice_verdict,
    primary_engine_config,
    systemone_primary_verdict,
)
from hermes_trader.agents.memory import memory
from hermes_trader.agents.prior_calls import build_prior_call_block
from hermes_trader.agents.rvol_context import build_rvol_block
from hermes_trader.agents.system_prompt import build_system_prompt
from hermes_trader.client.hl_client import (
    fetch_account_state,
    fetch_funding_history,
    fetch_hl_candles,
    resolve_user_address,
)
from hermes_trader.indicators.math import adx, atr, candle_val, ema, rsi
from hermes_trader.models.types import Candle

logger = logging.getLogger(__name__)


def _compute_indicators(candles: List[Candle]) -> Dict[str, Any]:
    """Compute EMA8/21, RSI, ATR, ADX for a set of candles."""
    if not candles:
        return {
            "ema8": None, "ema21": None, "slope_up": None,
            "rsi14": None, "atr14": None, "adx14": None,
            "last_close": 0, "last_time": 0,
        }

    closes = [candle_val(c, "c") for c in candles]

    if len(closes) < 21:
        return {
            "ema8": None, "ema21": None, "slope_up": None,
            "rsi14": None, "atr14": None, "adx14": None,
            "last_close": closes[-1],
            "last_time": candles[-1].t,
        }

    ema8_arr = ema(closes, 8)
    ema21_arr = ema(closes, 21)

    last_ema8 = ema8_arr[-1] if ema8_arr else None
    last_ema21 = ema21_arr[-1] if ema21_arr else None

    slope_up = None
    if last_ema8 is not None and len(ema8_arr) >= 3:
        slope_up = last_ema8 > ema8_arr[-3]

    rsi_arr = rsi(candles, 14)
    atr_arr = atr(candles, 14)
    adx_arr = adx(candles, 14)

    return {
        "ema8": last_ema8 if last_ema8 is not None and math.isfinite(last_ema8) else None,
        "ema21": last_ema21 if last_ema21 is not None and math.isfinite(last_ema21) else None,
        "slope_up": slope_up,
        "rsi14": rsi_arr[-1] if rsi_arr and math.isfinite(rsi_arr[-1]) else None,
        "atr14": atr_arr[-1] if atr_arr and math.isfinite(atr_arr[-1]) else None,
        "adx14": adx_arr[-1] if adx_arr and math.isfinite(adx_arr[-1]) else None,
        "last_close": closes[-1],
        "last_time": candles[-1].t,
    }


def _fetch_funding_rate(coin: str) -> str:
    """Latest hourly funding rate for a coin, or 'N/A' if unavailable."""
    start_time = int(time.time() * 1000) - 86_400_000
    history = fetch_funding_history(coin, start_time)
    if history:
        rate = float(history[-1].get("fundingRate", "0"))
        if math.isfinite(rate):
            return f"{rate * 100:.4f}%/hr"
    return "N/A"


# Only surface news from the last N days. Without this, Brave returned
# year-old articles (e.g. AIXBT's 2025 hack) that then tripped the binary-news
# gate on a fresh 2026 trade. The gate reasons about *imminent* event risk, so
# stale headlines are noise — both to the gate and to the LLM prompt.
NEWS_FRESHNESS_DAYS = 2


def _fetch_news(coin: str) -> str:
    """Recent (last NEWS_FRESHNESS_DAYS) news headlines for a coin via the
    Brave Search API.

    Returns a compact ' | '-joined headline string, or 'no news' when no
    BRAVE_API_KEY is set or the request fails — news is a supplementary
    signal, so a fetch failure degrades gracefully and never blocks research.
    """
    key = os.environ.get("BRAVE_API_KEY", "")
    if not key:
        return "no news"
    # Brave `freshness` takes a YYYY-MM-DDtoYYYY-MM-DD range; a 2-day window
    # approximates "within 48h" (the closest the API offers to an hour-precise
    # bound without per-result age filtering).
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=NEWS_FRESHNESS_DAYS)
    freshness = f"{start.isoformat()}to{today.isoformat()}"
    try:
        resp = httpx.get(
            "https://api.search.brave.com/res/v1/news/search",
            params={"q": f"{coin} crypto", "count": 5, "freshness": freshness},
            headers={"X-Subscription-Token": key, "Accept": "application/json"},
            timeout=10.0,
        )
        if not resp.is_success:
            return "no news"
        results = resp.json().get("results", []) or []
        headlines = [str(r.get("title", "")).strip() for r in results if r.get("title")]
        return " | ".join(headlines[:5]) if headlines else "no news"
    except Exception:
        return "no news"


def _signals_block(coin: str) -> str:
    """Free positioning signals (GEX / aggTrades whale / FINRA short-vol / news
    catalyst) formatted for the AI prompt. The executor already uses these, but
    the AI was BLIND to them — so it kept PASSing rippers it couldn't see the
    bullish context for. Surfacing them here lets the AI's own verdict reflect
    them. Wrapped so any signal outage never breaks research."""
    is_hip3 = ":" in (coin or "")
    lines: List[str] = []
    try:
        if is_hip3:
            from hermes_trader.agents.options_gex import gex_signal_cached
            g = gex_signal_cached(coin)
            if g:
                lines.append(
                    f"  - Dealer gamma (GEX): {g.regime}; call wall {g.call_wall} (overhead "
                    f"resistance / ride target), put wall {g.put_wall} (support), spot {g.spot:g}. "
                    + ("Negative gamma = squeeze-prone, lets moves RUN."
                       if g.regime == "trend_short_gamma"
                       else "Long gamma = pins/mean-reverts near the walls.")
                )
            from hermes_trader.agents.short_volume import short_volume_signal
            sv = short_volume_signal(coin)
            if sv:
                lines.append(
                    f"  - Short volume (FINRA): {sv.ratio * 100:.0f}% ({sv.regime}, {sv.trend})."
                    + (" Crowded short = SQUEEZE FUEL for a long."
                       if sv.regime == "crowded_short_squeeze_fuel" else "")
                )
        else:
            from hermes_trader.agents.crypto_whale import crypto_whale_signal
            w = crypto_whale_signal(coin, window_minutes=15)
            if w and w.whale_n:
                lines.append(
                    f"  - Whale order-flow (Binance aggTrades, 15m): {w.bias}, net "
                    f"${w.net_usd:+,.0f} across {w.whale_n} large prints."
                    + (" Large buyers stepping in (bullish)." if w.bias == "whale_buying"
                       else " Large sellers hitting bids (bearish)." if w.bias == "whale_selling"
                       else "")
                )
        from hermes_trader.agents.news_catalyst import catalyst_scan
        base = coin.split(":", 1)[1] if ":" in coin else coin
        n = catalyst_scan(base, timespan="1h")
        if n and (n.breaking or n.surge_x >= 1.5):
            top = n.headlines[0].title[:90] if n.headlines else ""
            lines.append(
                f"  - News catalyst: {'BREAKING' if n.breaking else f'elevated ({n.surge_x}x coverage)'}"
                f" — {top!r}"
            )
    except Exception as e:
        logger.debug(f"[research] signals block failed for {coin}: {e}")
    if not lines:
        return "Positioning signals (GEX/whale/short-vol/news): none flagged"
    return ("Positioning signals (free data — weigh these in your verdict):\n"
            + "\n".join(lines))


def _chronos_block(coin: str) -> str:
    """Chronos-2 forward forecast for the AI prompt, when
    `chronos_signal.in_prompt` is true. Calls the sync wrapper so the line is
    DETERMINISTIC per coin: it renders whenever the signal is enabled and the
    model is loaded (the model is preloaded at app init; steady-state cost is
    ~200ms/coin, dominated by the HL candle fetch). A disabled flag or a
    failed/absent forecast returns '' so the prompt shape is unchanged.

    The `median` figure is the PATH AVERAGE over the horizon (the mean of the
    12 step-by-step forecasts), NOT the point price at the horizon — the
    2026-08-28 60-flag replay showed the median path mean-reverts (TRUMP's
    q50 dipped to -1.4% and recovered to -0.6% by step 12), so the endpoint
    is nearly useless as a read. The replay ranked the reductions by loss
    avoidance: EARLY ADVERSE TAIL > skew > path-mean. This block therefore
    also renders the early tail — the extreme the adverse quantile reaches
    within the first 6 steps (~30 min), the same shape the
    `chronos_tail_trigger_gate` consumes — so the LLM can see the stop-risk
    the median understates (the replay's q10 tail overstated realized
    magnitude ~2x, so treat it as a fat-tail RISK flag, not a magnitude).

    Labeled as a DECAY/continuation warning on purpose: the bot's exit policy is
    scalp, so the horizon is LONGER than the hold. A negative call means the
    move is likely to fade over the window — drop conviction for a scalp, don't
    auto-PASS a confirmed fresh breakout. A positive call means the model sees
    continuation over the window — supports holding through noise.
    """
    try:
        cfg = read_agent_config().get("chronos_signal", {})
        if not cfg.get("in_prompt", False) or not cfg.get("enabled", False):
            return ""
        # Lazy import keeps the torch/chronos dep off the module-load path;
        # module-attr call (not `from ... import`) so tests can patch the sync
        # wrapper. The model is preloaded at init, so this rarely pays more
        # than the ~200ms candle fetch.
        from hermes_trader.agents import chronos_signal as _cs
        sig = _cs.get_chronos_signal_sync(coin, "long")
        if sig is None or sig.error or sig.median_pct is None:
            return ""
        med = sig.median_pct
        low = sig.q_low
        high = sig.q_high
        # Quantiles are absolute prices; convert to % vs context_last for the
        # prompt (the log line uses context_last too, so both are comparable).
        if low is not None and high is not None and sig.context_last:
            lo_pct = (low - sig.context_last) / sig.context_last * 100
            hi_pct = (high - sig.context_last) / sig.context_last * 100
            span = f", p10 avg {lo_pct:+.1f}% / p90 avg {hi_pct:+.1f}%"
        else:
            span = ""
        # Early adverse tail: the extreme the adverse quantile reaches within
        # the first 6 steps (~30 min) — the replay's most loss-avoiding read,
        # and the same value the tail-trigger gate consumes. The forecast is
        # side-independent (the cache is coin-keyed), so render both
        # directions: p10 path min (long-side stop risk) and p90 path max
        # (short-side stop risk).
        tail_bits = []
        _tail_steps = 6
        p10p = sig.q10_path_pct or []
        p90p = sig.q90_path_pct or []
        if p10p[:_tail_steps]:
            tail_bits.append(f"p10 min {min(p10p[:_tail_steps]):+.1f}%")
        if p90p[:_tail_steps]:
            tail_bits.append(f"p90 max {max(p90p[:_tail_steps]):+.1f}%")
        tail_s = (
            f"; early tail, first {_tail_steps * 5}m (stop-risk read — the "
            f"median understates it): {' / '.join(tail_bits)}"
            if tail_bits else ""
        )
        # Chronos runs on 5m candles; horizon bars * 5m = the forward window.
        hours = sig.horizon * 5 / 60
        hours_s = f"{hours:.0f}" if hours == int(hours) else f"{hours:g}"
        # Confidence floor (2026-08-30 HEMI replay): the FADE/continuation
        # notes are interpretive CLAIMS, so they only render when the median
        # clears a quarter of the model's own p10-p90 band (min_conf_ratio).
        # All 8 hourly HEMI forecasts at the live config were under the floor
        # — three of them rendered the full FADE warning at |median| 0.08–1.70%
        # inside 5–8% bands, and the LLM leaned on those for six hours of a
        # +38% rip. Below the floor the note says no-confident-direction and
        # the data lines above still carry the numbers for the LLM's own
        # judgement. Fail-safe: no spread -> ratio 0.0 -> neutral note
        # (never claim a fade we have no basis for).
        from hermes_trader.agents import chronos_signal as _cs2
        min_conf_ratio = _cs2.resolve_min_conf_ratio(cfg)
        ratio = _cs2.confidence_ratio(sig)
        if ratio < min_conf_ratio:
            spread_s = (f"{sig.spread_pct:.1f}%"
                        if sig.spread_pct is not None else "no band")
            note = (f"the model has no confident direction over the next ~{hours_s}h — the "
                    f"median ({med:+.2f}%) sits inside its own p10-p90 band "
                    f"({spread_s}, ratio {ratio:.2f} < {min_conf_ratio:.2f}); "
                    "treat it as noise, not a fade or continuation.")
        elif med > 0:
            note = (f"the model sees continuation for the next ~{hours_s}h — supports "
                    "holding through pullbacks, favours swing over scalp conviction.")
        elif med < 0:
            note = (f"the model expects the move to FADE within ~{hours_s}h — this is a "
                    "decay/continuation warning: lower conviction on late entries, "
                    f"prefer a tight scalp TP over holding for the {hours_s}h horizon; it is "
                    "NOT an instruction to auto-PASS a confirmed fresh breakout.")
        else:
            note = f"the model is directionally neutral over the next ~{hours_s}h."
        return (
            "Chronos forecast (shadow signal — weigh, don't obey):\n"
            f"  - Path-average price over the next ~{hours_s}h: {med:+.2f}%{span}{tail_s}. "
            + note
        )
    except Exception as e:
        logger.debug(f"[research] chronos block failed for {coin}: {e}")
        return ""


def _timesfm_block(coin: str) -> str:
    """TimesFM-3 forward forecast for the AI prompt, when
    `timesfm_signal.in_prompt` is true. Same determinism contract as
    _chronos_block: sync, cache-first read (shares the 300s per-coin cache
    with the executor attach and the gate-side read), '' on disabled /
    failed / absent so the prompt shape is unchanged.

    The 2026-09-02 two-model sweep: at its sweep-selected context (100 bars
    vs chronos' 50) TimesFM is the more accurate forecaster on this stream
    (direction 53% vs 51%) and the two disagree enough to be complementary —
    hence a SECOND block rather than a replacement. Rendered leaner than the
    chronos block: headline median + band + early tail, with an explicit
    cross-check framing so the LLM treats agreement/disagreement between the
    two forecasters as the informative signal (the sweep's best veto was the
    both-adverse AND shape).
    """
    try:
        cfg = read_agent_config().get("timesfm_signal", {})
        if not cfg.get("in_prompt", False) or not cfg.get("enabled", False):
            return ""
        from hermes_trader.agents import timesfm_signal as _ts
        sig = _ts.get_timesfm_signal_sync(coin, "long")
        if sig is None or sig.error or sig.median_pct is None:
            return ""
        med = sig.median_pct
        span = ""
        if sig.q_low is not None and sig.q_high is not None and sig.context_last:
            lo_pct = (sig.q_low - sig.context_last) / sig.context_last * 100
            hi_pct = (sig.q_high - sig.context_last) / sig.context_last * 100
            span = f", p10 avg {lo_pct:+.1f}% / p90 avg {hi_pct:+.1f}%"
        tail_bits = []
        _tail_steps = 6
        p10p = sig.q10_path_pct or []
        p90p = sig.q90_path_pct or []
        if p10p[:_tail_steps]:
            tail_bits.append(f"p10 min {min(p10p[:_tail_steps]):+.1f}%")
        if p90p[:_tail_steps]:
            tail_bits.append(f"p90 max {max(p90p[:_tail_steps]):+.1f}%")
        tail_s = f"; early tail, first {_tail_steps * 5}m: {' / '.join(tail_bits)}" if tail_bits else ""
        hours = sig.horizon * 5 / 60
        hours_s = f"{hours:.0f}" if hours == int(hours) else f"{hours:g}"
        # Confidence floor (same HEMI-replay semantics as the chronos block):
        # the directional note renders only when |median| clears
        # min_conf_ratio x the model's own p10-p90 spread.
        min_conf_ratio = _ts.resolve_min_conf_ratio(cfg)
        ratio = _ts.confidence_ratio(sig)
        if ratio < min_conf_ratio:
            note = (f"no confident direction over ~{hours_s}h (median inside its "
                    f"own band, ratio {ratio:.2f} < {min_conf_ratio:.2f})")
        elif med > 0:
            note = f"sees continuation over ~{hours_s}h"
        else:
            note = f"expects the move to FADE within ~{hours_s}h"
        return (
            "TimesFM-3 forecast (shadow signal, longer 5m context than Chronos — "
            f"cross-check the two, not obey either single one):\n"
            f"  - Path-average over the next ~{hours_s}h: {med:+.2f}%{span}{tail_s} — "
            + note + "."
        )
    except Exception as e:
        logger.debug(f"[research] timesfm block failed for {coin}: {e}")
        return ""


def _tirex_block(coin: str) -> str:
    """TiRex forward forecast for the AI prompt, when `tirex_signal.in_prompt`
    is true (2026-09-17 mirror of _chronos_block; ships DISABLED — added so a
    chronos->tirex prompt swap is a config flip once C.11/C.13 accruals settle).

    Same determinism contract as the other blocks: sync cache-first read
    (shares the 300s per-coin cache with the loop fire and the gate-side
    read), '' on disabled / failed / absent so the prompt shape is unchanged.

    Rendered lean like the timesfm block (headline median + band + early
    tail) rather than carrying chronos' decay-warning prose: offline eval
    (scratch/eval/moirai_eval, n=2,688) says tirex's edge over chronos is on
    MAGNITUDE calibration and TAIL discrimination (@3% stop-out AUC 0.898 vs
    0.863), while its direction call is a coinflip like everyone else's — so
    the block deliberately frames the tail as the informative read and says
    no-confident-direction below the same min_conf_ratio floor (HEMI-replay
    semantics; fail-safe zero).
    """
    try:
        cfg = read_agent_config().get("tirex_signal", {})
        if not cfg.get("in_prompt", False) or not cfg.get("enabled", False):
            return ""
        from hermes_trader.agents import tirex_signal as _tx
        sig = _tx.get_tirex_signal_sync(coin, "long")
        if sig is None or sig.error or sig.median_pct is None:
            return ""
        med = sig.median_pct
        span = ""
        if sig.q_low is not None and sig.q_high is not None and sig.context_last:
            lo_pct = (sig.q_low - sig.context_last) / sig.context_last * 100
            hi_pct = (sig.q_high - sig.context_last) / sig.context_last * 100
            span = f", p10 avg {lo_pct:+.1f}% / p90 avg {hi_pct:+.1f}%"
        tail_bits = []
        _tail_steps = 6
        p10p = sig.q10_path_pct or []
        p90p = sig.q90_path_pct or []
        if p10p[:_tail_steps]:
            tail_bits.append(f"p10 min {min(p10p[:_tail_steps]):+.1f}%")
        if p90p[:_tail_steps]:
            tail_bits.append(f"p90 max {max(p90p[:_tail_steps]):+.1f}%")
        tail_s = f"; early tail, first {_tail_steps * 5}m: {' / '.join(tail_bits)}" if tail_bits else ""
        hours = sig.horizon * 5 / 60
        hours_s = f"{hours:.0f}" if hours == int(hours) else f"{hours:g}"
        min_conf_ratio = _tx.resolve_min_conf_ratio(cfg)
        ratio = _tx.confidence_ratio(sig)
        if ratio < min_conf_ratio:
            note = (f"no confident direction over ~{hours_s}h (median inside its "
                    f"own band, ratio {ratio:.2f} < {min_conf_ratio:.2f})")
        elif med > 0:
            note = f"sees continuation over ~{hours_s}h"
        else:
            note = f"expects the move to FADE within ~{hours_s}h"
        return (
            "TiRex forecast (shadow signal, extreme-value-trained forecaster — "
            "its tails are its strongest read; direction is weak for all these "
            "models, so weigh the tail against stop-risk rather than as a sign call):\n"
            f"  - Path-average over the next ~{hours_s}h: {med:+.2f}%{span}{tail_s} — "
            + note + "."
        )
    except Exception as e:
        logger.debug(f"[research] tirex block failed for {coin}: {e}")
        return ""


def _build_user_message(
    coin: str,
    perception: Dict[str, Any],
    tf1h: Dict[str, Any],
    tf4h: Dict[str, Any],
    tf1d: Dict[str, Any],
    funding_rate: str,
    news: str,
    equity: float,
    open_positions: List[Dict[str, Any]] | None,
    mode: str,
    dex_equity: Dict[str, float] | None = None,
    recent_candles: List[Candle] | None = None,
    prior_block: str = "",
    rvol_block: str = "",
) -> str:
    """Build the user message passed to the LLM."""
    # rsi_divergence.shadow_mode / mfi_extreme.shadow_mode / band_snapback.shadow_mode:
    # the fired hit must NOT reach the prompt (shadow accrual is log-only; the
    # trigger still rides in perception for the counterfactual join). The
    # band-snapback umbrella's design statement is "NO band-snapback text
    # reaches the prompt" — the dedicated block below honors that, but the
    # trigger-summary line used to leak the fired hit's name+reason whenever
    # ANOTHER trigger surfaced the coin. Same filter closes that side door.
    # Fail-open: a config-read error keeps the historical render.
    try:
        _cfg = read_agent_config() or {}
        _rd_shadow = bool((_cfg.get("rsi_divergence") or {}).get("shadow_mode"))
        _mf_shadow = bool((_cfg.get("mfi_extreme") or {}).get("shadow_mode"))
        _bs_shadow = bool((_cfg.get("band_snapback") or {}).get("shadow_mode"))
        _vw_cfg_sum = _cfg.get("vwap_deviation") or {}
        _vw_shadow = bool(_vw_cfg_sum.get("shadow_mode"))
    except Exception:
        _rd_shadow = _mf_shadow = _bs_shadow = _vw_shadow = False
        _vw_cfg_sum = {}
    from hermes_trader.indicators.triggers import vwap_hit_promoted as _vw_promoted
    trigger_summary = (
        ", ".join(
            f"{t['name']}: {t['reason']}"
            for t in perception.get("triggers", [])
            if t.get("fired")
            and not (_rd_shadow and t.get("name") == "rsiDivergence")
            and not (_mf_shadow and t.get("name") == "mfiExtreme")
            and not (_bs_shadow and t.get("name") == "bandSnapback")
            and not (_vw_shadow and t.get("name") == "vwapDeviation"
                     and not _vw_promoted(_vw_cfg_sum, t))
        )
        or "no triggers fired"
    )

    # 1h-structure block — accumulation/exhaustion patterns the multi-tf
    # indicator blocks miss. Surfaced as an ENTRY-TIMING signal to be combined
    # WITH the 4h/1d trend, not as a reason to trade against it: in an uptrend a
    # 1h accumulation times a long pullback-entry; in a downtrend a 1h bounce is
    # a short entry (sell the rip), NOT a counter-trend dip-buy.
    _slow_burn_names = {"volumeBuildup1h", "trendFlip1h", "higherLows1h"}
    slow_burn_hits = [
        t for t in perception.get("triggers", [])
        if t.get("name") in _slow_burn_names and t.get("fired")
    ]
    if slow_burn_hits:
        structure_lines = ["1h structure signals (entry-timing — apply IN the 4h/1d trend direction):"]
        for t in slow_burn_hits:
            structure_lines.append(f"  - {t['name']}: {t['reason']}")
        structure_lines.append(
            "Use these to time the entry, not to pick the side. If 4h/1d are bullish, this is a "
            "long pullback-entry; if 4h/1d are bearish, a 1h pop is a SHORT entry (sell the rip) — "
            "do not buy the dip into a downtrend."
        )
        structure_block = "\n".join(structure_lines)
    else:
        structure_block = "1h structure signals: none fired (no accumulation / breakout setup detected)"

    # Whale-accumulation block: oi_funding_anomaly flag (deep-negative funding +
    # flat price + high OI = whales loading while retail shorts). When present
    # this is a strong LONG-bias signal — don't fight it.
    whale = perception.get("whale_signal")
    if whale:
        whale_block = (
            "Whale accumulation flag (oi_funding_anomaly):\n"
            f"  - funding rate: {whale.get('funding_rate', 0):.6f} (deeply negative = retail shorting)\n"
            f"  - 24h price change: {whale.get('price_24h_change_pct', 0):+.2f}% (relatively flat)\n"
            f"  - open interest: ${whale.get('oi', 0):,.0f}\n"
            f"  - confidence: {whale.get('confidence', 0):.2f}\n"
            "Interpretation: smart money is building long positions while retail pays them "
            "to short. When the shorts cover, price tends to squeeze UP. Bias LONG unless "
            "structure is overwhelmingly bearish."
        )
    else:
        whale_block = "Whale accumulation flag: not flagged for this coin"

    # Band-snapback block: a wick that poked OUT of the moving-average band and
    # snapped back INSIDE is a short-term mean-reversion tell in the fade
    # direction (lower poke + snap-back → LONG; upper poke + snap-back → SHORT).
    # Fed to the LLM as a VETO/weighting signal, NOT a standalone entry. The OOS
    # edge is better than a coin flip but fee-thin, so its value is telling the
    # LLM "don't fade this" — the observed dumb-trade mode was the LLM taking
    # the OPPOSITE side of an imminent snapback (e.g. shorting into a lower-poke
    # snap-back that wanted to revert long).
    #
    # Wording note (XMR 2026-09-03 01:26 UTC): the fired branch used to label
    # this a "COUNTER-signal" without ever saying counter to WHAT. Both live
    # models read it as "counter to the TREND" — the XMR scan had the snapback
    # pointing LONG into a bullish 1d (confluence) and the primary PASSed,
    # reasoning that "fading a strong 4h/1d move based on a thin mean-reversion
    # signal is dangerous". A trader.log scan of AI reasoning blocks found the
    # same inversion recurring, always LONG-snapback blocks turned into PASSes.
    # So: the word "counter" is gone from the fired branch, it names the side
    # positively, it states the relationship to the 4h/1d trend explicitly, and
    # it says plainly that the signal is never itself a reason to stand aside.
    _snap = next((t for t in perception.get("triggers", [])
                  if t.get("name") == "bandSnapback"), None)
    # band_snapback.shadow_mode (P3 umbrella): the trigger keeps computing,
    # but NO band-snapback text reaches the prompt — the fired branch, the
    # "band trending" context branch, and the "not present" fallback all
    # render empty (the trigger hit is still in perception's `triggers`; this
    # only stops the rendering). The counterfactual record is the
    # [band-snapback][SHADOW] accrual line the perception scan emits at the
    # trigger-hit site.
    _bs_shadow = bool(
        (read_agent_config().get("band_snapback") or {}).get("shadow_mode", False)
    )
    if _bs_shadow:
        snapback_block = ""
    elif _snap and _snap.get("fired"):
        _snap_reason = _snap.get("reason", "")
        _snap_side = "SHORT" if _snap_reason.startswith("short") else "LONG"
        _opp = "LONG" if _snap_side == "SHORT" else "SHORT"
        # Relationship to the higher-timeframe trend, computed from the SAME
        # snapshots the prompt's indicator blocks render (EMA8 vs EMA21 per TF),
        # so this line cannot contradict what the model reads elsewhere.
        def _tf_dir(snap: Dict[str, Any]) -> str:
            if not snap or snap.get("ema8") is None or snap.get("ema21") is None:
                return "unknown"
            return "bullish" if snap["ema8"] > snap["ema21"] else "bearish"

        _d4, _d1d = _tf_dir(tf4h), _tf_dir(tf1d)
        if _d4 == "unknown" and _d1d == "unknown":
            _htf_line = (
                f"\n  - Higher-TF context: unknown from the indicators above, so treat "
                f"{_snap_side} agreement as untested rather than as confluence."
            )
        else:
            _agree = [d for d in (_d4, _d1d) if d != "unknown"
                      and (d == "bullish") == (_snap_side == "LONG")]
            _n_known = len([d for d in (_d4, _d1d) if d != "unknown"])
            if len(_agree) == _n_known:
                _htf_line = (
                    f"\n  - Higher-TF context: 4h {_d4}, 1d {_d1d} — the {_snap_side} "
                    "snapback points WITH this trend. That is confluence, not a reason to "
                    "stand aside."
                )
            elif not _agree:
                _htf_line = (
                    f"\n  - Higher-TF context: 4h {_d4}, 1d {_d1d} — the {_snap_side} "
                    "snapback runs AGAINST the higher-timeframe trend, so it is a local "
                    "bounce/pullback scalp, not a trend entry. Size and stops accordingly."
                )
            else:
                _htf_line = (
                    f"\n  - Higher-TF context: 4h {_d4}, 1d {_d1d} — mixed, so the "
                    f"{_snap_side} snapback agrees with one of them and not the other."
                )
        snapback_block = (
            "Band snapback signal (fired — a wick poked OUT of the moving-average band and "
            f"snapped back INSIDE, setting up a short-term mean reversion to the "
            f"{_snap_side} side):"
            f"\n  - signal detail: {_snap_reason}"
            f"{_htf_line}"
            f"\n  - Read it as: the band's short-term reversion pressure currently "
            f"points {_snap_side}. It is a fresh bounce off a "
            f"{_snap_side.lower()} extreme — NOT a stretched, late-chase entry and NOT a "
            "signal that the move is topping out."
            f"\n  - How to weight it: use it to raise confidence in {_snap_side} and lower "
            f"confidence in {_opp}. It is not an entry by itself: its out-of-sample edge "
            "beats a coin flip but is thin once fees are in, so a trade on this alone "
            f"usually does not pay. A {_snap_side} thesis gains from it; a {_opp} thesis "
            f"must clear a higher bar, because {_opp} here means fading an imminent mean "
            "reversion — require explicit independent evidence (higher-TF trend, funding, "
            "structure, news) before taking that side."
            "\n  - What this signal is NOT: it is not an instruction to PASS, and it is not "
            "counter-trend evidence against your own thesis. If your analysis points "
            f"{_snap_side}, this signal supports it — do not turn it into a reason to stand "
            "aside. Whether the entry is too late or too extended is a separate call, made "
            "from the price, the 1h structure and any breakout/burst triggers, not from this "
            "signal."
        )
    elif _snap and _snap.get("reason", "").startswith("band trending"):
        # Silent but INFORMATIVE: the drift gate vetoes the fade because the
        # band itself is trending. That direction is exactly the context the
        # LLM was missing on counter-trend scalps (e.g. shorting while the 1h
        # band is drifting up with price at its lower edge = fading the local
        # structure). Render it as band context, with the counter-trend
        # caveat, rather than hiding it behind "not present".
        _bt_reason = _snap.get("reason", "")
        _bt_dir = "DOWN" if "trending DOWN" in _bt_reason else "UP"
        # Extension check: when price sits BEYOND the drift-side edge (drift
        # UP -> above the upper edge; drift DOWN -> below the lower edge), a
        # new entry ON the drift side is chasing the extension — the nearest
        # mean-reversion move is back toward the band, i.e. AGAINST the entry.
        # This is the mirror image of the counter-trend case above (LIT short
        # into an up-drift) and the exact shape of the 00:46 TRUMP short,
        # which entered with the 1h band 5.1% below its lower edge.
        _ext_note = ""
        _m = re.search(
            r"px\s+([+-]?\d+(?:\.\d+)?)%\s+vs\s+upper\s+edge,\s+"
            r"([+-]?\d+(?:\.\d+)?)%\s+vs\s+lower\s+edge", _bt_reason)
        if _m:
            _px_up, _px_lo = float(_m.group(1)), float(_m.group(2))
            _ext = _px_up if _bt_dir == "UP" else -_px_lo
            if _ext >= 1.0:
                _ext_note = (
                    f"\n  - price is {_ext:.1f}% BEYOND the drift-side band edge "
                    f"(extended past the band in the {_bt_dir} direction): a NEW entry on the "
                    f"{_bt_dir} side here is chasing the extension — the nearest "
                    "short-term reversion is back toward the band, so your stop sits on the "
                    "far side of the move. Prefer waiting for a retest of the band edge, or "
                    "demand a tight invalidation and favorable risk/reward (stop distance < "
                    "target distance).")
            else:
                # Counter-trend edge breach — the MIRROR the drift-side note above misses:
                # the band drifts ONE way but price is extended PAST THE OPPOSITE edge.
                # 1h band trending DOWN while price bounces ABOVE the upper edge = top of a
                # relief rally (the start-of-downswing shape); a NEW LONG there buys the
                # bounce against the drift and the nearest reversion is back toward the band.
                # This is the exact shape the LLM misread as "drift already priced in →
                # continuation" (GRASS long 2026-08-26 19:07, 1h band DOWN 6.4% drift,
                # price +6.6% above the upper edge).
                if _bt_dir == "DOWN":
                    _ct_ext, _ct_edge = _px_up, "upper"
                    _ct_shape = ("a bounce ABOVE the upper edge of a DOWN-drifting band — the "
                                 "TOP of a relief rally, i.e. the START of the next down-swing, "
                                 "not a continuation")
                else:  # UP
                    _ct_ext, _ct_edge = -_px_lo, "lower"
                    _ct_shape = ("a dip BELOW the lower edge of an UP-drifting band — the BOTTOM "
                                 "of a pullback, i.e. the START of the next up-swing, not a "
                                 "continuation")
                if _ct_ext >= 1.0:
                    _ext_note = (
                        f"\n  - price is {_ct_ext:.1f}% beyond the {_ct_edge} band edge on the "
                        f"OPPOSITE side of the band's drift: this is {_ct_shape}. The band's "
                        f"short-term mean-reversion pressure points WITH the drift ({_bt_dir}), "
                        f"so price extended past the {_ct_edge} edge is OVER-extended against the "
                        "drift — a NEW entry on the bounce/dip side here is a counter-trend chase, "
                        "and the nearest short-term reversion is back toward the band, AGAINST the "
                        "entry (stop on the far side of the move). Do NOT read 'price is beyond "
                        f"the band edge' as 'the {_bt_dir} drift is already priced in → "
                        "continuation': it is the OPPOSITE — the extension is what reverts. Prefer "
                        "waiting for the band edge to be reclaimed or a confirmed retest, or "
                        "explicit reversal structure, before entering the opposite-of-drift side.")
        snapback_block = (
            "Band context (no snapback signal — the MA band is TRENDING, which vetoes the "
            f"short-term fade in this band's timeframe):\n  - band state: {_bt_reason}"
            f"\n  - How to read it: the band's short-term mean-reversion pressure points WITH "
            f"the drift ({_bt_dir}). A position AGAINST that direction is a counter-trend "
            "scalp — it only pays when the local wick wins against an established drift. If "
            "your thesis is on the opposite side of the band drift, require explicit "
            "counter-trend evidence (reversal structure, divergence, funding flip) and a tight "
            "stop; do not size it as a trend position."
            + _ext_note
        )
    else:
        _other_reason = (_snap or {}).get("reason", "")
        snapback_block = (
            "Band snapback signal: not present (no wick poke-and-snapback in the band "
            f"timeframe this scan"
            + (f"; band state: {_other_reason}" if _other_reason and _other_reason != "flat" else "")
            + ")"
        )

    # VWAP-deviation block — gated on BOTH flags: renders only when
    # vwap_deviation.enabled AND NOT vwap_deviation.shadow_mode. Either flag
    # off/shadowed -> empty section -> prompt byte-identical to pre-feature
    # (the section is only spliced in when non-empty, so no stray newlines).
    # Under shadow_mode the fired hit is ALSO already filtered from the
    # trigger summary above; this block is the second surface the umbrella
    # must suppress (the band-snapback leak lesson: every render site needs
    # the flag). Fail-open on config-read error keeps the historical render.
    try:
        _vw_cfg = read_agent_config().get("vwap_deviation") or {}
    except Exception:
        _vw_cfg = {}
    _vw = next((t for t in perception.get("triggers", [])
                if t.get("name") == "vwapDeviation"), None)
    _vw_live = (bool(_vw_cfg.get("enabled"))
                and (not bool(_vw_cfg.get("shadow_mode"))
                     or _vw_promoted(_vw_cfg, _vw)))
    vwap_block = ""
    if _vw_live and _vw is not None:
        _vw_reason = _vw.get("reason", "")

        def _vw_tf_dir(snap: Dict[str, Any]) -> str:
            if not snap or snap.get("ema8") is None or snap.get("ema21") is None:
                return "unknown"
            return "bullish" if snap["ema8"] > snap["ema21"] else "bearish"

        if _vw.get("fired"):
            _vw_side = "SHORT" if _vw_reason.startswith("short") else "LONG"
            _vw_opp = "LONG" if _vw_side == "SHORT" else "SHORT"
            _vw_d4, _vw_d1d = _vw_tf_dir(tf4h), _vw_tf_dir(tf1d)
            if _vw_d4 == "unknown" and _vw_d1d == "unknown":
                _vw_htf = ("\n  - Higher-TF context: unknown from the indicators above, so "
                           f"treat {_vw_side} agreement as untested rather than confluence.")
            else:
                _vw_agree = [d for d in (_vw_d4, _vw_d1d) if d != "unknown"
                             and (d == "bullish") == (_vw_side == "LONG")]
                _vw_known = len([d for d in (_vw_d4, _vw_d1d) if d != "unknown"])
                if len(_vw_agree) == _vw_known:
                    _vw_htf = (f"\n  - Higher-TF context: 4h {_vw_d4}, 1d {_vw_d1d} — the "
                               f"{_vw_side} vwap signal points WITH this trend: confluence.")
                elif not _vw_agree:
                    _vw_htf = (f"\n  - Higher-TF context: 4h {_vw_d4}, 1d {_vw_d1d} — the "
                               f"{_vw_side} vwap signal runs AGAINST the higher-timeframe "
                               "trend, so it is a local reversion scalp at best, not a "
                               "trend entry.")
                else:
                    _vw_htf = (f"\n  - Higher-TF context: 4h {_vw_d4}, 1d {_vw_d1d} — mixed, "
                               f"so the {_vw_side} vwap signal agrees with one, not the other.")
            vwap_block = (
                "VWAP deviation signal (fired — distance from the volume-weighted COST "
                "BASIS of the current swing, measured from the VWAP anchored at the last "
                "confirmed swing extreme):"
                f"\n  - signal detail: {_vw_reason}"
                "\n  - What it measures: VWAP = the average price the swing's dollars "
                "actually traded at (high-volume zones pin the level, thin wicks barely "
                "move it); σ = that window's volume-weighted dispersion. \"fade\" = price "
                "is stretched that far from cost basis — the nearest short-term reversion "
                "is back toward VWAP. \"retest\" = an orderly pullback back TO cost basis "
                "mid-move — the move's average traders are being re-tested, a "
                "trend-aligned entry shape."
                f"{_vw_htf}"
                f"\n  - How to weight it: an ENTRY-TIMING observation about extension vs "
                f"cost basis, not a direction call and never itself a reason to PASS. A "
                f"{_vw_side} thesis at this distance gains from it; the {_vw_opp} side "
                f"here is entering INTO the stretch — require independent evidence "
                "(higher-TF trend, structure, funding, news) for that side."
            )
        elif _vw_reason.startswith("no setup"):
            # Silent but INFORMATIVE: the trigger computed and found no shape,
            # but the CURRENT z is entry context even when the coin surfaced
            # via another trigger — how extended is price from cost basis?
            vwap_block = (
                "VWAP context (no deviation signal this scan — current distance from the "
                "swing-anchored VWAP):"
                f"\n  - {_vw_reason}"
                "\n  - Read it as: how extended price currently is from the swing's "
                "average cost basis, in volume-weighted σ. Entering on the side that "
                "ADDS to that stretch means entering into extension; the nearer "
                "reversion target is back toward that VWAP."
            )

    def _fmt_px(p: float) -> str:
        """Adaptive precision so sub-cent coins (HMSTR at $0.000173 etc.) don't
        all read as '0.0002' to the LLM. Without this the AI returned identical
        entry/sl/tp on cheap coins because the prompt rounded them to the same
        4-decimal value."""
        if p == 0:
            return "0"
        ap = abs(p)
        if ap >= 1:
            return f"{p:.4f}"
        if ap >= 0.01:
            return f"{p:.5f}"
        if ap >= 0.0001:
            return f"{p:.6f}"
        return f"{p:.8f}"

    def _indicator_block(label: str, snap: Dict[str, Any]) -> str:
        parts = []
        if snap.get("ema8") is not None and snap.get("ema21") is not None:
            direction = "bullish" if snap["ema8"] > snap["ema21"] else "bearish"
            parts.append(
                f"EMA8={_fmt_px(snap['ema8'])}, EMA21={_fmt_px(snap['ema21'])}, {direction}"
            )
        if snap.get("slope_up") is not None:
            parts.append(f"EMA8 slope: {'rising' if snap['slope_up'] else 'falling'}")
        if snap.get("rsi14") is not None:
            parts.append(f"RSI(14)={snap['rsi14']:.1f}")
        if snap.get("atr14") is not None:
            parts.append(f"ATR(14)={_fmt_px(snap['atr14'])}")
        if snap.get("adx14") is not None:
            parts.append(f"ADX(14)={snap['adx14']:.1f}")
        parts.append(f"last close={_fmt_px(snap.get('last_close', 0))}")
        return f"{label}: {' | '.join(parts)}"

    # Only the coins/sides we already hold — purely so the LLM doesn't
    # double-trade a coin or can CLOSE one. Deliberately NO dollar sizes:
    # account notional/leverage must not influence the verdict (sizing and
    # every risk cap live in the execution gates, not here).
    #
    # Scoping matters (2026-09-04, HOOD): the block was researched FOR
    # xyz:HOOD while listing "ADA long, PURR long", and the duelist (Qwen2.5-
    # Coder-7B) read "CLOSE only if structure flipped" GLOBALLY — its
    # reasoning was about ADA/PURR and it returned {"verdict":"CLOSE"} for
    # the coin it holds nothing in. State the scope on the instruction line
    # itself so a small model doesn't generalize it to the listed positions.
    #
    # Age + best-move context (2026-09-05 dead-bag review): the six
    # stale_flat_timeout losers each got exactly one forced close-check and
    # every one PASSed — the prompt said only "Open positions: X long", so
    # "is this going anywhere?" was unanswerable. Annotate each held coin
    # with its age and peak excursion from the live DSL tracker (NO dollar
    # sizes — that rule stands; test_build_user_message_omits_account_equity_and_notional).
    # 30min direction window over CLOSED 5m candles: 6 bars = 30min. 5m is
    # the granularity the DSL peak ratchet is built around (a wick inside one
    # 5m candle is the shape that bit GRASS 2026-08-26); a 1h window is too
    # coarse — on VVV 2026-09-11 a 3h bar window would have read "−3.5%"
    # while the position was already at its full −5% stop inside the CURRENT
    # bar. A 30min recovery off the low is exactly the "trending back in the
    # right direction" tell a down-but-recovering close-check needs.
    _HELD_DIR_WINDOW_BARS = 6
    # RECOVERING flag window (ADA 2026-09-19): the close-check saw "best move
    # +0.2%, now +0.2%" and called a dead bag while price was climbing off a
    # −1.3% low — the one-way peak ratchet HID the dip, and the model closed a
    # green recovering position that ran +1.1% after. Compute the trough from
    # 5m closes over 2h (typical hold is 30–100min), truncated at entry when
    # the hold is shorter, and surface it explicitly so "never ran" has to
    # fight a RECOVERING token instead of a ratcheted number.
    _HELD_RECOVERY_WINDOW_BARS = 24

    def _held_annotation(h_coin: str, h_side: str) -> str:
        try:
            from hermes_trader.agents import dsl_exit as _dslx
            trk = _dslx._active_positions.get(f"{h_coin}_{h_side}")
            if trk is None or not trk.entry_px:
                return ""
            age_min = int((time.time() - trk.entry_time) // 60)
            e = float(trk.entry_px)
            pk = float(trk.peak_px or e)
            peak_pct = ((pk - e) / e * 100) if h_side == "long" else ((e - pk) / e * 100)
            # CURRENT PnL, not just the one-way peak. The peak ("best move")
            # ratchets only in the favourable direction, so a monotonically
            # sinking long shows "best move +0.2%" for the whole hold while the
            # real PnL goes to −5% — 2026-09-11 VVV long: every scan read it as
            # "barely moved" and the model never CLOSEd it. Surface the live
            # mark so "best move" and "now" can't both look fine on a loser.
            mark = getattr(trk, "last_mark_px", None)
            if not mark or float(mark) <= 0:
                try:
                    from hermes_trader.client.exchange import get_hl_price
                    mark = get_hl_price(h_coin)
                except Exception:
                    mark = 0.0
            now = ""
            if mark and float(mark) > 0:
                m = float(mark)
                now_pct = ((m - e) / e * 100) if h_side == "long" else ((e - m) / e * 100)
                now = f", now {now_pct:+.1f}% vs entry"
                # Pullback off the best move (how far below peak a long is —
                # the retrace the DSL exit itself arms on).
                peak_pull = ((m - pk) / e * 100) if h_side == "long" else ((pk - m) / e * 100)
                now += f", {peak_pull:+.1f}% vs best move"
                # Direction of travel over the last 30min from CLOSED 5m bars.
                # fetch_hl_candles' last row is the still-forming candle (the
                # fast-exit path exists precisely because of that), so drop it;
                # require the full window or omit the field — a partial window
                # would render as if the coin barely moved.
                try:
                    _n = max(_HELD_DIR_WINDOW_BARS,
                             _HELD_RECOVERY_WINDOW_BARS) + 1
                    c5m = fetch_hl_candles(h_coin, "5m", _n)
                    c5m = c5m[:-1] if len(c5m) > 1 else []
                    if len(c5m) >= _HELD_DIR_WINDOW_BARS:
                        c0 = candle_val(c5m[-_HELD_DIR_WINDOW_BARS], "c")
                        c1 = candle_val(c5m[-1], "c")
                        if c0 and c1:
                            # TRUMP 2026-09-23 vocabulary failure: a raw price
                            # delta is direction-ambiguous for shorts — the
                            # model read "price −0.2% over 30min" (favouring
                            # its short) as "recovering against our short".
                            # Tag every move with whether it helps or hurts
                            # THE POSITION, not what price did.
                            _d30 = (c1 - c0) / c0 * 100
                            _helps = (_d30 < 0) if h_side == "short" else (_d30 > 0)
                            _tag = ("MOVING IN FAVOUR of your " + h_side.upper()
                                    if _helps else
                                    "moving against your " + h_side.upper())
                            now += (f", price {_d30:+.1f}% over last 30min "
                                    f"(5m bars) — {_tag}")
                    # RECOVERING flag: trough of the CLOSED 5m closes vs entry,
                    # truncated at entry time so a pre-entry dip can't inflate
                    # it. Fires when the position has clawed back at least
                    # max(0.5%, half the dip) off that trough — deliberately
                    # above the ±0.3% fee-noise band.
                    try:
                        bars_in_hold = max(2, age_min // 5)
                        window = c5m[-min(len(c5m),
                                          _HELD_RECOVERY_WINDOW_BARS,
                                          bars_in_hold):]
                        pcts = []
                        for _c in window:
                            cv = candle_val(_c, "c")
                            if cv:
                                pcts.append(((cv - e) / e * 100)
                                            if h_side == "long"
                                            else ((e - cv) / e * 100))
                        if len(pcts) >= 2:
                            trough_pct = min(pcts)
                            recovery = now_pct - trough_pct
                            if recovery >= max(0.5, 0.5 * abs(min(trough_pct, 0.0))):
                                # TRUMP 2026-09-23: for a SHORT this flag sat
                                # next to its own "recovering against us"
                                # reasoning — "recovering"/"low" are price-
                                # centric words and the model read them as
                                # price rising (bad for the short) when the
                                # flag means the POSITION is clawing back off
                                # its worst mark. Spell out the referent: it
                                # is the position recovering, in favour of
                                # the side held.
                                now += (f", POSITION RECOVERING +{recovery:.1f}% "
                                        f"off its worst mark ({trough_pct:+.1f}%), "
                                        f"moving in favour of your {h_side.upper()}")
                    except Exception:
                        pass
                except Exception:
                    pass
            return f" (held {age_min}min, best move since entry {peak_pct:+.1f}%{now})"
        except Exception:
            return ""

    # Scope the held-coin block to THE COIN BEING RESEARCHED (2026-09-11 VVV
    # review): listing the whole book in every coin's prompt made the 4B model
    # repeatedly try to CLOSE the held position BY BORROWING another coin's
    # verdict — 13 foreign-coin CLOSE calls during the VVV hold, all nulled by
    # the parse_verdict guard (research.py CLOSE guard) and all carrying the
    # peak-only annotation ("+0.2% over 71 minutes, no adverse structure").
    # The no-pyramid / no-re-entry protection does not depend on this: the
    # execution gate blocks any re-entry on a held coin regardless of what the
    # LLM sees (risk_gates "already holding"). A CLOSE verdict can only ever
    # apply to the candidate coin itself, so only the candidate's own position
    # state is shown.
    # Book-slot context (Option A, 2026-09-23): the close-check's dominant
    # stated rationale is "free the capital for stronger setups" — but a
    # ledger reconstruction (49 ai_close exits) showed 33 of them fired at
    # 1/5 slots used and EVERY one of the last 12 closed the ONLY open
    # position. The scarcity claim was unfalsifiable from the prompt, so the
    # model recited it as boilerplate. Surface `slots used / max_concurrent`
    # as a bare fact (NO steering text, NO dollar figures — slots ride on
    # the max_concurrent entry gate and are not an exposure figure like
    # equity/notional, which stay banned). The model weighs it itself.
    # open_positions is None when the account-state fetch failed -> omit
    # (rendering "0 of N free" on a flaky read would fake scarcity info).
    def _slots_note() -> str:
        if open_positions is None:
            return ""
        try:
            cap = int(read_agent_config().get("max_concurrent", 3))
        except Exception:
            cap = 0
        used = len(open_positions)
        if cap <= 0:
            return ""
        return f" (book slots: {used} of {cap} in use)"

    _mine = [p for p in (open_positions or []) if p["coin"] == coin]
    if _mine:
        position_block = (
            f"Open position on {coin} (you hold it — do not re-enter; a CLOSE "
            f"verdict here exits YOUR {coin} position). Weigh the PnL "
            f"annotation: 'best move' is the one-way peak since entry and it "
            f"HIDES dips — a position can show a small best move after a deep "
            f"recovery; 'now' is the live PnL vs entry and is the number that "
            f"matters for a CLOSE call. CLOSE on PnL grounds requires BOTH: "
            f"'now' at or below entry AND no recovery — the 30min price field "
            f"flat or falling, no POSITION RECOVERING flag, and the structure "
            f"shows nothing back in the position's favour. Every move figure "
            f"is stated for THE POSITION, not for price: 'RECOVERING' / "
            f"'MOVING IN FAVOUR' means the position is clawing back toward "
            f"profit — for a SHORT that happens while PRICE FALLS, so do not "
            f"read it as price moving against you. A position flagged "
            f"POSITION RECOVERING (climbing off its worst mark toward/beyond "
            f"entry) is "
            f"NOT a close candidate on PnL grounds even if 'best move' looks "
            f"small — and a position with 'now' above entry is not a loser; "
            f"do not close a green, recovering position by relabelling it a "
            f"dead bag. 'Barely moved' means no progress, it "
            f"does not mean it is safe to keep: a young position that has made "
            f"no progress in either direction should be CLOSED rather than "
            f"nursed. Your own technicals for {coin} still matter as usual. "
            + ", ".join(f"{p['coin']} {p['side']}{_held_annotation(p['coin'], p['side'])}"
                        for p in _mine)
            + _slots_note()
        )
    else:
        position_block = (
            f"Open position on {coin}: none (you do not hold {coin}; the "
            f"account may hold OTHER positions — that is not your concern, "
            f"you only judge {coin}'s own setup)"
            + _slots_note()
        )

    # Raw recent price action so the LLM can read candlestick/chart patterns
    # directly (shooting star, hammer, engulfing, flags) — the indicator blocks
    # above summarize away the candle bodies/wicks that patterns live in.
    def _ohlc_block(candles: List[Candle] | None, n: int = 12) -> str:
        if not candles:
            return ""
        rows = []
        for i, c in enumerate(candles[-n:]):
            idx = -(len(candles[-n:]) - i)  # ... -2, -1 (newest = last closed)
            o, h, l, cl = (candle_val(c, k) for k in ("o", "h", "l", "c"))
            rows.append(f"  [{idx:>3}] O={_fmt_px(o)} H={_fmt_px(h)} L={_fmt_px(l)} C={_fmt_px(cl)}")
        return ("Recent 1h candles (oldest→newest, last row = most recent closed bar):\n"
                + "\n".join(rows))

    ohlc_block = _ohlc_block(recent_candles)

    # RVOL entry context (statefulness experiment, WATCHLIST B.32 item 2):
    # "how loud was the entry candle" — computed by research() via
    # rvol_context.build_rvol_block; "" when disabled (the default), so the
    # prompt is byte-identical to pre-feature unless the flag is flipped.
    rvol_section = f"{rvol_block}\n\n" if rvol_block else ""

    # Prior-call context (statefulness experiment, WATCHLIST B.32 item 1):
    # "your recent history on this coin" — computed by research() via
    # prior_calls.build_prior_call_block; "" when disabled (the default), so
    # the prompt is byte-identical to pre-feature unless the flag is flipped.
    prior_section = f"{prior_block}\n\n" if prior_block else ""

    # Same empty-safe splice for the VWAP block: vwap_block == "" (disabled,
    # shadowed, or no opinion) -> zero added bytes, no stray newlines.
    vwap_section = f"{vwap_block}\n\n" if vwap_block else ""

    return prior_section + rvol_section + "\n".join([
        f"Candidate: {coin} (HL {perception.get('type', 'perp')}-PERP)",
        f"Current mid: ${_fmt_px(perception.get('mid', 0))}",
        f"Perception score: {perception.get('composite_score', 0)}/100",
        f"Fired triggers: {trigger_summary}",
        "",
        "Market context (multi-timeframe):",
        _indicator_block("1h", tf1h),
        _indicator_block("4h", tf4h),
        _indicator_block("1d", tf1d),
        "",
        ohlc_block,
        "" if ohlc_block else "",
        structure_block,
        "",
        whale_block,
        "",
        snapback_block,
        "",
        vwap_section + _signals_block(coin),
        _chronos_block(coin),
        _timesfm_block(coin),
        _tirex_block(coin),
        "",
        f"Funding rate (latest): {funding_rate}",
        f"Recent news: {news}",
        position_block,
        "",
        f"Mode: {mode} — {'your verdict will execute against real funds' if mode == 'LIVE' else 'analysis only, no execution'}",
        "",
        'Respond with 3-5 bullet points of reasoning, then output your decision as VALID JSON on the very last line:',
        '{"verdict":"PASS"|"LONG"|"SHORT"|"CLOSE"|"VETO","confidence":0.0-1.0,"side":"long"|"short"|"null","entryPx":number,"stopPx":number,"tpPx":number,"reasoning":"brief"}',
        "Nothing after the JSON.",
    ])


def _call_ai(
    system_prompt: str,
    user_message: str,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
) -> tuple:
    """Call the LLM API (runs the async client in a fresh event loop).

    Returns ``(text, server_ms)`` — the raw response text plus the model
    server's self-reported processing time (``server_processing_ms`` from the
    response's ``timings`` block; None when the backend doesn't send it) —
    with ``("", None)`` on any failure.

    Endpoint resolution: explicit args win, otherwise the primary LLM_* env
    vars. The duelist (duel_store.call_duelist) uses its own LLM_DUEL_* values
    through this same code path — the prompt is byte-identical, which is what
    makes the A/B verdicts comparable.
    """
    if api_key is None:
        api_key = os.environ.get("LLM_API_KEY", os.environ.get("OPENROUTER_API_KEY", ""))
    if base_url is None:
        base_url = os.environ.get("LLM_BASE_URL", "https://openrouter.ai/api/v1")
    if model is None:
        # Config `llm.model` → env (hot, no cache) — the model swap is a
        # same-inode config flip, not a recreate.
        model = effective_primary_model()

    if not api_key:
        logger.warning("[research] LLM_API_KEY not set — returning empty response")
        return "", None

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_async_do_call(api_key, base_url, model, system_prompt, user_message))
    except httpx.TimeoutException:
        # Single retry on timeout (2026-08-27, user-requested), matching the
        # duelist behavior. A ReadTimeout here means the server was still
        # generating at 120s — either a genuinely slow healthy call or a
        # runaway; one identical retry is the whole policy (same budget,
        # same params), bounded at ~2 x 120s. The second timeout falls
        # through to the generic handler below. This preserves the
        # historical contract — _call_ai never raises, a failed call yields
        # "" and parse_verdict's ai_down PASS — while converting a
        # transiently-busy server from a lost coin to a recovered one.
        logger.warning(
            f"[research] LLM call timed out after 120s — retrying once "
            f"(server may be mid-generation)"
        )
        try:
            return loop.run_until_complete(_async_do_call(api_key, base_url, model, system_prompt, user_message))
        except httpx.TimeoutException:
            logger.warning(
                f"[research] LLM call TIMED OUT on both attempts (~240s total) — "
                f"coin defaults to PASS ai_down this cycle"
            )
            return "", None
    except Exception as e:  # noqa: BLE001 — research worker must survive any LLM fault
        # Non-timeout fault (ConnectionError to a dead endpoint, bad JSON,
        # …). LOUD: previously these returned "" via the same silent path
        # as a 402 — a dead LLM endpoint looked like "no setups".
        logger.warning(f"[research] LLM call failed (non-fatal): {type(e).__name__}: {e}")
        return "", None
    finally:
        loop.close()


def _archive_record(
    *,
    coin: str,
    perception: Dict[str, Any],
    role: str,
    model: str,
    system_prompt: str | None,
    user_message: str | None,
    raw_response: str,
    parsed: Dict[str, Any],
    wall_ms: int = 0,
    server_ms: int | None = None,
    prior_block_injected: bool = False,
) -> Dict[str, Any]:
    """Assemble one prompt_log record (see prompt_log.py for the storage
    model). ``system_prompt``/``user_message`` None → role="duelist" reference
    row (its prompt is byte-identical to the primary's; analysis joins on
    perception_id). Field truncation guards keep a runaway response from
    bloating a chunk. NEVER raises — record_call has its own belt, but this
    runs inside the parse path."""
    try:
        cfg = prompt_log._cfg()

        def _clip(text: str | None, limit: int) -> str | None:
            if text is None:
                return None
            if len(text) > limit:
                return text[:limit] + "…[TRUNCATED]"
            return text

        return {
            "ts": int(time.time() * 1000),
            "coin": coin,
            "role": role,
            "model": model,
            "perception_id": perception.get("id", "unknown"),
            "mode": str(read_agent_config().get("mode", "OFF")),
            "mid": perception.get("mid"),
            "composite_score": perception.get("composite_score"),
            "prior_block_injected": prior_block_injected,
            "system_prompt": _clip(system_prompt, int(cfg.get("max_prompt_chars", 40000))),
            "user_message": _clip(user_message, int(cfg.get("max_prompt_chars", 40000))),
            "raw_response": _clip(raw_response, int(cfg.get("max_response_chars", 20000))),
            "parsed": {
                "verdict": parsed.get("verdict"),
                "confidence": parsed.get("confidence"),
                "side": parsed.get("side"),
                "entry_px": parsed.get("entry_px"),
                "stop_px": parsed.get("stop_px"),
                "tp_px": parsed.get("tp_px"),
                "reasoning": _clip(str(parsed.get("reasoning") or ""), 2000),
                "ai_down": bool(parsed.get("ai_down")),
                "close_guard_downgraded": bool(parsed.get("close_guard_downgraded")),
            },
            "wall_ms": int(wall_ms or 0),
            "server_ms": server_ms,
        }
    except Exception as e:  # noqa: BLE001 — archive assembly must never break a verdict
        logger.debug(f"[research] archive record assembly failed for {coin}: {e}")
        return {}


def _duelist_verdict(
    system_prompt: str,
    user_message: str,
    coin: str,
    perception: Dict[str, Any],
    primary_verdict: str,
    primary_confidence: float,
    primary_ms: int = 0,
    primary_server_ms: int | None = None,
    held_coins: set | None = None,
) -> Dict[str, Any] | None:
    """Run the A/B duelist: the SAME prompt to the second model, recorded but
    never used. Returns the parsed verdict dict (for the session-log event +
    the entry-context snapshot), or None when the duelist is disabled/failed.

    Best-effort end to end: a duelist outage logs a warning and returns None —
    the primary path above has already produced the verdict that executes, so
    nothing downstream depends on this.

    `held_coins` (the open book) arms the same CLOSE guard as the primary
    parse: the duelist is the small model most prone to reading the open-book
    list globally (2026-09-04, it CLOSEd xyz:HOOD while reasoning about ADA/
    PURR). Passing it keeps the duelist's recorded A/B verdict honest — a
    misread CLOSE is logged as PASS, not as a spurious CLOSE the book can't
    act on.
    """
    try:
        if not duelist_enabled():
            return None
        cfg = duelist_config()
        _dl_t0 = time.monotonic()
        dl_text, dl_server_ms = call_duelist(cfg["api_key"], cfg["base_url"], cfg["model"],
                                             system_prompt, user_message,
                                             max_tokens=cfg["max_tokens"])
        duelist_ms = int((time.monotonic() - _dl_t0) * 1000)
        # Empty text = the duelist call failed (402/429/timeout — call_duelist
        # swallows errors and returns ("", None)). parse_verdict would tag it
        # verdict=PASS/ai_down, which would log a bogus "AGREE" row against a
        # primary that said PASS. A failed duelist is "no observation", not an
        # opinion — record nothing.
        if not (dl_text or "").strip():
            logger.warning(
                f"[duel] {coin}: duelist {cfg['model']} returned no text — not recorded"
            )
            return None
        dl_parsed = parse_verdict(dl_text, coin, perception, held_coins=held_coins)
        # Prompt/response archive (role=duelist). Its prompt is byte-identical
        # to the primary's — recorded as a reference so the archive stores the
        # text once per research call, not twice. Disabled by default; never
        # raises (prompt_log contract).
        prompt_log.record_call(_archive_record(
            coin=coin, perception=perception, role="duelist", model=cfg["model"],
            system_prompt=None, user_message=None, raw_response=dl_text,
            parsed=dl_parsed, wall_ms=duelist_ms, server_ms=dl_server_ms,
        ))
        row = {
            "coin": coin,
            "perception_id": perception.get("id", "unknown"),
            "mode": str(read_agent_config().get("mode", "OFF")),
            "primary_model": effective_primary_model(),
            "duelist_model": cfg["model"],
            "primary_verdict": primary_verdict,
            "primary_confidence": primary_confidence,
            "duelist_verdict": dl_parsed["verdict"],
            "duelist_confidence": dl_parsed["confidence"],
            "duelist_side": dl_parsed["side"],
            "duelist_reasoning": (dl_parsed["reasoning"] or "")[:300],
            # Wall time of each LLM call (ms) — the same prompt to both
            # models, so latency is a directly comparable A/B dimension
            # (a faster model with equal accuracy can change cycle budget).
            "primary_ms": int(primary_ms or 0),
            "duelist_ms": duelist_ms,
            # Server-reported processing time (ms, from the response's `timings`
            # block — queue-free: excludes the time a request waited behind
            # another on a serial server). None when the backend doesn't send
            # `timings` (OpenRouter etc.); the wall-vs-server gap IS the queue
            # wait. Both are recorded so the report can separate them.
            "primary_server_ms": (int(primary_server_ms) if primary_server_ms is not None else None),
            "duelist_server_ms": (int(dl_server_ms) if dl_server_ms is not None else None),
        }
        record_duel(row)
        # The log line carries wall + server times for each model so an
        # operator sees the queue-wait gap (wall − server) at a glance.
        _p_srv = f", {row['primary_server_ms']}ms srv" if row["primary_server_ms"] is not None else ""
        _d_srv = f", {row['duelist_server_ms']}ms srv" if row["duelist_server_ms"] is not None else ""
        logger.info(
            f"[duel] {coin}: primary {row['primary_model']} {primary_verdict} "
            f"(conf {primary_confidence:.2f}, {row['primary_ms']}ms{_p_srv}) "
            f"vs duelist {row['duelist_model']} {row['duelist_verdict']} "
            f"(conf {row['duelist_confidence']:.2f}, {row['duelist_ms']}ms{_d_srv}) — "
            f"{'AGREE' if row['duelist_verdict'] == primary_verdict else 'SPLIT'}"
        )
        return row
    except Exception as e:  # noqa: BLE001 — the primary verdict is already in hand
        logger.warning(f"[duel] duelist hook failed for {coin} (non-fatal): {e!r}")
        return None


def _duelist_close_exit_accrual(
    coin: str,
    duelist_row: Dict[str, Any],
    open_positions: List[Dict[str, Any]],
) -> None:
    """B.10 shadow accrual — duelist exit-voice (log-only, never acts).

    When the A/B duelist calls CLOSE on a HELD coin and the primary does not
    close it, log a WOULD-HAVE-CLOSED line so the counterfactual accrues
    forward (design + in-sample replay: .hermes/WATCHLIST.md §B.10 — 32
    held-CLOSEs, would-have-exit +$78.48 vs actual −$186.50; n=32 is not
    decision-grade, hence shadow-first per house pattern).

    The close-guard in parse_verdict already downgrades CLOSE-on-unheld to
    PASS, so a recorded CLOSE implies held — the open_positions check here is
    defensive only. Every firing is logged (no conf cutoff in code): the
    trigger variants (a) conf>=0.85, (b) 2-of-3 consecutive, (c) conf>=0.70
    AND peak<1% are priced OFFLINE from the logged fields, per the design.

    Fields on the line are what the join needs: dl_conf, position age, spot
    vs entry, one-way peak, entry/mark prices for exact next-5m-open pricing
    (HL 5m candles fetched by the join script from the line's timestamp), and
    perception_id to tie back to the duel row / prompt archive.

    Never raises: this is observability inside the research loop — a broken
    accrual must never cost a trade.
    """
    try:
        if str(duelist_row.get("duelist_verdict") or "") != "CLOSE":
            return
        held = next((p for p in (open_positions or []) if p.get("coin") == coin), None)
        if held is None:
            return
        side = str(held.get("side") or "?")
        conf = float(duelist_row.get("duelist_confidence") or 0.0)
        age_s = spot_s = peak_s = basis_s = mark_s = "n/a"
        try:
            from hermes_trader.agents import dsl_exit as _dslx
            trk = _dslx._active_positions.get(f"{coin}_{side}")
            if trk is not None and trk.entry_px:
                e = float(trk.entry_px)
                pk = float(trk.peak_px or e)
                age_s = f"{int((time.time() - trk.entry_time) // 60)}min"
                peak_pct = ((pk - e) / e * 100) if side == "long" else ((e - pk) / e * 100)
                peak_s = f"{peak_pct:+.2f}%"
                basis_s = f"{e:.6g}"
                m = float(getattr(trk, "last_mark_px", None) or 0.0)
                if m > 0:
                    spot_pct = ((m - e) / e * 100) if side == "long" else ((e - m) / e * 100)
                    spot_s = f"{spot_pct:+.2f}%"
                    mark_s = f"{m:.6g}"
        except Exception:  # noqa: BLE001 — tracker read is best-effort
            pass
        logger.info(
            f"[gate][SHADOW] duelist_close_exit WOULD HAVE CLOSED {coin} {side}: "
            f"dl_conf {conf:.2f}, position age {age_s}, spot {spot_s}, peak {peak_s}, "
            f"entry {basis_s}, mark {mark_s}, pid {duelist_row.get('perception_id', 'unknown')}"
        )
    except Exception as e:  # noqa: BLE001 — accrual must never break the loop
        logger.debug(f"[research] duelist_close_exit accrual failed for {coin}: {e}")


def _dv_close_exit_accrual(
    coin: str,
    dv_row: Dict[str, Any],
    open_positions: List[Dict[str, Any]],
) -> None:
    """Decision-voice close_veto shadow accrual (clef-gate scope Change 3,
    log-only, never acts — mirrors _duelist_close_exit_accrual).

    The dv observer already answers `close_now` (noul) on EVERY scan for a
    HELD coin (the question is only asked when held — the phantom-CLOSE class
    stays structurally impossible), so this check is free: no new endpoint
    call, no dsl_exit import cycle.

    Fires when: held AND close_now >= threshold AND the primary did NOT say
    CLOSE (a primary CLOSE closes the position anyway — not a split).
    grace_minutes suppresses the line on young positions (don't fight fresh
    entries; mirrors the dsl grace).

    Config `decision_voice_gate.close_veto` {shadow_mode, threshold,
    grace_minutes} — hot read. shadow_mode false is the PROMOTION step and
    not implemented here by design: promotion injects a synthetic CLOSE via
    the AI-CLOSE execution path (executor verdict=="CLOSE"), tagged
    close_source "dv" — that lands with the offline join's verdict, not now.
    Every firing logs the raw close_now so all threshold variants are priced
    offline. Never raises.
    """
    try:
        from hermes_trader.agents.config_store import read_agent_config
        gate = (read_agent_config().get("decision_voice_gate") or {})
        if not bool(gate.get("enabled", False)):
            return
        cv = gate.get("close_veto") or {}
        held = next((p for p in (open_positions or [])
                     if p.get("coin") == coin), None)
        if held is None:
            return
        close_p = dv_row.get("dv_close_now")
        if close_p is None:
            return
        close_p = float(close_p)
        threshold = float(cv.get("threshold", 0.50))
        if close_p < threshold:
            return
        side = str(held.get("side") or "?")
        age_s = spot_s = peak_s = basis_s = mark_s = "n/a"
        try:
            from hermes_trader.agents import dsl_exit as _dslx
            trk = _dslx._active_positions.get(f"{coin}_{side}")
            if trk is not None and trk.entry_px:
                e = float(trk.entry_px)
                pk = float(trk.peak_px or e)
                age_min = (time.time() - trk.entry_time) // 60
                grace = float(cv.get("grace_minutes", 90))
                if age_min < grace:
                    return  # young position — don't fight fresh entries
                age_s = f"{int(age_min)}min"
                peak_pct = ((pk - e) / e * 100) if side == "long" else ((e - pk) / e * 100)
                peak_s = f"{peak_pct:+.2f}%"
                basis_s = f"{e:.6g}"
                m = float(getattr(trk, "last_mark_px", None) or 0.0)
                if m > 0:
                    spot_pct = ((m - e) / e * 100) if side == "long" else ((e - m) / e * 100)
                    spot_s = f"{spot_pct:+.2f}%"
                    mark_s = f"{m:.6g}"
        except Exception:  # noqa: BLE001 — tracker read is best-effort
            pass
        if not bool(cv.get("shadow_mode", True)):
            # Promotion not wired yet (see docstring): log LOUDLY that the
            # condition fired with shadow off, but still do not act — the
            # synthetic-CLOSE injection rides a separate reviewed change.
            logger.warning(
                f"[gate] dv_close_veto FIRED (shadow OFF, action NOT wired) "
                f"{coin} {side}: close_now {close_p:.2f} >= {threshold:.2f}, "
                f"age {age_s}, spot {spot_s}, peak {peak_s}, entry {basis_s}, "
                f"mark {mark_s}, pid {dv_row.get('perception_id', 'unknown')}")
            return
        logger.info(
            f"[gate][SHADOW] dv_close_veto WOULD HAVE CLOSED {coin} {side}: "
            f"close_now {close_p:.2f} >= {threshold:.2f}, position age {age_s}, "
            f"spot {spot_s}, peak {peak_s}, entry {basis_s}, mark {mark_s}, "
            f"pid {dv_row.get('perception_id', 'unknown')}"
        )
    except Exception as e:  # noqa: BLE001 — accrual must never break the loop
        logger.debug(f"[research] dv_close_veto accrual failed for {coin}: {e}")


# Code default for the PRIMARY slot's sampling — exactly what was sent before
# this became configurable (temperature 0.1 ONLY; adding keys would itself
# change behavior). The live value is hot-tunable via the agent config's
# `llm.sampling` block (P11, moved into the `llm` block with the model name);
# the duelist slot has its own independent default + `llm.duelist_sampling`
# block in duel_store.py, so each slot's settings can travel with its own
# model.
PRIMARY_SAMPLING_DEFAULT: Dict[str, Any] = {"temperature": 0.1}


def effective_llm_sampling() -> Dict[str, Any]:
    """The primary slot's sampling profile, read at CALL time (hot, no cache).

    Merge rule: {**default, **config["llm"]["primary"]["sampling"]} — per-key
    override, NOT replace-whole-dict, so a partial block keeps every default
    key it doesn't name. The legacy flat `llm.sampling` is the fallback when
    the nested slot key is absent. Absent both = the code default, i.e.
    today's exact body. Fail-open: any config fault (missing/corrupt/
    unreadable file) degrades to the pure default — the LLM call path must
    never break on a config problem.
    """
    merged = dict(PRIMARY_SAMPLING_DEFAULT)
    try:
        overrides = slot_get("primary", "sampling", "sampling")
        if isinstance(overrides, dict):
            merged.update(overrides)
    except Exception:  # noqa: BLE001 — fail-open (see docstring)
        pass
    logger.debug(f"[research] primary LLM sampling: {merged}")
    return merged


async def _async_do_call(
    api_key: str,
    base_url: str,
    model: str,
    system_prompt: str,
    user_message: str,
) -> tuple:
    """Async POST to the chat-completions endpoint.

    Returns ``(text, server_ms)`` — the raw response text plus the model
    server's self-reported processing time (``server_processing_ms`` from the
    response's ``timings`` block; None when the backend doesn't send it) —
    with ``("", None)`` on any non-success response.

    On a 402 that includes an affordability hint ("can only afford N tokens"),
    retries ONCE with max_tokens shrunk to the affordable budget. During the
    2026-06-11 credit drought the bot sat fully blind for ~12h while OpenRouter
    was offering 842 affordable tokens per call — enough for a non-truncated
    verdict on most prompts. Degraded thinking beats no thinking; if the
    shrunken reply still truncates, parse_verdict falls back to PASS exactly
    as before (no new failure mode).
    """
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:

        # Completion budget: agent config `llm.primary.max_tokens` → legacy
        # llm.max_tokens → LLM_MAX_TOKENS (default 8192, read at call time —
        # a same-inode config flip, no recreate). It caps the RESPONSE
        # length only — the prompt size is governed by the model server's
        # context window.
        default_max_toks = effective_primary_max_tokens()
        # Per-request chat-template overrides (e.g. {"enable_thinking":
        # false} — think OFF for a thinking-capable model, so the answer
        # lands in `content` fast instead of a long `reasoning_content`
        # prefix). Absent = {} → the key is NOT sent (no-op).
        chat_kwargs = effective_chat_template_kwargs("primary")

        async def _post(max_toks: int):
            url = base_url.rstrip("/") + "/chat/completions"
            body = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                "stream": False,
                "max_tokens": max_toks,
                **effective_llm_sampling(),
            }
            if chat_kwargs:
                body["chat_template_kwargs"] = chat_kwargs
            return await client.post(
                url,
                json=body,
                headers={"Authorization": f"Bearer {api_key}"},
            )

        resp = await _post(default_max_toks)
        if resp.status_code == 402:
            # "...You requested up to 2048 tokens, but can only afford 842..."
            m = re.search(r"can only afford (\d+)", resp.text or "")
            if m and int(m.group(1)) >= 500:
                budget = int(m.group(1)) - 50  # headroom for billing jitter
                logger.warning(
                    f"[research] 402 with affordability hint — retrying DEGRADED "
                    f"at max_tokens={budget} (add credits to restore full reasoning)"
                )
                resp = await _post(budget)

        if resp.is_success:
            data = resp.json()
            choices = data.get("choices", [])
            if choices:
                msg = choices[0].get("message", {})
                text = msg.get("content") or msg.get("reasoning") or ""
                # server_ms from the response's own timings block (None when
                # the backend doesn't report it) — the queue-free processing
                # time the wall-clock timer can't separate from real work.
                return (text, server_processing_ms(data))
            logger.error("[research] LLM returned 200 but no choices — empty response")
            return "", None
        # LOUD failure. A non-200 (esp. 402 Payment Required = out of OpenRouter
        # credits, or 401/429) previously returned "" silently → parse_verdict
        # defaulted every coin to PASS conf 0.0, so a billing/API outage looked
        # identical to "no setups" and the bot sat blind for hours. Make it scream.
        body = resp.text[:200] if resp.text else ""
        logger.error(
            f"[research] LLM call FAILED: HTTP {resp.status_code} — AI research is "
            f"DOWN, all verdicts will default to PASS until fixed. {body}"
        )
    return "", None


def parse_verdict(
    ai_text: str,
    coin: str,
    perception: Dict[str, Any],
    held_coins: set | None = None,
) -> Dict[str, Any]:
    """Parse the AI response: JSON on the last line, with a regex fallback.

    `held_coins` (optional set of coin names the account currently holds)
    arms a deterministic CLOSE guard: a CLOSE verdict for a coin NOT in the
    book is a misread and is coerced to PASS. `None` disables the guard —
    backtest and legacy callers pass no book and keep the raw token.
    """
    if not ai_text:
        ai_text = ""

    logger = logging.getLogger("hermes_trader.agents.research")
    logger.info(f"[parse_verdict] {coin} raw AI text (last 800 chars):\n{ai_text[-800:]}" if len(ai_text) > 800 else f"[parse_verdict] {coin} raw AI text:\n{ai_text}")

    verdict = "PASS"
    confidence = 0.0
    side = None
    entry_px = perception.get("mid", 0)
    stop_px = 0.0
    tp_px = 0.0
    news_risk = "none"
    reasoning = ai_text.strip()

    lines = ai_text.strip().split("\n")

    # Find JSON on the last line
    json_str = ""
    for line in reversed(lines):
        line = line.strip()
        if line.startswith("{") and "verdict" in line and line.endswith("}"):
            json_str = line
            break

    # Fallback: regex match
    if not json_str:
        match = re.search(r'\{[^{}]*"verdict"[^{}]*\}', ai_text)
        if match:
            json_str = match.group(0)

    if json_str:
        try:
            cleaned = re.sub(r'```json\s*|```', '', json_str).strip()
            parsed = json.loads(cleaned)

            raw = str(parsed.get("verdict", "")).upper()
            if raw == "LONG":
                verdict = "LONG"
            elif raw == "SHORT":
                verdict = "SHORT"
            elif raw == "CLOSE":
                verdict = "CLOSE"
            elif raw == "VETO":
                # Active rejection (2026-09-03): "this setup is a bad idea",
                # distinct from neutral PASS. Never executes, never upgrades.
                verdict = "VETO"

            confidence = parsed.get("confidence", 0)
            side = parsed.get("side") if parsed.get("side") in ("long", "short") else None
            entry_px = parsed.get("entry_px") or parsed.get("entryPx", perception.get("mid", 0))
            stop_px = parsed.get("stop_px") or parsed.get("stopPx", 0)
            tp_px = parsed.get("tp_px") or parsed.get("tpPx", 0)
            nr = str(parsed.get("news_risk") or parsed.get("newsRisk") or "none").lower()
            news_risk = nr if nr in ("none", "positive", "negative") else "none"
            reasoning = parsed.get("reasoning", ai_text[:500])
        except json.JSONDecodeError:
            first_line = lines[0] if lines else ""
            if re.search(r"LONG", first_line, re.IGNORECASE):
                verdict = "LONG"
            elif re.search(r"SHORT", first_line, re.IGNORECASE):
                verdict = "SHORT"
            elif re.search(r"CLOSE", first_line, re.IGNORECASE):
                verdict = "CLOSE"

    # Deterministic CLOSE guard (2026-09-04, xyz:HOOD): the research prompt
    # lists the WHOLE open book, and a small model (the Qwen2.5-Coder-7B
    # duelist) read "CLOSE only if structure flipped" globally — its
    # reasoning was about ADA/PURR and it emitted {"verdict":"CLOSE"} for
    # HOOD, a coin the account doesn't hold. A CLOSE for a coin we don't
    # hold is by definition a misread of ANOTHER position's book state; it
    # can only route to a close_fn(coin) that no-ops `already_flat`, so it
    # has no legitimate meaning here. Coerce it to PASS with a trace flag.
    # `held_coins is None` = guard off (backtest / legacy callers): the raw
    # token is preserved so historical replay semantics don't shift.
    close_guard_downgraded = False
    if held_coins is not None and verdict == "CLOSE" and coin not in held_coins:
        logger.warning(
            f"[parse_verdict] {coin}: CLOSE verdict but {coin} is not an open "
            f"position (book={sorted(held_coins)}) — model misread another "
            f"position's book state; downgrading CLOSE -> PASS"
        )
        verdict = "PASS"
        close_guard_downgraded = True

    # Coerce confidence to a clamped float — the LLM occasionally returns it
    # as a string ("0.8") or out of range; a string would TypeError at the
    # gate comparison (`ctx.confidence >= 0.85`) on a live trade.
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    # Derive side from verdict when the LLM omitted/nulled the side field.
    # Without this a SHORT verdict with side=None falls through to the
    # executor's `or "long"` default and executes in the WRONG direction.
    if verdict == "LONG":
        side = "long"
    elif verdict == "SHORT":
        side = "short"
    # CLOSE/PASS/VETO keep whatever side was parsed (unused downstream).

    return {
        "verdict": verdict,
        "confidence": confidence,
        "side": side,
        "entry_px": entry_px,
        "stop_px": stop_px,
        "tp_px": tp_px,
        "news_risk": news_risk,
        "reasoning": reasoning,
        # Empty ai_text = the LLM call failed (402/429/timeout) and this PASS is
        # an ERROR CODE, not an opinion. Tagged so the executor's structural/whale
        # override won't upgrade a failure-PASS into a blind LONG — on 2026-06-11
        # a 402 window let the override shotgun 8 PASS→LONG upgrades in one
        # minute, filling the book with unvetted longs that then blocked real
        # AI SHORT signals on the movers.
        "ai_down": not ai_text.strip(),
        # True when this PASS is a DOWNGRADED CLOSE (the coin wasn't held when
        # the model said CLOSE) — the raw token was CLOSE, the guard coerced it.
        # Kept for forensics so a downgraded CLOSE is distinguishable from a
        # genuine PASS in the analysis dict / session log.
        "close_guard_downgraded": close_guard_downgraded,
    }


def research(coin: str, perception: Dict[str, Any]) -> Dict[str, Any]:
    """Full AI research pipeline for a perception — returns an analysis dict."""
    c1h = fetch_hl_candles(coin, "1h", 100)
    c4h = fetch_hl_candles(coin, "4h", 100)
    c1d = fetch_hl_candles(coin, "1d", 60)

    funding_raw = _fetch_funding_rate(coin)
    news = _fetch_news(coin)

    # Thin-history guard: multi-timeframe TA is meaningless without enough 4h
    # bars (EMA21/ADX need history). A near-empty series produced confident-
    # looking but baseless entries (e.g. WLD entered at 0.68 conf on "0 candles"
    # then ran straight to the stop). Decline outright — PASS, no LLM call, no entry.
    if len(c4h) < 30:
        logger.warning(f"[research] thin 4h history for {coin}: only {len(c4h)} candles — PASS (skip)")
        analysis = {
            "id": str(uuid.uuid4()), "perception_id": perception.get("id", "unknown"),
            "coin": coin, "verdict": "PASS", "confidence": 0.0, "side": None,
            "entry_px": perception.get("mid", 0), "stop_px": 0.0, "tp_px": 0.0,
            "reasoning": f"insufficient 4h history ({len(c4h)} candles) for reliable multi-TF TA",
            "news_context": news, "news_risk": "none",
            "created_at": int(time.time() * 1000),
            "composite_score": float(perception.get("composite_score", 0) or 0),
            "momentum_burst_fired": False, "slow_burn_fired": False,
            "slow_burn_count": 0,
            "daily_mover_fired": any(
                t.get("name") == "dailyMover" and t.get("fired")
                for t in (perception.get("triggers") or [])
            ),
            "daily_move_pct": perception.get("daily_move_pct"),
            "whale_signal": perception.get("whale_signal"),
        }
        memory.record_analysis(analysis)
        return analysis

    tf1h = _compute_indicators(c1h)
    tf4h = _compute_indicators(c4h)
    tf1d = _compute_indicators(c1d)

    config = read_agent_config()
    mode = str(config.get("mode", "OFF"))

    equity = 0.0
    dex_equity: Dict[str, float] = {}
    open_positions: List[Dict[str, Any]] = []
    user = resolve_user_address()

    if user:
        # Aggregated equity so the LLM sees true capital across main + HIP-3
        # dexes when reasoning about risk caps for HIP-3 candidates.
        state = fetch_account_state(user, include_hip3=True)
        equity = float(state.get("equity", "0"))
        dex_equity = state.get("dex_equity") or {}
        memory.update_equity(equity)

        open_positions = [
            {
                "coin": p.get("position", {}).get("coin", ""),
                "side": "long" if float(p.get("position", {}).get("szi", "0")) > 0 else "short",
                "size_usd": float(p.get("position", {}).get("positionValue", "0")) or (
                    abs(float(p.get("position", {}).get("szi", "0"))) *
                    float(p.get("position", {}).get("entryPx", "0"))
                ),
                # Entry + live PnL from the exchange (B.39 close-voice audit
                # 2026-10-10): the dv `close_now` question was asked BLIND to
                # how deep the position was (state carried side+size only),
                # plausibly capping dv_close_now at ~0.23 lifetime. These keys
                # feed decision_voice.build_state's held_position block; the
                # chat prompt builder ignores unknown keys (byte-identical
                # primary prompt).
                "entry_px": float(p.get("position", {}).get("entryPx", "0") or 0) or None,
                "unrealized_pnl_usd": float(p.get("position", {}).get("unrealizedPnl", "0") or 0),
            }
            for p in (state.get("asset_positions") or [])
            if float(p.get("position", {}).get("szi", "0")) != 0
        ]

    wr = memory.get_win_rate()
    system_prompt = build_system_prompt(mode, wr.get("rate", 0), int(wr.get("total", 0)))
    # Statefulness experiment (WATCHLIST B.32 item 1): "your recent history on
    # this coin" — prior verdicts + last completed round trip. Disabled by
    # default; build_prior_call_block returns "" and logs nothing on any fault.
    _primary_model_name = effective_primary_model()
    prior_block = build_prior_call_block(coin, primary_model=_primary_model_name)
    # RVOL entry context (WATCHLIST B.32 item 2): one factual volume line for
    # the entry candle. Disabled by default; build_rvol_block returns "" on any
    # fault and does its own TTL-cached 5m fetch.
    rvol_block = build_rvol_block(coin)
    user_message = _build_user_message(
        coin, perception, tf1h, tf4h, tf1d,
        funding_raw, news, equity, open_positions, mode,
        dex_equity=dex_equity, recent_candles=c1h,
        prior_block=prior_block,
        rvol_block=rvol_block,
    )

    # `held_coins` = the live open book (same `open_positions` fed to the
    # prompt), so a CLOSE on a coin we don't hold is deterministically
    # downgraded to PASS at parse time — the xyz:HOOD 2026-09-04 misread.
    held_coins = {p["coin"] for p in open_positions}

    # Primary-engine switch (2026-10-13): the decision model (Clef /
    # Decision-2.0 / d3 family, llama.cpp `POST /v1/systemone`) can drive
    # the entry verdict INSTEAD of the chat LLM generation call — the
    # §B.39 primary-replacement walk (d2 +$49.21 n=195 vs incumbent
    # +$33.64 n=212, ~0.6s vs 22.5s median). Config `llm.primary_engine`
    # (hot read, fail-open): absent = "llm" = the chat path below,
    # byte-for-byte. The systemone branch reuses the DV observer's proven
    # contract (build_state + build_questions + call_systemone +
    # verdict_from_answers, whose output is parse_verdict-shaped), so
    # every gate/sizing/executor site downstream is engine-agnostic.
    # The thin-history guard, prompt build, duelist, and everything after
    # `parsed` are untouched.
    engine_cfg = primary_engine_config()
    if engine_cfg["engine"] == "systemone":
        ai_t0 = time.monotonic()
        so = systemone_primary_verdict(
            coin, perception, tf1h, tf4h, tf1d,
            funding_raw, news, open_positions,
            wr.get("rate", 0), int(wr.get("total", 0)),
            engine_cfg,
        )
        primary_ms = int((time.monotonic() - ai_t0) * 1000)
        if so is None:
            # Owner call 2026-10-13: a failed systemone primary is a
            # failure-PASS tagged ai_down (the executor's override guard
            # treats it as an error code, not an opinion — same contract
            # as parse_verdict's ai_text-empty path). NO LLM fallback:
            # the seat exists so the 27B stays unloaded.
            logger.warning(
                f"[research] {coin}: systemone primary ({engine_cfg['model']}) "
                f"FAILED after {primary_ms}ms — PASS ai_down this cycle "
                f"(no LLM fallback)"
            )
            parsed = {
                "verdict": "PASS", "confidence": 0.0, "side": None,
                "entry_px": perception.get("mid", 0), "stop_px": 0.0, "tp_px": 0.0,
                "news_risk": "none",
                "reasoning": "systemone primary call failed — PASS is an "
                             "error code, not a verdict",
                "ai_down": True, "close_guard_downgraded": False,
            }
            ai_text, primary_server_ms, request_json = "", None, None
        else:
            parsed = so["parsed"]
            ai_text = json.dumps(so["answers"])
            primary_server_ms = so["server_ms"]
            request_json = so["request_json"]
            _srv_s = f", {primary_server_ms}ms server" if primary_server_ms is not None else ""
            logger.info(
                f"[research] systemone primary ({engine_cfg['model']}) completed "
                f"in {primary_ms}ms wall{_srv_s} (request ~{len(request_json)} chars)"
            )
        # Archive under role="primary" with the rendered {state, questions}
        # as user_message + the answers JSON as raw_response — the same
        # self-contained shape as the decision_voice rows, so llm_ab_replay
        # prep cohorts keep working unchanged.
        prompt_log.record_call(_archive_record(
            coin=coin, perception=perception, role="primary", model=engine_cfg["model"],
            system_prompt=None, user_message=request_json,
            raw_response=ai_text, parsed=parsed,
            wall_ms=primary_ms, server_ms=primary_server_ms,
            prior_block_injected=False,
        ))
    else:
        ai_t0 = time.monotonic()
        ai_text, primary_server_ms = _call_ai(system_prompt, user_message)
        primary_ms = int((time.monotonic() - ai_t0) * 1000)
        # Per-call LLM duration, ALWAYS logged (2026-09-22). Until now wall/server
        # times only surfaced on the [duel] line — with the duelist off the cycle
        # budget question ("is research_max_workers worth raising?") had no
        # per-call numbers in trader.log. Wall = queue + inference; server =
        # llama.cpp timings (queue-free) — the gap is server-side queue wait,
        # which is exactly what parallelising the research loop converts into
        # throughput. Sits adjacent to httpx's "HTTP Request: POST ... 200 OK"
        # line for the same call (same [COIN] prefix from log_context).
        _srv_s = f", {primary_server_ms}ms server" if primary_server_ms is not None else ""
        logger.info(
            f"[research] LLM call completed in {primary_ms}ms wall{_srv_s} "
            f"(prompt ~{len(user_message)} chars)"
        )
        parsed = parse_verdict(ai_text, coin, perception, held_coins=held_coins)

        # Prompt/response archive (role=primary): full prompt text + raw response +
        # parsed verdict, zstd-chunked under /app/log/prompt-log. Disabled by
        # default; record_call never raises (prompt_log.py contract).
        prompt_log.record_call(_archive_record(
            coin=coin, perception=perception, role="primary", model=_primary_model_name,
            system_prompt=system_prompt, user_message=user_message,
            raw_response=ai_text, parsed=parsed,
            wall_ms=primary_ms, server_ms=primary_server_ms,
            prior_block_injected=bool(prior_block),
        ))

    # A/B duelist: the SAME prompt to the second model, recorded but never
    # used. Runs AFTER the primary verdict so a duelist outage (slow 9B server
    # included) can never delay or break execution. `duelist_at_entry` is
    # carried into the analysis dict → executor's entry-context snapshot →
    # the outcome store's close row, which is the join the A/B report
    # (duel_store.aggregate) keys on. The primary's wall AND server-reported
    # processing times ride along so the duel row can report the queue gap.
    duelist_row = _duelist_verdict(
        system_prompt, user_message, coin, perception,
        parsed["verdict"], parsed["confidence"],
        primary_ms=primary_ms,
        primary_server_ms=primary_server_ms,
        held_coins=held_coins,
    )
    # Decision-voice observer (2026-10-07): a DIFFERENT CLASS of LLM call —
    # a decision model (Cloudflare Clef family) answering a typed
    # state+questions schema via llama.cpp's /v1/systemone, probabilities in
    # one forward pass, no text generation, no parse_verdict. Shadow-only
    # like the duelist: recorded, never gates, never executes. Dormant when
    # no endpoint/model is named (decision_voice.py). Runs AFTER the primary
    # verdict, so an outage can never delay or break execution.
    wr_dv = wr  # the same win-rate snapshot the system prompt used
    dv_row = decision_voice_verdict(
        coin, perception, tf1h, tf4h, tf1d,
        funding_raw, news, open_positions,
        wr_dv.get("rate", 0), int(wr_dv.get("total", 0)),
        parsed["verdict"], parsed["confidence"],
        primary_ms=primary_ms,
    )
    if dv_row is not None:
        # clef-gate scope Change 3: dv close_now exit check (log-only shadow
        # accrual, mirrors the B.10 duelist line). Skipped when the primary
        # also said CLOSE — that split is not a would-have-closed case.
        if parsed["verdict"] != "CLOSE":
            _dv_close_exit_accrual(coin, dv_row, open_positions)
        try:
            from hermes_trader.session_log import append as _log_event_dv
            _log_event_dv({
                "event": "decision_voice", "coin": coin,
                "primary_verdict": dv_row["primary_verdict"],
                "dv_verdict": dv_row["dv_verdict"],
                "dv_model": dv_row["dv_model"],
                "agree": dv_row["dv_verdict"] == dv_row["primary_verdict"],
                "dv_confidence": dv_row["dv_confidence"],
                "dv_ms": dv_row["dv_ms"],
                "dv_server_ms": dv_row["dv_server_ms"],
            })
        except Exception:
            pass

    if duelist_row is not None:
        # B.10 shadow accrual: duelist CLOSE on a held coin that the primary
        # did NOT close -> log the counterfactual (log-only, never acts).
        # Skipped when the primary also said CLOSE — that split is not a
        # would-have-closed case (the position closes anyway).
        if parsed["verdict"] != "CLOSE":
            _duelist_close_exit_accrual(coin, duelist_row, open_positions)
        try:
            from hermes_trader.session_log import append as _log_event
            _log_event({
                "event": "duel", "coin": coin,
                "primary_verdict": duelist_row["primary_verdict"],
                "duelist_verdict": duelist_row["duelist_verdict"],
                "primary_model": duelist_row["primary_model"],
                "duelist_model": duelist_row["duelist_model"],
                "agree": duelist_row["duelist_verdict"] == duelist_row["primary_verdict"],
                "primary_ms": duelist_row["primary_ms"],
                "duelist_ms": duelist_row["duelist_ms"],
                "primary_server_ms": duelist_row["primary_server_ms"],
                "duelist_server_ms": duelist_row["duelist_server_ms"],
            })
        except Exception:
            pass

    # The model name that ANSWERED this call (config `llm.model` → env),
    # resolved at the call site so the `Verdict:` log line and the
    # `Trade result` row always show the model that actually spoke — the
    # 2026-08-25 model-switch forensics gap (a name read later from env can
    # disagree with the one used when the config has since been flipped).
    # Same principle for the systemone seat: the decision model that
    # answered is the primary_model the ledger join attributes the trade to.
    primary_model = (
        engine_cfg["model"] if engine_cfg["engine"] == "systemone"
        else effective_primary_model()
    )

    analysis = {
        "id": str(uuid.uuid4()),
        "perception_id": perception.get("id", "unknown"),
        "coin": coin,
        "verdict": parsed["verdict"],
        "confidence": parsed["confidence"],
        "side": parsed["side"],
        "entry_px": parsed["entry_px"],
        "stop_px": parsed["stop_px"],
        "tp_px": parsed["tp_px"],
        "reasoning": parsed["reasoning"],
        # Cohort tag (B.46): set by the systemone seat for with-the-signal
        # vwap entries; consumed by the runner gate's cohort_admissions.
        # Must survive this whitelist or the gate never sees it.
        "cohort_tag": parsed.get("cohort_tag"),
        "news_context": news,
        "primary_model": primary_model,
        # AI's good/bad judgment of the recent news — drives the news gate
        # (only "negative" stands the trade down; an earnings beat is fine).
        "news_risk": parsed["news_risk"],
        # Failure-PASS marker — must survive this whitelist or the executor's
        # override guard never sees it (it didn't, on first deploy).
        "ai_down": bool(parsed.get("ai_down")),
        # Downgraded-CLOSE marker (2026-09-04, xyz:HOOD) — a CLOSE on a coin
        # we don't hold was coerced to PASS at parse time. Carried for
        # forensics so the session log / memory distinguish a downgraded
        # CLOSE from a genuine PASS.
        "close_guard_downgraded": bool(parsed.get("close_guard_downgraded")),
        # A/B duelist verdict (None when the duelist is disabled/failed). Must
        # survive this whitelist: the executor snapshots it into the entry
        # context so the close row can attribute the same trade to the second
        # model. Carries only verdict-level data — the full reasoning lives in
        # the duel JSONL to keep the analysis (and the memory file) lean.
        "duelist_at_entry": (
            {
                "model": duelist_row["duelist_model"],
                "verdict": duelist_row["duelist_verdict"],
                "confidence": duelist_row["duelist_confidence"],
                "side": duelist_row["duelist_side"],
            }
            if duelist_row is not None else None
        ),
        # Decision-voice verdict at entry (None when disabled/failed). Same
        # join purpose as duelist_at_entry: the executor snapshots it into
        # the entry context so the close row attributes the same trade to
        # the decision model. Verdict-level only — the raw per-option
        # probabilities live in the DV JSONL.
        "decision_voice_at_entry": (
            {
                "model": dv_row["dv_model"],
                "verdict": dv_row["dv_verdict"],
                "confidence": dv_row["dv_confidence"],
                "side": dv_row["dv_side"],
                # SCALARS the live gates read (clef-gate scope Change 1).
                # WHITELIST REGRESSION POINT: a field not listed here
                # silently never reaches the executor (2026-08 pitfall —
                # duelist_at_entry lost its fields the same way).
                "trap": dv_row["dv_trap"],
                "close_now": dv_row["dv_close_now"],
            }
            if dv_row is not None else None
        ),
        "created_at": int(time.time() * 1000),
        # Carry forward so risk gates can read own-coin signal strength.
        "composite_score": float(perception.get("composite_score", 0) or 0),
        "momentum_burst_fired": any(
            t.get("name") == "momentumBurst" and t.get("fired")
            for t in (perception.get("triggers") or [])
        ),
        "slow_burn_fired": any(
            t.get("name") in ("volumeBuildup1h", "trendFlip1h", "higherLows1h")
            and t.get("fired")
            for t in (perception.get("triggers") or [])
        ),
        "slow_burn_count": sum(
            1 for t in (perception.get("triggers") or [])
            if t.get("name") in ("volumeBuildup1h", "trendFlip1h", "higherLows1h")
            and t.get("fired")
        ),
        # O'Neil breakout pair — feeds the breakout force-execute (a hedged AI
        # PASS on a 20-period-high break WITH a volume surge gets upgraded;
        # XPL +32% 2026-06-12 was researched 38x, PASSed 21x, never traded
        # while both of these were fired hours before the move).
        "breakout_fired": any(
            t.get("name") == "breakout" and t.get("fired")
            for t in (perception.get("triggers") or [])
        ),
        "volume_spike_fired": any(
            t.get("name") == "volumeSpike" and t.get("fired")
            for t in (perception.get("triggers") or [])
        ),
        "uptrend_momentum_fired": any(
            t.get("name") == "uptrendMomentum" and t.get("fired")
            for t in (perception.get("triggers") or [])
        ),
        "downtrend_momentum_fired": any(
            t.get("name") == "downtrendMomentum" and t.get("fired")
            for t in (perception.get("triggers") or [])
        ),
        "daily_mover_fired": any(
            t.get("name") == "dailyMover" and t.get("fired")
            for t in (perception.get("triggers") or [])
        ),
        # Signed 24h move — feeds the executor's falling-knife guard for the long
        # sidestep (upstream 51bc23b); None when perception couldn't compute it.
        "daily_move_pct": perception.get("daily_move_pct"),
        # OI+funding accumulation signal (oi_funding_anomaly). When present,
        # the coin shows whale-loading patterns (high OI, negative funding,
        # flat price). Used as a counter-regime bypass for LONGs.
        "whale_signal": perception.get("whale_signal"),
    }

    # Chronos shadow logging moved to executor.py (trade result path).
    # Keeping this would duplicate logs; executor owns the signal now.
    if parsed["verdict"] in ("LONG", "SHORT"):
        pass  # Chronos handled in executor.py

    memory.record_analysis(analysis)
    return analysis


def _fire_chronos_shadow(coin: str, side: str) -> None:
    """Fire a Chronos-2 forecast in the background for shadow-mode logging.

    Runs async on a daemon thread; NEVER blocks research or the execute hot path.
    The signal is LOGGED only — NOT fed into the LLM prompt, NOT used for gating.
    This is pure forward-validation so we can measure forecast quality before
    any decision integration.
    """
    try:
        from hermes_trader.agents.chronos_signal import get_chronos_signal_async
        side_str = side or "long" if side else "long"
        get_chronos_signal_async(coin, side_str)
    except Exception as e:
        logger.debug(f"[research] chronos shadow failed for {coin}: {e}")
