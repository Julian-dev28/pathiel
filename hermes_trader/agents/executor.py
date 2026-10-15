"""Auto-executor: validates through risk gates, sizes the trade, executes LIVE.

Integrates the DSL exit engine for two-phase trailing stops
(loss protection -> profit locking).
"""

from __future__ import annotations

import functools
import logging
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from hermes_trader.agents.config_store import read_agent_config
from hermes_trader.agents.duel_store import effective_primary_model
from hermes_trader.agents.chronos_signal import (
    get_chronos_signal_sync,
    resolve_min_conf_ratio as _chronos_resolve_min_conf_ratio,
)
from hermes_trader.agents.dsl_exit import (
    ExitPolicy,
    RetraceTier,
    active_position_coins,
    check_all_positions,
    deregister_position,
    register_position,
)
from hermes_trader.agents.memory import memory
from hermes_trader.ledger import record_open, record_close
from hermes_trader.agents.risk_gates import GateContext, eval_all_gates
from hermes_trader.client.exchange import (
    HL_LEVERAGE,
    cancel_open_orders_for_coin,
    entry_size_for_notional,
    get_hl_atr,
    get_hl_price,
    get_max_leverage,
    min_entry_notional_usd,
    place_hl_order,
    place_hl_trigger_order,
    set_leverage,
)
from hermes_trader.client.hl_client import (fetch_account_state,
                                            fetch_hl_candles, resolve_user_address)

logger = logging.getLogger(__name__)

# Serializes all trade execution (gate eval → sizing → order placement → DSL
# registration → memory/ledger writes) so the parallel research phase can't
# double-spend capital. With research_max_workers > 1 each worker runs
# maybe_execute() concurrently; two of them evaluating max_concurrent / capital
# rotation against the SAME pre-trade account state would both pass and both
# fire, overshooting the position cap. Holding this across the WHOLE function —
# including the rotation retry, which re-enters maybe_execute() and
# close_position_market() on the SAME thread — is why it's an RLock, not a Lock
# (a plain Lock would deadlock on that self-retry). Research (the slow LLM part)
# happens OUTSIDE this lock; only the fast execution tail is serialized.
_exec_lock = threading.RLock()


def _serialized(fn):
    """Run `fn` under _exec_lock so concurrent executions never interleave."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with _exec_lock:
            return fn(*args, **kwargs)
    return wrapper

# Backup server-side stop multiplier. RETUNED 2026-06-02 (microscope audit): was
# 3.5 -> ~5.5% spot on median names, far too wide to catch anything. The data showed
# 54% of max_loss exits GAP PAST the 1.2% DSL cap (median realized -1.56%, worst -3.6%)
# because the DSL loop only checks every 60s. A tighter server-side backup fires
# INSTANTLY at the exchange between our scans, catching the gap cluster. 1.5x ATR sits
# ~2.4% on median names (above the 1.2% DSL so DSL still fires first on normal exits,
# but tight enough to cap the gap-throughs that were the asymmetry killer). Config-tunable.
_DEFAULT_SL_ATR_MULT = 1.5
TP_ATR_MULT = 1.0
# Headroom on the maintenance-aware liquidation distance for the server-side
# backup SL. A stop placed OUTSIDE liq can never fire — the position liquidates
# first — so the SL must clear `liq_safety_frac * (1/lev - maint)`. 0.85 keeps
# the SL reachable while holding live books at their running leverage (a 1.5x
# ATR SL is well inside liq on normal names; only high-ATR / low-maxLeverage
# coins ever trip the bound). CODE default, hot-read, reversible: set
# liq_safety_frac=0 to disable the bound. T2.1/T2.2 port (maintenance-aware liq
# bound + cap leverage to honor the stop, don't shrink it).
_DEFAULT_LIQ_SAFETY_FRAC = 0.85

# Static fallback 24h volumes, used ONLY when the live universe lookup fails.
# WIRING FIX 2026-06-11: these constants used to be the ONLY volume source for
# the liquidity gates — every non-major coin read $10M, so min_short_volume_usd
# (50M) blocked ALL non-major shorts (including the measured short winners) and
# min_market_volume_usd never blocked anything. Real dayNtlVlm now feeds the
# gates; this map is the degraded-read fallback.
_MAJOR_VOLUMES = {
    "BTC": 1e8, "ETH": 1e8, "SOL": 1e8, "BNB": 1e8,
    "XRP": 1e8, "DOGE": 1e8, "ADA": 1e8, "AVAX": 1e8,
}


def _get_market_volume_24h(coin: str) -> float:
    """Real 24h notional volume from the (disk-cached) universe; static fallback."""
    try:
        from hermes_trader.client.universe import get_universe
        for m in get_universe(include_hip3=(":" in coin)):
            if m.get("coin") == coin:
                vol = float(m.get("dayNtlVlm", 0) or 0)
                if vol > 0:
                    return vol
                break
    except Exception as e:
        logger.warning(f"[executor] live volume lookup failed for {coin}: {e} — using static fallback")
    return _MAJOR_VOLUMES.get(coin, 1e7)


def kelly_size(
    confidence: float,
    equity: float,
    reward_risk_ratio: float,
    max_trade_notional: float,
) -> float:
    """Calculate trade size using the half-Kelly criterion."""
    p = confidence
    q = 1 - p
    b = reward_risk_ratio
    f_star = max(0, (p * b - q) / b) if b != 0 else 0
    half_kelly = f_star / 2
    notional = half_kelly * equity
    return min(notional, max_trade_notional)


def liq_safe_leverage(leverage: int, stop_frac: float, coin_max_leverage: int,
                      liq_safety_frac: float = _DEFAULT_LIQ_SAFETY_FRAC) -> int:
    """Highest leverage <= `leverage` whose maintenance-aware liquidation
    distance still clears a server-side stop of width `stop_frac` (fraction of
    entry price, e.g. the 1.5x-ATR backup SL).

    `1/lev` OVERSTATES the isolated liq distance: maintenance margin eats into
    it, so the real liq move is `1/lev - maint`, `maint = 1/(2 * coin_max_lev)`.
    On a high-maxLeverage coin maint is negligible (a maxLev-20 coin at 5x
    liquidates at 17.5% vs the naive 20%); on a low-maxLeverage coin it
    dominates (a maxLev-3 coin at 3x liquidates at 16.7%, not the naive 33%).
    A stop placed BEYOND that distance can never fire — the position is
    liquidated first — so on a violent gap the backup SL would be useless in
    the exact regime it exists to catch.

    We therefore CAP LEVERAGE to keep the stop reachable rather than shrink the
    stop to fit leverage: the stop width (sl_atr_mult x ATR) is the validated
    parameter. The narrower DSL floor (max_loss spot) is the binding NORMAL exit
    and is inside liq by construction (worst live class: maxLev-5 → liq 10%,
    floor 5%), so only the wider backup SL needs this bound.

    `liq_safety_frac` (0.85) is the headroom on the liq bound; 0 disables it.
    Returns `leverage` unchanged when no stop / headroom is requested or it
    already fits; never returns below 1.
    """
    stop = float(stop_frac or 0.0)
    lev = int(leverage)
    safety = float(liq_safety_frac or 0.0)
    if stop <= 0 or safety <= 0 or lev <= 1:
        return max(1, lev)
    max_lev = int(coin_max_leverage or 0)
    maint = (1.0 / (2.0 * max_lev)) if max_lev > 0 else 0.0
    # Both liq distance and the bound are monotonic decreasing in leverage
    # (higher lev → tighter liq), so walk down from the request until the stop
    # fits. +1e-9 absorbs binary-float dust on exact fits.
    while lev > 1:
        liq = 1.0 / lev - maint
        if liq > 0 and stop <= safety * liq + 1e-9:
            return lev
        lev -= 1
    return 1


# Conviction sizing: scale the per-trade equity fraction by AI confidence so
# high-conviction setups bet bigger. The tiers are configurable via the
# `conviction_tiers` config key; these are the defaults if it's unset.
_DEFAULT_CONVICTION_TIERS = [(0.80, 1.5), (0.65, 1.0), (0.0, 0.7)]


def _parse_conviction_tiers(raw: Any) -> List[tuple]:
    """Parse `conviction_tiers` config into descending (threshold, mult) pairs.

    Accepts a list of [min_confidence, size_multiplier] pairs. Falls back to the
    defaults on any malformed input — this runs in the live trade path and must
    never raise. Drops non-positive multipliers; sorts highest-threshold-first
    so the multiplier lookup picks the best tier the confidence clears."""
    if not raw:
        return _DEFAULT_CONVICTION_TIERS
    try:
        tiers = [(float(t[0]), float(t[1])) for t in raw if float(t[1]) > 0]
    except (TypeError, ValueError, IndexError):
        return _DEFAULT_CONVICTION_TIERS
    if not tiers:
        return _DEFAULT_CONVICTION_TIERS
    tiers.sort(key=lambda t: t[0], reverse=True)
    return tiers


def _conviction_multiplier(confidence: float, tiers: List[tuple]) -> float:
    """First tier (descending) whose threshold `confidence` meets wins. Below
    every threshold → the lowest tier's multiplier."""
    for threshold, mult in tiers:
        if confidence >= threshold:
            return mult
    return tiers[-1][1]


def select_exit_params(dsl_config: Dict[str, Any], regime: str,
                       entry_class: Optional[str] = None) -> tuple:
    """Regime-aware exit selection. The base dsl_config is the SCALP config
    (bank fast — +EV in chop/down per the controlled backtest: scalp +$1536/63%
    vs trend-ride -$757/47%). When regime=='up' (sustained up-trend) and
    regime_aware is enabled, LOOSEN to trend-ride params so we RIDE the rippers
    (trend-ride is +EV in trends — that's where it was originally validated).

    `regime_aware.scope` narrows WHICH regime-up trades get trend_ride
    (B.27 scope knob, 2026-09-21): absent / "" / "all" = every regime-up trade
    (original behavior); "late_chase" = ONLY entries that entered via the
    late-chase bypass (`entry_class == "late_chase"`, tagged at open by
    _runner_entry_block_reason). Rationale: the scope A/B replay showed the
    blocked-late-chase cohort is a genuinely good trend_ride population while
    regime-up-WIDE trend_ride is tail-carried (top-5 = 172% of net, win
    76%→61%). An unrecognised scope value fails SAFE to scalp. `enabled`
    remains the master switch: off ⇒ scalp for everyone, scope irrelevant.
    Returns (protect_pct, retrace_threshold, phase2_tiers_raw, label)."""
    base_protect = dsl_config.get("protect_pct", 1.5)
    base_retrace = dsl_config.get("retrace_threshold", 0.30)
    base_tiers = dsl_config.get("phase2_tiers")
    ra = dsl_config.get("regime_aware") or {}
    if ra.get("enabled", False) and regime == "up":
        scope = str(ra.get("scope", "") or "all").strip().lower()
        if scope == "late_chase":
            if entry_class != "late_chase":
                return (base_protect, base_retrace, base_tiers, "scalp")
            tr = ra.get("trend_ride") or {}
            return (float(tr.get("protect_pct", 3.0)),
                    float(tr.get("retrace_threshold", 0.55)),
                    tr.get("phase2_tiers", base_tiers),
                    "trend_ride(up-regime,late_chase)")
        elif scope not in ("", "all"):
            logger.warning(f"[executor] regime_aware.scope={scope!r} unrecognised "
                           f"— failing safe to scalp")
            return (base_protect, base_retrace, base_tiers, "scalp")
        tr = ra.get("trend_ride") or {}
        return (float(tr.get("protect_pct", 3.0)),
                float(tr.get("retrace_threshold", 0.55)),
                tr.get("phase2_tiers", base_tiers),
                "trend_ride(up-regime)")
    return (base_protect, base_retrace, base_tiers, "scalp")


@dataclass(frozen=True)
class MomentumReentryDecision:
    """Outcome of the momentum-continuation re-entry decision.

    Carries the decision AND its three-state meaning:
    ``allowed=True`` (bypass), ``suppressed=True`` (the condition FIRED but
    ``momentum_reentry.shadow_mode`` kept it log-only — the cooldown still
    binds), or neither (no opinion). The decision is pure — no logging — so
    the call sites (which hold the coin name) emit the accrual line via
    :meth:`shadow_accrual_line`, giving that format exactly one owner while
    keeping I/O out of the decision. (The coin must not enter the decision
    API: it is a logging need, not a decision input — the earlier
    ``coin: str = ""`` kwarg was that smuggling, corrected 2026-09-09.)"""
    allowed: bool
    reason: str
    suppressed: bool

    def is_allowed(self) -> bool:
        return self.allowed

    def shadow_accrual_line(self, coin: str) -> str:
        """The counterfactual accrual record for a shadow-suppressed bypass.
        THE single owner of this line's format — both call sites log exactly
        this string, nothing else."""
        return (f"[gate][SHADOW] momentum_reentry WOULD BYPASS cooldown for "
                f"{coin} ({self.reason}) — shadow_mode ON, NOT applying "
                f"(cooldown still binds)")


def momentum_reentry_allowed(last_exit_px, last_side, current_mid, composite,
                             cfg: Dict[str, Any]) -> MomentumReentryDecision:
    """Should we BYPASS the loss-cooldown because a stopped name has RESUMED its
    uptrend? (The autopsy leak: SPCX was force-entered, noise-stopped, then the
    180m loss-cooldown locked us out of its +29% run.) The cooldown is
    anti-revenge — correct for a FALLING name; but a name that breaks back
    ABOVE where it stopped us, with strong composite, is a momentum-continuation
    re-entry, not revenge.

    Conservative + whipsaw-guarded: requires price to reclaim `reclaim_pct`%
    ABOVE the prior stop-out price AND composite >= min_composite. LONG-only.
    Each re-entry that loses re-arms the cooldown at a NEW (higher) stop, so
    repeated whipsaw must clear an ever-rising bar.

    PURE — no coin, no logging: returns a :class:`MomentumReentryDecision`.
    ``momentum_reentry.shadow_mode`` (code default false) does not change the
    decision to ``allowed``; it sets ``suppressed`` when the condition fires
    so the call sites log the counterfactual accrual line while the cooldown
    still binds (no-op merge: shadow absent -> ``allowed`` exactly as today)."""
    mr = cfg.get("momentum_reentry") or {}
    if not mr.get("enabled", False):
        return MomentumReentryDecision(False, "", False)
    shadow = bool(mr.get("shadow_mode", False))
    try:
        last_exit_px = float(last_exit_px or 0)
        current_mid = float(current_mid or 0)
    except (TypeError, ValueError):
        return MomentumReentryDecision(False, "", False)
    if (last_side or "").lower() != "long" or last_exit_px <= 0 or current_mid <= 0:
        return MomentumReentryDecision(False, "", False)
    reclaim = float(mr.get("reclaim_pct", 1.0)) / 100.0
    min_comp = float(mr.get("min_composite", 30))
    if current_mid >= last_exit_px * (1 + reclaim) and float(composite or 0) >= min_comp:
        gain = (current_mid / last_exit_px - 1) * 100
        reason = (f"reclaimed +{gain:.1f}% above stop {last_exit_px:g}, "
                  f"composite {float(composite or 0):.0f}")
        if shadow:
            # The condition HOLDS but the bypass must not apply: mark it
            # shadow-suppressed so BOTH call sites log the same accrual line
            # and the cooldown still binds. No log here — the decision stays
            # pure; the call sites (which know the coin) emit it.
            return MomentumReentryDecision(False, reason, True)
        return MomentumReentryDecision(True, reason, False)
    return MomentumReentryDecision(False, "", False)


def _attach_chronos_to_result(result: Dict[str, Any], coin: str, side: str) -> None:
    """Attach compact Chronos fields to a trade result dict.

    Wrapped in try/except so it never breaks the trade path.
    Output shape:
      chronos_median_pct: float | null
      chronos_aligned: bool | null (True if median move agrees with side)
      chronos_error: str | null
    """
    try:
        sig = get_chronos_signal_sync(coin, side)
        result["chronos_median_pct"] = round(sig.median_pct * 10) / 10 if sig.median_pct is not None else None
        if sig.median_pct is not None:
            result["chronos_aligned"] = (
                (side == "long" and sig.median_pct > 0) or (side == "short" and sig.median_pct < 0)
            )
        else:
            result["chronos_aligned"] = None
        # Tail value for the chronos_tail_trigger_gate shadow log: adverse
        # quantile extreme over the first `window_steps` steps of the path
        # (min q10 for longs, max q90 for shorts). Same warm cache, no
        # recompute. None when the signal predates per-step path storage.
        _tcfg = None
        try:
            from hermes_trader.agents.config import get_config as _get_cfg
            _tcfg = (_get_cfg().get("chronos_tail_trigger_gate") or {})
        except Exception:
            _tcfg = {}
        _k = int((_tcfg or {}).get("window_steps", 6) or 6)
        _tpath = (sig.q10_path_pct if side == "long" else sig.q90_path_pct) or None
        if _tpath and len(_tpath) >= _k:
            _tw = _tpath[:_k]
            _tail = min(_tw) if side == "long" else max(_tw)
            result["chronos_tail_pct"] = round(_tail * 10) / 10
        else:
            result["chronos_tail_pct"] = None
        result["chronos_error"] = sig.error
    except Exception as e:
        result["chronos_median_pct"] = None
        result["chronos_aligned"] = None
        result["chronos_error"] = str(e)


def _attach_timesfm_to_result(result: Dict[str, Any], coin: str, side: str) -> None:
    """Attach compact TimesFM-3 shadow fields to a trade result dict.

    SHADOW ONLY (Google TimesFM-3, Aug 2026): logged next to the Chronos
    fields so the two forecasters are directly comparable on the identical
    5m context (same candle cache, same horizon default). Never gates, never
    sizes. Config-gated off by default — and note the weights are under the
    non-commercial license, so enabling against the live book is a conscious
    operator decision. Wrapped in try/except so it never breaks the trade
    path. Output shape:
      timesfm_median_pct: float | null
      timesfm_aligned: bool | null (True if median move agrees with side)
      timesfm_error: str | null
    """
    try:
        from hermes_trader.agents.timesfm_signal import get_timesfm_signal_sync
        sig = get_timesfm_signal_sync(coin, side)
        result["timesfm_median_pct"] = round(sig.median_pct * 10) / 10 if sig.median_pct is not None else None
        if sig.median_pct is not None:
            result["timesfm_aligned"] = (
                (side == "long" and sig.median_pct > 0) or (side == "short" and sig.median_pct < 0)
            )
        else:
            result["timesfm_aligned"] = None
        result["timesfm_error"] = sig.error
    except Exception as e:
        result["timesfm_median_pct"] = None
        result["timesfm_aligned"] = None
        result["timesfm_error"] = str(e)


def _attach_tirex_to_result(result: Dict[str, Any], coin: str, side: str) -> None:
    """Attach compact TiRex 1.1 shadow fields to a trade result dict.

    SHADOW ONLY (NX-AI TiRex 1.1, Sep 2026): logged next to the Chronos and
    TimesFM fields so all three forecasters are directly comparable on the
    identical 5m context. Never gates, never sizes. Chosen for shadow because
    it was the best single in the 2026-09-16 MOE evaluation (median-MAE +
    tail-AUC both axes; scratch/eval/moirai_eval/RESULTS.md) — candidate for
    a drop-in chronos replacement. Cache-first sync read (the loop fire
    warms it); config-gated off by default. Wrapped in try/except so it never
    breaks the trade path. Output shape:
      tirex_median_pct: float | null
      tirex_aligned: bool | null (True if median move agrees with side)
      tirex_error: str | null
    """
    try:
        from hermes_trader.agents.tirex_signal import get_tirex_signal_sync
        sig = get_tirex_signal_sync(coin, side)
        result["tirex_median_pct"] = round(sig.median_pct * 10) / 10 if sig.median_pct is not None else None
        if sig.median_pct is not None:
            result["tirex_aligned"] = (
                (side == "long" and sig.median_pct > 0) or (side == "short" and sig.median_pct < 0)
            )
        else:
            result["tirex_aligned"] = None
        result["tirex_error"] = sig.error
    except Exception as e:
        result["tirex_median_pct"] = None
        result["tirex_aligned"] = None
        result["tirex_error"] = str(e)


def _attach_expert_select_to_result(result: Dict[str, Any], coin: str, side: str) -> None:
    """Attach compact MoiraiAgent expert-selection shadow fields to a trade
    result dict.

    SHADOW ONLY (Salesforce MoiraiAgent expert-selection, ported 2026-09): a
    pool of candidate forecasters (chronos/timesfm/tirex/moirai2) is
    cross-validated on the history tail and an LLM picks the winner (or a
    re-centered mixture); the final re-centered-mixture median is logged next
    to the single-model forecasts so model SELECTION quality is measurable on
    the identical 5m context. Never gates, never sizes, never enters the
    verdict prompt. Cache-first — the sync attach reads the per-coin cache
    only (never blocks the exec loop); a cold/absent entry yields the
    disabled-equivalent None fields. Config-gated off by default
    (expert_select.enabled: false). Wrapped in try/except so it never breaks
    the trade path. Output shape:
      expert_median_pct: float | null
      expert_aligned: bool | null (True if median move agrees with side)
      expert_selected_model: str | null (chronos/timesfm/tirex/moirai2/mixture)
      expert_error: str | null
    """
    try:
        from hermes_trader.agents.expert_select import peek_expert_select
        sig = peek_expert_select(coin)
        if sig is None or sig.median_pct is None:
            result["expert_median_pct"] = None
            result["expert_aligned"] = None
            result["expert_selected_model"] = sig.selected_model if sig else None
            result["expert_error"] = (sig.error if sig else "no cached selection")
            return
        result["expert_median_pct"] = round(sig.median_pct * 10) / 10
        result["expert_aligned"] = (
            (side == "long" and sig.median_pct > 0) or (side == "short" and sig.median_pct < 0)
        )
        result["expert_selected_model"] = sig.selected_model
        result["expert_error"] = sig.error
    except Exception as e:
        result["expert_median_pct"] = None
        result["expert_aligned"] = None
        result["expert_selected_model"] = None
        result["expert_error"] = str(e)


def _attach_llm_context_to_result(result: Dict[str, Any], analysis: Dict[str, Any]) -> None:
    """Attach the LLM's own context to a trade result dict.

    The `Trade result:` line is the one that answers "why did the executor
    block/execute this?", but the blocked results were flat: reason +
    forecast attachments only. Everything below is already computed on
    `analysis` earlier in the cycle — carrying it onto the result means
    blocked-trade forensics (counterfactual P/L sweeps) don't parse English
    out of `reason` or join to the `Verdict:` / `Researching` lines by
    timestamp. Applied at every trade-result path (execute blocked,
    executed, no-action PASS/VETO). Output shape:
      llm_confidence: float | null — model conviction (null = no real verdict,
                                     e.g. a failure-PASS)
      llm_model: str | null        — the LLM that produced it (model-switch
                                     forensics without triangulating logs)
      ai_down: bool                — research call failed (402/timeout): the
                                     verdict is an error code, not an opinion
      composite_score: float | null— the number the runner/whale/duelist gates
                                     key off (null = not computed this cycle)
      volume_spike_fired / breakout_fired / momentum_burst_fired /
      daily_mover_fired / slow_burn_count / whale_fired — the raw TA inputs
                                     behind the fresh_impulse/structured logic
                                     encoded in `reason`
    """
    try:
        # A failure-PASS arrives as confidence 0.0 + ai_down=True — the model
        # never spoke, so record null conviction (not a fake 0.0 "maximally
        # uncertain" verdict): null + ai_down is unambiguous.
        _c = None if analysis.get("ai_down") else analysis.get("confidence")
        result["llm_confidence"] = round(float(_c), 2) if _c is not None else None
        # The model that ANSWERED this call (carried on the analysis by
        # research.research) — mirrors the trading_loop's `Verdict:` line.
        # The env/helper fallback only covers analyses built before this key
        # existed.
        result["llm_model"] = analysis.get("primary_model") or effective_primary_model()
        result["ai_down"] = bool(analysis.get("ai_down"))
        _cs = analysis.get("composite_score")
        result["composite_score"] = round(float(_cs), 1) if _cs is not None else None
        result["volume_spike_fired"] = bool(analysis.get("volume_spike_fired"))
        result["breakout_fired"] = bool(analysis.get("breakout_fired"))
        result["momentum_burst_fired"] = bool(analysis.get("momentum_burst_fired"))
        result["daily_mover_fired"] = bool(analysis.get("daily_mover_fired"))
        result["slow_burn_count"] = int(analysis.get("slow_burn_count", 0) or 0)
        result["whale_fired"] = bool(analysis.get("whale_signal"))
    except Exception:
        # Fail closed: never raise into the trade path, never fake a value.
        result["llm_confidence"] = None
        result["llm_model"] = None
        result["ai_down"] = False
        result["composite_score"] = None
        result["volume_spike_fired"] = False
        result["breakout_fired"] = False
        result["momentum_burst_fired"] = False
        result["daily_mover_fired"] = False
        result["slow_burn_count"] = 0
        result["whale_fired"] = False


def build_open_config_snapshot(regime: str, exit_policy_label: str,
                               sl_atr_mult: float, tp_atr_mult: float) -> Dict[str, Any]:
    """Build the OPEN-row config snapshot for the append-only ledger.

    Cooldown windows are read FRESH from .agent-config.json (not a cached
    module-level copy) because the file hot-reloads every cycle, and the
    snapshot's whole job is to let a future incident be reconstructed
    without guessing what was in effect at entry time.
    """
    _live = read_agent_config()
    return {
        "regime": regime,
        "exit_policy_label": exit_policy_label,
        "sl_atr_mult": sl_atr_mult,
        "tp_atr_mult": tp_atr_mult,
        "cooldown_min": _live.get("cooldown_min"),
        "loss_cooldown_min": _live.get("loss_cooldown_min"),
    }


@_serialized
def maybe_execute(analysis: Dict[str, Any], _rotation_retry: bool = False) -> Dict[str, Any]:
    """Execute an analysis through risk gates and into the market.

    `_rotation_retry` is set on the single self-retry after capital rotation
    closed a weak position to free room — it blocks a second rotation so we can
    never loop.
    """
    config = read_agent_config()
    mode = str(config.get("mode", "OFF")).upper()

    if mode == "OFF":
        return {
            "executed": False, "mode": mode,
            "analysis_id": analysis["id"], "reason": "mode_off",
        }
    shadow_mode = mode == "SHADOW"

    # AI active rejection (2026-09-03): a VETO verdict means "this setup is a
    # trap — avoid like the plague", categorically stronger than PASS. Refuse
    # it here BEFORE the structural-override section: unlike a hedged PASS, a
    # VETO must never be upgraded to LONG by whale/composite/breakout force
    # paths. This guard also closes the direct-call hole (server.py /execute
    # and the MCP server call maybe_execute with any remembered analysis):
    # every downstream PASS guard is an equality check, so an unhandled VETO
    # would fall through to `trade_side = side or "long"` and blind-LONG.
    if str(analysis.get("verdict") or "").upper() == "VETO":
        return {
            "executed": False, "mode": mode,
            "analysis_id": analysis["id"],
            "reason": f"ai_veto (active rejection: {str(analysis.get('reasoning') or '')[:160]})",
        }

    # Asset-class gate. Mirrors the perception-time filter so a stale
    # perception (e.g. one re-evaluated from memory after the operator
    # flips the flag) can't sneak through to a real trade. Crypto =
    # native HL coin (no colon); HIP-3 = colon-namespaced (`xyz:MU`).
    is_hip3 = ":" in (analysis.get("coin") or "")
    if is_hip3 and not bool(config.get("enable_hip3", False)):
        return {
            "executed": False, "mode": mode,
            "analysis_id": analysis["id"],
            "reason": "hip3_disabled (set enable_hip3=true to trade tokenized-equity perps)",
        }
    if (not is_hip3) and not bool(config.get("enable_crypto", True)):
        return {
            "executed": False, "mode": mode,
            "analysis_id": analysis["id"],
            "reason": "crypto_disabled (set enable_crypto=true to trade native HL perps)",
        }

    # Shadow-signals (free-signal suite): log what GEX / FINRA short-vol / whale /
    # news WOULD say about this candidate, to validate them forward before any is
    # allowed to gate entries. Fire-and-forget on a daemon thread so it can NEVER
    # add latency or amplify the execute hot path. Gated + hot-read reversible.
    _shadow_cfg = config.get("shadow_signals") or {}
    if bool(_shadow_cfg.get("enabled", False)):
        try:
            from hermes_trader.agents.shadow_signals import run_shadow_async
            run_shadow_async(analysis["coin"], analysis.get("side", "long"), _shadow_cfg)
        except Exception as _sh_e:
            logger.debug(f"[shadow-signals] dispatch failed (non-fatal): {_sh_e}")

    # Structural-override: don't let a hedging AI PASS leave an objectively
    # strong accumulation setup on the table. Upgrade to LONG conf 0.70 and
    # let the gates do the real risk check. Two independent triggers, both
    # LONG-biased (we never force a SHORT):
    #   (a) composite >= 40 AND 2+ slow-burn 1h triggers fired, OR
    #   (b) a whale-accumulation signal fired (oi_funding_anomaly) —
    #       whale signals get their own override because smart-money loading
    #       (negative funding, flat price, high OI) is a high-conviction
    #       contrarian-to-retail setup we want to capitalize on even when the
    #       AI hedges and even against trend.
    # Live signal enforcement (Veto + Boost, 2026-06-16): consult our free signals
    # (GEX / FINRA short-vol / aggTrades whale / news) to gate the FORCED-OVERRIDE
    # path. CACHE-ONLY (never fetches here — the async shadow advisor above warms
    # the caches; cold cache => fail-open). BOOST lowers the override bar for a name
    # with a strong catalyst (breaking news / whale buying / crowded-short squeeze)
    # so we catch more rippers; VETO (applied below) blocks chop-traps / whales
    # dumping. Bounded: never bypasses the risk/regime/counter-trend/kill gates.
    _enf = None
    _base_override_composite = float(config.get("force_execute_composite", 40))
    override_composite = _base_override_composite
    try:
        from hermes_trader.agents.shadow_signals import enforce_signals
        _enf = enforce_signals(analysis["coin"], "long", config)
        if _enf and _enf.boost:
            _delta = float((config.get("signal_enforcement") or {}).get("boost_bar_delta", 4))
            override_composite = max(0.0, _base_override_composite - _delta)
            logger.info(f"[executor] signal BOOST on {analysis['coin']}: "
                        f"override bar {_base_override_composite:.0f}→{override_composite:.0f} "
                        f"({_enf.boost_reason})")
    except Exception as _enf_e:
        logger.debug(f"[executor] signal enforcement failed (non-fatal): {_enf_e}")
    override_min_slow_burn = int(config.get("force_execute_slow_burn_count", 2))
    # whale_force_execute gates whether a whale signal alone can upgrade a PASS.
    whale_fired = bool(analysis.get("whale_signal")) and bool(config.get("whale_force_execute", False))
    slow_burn_strong = (
        bool(config.get("composite_force_execute", False))
        and
        float(analysis.get("composite_score", 0) or 0) >= override_composite
        and int(analysis.get("slow_burn_count", 0) or 0) >= override_min_slow_burn
    )
    # Breakout force-execute (O'Neil rule, added 2026-06-12): a 20-period-high
    # break WITH a volume spike and composite >= bar is an objectively strong
    # setup — the AI hedged these to PASS 21x on XPL while it ran +32% (38
    # researches, zero LONG verdicts, no gate ever blocked it). LONG path only:
    # forced shorts are the audit's worst bucket (AI shorts 0/8).
    # RETUNED same-day (forensic on own rule): XPL's composite never exceeded
    # 4.6 — the >=40 bar was DEAD for volume-surge setups (normalized composite
    # barely moves on 1-2 fired triggers), and `breakout` never co-fired at scan
    # times. XPL's actual signature: volumeSpike + uptrendMomentum + >=1 slow-burn
    # (volumeBuildup1h/higherLows1h). Qualify on that, with composite>=bar kept
    # as an alternative for true high-composite breaks.
    breakout_strong = (
        bool(config.get("breakout_force_execute", False))
        and bool(analysis.get("volume_spike_fired"))
        and (bool(analysis.get("breakout_fired"))
             or bool(analysis.get("uptrend_momentum_fired")))
        and (int(analysis.get("slow_burn_count", 0) or 0) >= 1
             or float(analysis.get("composite_score", 0) or 0) >= override_composite)
    )
    # Composite force-execute (validated 2026-06-15): a TA-CONFIRMED signal whose
    # composite clears the bar enters even on an AI PASS, REGARDLESS of the
    # breakout/whale pattern. Root cause it fixes: the AI lagged/vetoed real movers
    # the funnel had already confirmed (GRASS confirmed at the base → AI PASS 8h;
    # xyz equities WDC/BIRD at composite 34 → PASS, never entered). Replay across
    # the 64 PASS'd composite-30 names was net +121.8% spot / 64% win, duds capped
    # at the ROE stop (−1.85% @10x); xyz subset +17.4%. LONG-only; the market_regime
    # gate STILL blocks counter-trend longs (override conf < counter_regime bar), so
    # downtrend duds are filtered. Gated `composite_force_execute` (hot-read → revert
    # instantly to 40/off if a down regime floods duds).
    composite_strong = (
        bool(config.get("composite_force_execute", False))
        and float(analysis.get("composite_score", 0) or 0) >= override_composite
    )
    ta_sidestep_strong = (
        bool(config.get("ta_sidestep_force_execute", False))
        and (
            float(analysis.get("composite_score", 0) or 0) >= override_composite
            or bool(analysis.get("momentum_burst_fired"))
            or int(analysis.get("slow_burn_count", 0) or 0) >=
            int(config.get("ta_sidestep_min_slow_burn_count", 1) or 1)
        )
    )
    override_strong = (
        slow_burn_strong or whale_fired or breakout_strong
        or composite_strong or ta_sidestep_strong
    )
    # A PASS produced by a FAILED LLM call (402/timeout → ai_down) is an error
    # code, not a hedged opinion — upgrading it trades blind with no AI judgment
    # behind the entry AND no working AI close behind the exit. Refuse the
    # structural/whale upgrade on those unless the operator explicitly opts out
    # via override_requires_ai=false (reversible).
    _ai_down_block = bool(analysis.get("ai_down")) and \
        bool(config.get("override_requires_ai", True))
    if analysis.get("verdict") == "PASS" \
            and override_strong \
            and _ai_down_block:
        logger.info(
            f"[executor] Structural override SKIPPED on {analysis['coin']}: "
            f"AI research is DOWN (failure-PASS, not an opinion) — no blind upgrade"
        )
        return {
            "executed": False, "mode": mode,
            "analysis_id": analysis["id"],
            "reason": "override_blocked_ai_down (research failed; PASS is an error, not a verdict)",
        }
    # Signal VETO (Veto+Boost live, 2026-06-16): block a FORCED override LONG when
    # our free signals say it's a trap — xyz pinned in long-gamma against the call
    # wall (GEX pin-trap), or crypto with whales aggressively DUMPING (aggTrades).
    # CACHE-ONLY (computed above in `_enf`, no network here). The broader HIP-3
    # GEX entry veto for normal LONGs lives in _runner_entry_block_reason below.
    # Fully reversible via signal_enforcement.enabled / .veto (hot-read).
    # gex_signal.shadow_mode (if set) still downgrades the GEX veto to log-only.
    if analysis.get("verdict") == "PASS" \
            and override_strong \
            and _enf is not None and _enf.veto:
        _gex_shadow = ":" in analysis["coin"] and \
            bool((config.get("gex_signal") or {}).get("shadow_mode", False))
        if _gex_shadow:
            logger.info(f"[executor] signal VETO [GEX SHADOW — not blocked] on "
                        f"{analysis['coin']}: {_enf.veto_reason}")
            analysis = dict(analysis)
            analysis["signal_veto"] = _enf.veto_reason
        else:
            logger.info(f"[executor] signal VETO — forced override SKIPPED on "
                        f"{analysis['coin']}: {_enf.veto_reason}")
            return {
                "executed": False, "mode": mode,
                "analysis_id": analysis["id"],
                "reason": f"signal_veto ({_enf.veto_reason})",
            }

    # Falling-knife guard (port of upstream 51bc23b): the composite triggers
    # the sidestep qualifies on (pctMoveSpike/volumeSpike/shockDay/momentumBurst)
    # are MAGNITUDE-based, so a violent SELLOFF fires them like a breakout and
    # the PASS->LONG upgrade would buy the open of a red breakdown bar (the
    # xyz:SMSN case). Skip the upgrade when the direction is bearish (downtrend
    # momentum or a clearly-negative 24h move, no uptrend). Sidestep path ONLY —
    # the whale/breakout/composite override branches keep their own gates.
    if analysis.get("verdict") == "PASS" and ta_sidestep_strong:
        _bear_block = _sidestep_bearish_block_reason(analysis, config)
        if _bear_block:
            logger.info(f"[executor] TA sidestep SKIPPED on {analysis['coin']}: {_bear_block}")
            return {
                "executed": False, "mode": mode,
                "analysis_id": analysis["id"],
                "reason": _bear_block,
            }

    if analysis.get("verdict") == "PASS" and override_strong:
        trigger = ("whale-accumulation" if whale_fired
                   else f"composite={analysis.get('composite_score'):.0f}+{analysis.get('slow_burn_count')} slow-burn"
                   if slow_burn_strong
                   else "breakout+volume (O'Neil)" if breakout_strong
                   else "TA sidestep" if ta_sidestep_strong
                   else f"composite={analysis.get('composite_score'):.0f}>={override_composite:.0f} (momentum force)")
        # Upgrade to the configured confidence floor (not a hardcoded 0.70) so a
        # structural/whale override still clears the confidence_gate after the bar
        # is raised. Otherwise raising min_ai_confidence would silently kill the
        # whale overrides — empirically the one flat-positive bucket.
        _conf_floor = float(config.get("min_ai_confidence", 0.70))
        logger.info(
            f"[executor] Structural override on {analysis['coin']}: "
            f"AI PASS but {trigger} → upgrading to LONG conf {_conf_floor:.2f}"
        )
        analysis = dict(analysis)
        analysis["verdict"] = "LONG"
        analysis["side"] = "long"
        analysis["confidence"] = max(_conf_floor, float(analysis.get("confidence", 0) or 0))
        if ta_sidestep_strong:
            analysis["sidestep_override"] = True
        analysis["reasoning"] = (
            "[structural override] " + (analysis.get("reasoning", "") or "")
        )[:500]

    # Safety guard: a PASS that did NOT qualify for the structural override must
    # never reach order placement (trade_side defaults to "long" downstream, so
    # an un-upgraded PASS would otherwise silently fire a long). route_verdict
    # only sends a PASS here when an override HINT applies — this is the real
    # check that no-ops cleanly when the override doesn't actually hold.
    if analysis.get("verdict") == "PASS":
        return {
            "executed": False, "mode": mode,
            "analysis_id": analysis["id"], "reason": "pass_no_override",
        }

    # Loss cooldown: refuse re-entry on a coin whose last close was a LOSS and
    # whose extended block hasn't expired (armed in close_position_market).
    # Checked BEFORE the runner entry gate: the runner gate is an entry-quality
    # filter (fresh impulse only) while the cooldown is an anti-revenge RULE —
    # when both would block, the trade result should say WHY the rule fired,
    # not the quality filter that happened to run first (2026-08-26 ZEC: the
    # 180min cooldown was armed but the log read runner_gate_blocked, which
    # looked like the cooldown "wasn't working" — it was, just masked).
    _lc_remaining = memory.loss_cooldown_remaining_min(analysis["coin"])
    if _lc_remaining > 0:
        # Momentum-continuation re-entry: if the name has reclaimed above where it
        # stopped us (resumed uptrend, strong composite), bypass the anti-revenge
        # cooldown — that's a run we got shaken out of, not a falling knife.
        _last = memory.last_close_for(analysis["coin"]) or {}
        _mr = momentum_reentry_allowed(
            _last.get("exit_px"), _last.get("side"),
            analysis.get("mid"), analysis.get("composite_score"), config)
        if _mr.suppressed:
            # The re-entry condition FIRED but shadow_mode keeps it log-only —
            # the counterfactual accrual record. The cooldown still binds
            # below (the block branch is reached: is_allowed() is False).
            logger.warning(_mr.shadow_accrual_line(analysis["coin"]))
        if _mr.is_allowed():
            logger.info(f"[executor] momentum re-entry on {analysis['coin']}: "
                        f"{_mr.reason} — bypassing {_lc_remaining:.0f}min loss cooldown")
        else:
            return {
                "executed": False, "mode": mode,
                "analysis_id": analysis["id"],
                "reason": (f"loss_cooldown ({analysis['coin']} closed at a loss recently — "
                           f"{_lc_remaining:.0f}min remaining)"),
            }

    _runner_cfg = config.get("runner_entry_gate") or {}
    _sidestep_bypasses_runner = (
        bool(analysis.get("sidestep_override"))
        and bool(_runner_cfg.get("bypass_sidestep_overrides", False))
    )
    if not _sidestep_bypasses_runner:
        runner_block = _runner_entry_block_reason(analysis, config)
        if runner_block:
            coin = analysis.get("coin") or "unknown"
            side = analysis.get("side", "long") or "long"
            result = {
                "executed": False, "mode": mode,
                "analysis_id": analysis["id"], "reason": runner_block,
            }
            _attach_chronos_to_result(result, coin, side)
            _attach_timesfm_to_result(result, coin, side)
            _attach_tirex_to_result(result, coin, side)
            _attach_expert_select_to_result(result, coin, side)
            return result

    # Decision-voice entry trap-veto (clef-gate scope Change 2) — same site
    # and same forensics path as the runner gate; shadow_mode logs the
    # would-be-block and lets the trade through (decision_voice.py observer
    # must be armed for this to ever see a trap scalar; fail-open without it).
    dv_block = _dv_trap_veto_reason(analysis, config)
    if dv_block:
        coin = analysis.get("coin") or "unknown"
        side = analysis.get("side", "long") or "long"
        result = {
            "executed": False, "mode": mode,
            "analysis_id": analysis["id"], "reason": dv_block,
        }
        _attach_chronos_to_result(result, coin, side)
        _attach_timesfm_to_result(result, coin, side)
        _attach_tirex_to_result(result, coin, side)
        _attach_expert_select_to_result(result, coin, side)
        return result

    # Idempotency: don't double-execute
    already = next(
        (t for t in memory.get_recent_trades(100)
         if t.get("analysis_id") == analysis["id"] and t.get("size_usd", 0) > 0),
        None,
    )
    if already:
        return {
            "executed": False, "mode": mode,
            "analysis_id": analysis["id"], "reason": "already_executed",
            "order_id": already.get("order_id"),
        }

    user = resolve_user_address()

    # HIP-3 dex-balance preflight: dexes are separate clearinghouses, so
    # refuse cleanly when the target dex truly has no funds. Distinguishes
    # "API returned $0" from "API call failed / returned no marginSummary" —
    # the latter is a transient lookup failure (the per-dex endpoint flakes
    # intermittently) and shouldn't be reported as "underfunded" when funds
    # are sitting on the dex. One retry, then back off rather than block
    # falsely with a wire-USDC-to-dex error.
    coin_for_dex_check = analysis["coin"]
    if ":" in coin_for_dex_check:
        dex_name = coin_for_dex_check.split(":", 1)[0]
        from hermes_trader.client.hl_client import _http_post

        def _read_dex_value() -> tuple[bool, float]:
            try:
                state_resp = _http_post("/info", {
                    "type": "clearinghouseState", "user": user, "dex": dex_name,
                })
            except Exception as e:
                logger.warning(f"[executor] HIP-3 dex query raised for {dex_name}: {e}")
                return (False, 0.0)
            ms = (state_resp or {}).get("marginSummary")
            if not ms:
                return (False, 0.0)  # No marginSummary → response missing/malformed
            return (True, float(ms.get("accountValue", 0) or 0))

        ok, dex_value = _read_dex_value()
        if not ok:
            import time as _time
            _time.sleep(0.3)
            ok, dex_value = _read_dex_value()

        if not ok:
            logger.warning(f"[executor] HIP-3 dex-balance lookup failed twice for {dex_name}; letting HL adjudicate")
            # Fall through and let HL reject if it has to — better than
            # falsely claiming the dex is empty when we couldn't verify.
        elif dex_value < 1.0:
            return {
                "executed": False, "mode": mode,
                "analysis_id": analysis["id"],
                "reason": (
                    f"hip3_dex_underfunded ({dex_name}: ${dex_value:.2f}). "
                    f"Transfer USDC to '{dex_name}' via the HL frontend."
                ),
            }

    # include_hip3=True so the concurrency + exposure gates COUNT every open
    # position, including tokenized-equity (xyz:) HIP-3 perps. The old main-only
    # fetch returned 0 positions / $0 notional whenever the book was all HIP-3,
    # so max_concurrent and equity_risk never capped it — the book ballooned
    # past max_concurrent (to ~22) and to ~17x notional. Sizing still uses the
    # MAIN-dex clearinghouse ("") equity/available below, so per-trade size is
    # unchanged; only the gate inputs are corrected to the aggregated book.
    # The per-dex clearinghouse endpoint flakes under burst load (several
    # executes in one cycle), returning $0 for the MAIN dex even when funds are
    # there — which used to spuriously block real trades with "equity_unavailable
    # (live account state returned 0)" while the account was healthy. Read once,
    # and on a $0 main-equity read retry up to twice before believing it. A
    # genuine $0 still refuses (never size an unsized order); a transient blip
    # recovers.
    # PER-DEX FIX 2026-06-12: each dex is a separate clearinghouse, so a HIP-3
    # trade must be sized and margin-checked against ITS OWN dex's equity and
    # available margin — not the main dex's. Before this, xyz:DRAM was blocked
    # with "available $0.00 / equity $39.90" (main dex) while the xyz dex held
    # $59.04 free: HIP-3 entries starved whenever main margin was committed,
    # and vice versa. Main-dex (crypto) trades behave exactly as before.
    _target_dex = analysis["coin"].split(":", 1)[0] if ":" in analysis["coin"] else ""

    def _read_state() -> tuple[dict, float, float]:
        st = fetch_account_state(user, include_hip3=True) or {}
        logger.info(f"[executor] raw account state: equity={st.get('equity')!r} available={st.get('available')!r} spot_usdc={st.get('spot_usdc')!r}")
        deq = st.get("dex_equity") or {}
        dav = st.get("dex_available") or {}
        if _target_dex:
            eq = float(deq.get(_target_dex, 0) or 0)
            av = float(dav.get(_target_dex, 0) or 0)
        else:
            eq = float(deq.get("", st.get("equity")) or 0)
            av = float(dav.get("", st.get("available")) or 0)

        # For unified accounts: spot USDC is shared margin for perps.
        # If perp equity is 0 but we have spot USDC, treat it as available.
        spot_usdc = float(st.get("spot_usdc", 0) or 0)
        if eq == 0.0 and spot_usdc > 0:
            logger.info(f"[executor] Unified account detected — using spot_usdc={spot_usdc:.2f} as equity")
            eq = spot_usdc
            av = max(av, spot_usdc)

        logger.info(f"[executor] resolved equity={eq:.2f} available={av:.2f} target_dex={_target_dex or 'main'}")
        return st, eq, av

    state, equity, available = _read_state()
    for _attempt in range(2):
        if equity > 0:
            break
        import time as _t
        _t.sleep(0.4)
        state, equity, available = _read_state()
    agg_equity = float(state.get("equity") or equity)                # aggregated → exposure gate
    total_open_notional = float(state.get("total_ntl") or 0)         # aggregated → notional gate
    # Per-account sizing equity: a trade is FUNDED by one account (main for crypto,
    # the specific HIP-3 dex for colon coins), so the per-trade RISK budget must be
    # based on THAT account's equity, not the aggregate. Sizing on aggregate over-sizes
    # vs the balance that actually funds the trade -> the funding account saturates and
    # every other mover is margin-blocked. `equity` above is already the per-`_target_dex`
    # resolution; fall back to aggregate only on a degraded/missing breakdown (equity<=0).
    # (Upstream 729be391cac3.) `agg_equity` stays for the aggregate exposure/gross caps.
    size_equity = float(equity) if (equity or 0) > 0 else agg_equity
    if equity <= 0:
        # Persisted across retries — refuse rather than send an unsized order.
        return {
            "executed": False, "mode": mode,
            "analysis_id": analysis["id"],
            "reason": "equity_unavailable (live account state returned 0 after retries)",
        }

    # Free-margin floor: leave headroom for maintenance + slippage so HL
    # doesn't reject mid-pipeline with "Insufficient margin".
    min_avail_pct = float(config.get("min_available_margin_pct", 0.10))
    if equity > 0 and (available / equity) < min_avail_pct:
        return {
            "executed": False, "mode": mode,
            "analysis_id": analysis["id"],
            "reason": (f"insufficient_free_margin on dex '{_target_dex or 'main'}' "
                       f"(available ${available:.2f} / equity ${equity:.2f} = "
                       f"{100*available/equity:.1f}%, floor {100*min_avail_pct:.0f}%)"),
        }

    # Track daily PnL off the AGGREGATE equity (main + HIP-3), not main-dex-only
    # `equity` (which is kept main-only for margin sizing). Using main-only here
    # poisoned daily_pnl/peak vs the heartbeat's aggregate — it read ~$30 low and
    # spuriously fired the daily give-back breaker (saw day $24 vs true $54).
    memory.track_daily_pnl(agg_equity)
    daily_pnl = memory.get_daily_pnl()

    # Daily-halt timer policy (pairs with daily_loss_kill_switch): arm when
    # the day breaches the equity-relative kill threshold; clear early when
    # the day has recovered above the release band (a real regime change
    # back), so entries resume before the timer would expire. Early-release
    # only counts on the day the halt was ARMED — after a UTC roll the fresh
    # day's ~0 PnL proves nothing, and the halt runs to its own expiry.
    from hermes_trader.agents.risk_gates import effective_daily_kill_usd
    from datetime import datetime as _dt_halt, timezone as _tz_halt
    _kill_thr = effective_daily_kill_usd(config, agg_equity)
    if _kill_thr > 0:
        _halt_cfg = config.get("daily_loss_halt") or {}
        _halt_min = float(_halt_cfg.get("halt_min", 360) or 360)
        _rel_band = float(_halt_cfg.get("release_band_pct", 0.5) or 0.5)
        _today_utc = _dt_halt.now(_tz_halt.utc).strftime("%Y-%m-%d")
        if daily_pnl <= -_kill_thr:
            if memory.daily_halt_remaining_min() <= 0:
                memory.arm_daily_halt(_halt_min, utc_day=_today_utc)
                logger.warning(
                    f"[executor] DAILY LOSS HALT armed: day PnL ${daily_pnl:.2f} "
                    f"<= -${_kill_thr:.2f} (equity ${agg_equity:.2f}) — no new "
                    f"entries for {_halt_min:.0f}min unless day recovers")
        elif (memory.daily_halt_remaining_min() > 0
                and memory.daily_halt_armed_day() == _today_utc
                and daily_pnl > -_kill_thr * _rel_band):
            memory.clear_daily_halt()
            logger.info(f"[executor] daily loss halt CLEARED early: day PnL recovered "
                        f"to ${daily_pnl:.2f} (release band -${_kill_thr * _rel_band:.2f})")

    positions = [
        {
            "coin": p["position"]["coin"],
            "side": "long" if float(p["position"]["szi"]) > 0 else "short",
            "size_usd": abs(float(p["position"]["szi"])) * (analysis.get("entry_px") or 0),
        }
        for p in state["asset_positions"]
    ]

    # Restart-safe re-entry backstop: a flaky/empty live account read can drop a
    # held position from asset_positions, letting opposite_direction_guard fail
    # open and STACK the position (observed: xyz:SP500 pyramided to ~8x during a
    # restart's rehydration window). The DSL registry rehydrates from disk, so
    # merge any tracked coin the live read missed — a held position then blocks
    # re-entry even when the API momentarily forgets it. (Skipping a trade costs
    # $0; a silent pyramid does not.)
    _live_coins = {p["coin"] for p in positions}
    for _coin, _side in active_position_coins().items():
        if _coin not in _live_coins:
            logger.warning(
                f"[executor] {_coin} tracked by DSL but absent from live account "
                f"read — treating as held (re-entry backstop)")
            positions.append({"coin": _coin, "side": _side, "size_usd": 0})

    # `tp_px` / `stop_px` are fallbacks for bracket calculation when ATR
    # is unavailable; the executor uses a fresh live mid as entry.
    tp_px = analysis.get("tp_px")
    stop_px = analysis.get("stop_px")

    try:
        _max_lev = get_max_leverage(analysis["coin"])
    except Exception as _mle:
        # Refuse rather than guess. get_max_leverage raises when the meta is
        # unusable (e.g. a partial cache entry missing maxLeverage); defaulting
        # to 1x here would open a position at a third of the intended leverage,
        # silently, with no error anywhere.
        return {
            "executed": False, "mode": mode, "analysis_id": analysis["id"],
            "reason": f"leverage_unresolved ({analysis['coin']}: {_mle})",
        }
    leverage = min(int(config.get("leverage", HL_LEVERAGE)), _max_lev)
    _notional_cap = float(config.get("max_trade_notional_usd", 0) or 0)
    _atr_sizing = config.get("atr_risk_sizing", {}) or {}
    _atr_sizing_enabled = bool(_atr_sizing.get("enabled", False))
    mid_price = 0.0
    atr = 0.0
    size_in_coin = 0.0

    # Maintenance-aware liquidation bound on the server-side backup SL (T2.1/T2.2
    # port). The backup SL (sl_atr_mult x ATR) is the WIDEST exchange-side stop and
    # is placed later with no liq clamp. On a high-ATR / low-maxLeverage coin it can
    # sit OUTSIDE the maintenance-aware liq distance (1/lev - maint), so on a
    # violent gap the position liquidates BEFORE the SL fires — useless in the exact
    # regime it exists to catch. We cap leverage to keep the SL reachable (don't
    # shrink the SL, the validated param). The narrower DSL floor (max_loss spot) is
    # the binding normal exit and is inside liq by construction, so only the wider
    # backup SL needs this bound. Runs BEFORE sizing so notional/margin are computed
    # at the (possibly capped) leverage; a lower lev is strictly safer for liq.
    # Gated: liq_safety_frac=0 disables. A lookup hiccup never kills a trade — the
    # DSL floor still protects normal exits.
    _liq_safety = float(config.get("liq_safety_frac", _DEFAULT_LIQ_SAFETY_FRAC) or 0)
    if _liq_safety > 0:
        try:
            _sl_mult = float(config.get("sl_atr_mult", _DEFAULT_SL_ATR_MULT))
            if mid_price <= 0:
                mid_price = get_hl_price(analysis["coin"])
            if atr <= 0:
                atr = get_hl_atr("4h", 14, analysis["coin"])
            if mid_price > 0 and atr > 0:
                _sl_frac = _sl_mult * atr / mid_price
                _lev_liq = liq_safe_leverage(leverage, _sl_frac, _max_lev, _liq_safety)
                if _lev_liq < leverage:
                    logger.warning(
                        f"[executor] {analysis['coin']}: backup SL {_sl_mult}x ATR "
                        f"({_sl_frac*100:.1f}% spot) sits outside liq at {leverage}x "
                        f"(maxLev {_max_lev}) — capping leverage to {_lev_liq}x so the "
                        f"server-side stop stays inside liquidation "
                        f"(book={analysis.get('strategy_book') or 'main'})")
                    leverage = _lev_liq
        except Exception as _liq_e:
            logger.debug(f"[executor] liq-bound lookup failed for {analysis['coin']}: {_liq_e}")

    if _atr_sizing_enabled:
        coin = analysis["coin"]
        mid_price = get_hl_price(coin)
        if mid_price <= 0:
            return {"executed": False, "mode": mode, "analysis_id": analysis["id"],
                    "reason": f"invalid_price_for_{coin}"}
        atr = get_hl_atr("4h", 14, coin)
        if atr <= 0:
            atr = get_hl_atr("4h", 14, coin)
        if atr <= 0:
            return {
                "executed": False, "mode": mode, "analysis_id": analysis["id"],
                "reason": f"no_atr_no_stop ({coin}: insufficient candle history to size a stop)",
            }

        from hermes_trader.agents.sizing import atr_equal_risk_notional
        _max_total_pct = float(config.get("max_total_notional_pct", 0) or 0)
        _room = (_max_total_pct * agg_equity - total_open_notional) if _max_total_pct > 0 else 0.0
        _cap = _notional_cap
        if _room > 0:
            _cap = min(_cap, _room) if _cap > 0 else _room
        _risk_pct = float(_atr_sizing.get("risk_per_trade_pct", 0.0075))
        _sizing_basis = str(_atr_sizing.get("sizing_basis", "atr_stop") or "atr_stop").lower()
        if _sizing_basis in ("primary_stop", "dsl_stop"):
            _dsl = config.get("dsl_exit", {}) or {}
            _max_loss = float(_dsl.get("max_loss_pct", 2.0) or 2.0)
            _max_roe = float(_dsl.get("max_loss_roe_pct", 40.0) or 40.0)
            _lev = max(1, leverage)
            _stop_frac = min(_max_loss, _max_roe / _lev) / 100.0
            if size_equity <= 0 or _risk_pct <= 0 or _stop_frac <= 0:
                return {
                    "executed": False, "mode": mode, "analysis_id": analysis["id"],
                    "reason": f"primary_stop_sizing_zero ({coin}: invalid inputs)",
                }
            trade_notional = (_risk_pct * size_equity) / _stop_frac
            # `leverage` here is the FINAL value: min(config, exchange max) THEN
            # the liq-bound cap above. Clamp notional to it so a liq-capped lev
            # can't be out-sized (notional > lev*equity → margin shortfall on HL).
            _lev_cap = leverage
            _max_by_lev = max(1, _lev_cap) * size_equity
            _clamped = []
            if trade_notional > _max_by_lev:
                trade_notional = _max_by_lev
                _clamped.append("max_leverage")
            if _cap > 0 and trade_notional > _cap:
                trade_notional = _cap
                _clamped.append("notional_cap")
            logger.info(
                f"[executor] primary-stop equal-risk sizing {coin}: notional ${trade_notional:.0f} "
                f"(risk ${trade_notional*_stop_frac:.2f} @ {_stop_frac*100:.2f}% stop"
                f"{', clamped:'+','.join(_clamped) if _clamped else ''})")
        else:
            _sz = atr_equal_risk_notional(
                equity=size_equity,
                risk_per_trade_pct=_risk_pct,
                atr_abs=atr,
                entry_px=mid_price,
                sl_atr_mult=float(config.get("sl_atr_mult", _DEFAULT_SL_ATR_MULT)),
                max_trade_notional_usd=_cap,
                coin_max_leverage=get_max_leverage(coin),
                # FINAL leverage (config ∩ exchange max ∩ liq-bound cap) so the
                # sized notional can't exceed the leverage actually set on HL.
                config_max_leverage=leverage,
            )
            if _sz.notional_usd <= 0:
                return {
                    "executed": False, "mode": mode, "analysis_id": analysis["id"],
                    "reason": f"atr_sizing_zero ({coin}: {_sz.clamped_by or 'invalid inputs'})",
                }
            trade_notional = _sz.notional_usd
            logger.info(
                f"[executor] ATR equal-risk sizing {coin}: notional ${trade_notional:.0f} "
                f"(impl_lev {_sz.implied_leverage:.1f}x, risk ${_sz.risk_usd:.2f} @ "
                f"{_sz.stop_distance_frac*100:.2f}% stop"
                f"{', clamped:'+_sz.clamped_by if _sz.clamped_by else ''})")
    else:
        # Legacy fallback when ATR equal-risk sizing is explicitly disabled:
        # equity × fraction × leverage × optional conviction multiplier.
        base_fraction = float(config.get("equity_fraction_per_trade", 0.01))
        if bool(config.get("conviction_sizing", True)):
            conf = float(analysis.get("confidence", 0) or 0)
            tiers = _parse_conviction_tiers(config.get("conviction_tiers"))
            conviction_mult = _conviction_multiplier(conf, tiers)
            # Whale-signal boost: when smart-money accumulation is flagged on this
            # coin, bet bigger. Clamps so a whale + high-conf trade can't exceed 2× base.
            if analysis.get("whale_signal"):
                whale_mult = float(config.get("whale_size_multiplier", 1.3))
                conviction_mult = min(conviction_mult * whale_mult, 2.0)
        else:
            conviction_mult = 1.0
        equity_fraction = base_fraction * conviction_mult
        trade_notional = equity * equity_fraction * leverage
        # Clamp to the per-trade notional ceiling so an oversized conviction bet is
        # SIZED DOWN to the cap rather than REJECTED by the notional gate.
        if _notional_cap > 0 and trade_notional > _notional_cap:
            logger.info(f"[executor] notional ${trade_notional:.0f} > cap "
                        f"${_notional_cap:.0f} — clamping to cap")
            trade_notional = _notional_cap

    # Normalize to the exact HL-valid entry size BEFORE risk gates. The order
    # layer enforces a $10.50 minimum and coin-size precision; if we wait until
    # place_hl_order() to apply that, the gates, DSL tracker, memory, and SL/TP
    # brackets all believe a smaller position exists than the one actually sent.
    coin = analysis["coin"]
    if mid_price <= 0:
        mid_price = get_hl_price(coin)
        if mid_price <= 0:
            return {"executed": False, "mode": mode, "analysis_id": analysis["id"],
                    "reason": f"invalid_price_for_{coin}"}
    try:
        min_notional = min_entry_notional_usd(coin, mid_price)
        if min_notional > 0 and trade_notional < min_notional:
            # Entries dropped at this floor are forfeited EV, not saved risk —
            # when the intent merely falls SHORT of the floor, round UP to it;
            # added risk is bounded by the floor itself. Below the bump ceiling
            # the intent is genuinely too small to express and still skips.
            bump_max = float(config.get("min_order_bump_max_mult", 2.0) or 0.0)
            if bump_max > 0 and trade_notional * bump_max >= min_notional:
                logger.info(
                    f"[executor] {coin}: sized ${trade_notional:.2f} < HL floor "
                    f"${min_notional:.2f} — rounding UP to the floor "
                    f"(within {bump_max:g}x bump ceiling)")
                trade_notional = min_notional
            else:
                return {
                    "executed": False, "mode": mode,
                    "analysis_id": analysis["id"],
                    "reason": (f"below_min_order_notional ({coin}: sized "
                               f"${trade_notional:.2f}, HL minimum after precision "
                               f"${min_notional:.2f})"),
                }
        size_in_coin = entry_size_for_notional(coin, trade_notional, mid_price)
    except Exception as e:
        return {
            "executed": False, "mode": mode,
            "analysis_id": analysis["id"],
            "reason": f"entry_size_unavailable ({coin}: {e})",
        }
    if size_in_coin <= 0:
        return {
            "executed": False, "mode": mode,
            "analysis_id": analysis["id"],
            "reason": f"entry_size_zero ({coin})",
        }
    normalized_notional = size_in_coin * mid_price
    if abs(normalized_notional - trade_notional) >= 0.01:
        logger.info(
            f"[executor] normalized entry size {coin}: target ${trade_notional:.2f} "
            f"→ {size_in_coin:g} coin (${normalized_notional:.2f})")
    trade_notional = normalized_notional

    # Cooldown must measure against the NEWEST trade on this coin: the ledger is
    # chronological, so `next()` over recent_trades would pick the OLDEST —
    # letting a second entry through 30min after the first while the coin was
    # re-traded minutes ago (observed: ACE leg 2 at 32min after leg 1, leg 3 at
    # 59min, all into one position). 2026-09-02: close-armed — the map floors
    # at the newest close too, so exiting a long hold no longer leaves a
    # zero-breathing-room re-entry window (open fills still count, ACE legs
    # behave unchanged).
    last_trade_time = memory.cooldown_floor_ts_by_coin().get(analysis["coin"])

    # News blackout: stand down only on GENUINELY adverse news. The AI judges
    # the recent (last 48h) headlines and emits news_risk; only "negative"
    # blocks. This replaced a dumb keyword blocklist that fired on the mere
    # mention of "earnings"/"SEC" etc. — an earnings BEAT is bullish and must
    # not block. Sentiment also makes the old equity-perp exemption unnecessary:
    # the AI won't flag a beat as negative, but WILL flag a miss/fraud.
    news_text = analysis.get("news_context") or ""
    news_risk = str(analysis.get("news_risk") or "none").lower()
    has_binary_news = news_risk == "negative"
    binary_news_match = ""
    if has_binary_news and news_text:
        # Surface a representative adverse headline so the log says what tripped it.
        m = re.search(
            r"\b(hack|exploit|lawsuit|halt|delist|miss|crash|plunge|fraud)\w*"
            r"|\bfomc\b|\bcpi\b|\bsec\b|\bfed(eral)?\b",
            news_text, re.IGNORECASE,
        )
        if m:
            term = m.group(0)
            headline = next(
                (h.strip() for h in news_text.split("|") if term.lower() in h.lower()),
                news_text[:140],
            )
            binary_news_match = f"'{term}' in: {headline}"
        else:
            binary_news_match = news_text[:140]

    trade_side = analysis.get("side", "long") or "long"
    # Sync Chronos read for the mismatch / tail-trigger gates:
    # get_chronos_signal_sync returns the warm cache entry when fresh and
    # computes once on a cold/expired cache (one 5m candleSnapshot POST +
    # ~40ms CPU inference, bounded by the 90s shared candle cache). The
    # cache-only peek raced the attach compute — the attach's fresh signal
    # landed ~5s AFTER the gate read an expired cache, so the shadow gates
    # systematically missed would-blocks on fresh-compute trades (2026-08-28
    # ENA: tail −3.9% attached, gate passed on a 375s-old entry). An error
    # signal (disabled / no candles / inference failure) carries no usable
    # values → the gates then simply have no opinion.
    try:
        _csig = get_chronos_signal_sync(analysis["coin"], trade_side)
        _chronos_med = _csig.median_pct if _csig else None
        # p10-p90 spread, % vs last close — same sync read. Fed to the
        # tail_spread_comparator (log-only, plan D4). The ratio-aware deadband
        # counterfactual that also consumed this was removed 2026-09-18 (§C.1
        # die ruling); the field itself stays for the comparator.
        _chronos_spread = _csig.spread_pct if _csig else None
        # Per-step quantile paths for the tail-trigger gate; absent on error
        # signals or pre-change signals — the gate then passes.
        _chronos_q10p = _csig.q10_path_pct if _csig else None
        _chronos_q90p = _csig.q90_path_pct if _csig else None
    except Exception as _pe:
        logger.debug(f"[executor] chronos sync read failed for {analysis['coin']}: {_pe}")
        _chronos_med = None
        _chronos_spread = None
        _chronos_q10p = _chronos_q90p = None
    # Gate-side TimesFM-3 read for forecast_agreement_veto_gate: same warm
    # sync pattern as chronos above (the trade-result attach shares the 300s
    # per-coin cache, so steady state is a dict read). Paths only — the
    # agreement gate needs the per-step tails, not the scalar. Error/disabled
    # → None → the gate has no opinion and passes. Guarded on the passed
    # config's timesfm_signal.enabled (fail-closed: a config without the key
    # never pays the fetch, and tests can't reach the wire through here).
    _timesfm_q10p = _timesfm_q90p = _timesfm_med = None
    if (config.get("timesfm_signal") or {}).get("enabled", False):
        try:
            from hermes_trader.agents.timesfm_signal import get_timesfm_signal_sync as _ts
            _tsig = _ts(analysis["coin"], trade_side)
            _timesfm_q10p = _tsig.q10_path_pct if _tsig else None
            _timesfm_q90p = _tsig.q90_path_pct if _tsig else None
            # TimesFM median for the timesfm_mismatch mirror gate — same warm
            # 300s per-coin cache read (no new API cost). None on error
            # signals → the gate has no opinion and passes.
            _timesfm_med = _tsig.median_pct if _tsig else None
        except Exception as _te:
            logger.debug(f"[executor] timesfm gate-side read failed for {analysis['coin']}: {_te}")
    # Gate-side TiRex 1.1 read for tirex_tail_trigger_gate: same warm sync
    # pattern as chronos/timesfm above (shares the 300s per-coin cache with
    # the loop fire + trade-result attach, so steady state is a dict read).
    # Paths only — the tail gate needs the per-step adverse quantiles.
    # Error/disabled -> None -> the gate has no opinion and passes.
    _tirex_q10p = _tirex_q90p = None
    if (config.get("tirex_signal") or {}).get("enabled", False):
        try:
            from hermes_trader.agents.tirex_signal import get_tirex_signal_sync as _txs
            _txsig = _txs(analysis["coin"], trade_side)
            _tirex_q10p = _txsig.q10_path_pct if _txsig else None
            _tirex_q90p = _txsig.q90_path_pct if _txsig else None
        except Exception as _xe:
            logger.debug(f"[executor] tirex gate-side read failed for {analysis['coin']}: {_xe}")
    # A/B duelist verdict at entry (research.py's `duelist_at_entry` snapshot):
    # LONG / SHORT / PASS / VETO, or None when the duelist is disabled /
    # failed. Fed to duelist_veto_gate — the explicit-VETO / opposite-side
    # veto shape (re-keyed 2026-09-03: a neutral PASS no longer vetoes). A
    # data gap (None) can never block a trade: the gate passes with no opinion.
    _duelist_at = analysis.get("duelist_at_entry") or {}
    _duelist_verdict = _duelist_at.get("verdict") or None
    ctx = GateContext(
        confidence=analysis["confidence"],
        current_positions=positions,
        trade_notional_usd=trade_notional,
        daily_pnl=daily_pnl,
        market_volume_24h_usd=_get_market_volume_24h(analysis["coin"]),
        coin=analysis["coin"],
        trade_side=trade_side,
        has_binary_news_risk=has_binary_news,
        binary_news_match=binary_news_match,
        equity=agg_equity,
        total_open_notional=total_open_notional,
        composite_score=float(analysis.get("composite_score", 0) or 0),
        momentum_burst_fired=bool(analysis.get("momentum_burst_fired", False)),
        # Fresh-impulse twins for the tail gates' impulse-class escape denial
        # (_tail_composite_escape_allowed). Same analysis flags the trade-
        # result line reports as volume_spike_fired / breakout_fired.
        volume_spike_fired=bool(analysis.get("volume_spike_fired", False)),
        breakout_fired=bool(analysis.get("breakout_fired", False)),
        slow_burn_fired=bool(analysis.get("slow_burn_fired", False)),
        # whale_regime_bypass gates whether a whale signal can bypass the
        # counter-regime gate. Missing config fails closed.
        whale_signal_fired=bool(analysis.get("whale_signal")) and bool(config.get("whale_regime_bypass", False)),
        peak_daily_pnl=memory.peak_daily_pnl(),
        chronos_median_pct=_chronos_med,
        chronos_spread_pct=_chronos_spread,
        chronos_q10_path_pct=_chronos_q10p,
        chronos_q90_path_pct=_chronos_q90p,
        timesfm_q10_path_pct=_timesfm_q10p,
        timesfm_q90_path_pct=_timesfm_q90p,
        timesfm_median_pct=_timesfm_med,
        tirex_q10_path_pct=_tirex_q10p,
        tirex_q90_path_pct=_tirex_q90p,
        duelist_verdict=_duelist_verdict,
        cohort_tag=analysis.get("cohort_tag"),
        primary_model=analysis.get("primary_model"),
    )

    gate_output = eval_all_gates(ctx, config, last_trade_time)

    # The mismatch gate runs shadow by default: it passes, but carries a
    # shadow_would_block marker when a counter-forecast entry would have been
    # vetoed at the live bar. Log it loudly so we can count false-positives
    # before ever flipping shadow_mode off.
    _cm = gate_output["results"].get("chronos_mismatch") or {}
    if _cm.get("shadow_would_block"):
        logger.warning(
            f"[gate][SHADOW] chronos_mismatch WOULD HAVE BLOCKED "
            f"{analysis['coin']} {trade_side.upper()} "
            f"(conf {analysis['confidence']:.2f}, composite "
            f"{analysis.get('composite_score', 0):.1f}): {_cm.get('reason')} — "
            f"NOT blocking (shadow mode)")

    # Tail-trigger shadow: same would-block pattern, keys off the adverse
    # quantile PATH (validated K=6/X=3.0 in the 2026-08-28 60-flag replay —
    # saved ~$3.21 vs ~$0.96 for the path-mean reduction).
    _ct = gate_output["results"].get("chronos_tail_trigger") or {}
    if _ct.get("shadow_would_block"):
        logger.warning(
            f"[gate][SHADOW] chronos_tail_trigger WOULD HAVE BLOCKED "
            f"{analysis['coin']} {trade_side.upper()} "
            f"(conf {analysis['confidence']:.2f}, composite "
            f"{analysis.get('composite_score', 0):.1f}): {_ct.get('reason')} — "
            f"NOT blocking (shadow mode)")
    # TiRex tail-trigger mirror accrual (2026-09-17): same would-block line on
    # the tirex adverse q-path so the chronos-vs-tirex either/or settles on
    # live executed-cohort P/L (offline: tirex wins AUC, ties capture at
    # X=2.5). Shadow while shadow_mode is true; can block if flipped.
    _xt = gate_output["results"].get("tirex_tail_trigger") or {}
    if _xt.get("shadow_would_block"):
        logger.warning(
            f"[gate][SHADOW] tirex_tail_trigger WOULD HAVE BLOCKED "
            f"{analysis['coin']} {trade_side.upper()} "
            f"(conf {analysis['confidence']:.2f}, composite "
            f"{analysis.get('composite_score', 0):.1f}): {_xt.get('reason')} — "
            f"NOT blocking (shadow mode)")
    # Log-only comparator accrual (plan D4, 2026-09-15): per evaluated entry,
    # what the PATH-tail veto vs the band-WIDTH veto would each have done. The
    # 2026-09 sweep found near-equal stop-out AUC — accrue divergence counts
    # (grep "[gate][CMP] tail_vs_spread") and settle path-vs-width with real
    # outcomes before touching the armed gate. Never affects execution.
    _tsc = gate_output["results"].get("tail_spread_cmp") or {}
    if _tsc.get("cmp"):
        _c = _tsc["cmp"]
        logger.info(
            f"[gate][CMP] tail_vs_spread {analysis['coin']} {trade_side.upper()}: "
            f"tail_trip={_c['tail_trip']} (tail {_c['tail_pct'] if _c['tail_pct'] is not None else float('nan'):+.2f}%), "
            f"spread_trip={_c['spread_trip']} (spread {(_c['spread_pct'] if _c['spread_pct'] is not None else float('nan')):.2f}%)")

    # Band counter-trend breach shadow gate: same would-block pattern. Fires
    # on the GRASS shape — a counter-trend bounce/dip entry extended beyond
    # the opposite edge of a drifting MA band. Logs loudly while shadow_mode
    # is on so we can count false positives before it can block anything.
    _bc = gate_output["results"].get("band_counter_breach") or {}
    if _bc.get("shadow_would_block"):
        logger.warning(
            f"[gate][SHADOW] band_counter_breach WOULD HAVE BLOCKED "
            f"{analysis['coin']} {trade_side.upper()} "
            f"(conf {analysis['confidence']:.2f}, composite "
            f"{analysis.get('composite_score', 0):.1f}): {_bc.get('reason')} — "
            f"NOT blocking (shadow mode)")

    # Quiet broad-tape gate (2026-09-10, WATCHLIST §B.17): a quiet
    # broad tape — trail24h vol/|drift| pair below the sweep thresholds —
    # says the tape is inactive; on the 09-05/06/07 bleed the whole book
    # bled into exactly this regime. MULTI-VARIANT (2026-09-11): the `btc`
    # variant is the original proxy; the `alt` variant reads the alt-basket
    # index (the tape actually traded). Same would-block pattern;
    # `shadow_reasons` carries the per-variant join keys (`quiet_tape[alt]
    # (…)` / `quiet_tape[btc] (…)`).
    _qt = gate_output["results"].get("quiet_tape") or {}
    if _qt.get("shadow_would_block"):
        _qt_reasons = _qt.get("shadow_reasons") or [_qt.get("reason")]
        if _qt.get("pass"):
            _qt_suffix = "NOT blocking (shadow mode)"
        else:
            _qt_suffix = ("shadow variant(s) would ALSO block — the entry is "
                          "already blocked by a live variant (see gate reasons)")
        logger.warning(
            f"[gate][SHADOW] quiet_tape WOULD HAVE BLOCKED "
            f"{analysis['coin']} {trade_side.upper()} "
            f"(conf {analysis['confidence']:.2f}, composite "
            f"{analysis.get('composite_score', 0):.1f}): "
            f"{'; '.join(str(x) for x in _qt_reasons)} — "
            f"{_qt_suffix}")
    # OR-vs-AND merge accrual (2026-09-11): the entry is NOT blocked by
    # quiet_tape (merge="and") while ≥1 LIVE variant would have blocked
    # alone — the OR merge would have vetoed this entry. Logged on every
    # such decision (executed or blocked-by-other-gate) so the released
    # cohort accrues against the ledger: join by coin/side/ts + the
    # quiet_tape[btc] / quiet_tape[alt] join keys in `released_reasons`.
    # The raw per-variant reads ride in gate_results (Trade result JSON)
    # for every decision, quiet or loud.
    if _qt.get("released_by_merge"):
        _rel = _qt.get("released_reasons") or []
        _rel_txt = "; ".join(str(x) for x in _rel) if _rel else "(no reason)"
        logger.warning(
            f"[gate][ACC] quiet_tape[{_qt.get('merge', 'and')}] RELEASED "
            f"{analysis['coin']} {trade_side.upper()} "
            f"(conf {analysis['confidence']:.2f}, composite "
            f"{analysis.get('composite_score', 0):.1f}): "
            f"{_rel_txt} — an OR merge would have BLOCKED")

    # Quiet-tape MOMENTUM-RELEASE accrual (2026-10-06, WATCHLIST §B.17):
    # the broad tape read quiet (block) but the COIN's own trail24h read is
    # loud in the called direction. Shadow mode logs the counterfactual so
    # released-vs-saved prices out-of-sample against the same 24h-forward
    # join the daily report already runs; live mode releases the entry and
    # logs the release. Join key: `quiet_tape_momentum` in gate_results.
    _mom = _qt.get("momentum") or {}
    if _mom.get("fires"):
        _mr = _mom.get("reads") or {}
        _mt = _mom.get("thresholds") or {}
        _mom_txt = (f"coin trail24h aligned drift {_mr.get('aligned_drift', 0):+.2f}% "
                    f"vs bar {_mt.get('aligned_drift_pct', 0):.2f}% OR vol "
                    f"{_mr.get('vol', 0):.2f}% vs bar {_mt.get('coin_vol_pct', 0):.2f}%")
        if _mom.get("live"):
            logger.warning(
                f"[gate][ACC] quiet_tape_momentum RELEASED "
                f"{analysis['coin']} {trade_side.upper()} "
                f"(conf {analysis['confidence']:.2f}, composite "
                f"{analysis.get('composite_score', 0):.1f}): {_mom_txt} — "
                f"quiet_tape block overridden by the momentum-release clause")
        else:
            logger.warning(
                f"[gate][SHADOW] quiet_tape_momentum WOULD HAVE RELEASED "
                f"{analysis['coin']} {trade_side.upper()} "
                f"(conf {analysis['confidence']:.2f}, composite "
                f"{analysis.get('composite_score', 0):.1f}): {_mom_txt} — "
                f"NOT releasing (shadow-only); the entry stays blocked by "
                f"quiet_tape")

    # TimesFM mirror-leg shadow accruals: the timesfm-alone per-forecaster
    # counterfactuals (the AND leg is the forecast_agreement_veto line
    # below). mismatch stays SHADOW-ONLY by construction. tail_trigger is
    # shadow-CAPABLE (C.9 promotion 2026-09-17): while its config keeps
    # shadow_mode true (or absent) these accrual lines are the counterfactual;
    # once armed live it blocks via gate_results instead and this line goes
    # quiet for it. The anchored strings below ('timesfm_mismatch WOULD HAVE
    # BLOCKED' / 'timesfm_tail_trigger WOULD HAVE BLOCKED') are the
    # counterfactual join keys.
    _cm2 = gate_output["results"].get("timesfm_mismatch") or {}
    if _cm2.get("shadow_would_block"):
        logger.warning(f"[gate][SHADOW] timesfm_mismatch WOULD HAVE BLOCKED {analysis['coin']} {trade_side.upper()} (conf {analysis['confidence']:.2f}, composite {analysis.get('composite_score', 0):.1f}): {_cm2.get('reason')} — NOT blocking (shadow-only)")
    _ct2 = gate_output["results"].get("timesfm_tail_trigger") or {}
    if _ct2.get("shadow_would_block"):
        logger.warning(f"[gate][SHADOW] timesfm_tail_trigger WOULD HAVE BLOCKED {analysis['coin']} {trade_side.upper()} (conf {analysis['confidence']:.2f}, composite {analysis.get('composite_score', 0):.1f}): {_ct2.get('reason')} — NOT blocking (shadow-only)")

    # Duelist veto shadow: the A/B duelist EXPLICITLY vetoed (or took the
    # opposite side on) this directional entry. The gate structurally passes;
    # this is the would-block marker (the marker also rides along in
    # gate_results → the Trade result line → ledger joinable).
    _dv = gate_output["results"].get("duelist_veto") or {}
    if _dv.get("shadow_would_block"):
        _dl_model = ((analysis.get("duelist_at_entry") or {}).get("model") or "?")
        logger.warning(
            f"[gate][SHADOW] duelist_veto WOULD HAVE BLOCKED "
            f"{analysis['coin']} {trade_side.upper()} "
            f"(duelist {_dl_model} said "
            f"{((analysis.get('duelist_at_entry') or {}).get('verdict') or '?').upper()}"
            f"; conf {analysis['confidence']:.2f}, composite "
            f"{analysis.get('composite_score', 0):.1f}): {_dv.get('reason')} — "
            f"NOT blocking (shadow mode)")

    # Forecast-agreement veto shadow: BOTH zero-shot forecasters (chronos AND
    # timesfm) show an adverse quantile tail past their own thresholds. The
    # 2026-09-02 two-model sweep picked this AND shape over either model
    # alone (most split-half-robust cell, vetoes fewer entries than the
    # chronos-alone X2.5 rule for equal savings). Shadow marker only until
    # shadow_mode is flipped.
    _fav = gate_output["results"].get("forecast_agreement_veto") or {}
    if _fav.get("shadow_would_block"):
        logger.warning(
            f"[gate][SHADOW] forecast_agreement_veto WOULD HAVE BLOCKED "
            f"{analysis['coin']} {trade_side.upper()} "
            f"(conf {analysis['confidence']:.2f}, composite "
            f"{analysis.get('composite_score', 0):.1f}): {_fav.get('reason')} — "
            f"NOT blocking (shadow mode)")

    # Divergent-sizing shadow accrual (WATCHLIST §C.16 follow-up, owner go
    # 2026-10-07): the divergence flag's surviving value is as a RISK marker,
    # not a gate — divergent-executed entries showed mean early adverse
    # excursion -2.91% vs -1.17% non-divergent (n=3 vs 40, accrual-only).
    # The gate-lever cohort died (blocked cohort net-negative); a sizing
    # haircut has the opposite asymmetry (shrinks the knife, never forfeits
    # a winner). This logs the would-be haircut WITHOUT applying it: the
    # executed trade keeps its full size. Re-judge bar: at n>=15
    # divergent-executed, re-run scratch/_c16_pullweight.py; the haircut
    # becomes decision-ready only if the mean-adverse split holds >=1.5x.
    # Config `divergent_sizing_shadow.enabled` (default False) — accrual
    # line only, never touches trade_notional.
    try:
        _dss = config.get("divergent_sizing_shadow", {}) or {}
        if bool(_dss.get("enabled", False)):
            _mr_res = gate_output["results"].get("market_regime") or {}
            _dv = _mr_res.get("divergence") or {}
            if _dv.get("coin_vs_btc_diverged"):
                _half = float(_dss.get("haircut", 0.5))
                _tn = float(trade_notional or 0.0)
                if _tn > 0:
                    logger.warning(
                        f"[gate][SHADOW] divergent_sizing WOULD HAVE HALVED "
                        f"{analysis['coin']} {trade_side.upper()} "
                        f"(btc={_dv.get('btc_regime')} coin={_dv.get('coin_regime')} "
                        f"conf {analysis['confidence']:.2f}): notional "
                        f"${_tn:.2f} -> ${_tn * _half:.2f} — NOT applying "
                        f"(shadow-only)")
    except Exception as _dss_e:  # accrual must never break the execution path
        logger.debug(f"[executor] divergent_sizing shadow accrual failed: {_dss_e}")

    # Favorable-tail shadow accrual (WATCHLIST §C.21, owner go 2026-10-08):
    # the size-up counterpart of the divergent haircut. The 2026-10-07 week
    # survey (scratch/eval/expert_week/score_upsize.py, n=580 side-matched
    # anchors) found the PER-MODEL favorable tail marks opportunity: top
    # decile of max q90[:6] (long) / -min q10[:6] (short) realized +0.6..1.2pp
    # mean end-return and 52-66% win vs +0.10pp / 46% baseline — chronos and
    # timesfm strongest, the re-centered mixture DILUTES the tilt. Caveat:
    # one chop week, n=58/decile, rank-corr only ~0.05 (a threshold signal,
    # not a dial). This logs the per-model fav-score on every executed entry
    # WITHOUT touching size; re-judge bar in the watchlist (n>=40 entries,
    # across both chop and trend regimes, tilt must survive before any
    # 1.5x nudge is proposed — never 2x on this evidence class).
    # Config `fav_tail_shadow.enabled` (default False).
    try:
        _fts = config.get("fav_tail_shadow", {}) or {}
        if bool(_fts.get("enabled", False)):
            from hermes_trader.agents.expert_select import peek_expert_select
            _esig = peek_expert_select(analysis["coin"])
            if _esig is not None:
                _K = 6
                _long = str(trade_side).lower() == "long"
                _src = (_esig.candidate_q90_pct if _long
                        else _esig.candidate_q10_pct)
                _parts = []
                _hot = []
                for _m, _path in sorted(_src.items()):
                    if not _path:
                        continue
                    _w = _path[:_K]
                    _fav = max(_w) if _long else -min(_w)
                    _parts.append(f"{_m}={_fav:+.2f}")
                    if _fav >= float(_fts.get("threshold_pp", 4.0)):
                        _hot.append(_m)
                if _parts:
                    logger.warning(
                        f"[gate][ACC] fav_tail {analysis['coin']} "
                        f"{trade_side.upper()} fav6pp[{','.join(_parts)}] "
                        f"conf {analysis['confidence']:.2f} "
                        f"hot={','.join(_hot) if _hot else '-'} "
                        f"(accrual-only, size NOT changed)")
    except Exception as _fts_e:  # accrual must never break the execution path
        logger.debug(f"[executor] fav_tail shadow accrual failed: {_fts_e}")

    if gate_output["blocked"]:
        # ── Capital-rotation (Phase-1 lever) — SHADOW by default ─────────────
        # Phase-1 finding: 94% of missed movers die at the 300% cap / max_concurrent
        # (book full), not at the signal. When a strong fresh candidate is blocked
        # PURELY by capital, evaluate whether it should displace the weakest stale
        # non-winner. shadow_mode logs the decision WITHOUT acting so we validate
        # the ranking on live data before it ever moves real money. Fully wrapped:
        # a rotation bug can never break the (already-blocked) execution path.
        try:
            _rot = config.get("capital_rotation", {}) or {}
            if bool(_rot.get("enabled", False)):
                from hermes_trader.agents.rotation import decide_rotation
                _now_ms = time.time() * 1000
                _trade_ts = memory.latest_trade_ts_by_coin(50)
                _opos = []
                for _p in (state.get("asset_positions") or []):
                    _pp = _p.get("position", {}) or {}
                    _c = _pp.get("coin")
                    if not _c:
                        continue
                    _opos.append({
                        "coin": _c,
                        "roe_pct": float(_pp.get("returnOnEquity", 0) or 0) * 100,
                        "age_minutes": (_now_ms - _trade_ts.get(_c, _now_ms)) / 60000.0,
                    })
                _d = decide_rotation(
                    candidate_coin=analysis["coin"],
                    candidate_composite=float(analysis.get("composite_score", 0) or 0),
                    blocked_reasons=gate_output["block_reasons"],
                    open_positions=_opos,
                    min_candidate_composite=float(_rot.get("min_candidate_composite", 40.0)),
                    min_hold_minutes=float(_rot.get("min_hold_minutes", 30.0)),
                    protect_winner_roe_pct=float(_rot.get("protect_winner_roe_pct", 3.0)),
                )
                if _d.should_rotate and not _rotation_retry:
                    if bool(_rot.get("shadow_mode", True)):
                        logger.warning(f"[rotation][SHADOW] {_d.reason} "
                                       f"(would execute if rotation goes live)")
                    else:
                        # LIVE: close the weakest non-winner to free capital, then
                        # retry THIS candidate once. The retry re-reads account state
                        # (sees the freed margin/slot) and goes through every risk
                        # gate again — rotation only relieves the capital constraint,
                        # it never bypasses a real veto. _rotation_retry=True blocks a
                        # second rotation so this can't loop.
                        logger.warning(f"[rotation][LIVE] {_d.reason} — closing {_d.evict_coin}")
                        _cr = close_position_market(_d.evict_coin, "rotation_evict")
                        if _cr.get("ok"):
                            logger.warning(f"[rotation][LIVE] evicted {_d.evict_coin} "
                                           f"(rl {_cr.get('realized_pnl_pct')}%) → retrying {analysis['coin']}")
                            return maybe_execute(analysis, _rotation_retry=True)
                        logger.warning(f"[rotation][LIVE] evict {_d.evict_coin} failed "
                                       f"({_cr.get('error')}) — no rotation")
        except Exception as _e:
            logger.warning(f"[rotation] eval failed (non-fatal): {_e}")

        # Don't write blocked attempts to memory._trades — the cooldown gate
        # keys off the most recent trade-by-coin and would self-perpetuate.
        # Visibility comes from the `execute` event in the session log.
        coin = analysis.get("coin") or "unknown"
        side = analysis.get("side", "long") or "long"
        result = {
            "executed": False, "mode": mode,
            "analysis_id": analysis["id"],
            "blocked_by": gate_output["block_reasons"],
            "gate_results": gate_output["results"],
        }
        _attach_chronos_to_result(result, coin, side)
        _attach_timesfm_to_result(result, coin, side)
        _attach_tirex_to_result(result, coin, side)
        _attach_expert_select_to_result(result, coin, side)
        return result

    if shadow_mode:
        _side = analysis.get("side", "long") or "long"
        _res = {
            "executed": False, "mode": mode,
            "analysis_id": analysis["id"],
            "reason": "shadow_mode_would_execute",
            "gate_results": gate_output["results"],
            "size_usd": trade_notional,
        }
        _attach_chronos_to_result(_res, coin, _side)
        _attach_timesfm_to_result(_res, coin, _side)
        _attach_tirex_to_result(_res, coin, _side)
        _attach_expert_select_to_result(_res, coin, _side)
        return _res

    if not os.environ.get("HYPERLIQUID_PRIVATE_KEY"):
        return {
            "executed": False, "mode": mode,
            "analysis_id": analysis["id"],
            "reason": "private_key_missing",
        }

    is_buy = trade_side == "long"

    # Fetch live mid if legacy sizing did not already need it for ATR sizing.
    if mid_price <= 0:
        mid_price = get_hl_price(coin)
        if mid_price <= 0:
            return {"executed": False, "mode": mode, "analysis_id": analysis["id"],
                    "reason": f"invalid_price_for_{coin}"}

    position_notional = trade_notional

    if atr <= 0:
        atr = get_hl_atr("4h", 14, coin)
        if atr <= 0:
            atr = get_hl_atr("4h", 14, coin)
        if atr <= 0:
            return {
                "executed": False, "mode": mode, "analysis_id": analysis["id"],
                "reason": f"no_atr_no_stop ({coin}: insufficient candle history to size a stop)",
            }

    set_leverage(coin, leverage)
    order_res = place_hl_order(is_buy, size_in_coin, mid_price, coin)

    if not order_res.get("ok"):
        return {
            "executed": False, "mode": mode, "analysis_id": analysis["id"],
            "reason": f"order_failed: {order_res.get('error', 'unknown')}",
            "gate_results": gate_output["results"],
        }

    arrival_mid = float(mid_price or 0)
    try:
        filled_px = float(order_res.get("avg_px") or 0)
    except (TypeError, ValueError):
        filled_px = 0.0
    try:
        filled_size = float(order_res.get("total_sz") or 0)
    except (TypeError, ValueError):
        filled_size = 0.0
    # ok-without-fill-detail means the IOC matched nothing (no avgPx in the
    # filled status). Refuse rather than book: an OPEN row + DSL tracker for a
    # position the exchange never held is the same defect class as the
    # 2026-08-23 phantom CLOSE rows — bookkeeping without an exchange fill.
    # The order itself was a no-op (IOC cancelled on the exchange); if HL did
    # in fact fill, rehydrate_from_exchange re-synthesizes the tracker next
    # cycle from the live position, so nothing is lost.
    if filled_px <= 0:
        logger.warning(f"[executor] {coin} {trade_side}: IOC returned ok with no "
                       f"fill detail (no avgPx) — refusing to book a tracker/OPEN "
                       f"for a fill the exchange may not have made. If HL did "
                       f"fill, rehydrate_from_exchange re-synthesizes the tracker "
                       f"next cycle.")
        return {
            "executed": False, "mode": mode, "analysis_id": analysis["id"],
            "reason": "no_fill (IOC returned ok with no fill detail — nothing booked)",
            "gate_results": gate_output["results"],
        }
    entry_px = filled_px
    if filled_size > 0:
        size_in_coin = filled_size
    position_notional = abs(size_in_coin) * entry_px

    # Register the position with the DSL tracker; it re-evaluates the exit
    # floor on every scan tick (loss protection -> profit locking).
    dsl_config = config.get("dsl_exit", {})
    # Regime-aware exits: scalp (base) in chop/down to bank fast; trend-ride params
    # when regime=='up' to ride rippers. detect_regime is cached (TTL) and already
    # computed by the market_regime gate in this same execute flow — no extra fetch.
    _regime = "neutral"
    try:
        from hermes_trader.agents.market_regime import detect_regime
        _regime = detect_regime(analysis["coin"])
    except Exception as _re_e:
        logger.debug(f"[executor] regime lookup failed (non-fatal): {_re_e}")
    _ex_protect, _ex_retrace, _tiers_raw, _ex_label = select_exit_params(
        dsl_config, _regime, entry_class=analysis.get("_entry_class"))
    # phase2_tiers is optional in config; when present it OVERRIDES the class
    # default ladder so profit-locking tightness is tunable without code edits.
    _tiers = [RetraceTier(**t) for t in _tiers_raw] if _tiers_raw else None
    _atr_cfg = dsl_config.get("atr_stop", {}) or {}
    _noise_cfg = dsl_config.get("noise_band", {}) or {}
    logger.info(f"[executor] exit policy = {_ex_label} (regime={_regime}) "
                f"protect={_ex_protect} retrace={_ex_retrace}")
    policy = ExitPolicy(
        max_loss_pct=dsl_config.get("max_loss_pct", 2.5),
        max_loss_roe_pct=dsl_config.get("max_loss_roe_pct", 50.0),
        protect_pct=_ex_protect,
        retrace_threshold=_ex_retrace,
        hard_timeout_minutes=dsl_config.get("hard_timeout_minutes", 180.0),
        breakeven_trigger_pct=dsl_config.get("breakeven_trigger_pct", 0.0),
        breakeven_lock_pct=dsl_config.get("breakeven_lock_pct", 0.0),
        atr_stop_enabled=bool(_atr_cfg.get("enabled", False)),
        atr_stop_mult=float(_atr_cfg.get("atr_mult", 1.5)),
        atr_stop_floor_pct=float(_atr_cfg.get("floor_pct", 1.0)),
        atr_stop_ceiling_pct=float(_atr_cfg.get("ceiling_pct", 4.0)),
        stale_flat_timeout_minutes=float(dsl_config.get("stale_flat_timeout_minutes", 0.0) or 0.0),
        stale_flat_deep_release=dict(dsl_config.get("stale_flat_deep_release", {}) or {}),
        consecutive_breaches_required=int(dsl_config.get("consecutive_breaches_required", 1) or 1),
        noise_band_enabled=bool(_noise_cfg.get("enabled", False)),
        noise_band_atr_mult=float(_noise_cfg.get("atr_mult", 1.0)),
        floor_exit_grace_sec=float(dsl_config.get("floor_exit_grace_sec", 0.0) or 0.0),
        phase2_tiers=_tiers if _tiers else ExitPolicy().phase2_tiers,
    )
    # ATR as % of entry — captured once here so the DSL stop width is stable
    # for the life of the trade (the atr_stop feature scales off this).
    entry_atr_pct = (atr / entry_px * 100) if entry_px > 0 else 0.0

    _entry_ts = int(time.time() * 1000)
    memory.record_trade({
        "id": str(uuid.uuid4()),
        "analysis_id": analysis["id"],
        "coin": coin,
        "side": trade_side,
        "entry_px": entry_px,
        "size_usd": position_notional,
        "order_id": order_res.get("order_id"),
        "executed_at": _entry_ts,
    })
    # Append-only ledger (separate from operational memory)
    try:
        _sl_mult = float(config.get("sl_atr_mult", 1.5))
        record_open(
            coin=coin,
            side=trade_side,
            entry_px=entry_px,
            notional_usd=round(position_notional, 4),
            order_id=order_res.get("order_id"),
            leverage=leverage,
            analysis_id=analysis.get("id"),
            perception_id=analysis.get("perception_id"),
            config_snapshot=build_open_config_snapshot(
                _regime, _ex_label, _sl_mult, TP_ATR_MULT),
        )
    except Exception as _le:
        logger.debug(f"[executor] ledger record_open failed (non-fatal): {_le}")

    # Entry-context snapshot for the forward signal backtest: record WHEN we opened
    # and WHAT the free signals said at entry (cache-only — no network on the hot
    # path) plus the enforcement decision. The matching close pulls this so each
    # outcome row carries (entry_time, signals_at_entry) — the join the backtest
    # needs and that the outcome store previously lacked.
    try:
        from hermes_trader.agents.shadow_signals import gather_shadow_signals
        _entry_sig = gather_shadow_signals(coin, trade_side,
                                           config.get("shadow_signals") or {}, allow_fetch=False)
        # Execution-quality capture: arrival mid vs actual fill = real entry
        # slippage (the # the backtests don't model). Signed as adverse cost bps
        # (long paying above mid / short selling below = positive cost).
        _arr_mid = arrival_mid
        _fill = filled_px
        _slip_bps = None
        if _arr_mid > 0 and _fill > 0:
            raw = (_fill - _arr_mid) / _arr_mid * 1e4
            _slip_bps = round(raw if trade_side == "long" else -raw, 1)
        # Funding carry: capture the latest hourly funding rate at entry (one call;
        # entries are rare so this isn't the rate-sensitive scan path). Realized
        # funding cost is estimated at close from rate × hold_hrs × notional × side.
        _funding_hr = None
        try:
            from hermes_trader.client.hl_client import fetch_funding_history
            _fh = fetch_funding_history(coin, int(time.time() * 1000) - 86_400_000)
            if _fh:
                _r = float(_fh[-1].get("fundingRate", 0) or 0)
                _funding_hr = _r if _r == _r else None  # NaN guard
        except Exception:
            _funding_hr = None
        memory.record_entry_context(coin, trade_side, {
            "entry_time": _entry_ts,
            "arrival_mid": _arr_mid,
            "entry_fill": _fill,
            "entry_slip_bps": _slip_bps,
            "funding_rate_hr": _funding_hr,
            "regime": _regime,          # market_regime at entry (already computed above)
            "signals": _entry_sig,
            "enforcement": ({"veto": _enf.veto, "veto_reason": _enf.veto_reason,
                             "boost": _enf.boost, "boost_reason": _enf.boost_reason}
                            if _enf is not None else {}),
            "override_bar": override_composite,
            "forced_override": analysis.get("verdict") == "LONG"
                               and "[structural override]" in (analysis.get("reasoning") or ""),
            # A/B duelist: the second model's verdict on the SAME prompt, so the
            # close row can score it on this exact trade. None when disabled.
            "duelist": analysis.get("duelist_at_entry"),
            # Decision voice (Clef-family): its verdict + trap/close_now scalars
            # at entry, so the close row can price the dv counterfactuals on
            # this exact trade (clef-gate scope Change 1 — the whitelist must
            # carry trap/close_now or they never reach here).
            "decision_voice": analysis.get("decision_voice_at_entry"),
            # Join key back to the perception (+ the duel log row that holds
            # both models' full reasoning for this trade).
            "perception_id": analysis.get("perception_id"),
        })
    except Exception as _ec_e:
        logger.debug(f"[executor] entry-context capture failed (non-fatal): {_ec_e}")

    # Backup exchange stop-loss bracket — fires server-side (instantly, between our
    # 60s DSL checks) to cap the gap-throughs the DSL loop misses. DSL is still the
    # primary/normal exit; this is the fast safety net.
    sl_atr_mult = float(config.get("sl_atr_mult", _DEFAULT_SL_ATR_MULT))
    sl_missing = False
    if atr > 0 and size_in_coin > 0:
        sl_px = entry_px - atr * sl_atr_mult if is_buy else entry_px + atr * sl_atr_mult
        sl_res = place_hl_trigger_order(is_buy, size_in_coin, sl_px, "sl", coin)
        if not sl_res.get("ok"):
            # One retry after a beat — observed failures are transient 429s; a
            # position with no server-side stop carries the full gap-through
            # risk between 60s DSL checks, so a single retry is cheap insurance.
            time.sleep(2)
            sl_res = place_hl_trigger_order(is_buy, size_in_coin, sl_px, "sl", coin)
        if sl_res.get("ok"):
            logger.info(f"[executor] Placed backup SL at {sl_px} ({sl_atr_mult}x ATR)")
        else:
            sl_missing = True
            logger.error(f"[executor] Backup SL FAILED twice for {coin} — POSITION HAS "
                         f"NO SERVER-SIDE STOP (DSL loop is sole protection): {sl_res.get('error')}")

    # Take-profit scale-out — the OFFENSIVE complement to the backup SL. Banks a
    # fraction of the position SERVER-SIDE at the TP target so a winner is
    # CAPTURED at target (instantly, between 60s DSL checks) instead of running
    # to a peak and round-tripping back into the trailing stop — the documented
    # "we had it all and gave it back" leak. The remainder rides the DSL trail,
    # so we lock realized profit AND keep upside. Disable with tp_scale_fraction<=0.
    tp_scale_fraction = float(config.get("tp_scale_fraction", 0.5))
    if atr > 0 and size_in_coin > 0 and 0 < tp_scale_fraction <= 1.0:
        tp_px_trig = entry_px + atr * TP_ATR_MULT if is_buy else entry_px - atr * TP_ATR_MULT
        tp_size = size_in_coin * tp_scale_fraction
        tp_res = place_hl_trigger_order(is_buy, tp_size, tp_px_trig, "tp", coin)
        if tp_res.get("ok"):
            logger.info(f"[executor] Placed TP scale-out {tp_scale_fraction:.0%} "
                        f"at {tp_px_trig} ({TP_ATR_MULT}x ATR)")
        else:
            logger.error(f"[executor] TP scale-out FAILED for {coin}: {tp_res.get('error')}")

    final_sl = (entry_px - atr * sl_atr_mult) if is_buy else (entry_px + atr * sl_atr_mult) if atr > 0 else stop_px

    # Register DSL tracking AFTER we know the server-side levels.
    register_position(coin, trade_side, entry_px, policy=policy, leverage=leverage,
                      entry_atr_pct=entry_atr_pct)
    logger.info(f"[executor] Registered DSL exit for {coin} {trade_side} @ {entry_px} ({leverage}x)")

    result = {
        "executed": True, "mode": mode,
        "analysis_id": analysis["id"],
        "order_id": order_res.get("order_id"),
        "gate_results": gate_output["results"],
        "size_usd": position_notional,
        "entry_px": entry_px,
        "stop_px": final_sl,
        "tp_px": tp_px,
        "dsl_registered": True,
        "sl_missing": sl_missing,
    }
    _attach_chronos_to_result(result, coin, trade_side)
    _attach_timesfm_to_result(result, coin, trade_side)
    _attach_tirex_to_result(result, coin, trade_side)
    _attach_expert_select_to_result(result, coin, trade_side)
    return result


def monitor_exits(mids: Dict[str, float]) -> List[Dict[str, Any]]:
    """Check all DSL-tracked positions and return those that should be closed.

    `side` is the long/short of the actual position; `phase` is the DSL phase
    (phase1/phase2/timeout). `leveraged_pct` ≈ spot move × leverage and matches
    what Hyperliquid's UI shows on the user's margin.
    """
    exits = check_all_positions(mids)
    return [
        {
            "coin": v.coin,
            "side": v.position_side,
            "phase": v.phase,
            "leverage": v.leverage,
            "reason": v.reason,
            "unrealized_pct": v.unrealized_pct,
            "leveraged_pct": v.unrealized_pct * v.leverage,
        }
        for v in exits
    ]


def fast_exit_pass(
    candle_interval: str = "1m",
    candle_count: int = 3,
) -> List[Dict[str, Any]]:
    """Lighter-cadence DSL exit check for the (few) HELD positions only.

    The main-loop monitor pass samples once per full scan cycle (~3min+ under
    load), so an intrabar spike that round-trips inside one candle can be
    missed entirely by the mid-only peak (GRASS 2026-08-26: spiked +1.37%,
    sampled peak +0.73%, phase-2 never armed, timed out at a loss). This pass
    is run from a dedicated ~15s daemon thread while a position is open:

      1. Ratchet each held tracker's PEAK from a fresh 1m candle's high/low
         (not just the sampled mid) — a wick can no longer be missed.
      2. Run the full DSL floor check on a fresh mid.

    Returns verdicts shaped exactly like `monitor_exits`, so the caller
    (trading_loop) closes via the SAME idempotent `close_position_market` path.
    Only active (held) trackers are touched, so the steady-state cost is
    `#open_positions` fresh 1m-candle + mid fetches per interval — negligible.
    Never raises: a transient fetch failure just skips that coin this tick.
    """
    from hermes_trader.agents import dsl_exit
    trackers = list(dsl_exit._active_positions.values())
    if not trackers:
        return []

    mids: Dict[str, float] = {}
    highs: Dict[str, List[float]] = {t.coin: [] for t in trackers}
    lows: Dict[str, List[float]] = {t.coin: [] for t in trackers}

    for tr in trackers:
        try:
            # Fresh mid for the floor check + also a candidate peak extreme.
            # The mid is the live current print — always post-entry — so it is
            # unconditionally ratchetable.
            mid = get_hl_price(tr.coin)
            if mid > 0:
                mids[tr.coin] = mid
                highs[tr.coin].append(mid)
                lows[tr.coin].append(mid)
            # Fresh 1m candle extremes for the peak ratchet. fresh=True
            # bypasses the read-side TTL so the still-forming candle is seen
            # every tick (the point of the tighter cadence).
            #
            # CRITICAL (two-part): only ratchet from a candle if it opened AT
            # OR AFTER the fill. A candle that opened *before* the fill —
            # including the entry candle still forming around the fill — has a
            # high/low that mixes the PRE-FILL spike (the very move the order
            # was chasing) with post-fill action. Ratcheting the peak off it
            # arms the breakeven / phase-2 floor on a wick the position never
            # owned, and a pull back to entry closes the trade in <60s.
            #
            #   * 2026-09-03 JUP/ARB fix: dropped candles that CLOSED before
            #     entry (`c.t + interval_ms <= entry_ms`). Correct, but the
            #     ENTRY CANDLE itself (opened a few seconds pre-fill, still
            #     forming) survived that filter — its high was the pre-fill
            #     spike. SOPH 09-07 18:08 (held 12s) and 09-08 03:51 (held
            #     24s) both died to exactly that: the first daemon tick
            #     ratcheted peak to the entry candle's high, the 0.7% breakeven
            #     lock armed, mark < floor -> floor_breach at ~entry while price
            #     then ran +3.7–11.5% spot.
            #   * 2026-09-08 peak-seed fix: exclude ANY candle whose OPEN is
            #     pre-fill (`c.t < entry_ms`). On the entry candle the only
            #     clean post-entry peak candidate is the live MID (appended
            #     above); the candle's own high/low is withheld until a
            #     post-entry candle (opened >= entry) exists to ratchet from.
            #     `c.t < entry_ms` is strictly stricter than the old
            #     `c.t + interval_ms <= entry_ms`, so it subsumes the JUP/ARB
            #     pre-entry filter and additionally drops the contaminated
            #     entry candle.
            candles = fetch_hl_candles(tr.coin, candle_interval, candle_count, fresh=True)
            entry_ms = tr.entry_time * 1000
            for c in candles:
                if c.t < entry_ms:
                    # Opened before the fill — pre-fill price action (the
                    # chased spike) the position can't give back. The live mid
                    # is the clean seed for the peak on this candle.
                    continue
                highs[tr.coin].append(float(c.h))
                lows[tr.coin].append(float(c.l))
        except Exception as e:
            logger.warning(f"[dsl-fast] {tr.coin}: peak/mid fetch failed ({e}); skipping this tick")

    if not mids:
        return []

    for coin, h_list in highs.items():
        lo_list = lows.get(coin) or []
        hi = max(h_list) if h_list else None
        lo = min(lo_list) if lo_list else None
        tr = dsl_exit._active_positions.get(f"{coin}_long") or dsl_exit._active_positions.get(f"{coin}_short")
        if tr is not None:
            tr.ratchet_peak(hi, lo)

    exits = check_all_positions(mids)
    return [
        {
            "coin": v.coin,
            "side": v.position_side,
            "phase": v.phase,
            "leverage": v.leverage,
            "reason": v.reason,
            "unrealized_pct": v.unrealized_pct,
            "leveraged_pct": v.unrealized_pct * v.leverage,
        }
        for v in exits
    ]


def _ai_close_slot_defer_check(coin: str) -> Optional[Dict[str, Any]]:
    """Decide whether an LLM CLOSE verdict for `coin` falls OUTSIDE the LLM's
    slot-pressure jurisdiction. Returns None when the close should proceed
    untouched (guard off, no tracker, position armed, or book contended).

    Mirrors the code-level stale-flat timeout's own contention condition
    (dsl_exit.stale_flat_min_positions): the timeout deliberately does NOT cut
    never-armed drifters when the book is below the contention floor, because
    the "free the slot" rationale is false there. The LLM gets the same limit.
    Config lives in .agent-config.json under `ai_close_slot_defer`:
      enabled (default True), shadow_mode (default True = log only).
    Fail-safe: any read failure or missing tracker -> None (close proceeds).
    """
    try:
        cfg = read_agent_config().get("ai_close_slot_defer") or {}
    except Exception:
        return None
    if not bool(cfg.get("enabled", True)):
        return None
    try:
        from hermes_trader.agents import dsl_exit
        with dsl_exit._registry_lock:
            trackers = [t for t in dsl_exit._active_positions.values()
                        if t.coin == coin]
            used = len(dsl_exit._active_positions)
        if not trackers:
            return None  # no tracker (flat / not booked) — close no-ops anyway
        cap = int(read_agent_config().get("max_concurrent", 5) or 5)
        floor = 3
        dsl_cfg = read_agent_config().get("dsl_exit") or {}
        floor = int(dsl_cfg.get("stale_flat_min_positions", 3) or 0)
        if used >= floor:
            return None  # contended book — LLM close is legitimate
        # NEVER-ARMED test: peak profit below protect_pct for every tracker
        # on this coin. Uses mark_peak_px (best SAMPLED mark), not peak_px:
        # peak_px is wick-ratcheted from candle extremes, but the phase-2
        # floor only ever arms in check() on a sampled mark. CHIP 2026-10-06:
        # a wick ratcheted peak_px to exactly protect (1.0%) while the best
        # mark was +0.77% — the floor had never armed, yet the old peak_px
        # test read "armed" and let an LLM stale-close through.
        worst: Optional[Dict[str, Any]] = None
        for t in trackers:
            pol = t.policy
            mark_peak = getattr(t, "mark_peak_px", t.peak_px)
            if t.is_long():
                peak_pct = (mark_peak - t.entry_px) / t.entry_px * 100
            else:
                peak_pct = (t.entry_px - mark_peak) / t.entry_px * 100
            if peak_pct >= pol.protect_pct:
                return None  # armed at least once — LLM jurisdiction stands
            if worst is None or peak_pct < worst["peak_pct"]:
                worst = {"peak_pct": peak_pct, "protect_pct": pol.protect_pct}
        assert worst is not None
        # ── Trend-release escape (B.37-A, 2026-10-07) ─────────────────
        # "Never armed" tests PEAK profit, not direction of travel: a flat
        # drifter and a steady bleeder read identically. ADA 2026-10-06 hit
        # the guard's registered die-trigger — 97 DEFERRED lines while the
        # LLM insisted CLOSE at -0.9% -> -4.0%, and the only deterministic
        # exit left was the -5% exchange stop (-$1.36 vs ~-$0.30 early cut).
        # n=36 replay (scratch/_b37_cf_replay_1007.py): releasing LLM
        # jurisdiction once the mark is deeply negative beats pure defer at
        # every depth -1.5..-3.0 (net -12.0..-14.1 vs -15.1); -2.0 chosen
        # as plateau midpoint. Config: ai_close_slot_defer.trend_release
        # {enabled, depth_pct}; depth_pct<=0 or missing = clause inert
        # (byte-identical to pre-change guard).
        rel_cfg = cfg.get("trend_release") or {}
        if bool(rel_cfg.get("enabled", False)):
            _depth = float(rel_cfg.get("depth_pct", 0.0) or 0.0)
            if _depth > 0:
                for t in trackers:
                    _mark = getattr(t, "last_mark_px", None)
                    if _mark is None:
                        continue  # no fresh mark — fail-safe: guard stands
                    _upct = t._unrealized_pct(_mark)
                    if _upct <= -_depth:
                        logger.info(
                            f"[route_verdict] {coin}: ai_close TREND-RELEASED "
                            f"(never armed but unrealized {_upct:+.2f}% <= "
                            f"-{_depth:.2f}% spot) — deep bleeder, LLM "
                            f"jurisdiction restored."
                        )
                        return None
        return {
            "shadow": bool(cfg.get("shadow_mode", True)),
            "used": used, "cap": cap, "floor": floor,
            "peak_pct": worst["peak_pct"], "protect_pct": worst["protect_pct"],
            "verb": ("closing anyway (shadow)" if cfg.get("shadow_mode", True)
                     else "deferring to"),
            "reason": (f"ai_close_slot_defer: slots {used}/{cap} < floor {floor}, "
                       f"never armed (mark peak {worst['peak_pct']:+.2f}% < "
                       f"protect {worst['protect_pct']:.2f}%)"),
        }
    except Exception as exc:  # fail-safe: never block a close on guard bugs
        logger.warning(f"[route_verdict] ai_close slot-defer check failed "
                       f"for {coin}: {exc} — closing")
        return None


def route_verdict(analysis: Dict[str, Any], *, execute_fn=None, close_fn=None) -> Dict[str, Any]:
    """Route an analysis to the right action based on its verdict.

    Pure routing logic with the side-effecting functions injected, so EVERY
    verdict path is unit-testable. This exists because the dropped-CLOSE bug
    hid inside the trading loop's inline `if verdict in (...)` — orchestration
    that couldn't be tested. Now the loop calls this and just logs the result.

    Returns {"action": <str>, "verdict": <str>, "result": <dict|None>}:
      - LONG / SHORT  → action="execute", result = execute_fn(analysis)
      - CLOSE         → action="close",   result = close_fn(coin)
      - PASS          → action="none" (structural-override hints may still route it)
      - VETO          → action="none" (active rejection — override hints NEVER apply)
      - anything else → action="unknown" (logged loudly; never silently dropped)
    """
    execute_fn = execute_fn or maybe_execute
    close_fn = close_fn or close_position_market
    verdict = (analysis.get("verdict") or "").upper()
    coin = analysis.get("coin")

    if verdict in ("LONG", "SHORT"):
        _res = execute_fn(analysis)
        if isinstance(_res, dict):
            _attach_llm_context_to_result(_res, analysis)
        return {"action": "execute", "verdict": verdict, "result": _res}
    if verdict == "CLOSE":
        _reason = f"ai_close: {str(analysis.get('reasoning') or '')[:200]}"
        # ── Slot-pressure jurisdiction guard (2026-10-05, scan #5) ──────
        # The LLM's ai_close cohort cost ~$3.06 vs letting the deterministic
        # stale-flat clock run (n=15 replay, WATCHLIST §B.35 follow-up): every
        # dead-bag cut in the window fired at 1-of-5 slots where the "free the
        # capital" rationale is provably false, and 0/625 prompt-log responses
        # engaged with the book-slots fact the prompt already carries. The
        # code-level stale-flat timeout ALREADY defers below its contention
        # floor (stale_flat_min_positions, 2026-07-11); this gives the LLM the
        # same jurisdiction limit: a CLOSE for a NEVER-ARMED position (peak
        # profit < protect_pct — the same test the timeout uses) on an
        # UNCONTENDED book is deferred to the deterministic machinery
        # (max_loss / floor / hard_timeout). Armed positions and contended
        # books close exactly as before.
        # SHADOW MODE (default, house convention): log WOULD-HAVE-DEFERRED
        # loudly and close anyway — accrues the counterfactual until the
        # operator flips shadow_mode to false in .agent-config.json.
        _defer = _ai_close_slot_defer_check(coin) if coin else None
        if _defer is not None:
            _mode = "WOULD-HAVE-DEFERRED" if _defer["shadow"] else "DEFERRED"
            logger.warning(
                f"[route_verdict] {coin}: ai_close {_mode} "
                f"(slots {_defer['used']}/{_defer['cap']} in use < contention "
                f"floor {_defer['floor']}, never armed: mark peak "
                f"{_defer['peak_pct']:+.2f}% < protect {_defer['protect_pct']:.2f}%)"
                f" — {_defer['verb']} deterministic exits keep ownership. "
                f"LLM reasoning: {_reason[:160]}"
            )
            if not _defer["shadow"]:
                return {"action": "none", "verdict": verdict, "result": None,
                        "deferred_reason": _defer["reason"]}
        return {"action": "close", "verdict": verdict, "result": close_fn(coin, _reason)}
    if verdict in ("PASS", "VETO"):
        # PASS = neutral abstention; VETO (2026-09-03) = ACTIVE rejection,
        # categorically stronger. Both are no-action here, but the
        # structural-override HINT routing below applies to PASS ONLY — a
        # whale/composite/breakout force-execute can never upgrade a VETO.
        # maybe_execute carries its own guard for direct callers (server
        # /execute, MCP).
        # A hedging AI PASS can still carry a structural-override HINT: a whale
        # accumulation signal, or a strong slow-burn composite. maybe_execute
        # owns the real override decision and all gates, but it's only ever
        # reached via this router — so route only PASS verdicts whose force path
        # is actually enabled. A disabled force path should be a true no-op, not
        # an executor round-trip that looks like a missed trade in the logs.
        _rv_cfg = read_agent_config()
        _bar = float(_rv_cfg.get("force_execute_composite", 40))
        has_whale = (
            bool(analysis.get("whale_signal"))
            and bool(_rv_cfg.get("whale_force_execute", False))
        )
        slow_burn_hint = (
            bool(_rv_cfg.get("composite_force_execute", False))
            and float(analysis.get("composite_score", 0) or 0) >= _bar
            and int(analysis.get("slow_burn_count", 0) or 0)
            >= int(_rv_cfg.get("force_execute_slow_burn_count", 2))
        )
        breakout_hint = (
            bool(_rv_cfg.get("breakout_force_execute", False))
            and
            bool(analysis.get("volume_spike_fired"))
            and (bool(analysis.get("breakout_fired"))
                 or bool(analysis.get("uptrend_momentum_fired")))
            and (int(analysis.get("slow_burn_count", 0) or 0) >= 1
                 or float(analysis.get("composite_score", 0) or 0) >= _bar)
        )
        # Composite hint: route a TA-confirmed composite>=bar PASS to maybe_execute
        # so its (gated) composite_force_execute path can upgrade it. No-ops cleanly
        # in maybe_execute when the flag is off, so this is safe either way.
        composite_hint = (
            bool(_rv_cfg.get("composite_force_execute", False))
            and float(analysis.get("composite_score", 0) or 0) >= _bar
        )
        sidestep_hint = (
            bool(_rv_cfg.get("ta_sidestep_force_execute", False))
            and (
                float(analysis.get("composite_score", 0) or 0) >= _bar
                or bool(analysis.get("momentum_burst_fired"))
                or int(analysis.get("slow_burn_count", 0) or 0) >=
                int(_rv_cfg.get("ta_sidestep_min_slow_burn_count", 1) or 1)
            )
        )
        if verdict == "PASS" and (has_whale or slow_burn_hint or breakout_hint
                                  or composite_hint or sidestep_hint):
            _res = execute_fn(analysis)
            if isinstance(_res, dict):
                _attach_llm_context_to_result(_res, analysis)
            return {"action": "execute", "verdict": "PASS", "result": _res}
        # PASS/VETO have no implied direction; show median forecast with
        # alignment for both sides.
        try:
            from hermes_trader.agents.chronos_signal import get_chronos_signal_sync
            c = coin or "unknown"
            sig = get_chronos_signal_sync(c, "long")
            m = sig.median_pct if sig.median_pct is not None else None
            _res = {
                "action": "none", "verdict": verdict, "result": None,
                "chronos_median_pct": round(m * 10) / 10 if m is not None else None,
                "chronos_aligned_if_long": bool(m is not None and m > 0),
                "chronos_aligned_if_short": bool(m is not None and m < 0),
            }
            if sig.error:
                _res["chronos_error"] = sig.error
        except Exception as e:
            _res = {
                "action": "none", "verdict": verdict, "result": None,
                "chronos_median_pct": None,
                "chronos_aligned_if_long": None,
                "chronos_aligned_if_short": None,
                "chronos_error": str(e),
            }
        # TimesFM-3 shadow read, same PASS shape (direction-agnostic: one
        # median + both-side alignment flags — two calls would return
        # identical medians). Separate try/except so a timesfm outage can
        # never blank the chronos fields above. Disabled => error field only.
        try:
            from hermes_trader.agents.timesfm_signal import get_timesfm_signal_sync
            tsig = get_timesfm_signal_sync(coin or "unknown", "long")
            tm = tsig.median_pct if tsig.median_pct is not None else None
            _res["timesfm_median_pct"] = round(tm * 10) / 10 if tm is not None else None
            _res["timesfm_aligned_if_long"] = bool(tm is not None and tm > 0)
            _res["timesfm_aligned_if_short"] = bool(tm is not None and tm < 0)
            if tsig.error:
                _res["timesfm_error"] = tsig.error
        except Exception as e:
            _res["timesfm_median_pct"] = None
            _res["timesfm_aligned_if_long"] = None
            _res["timesfm_aligned_if_short"] = None
            _res["timesfm_error"] = str(e)
        # TiRex 1.1 shadow read, same PASS shape (direction-agnostic: one
        # median + both-side alignment flags). Separate try/except so a tirex
        # outage can never blank the chronos/timesfm fields above. Disabled
        # => error field only.
        try:
            from hermes_trader.agents.tirex_signal import get_tirex_signal_sync
            x_sig = get_tirex_signal_sync(coin or "unknown", "long")
            xm = x_sig.median_pct if x_sig.median_pct is not None else None
            _res["tirex_median_pct"] = round(xm * 10) / 10 if xm is not None else None
            _res["tirex_aligned_if_long"] = bool(xm is not None and xm > 0)
            _res["tirex_aligned_if_short"] = bool(xm is not None and xm < 0)
            if x_sig.error:
                _res["tirex_error"] = x_sig.error
        except Exception as e:
            _res["tirex_median_pct"] = None
            _res["tirex_aligned_if_long"] = None
            _res["tirex_aligned_if_short"] = None
            _res["tirex_error"] = str(e)
        # MoiraiAgent expert-selection shadow, same PASS shape as chronos/
        # timesfm (direction-agnostic: one re-centered-mixture median + both-
        # side alignment flags — the selection is side-independent, so one
        # cached read, never two). Cache-only; a cold entry is a no-op None
        # field set. Separate try/except so a selection outage can never
        # blank the chronos/timesfm fields above.
        try:
            from hermes_trader.agents.expert_select import peek_expert_select
            esig = peek_expert_select(coin or "unknown")
            em = esig.median_pct if (esig and esig.median_pct is not None) else None
            _res["expert_median_pct"] = round(em * 10) / 10 if em is not None else None
            _res["expert_aligned_if_long"] = bool(em is not None and em > 0)
            _res["expert_aligned_if_short"] = bool(em is not None and em < 0)
            _res["expert_selected_model"] = esig.selected_model if esig else None
            if esig and esig.error:
                _res["expert_error"] = esig.error
        except Exception as e:
            _res["expert_median_pct"] = None
            _res["expert_aligned_if_long"] = None
            _res["expert_aligned_if_short"] = None
            _res["expert_selected_model"] = None
            _res["expert_error"] = str(e)
        # The LLM's own conviction next to the forecast, so a no-action
        # PASS/VETO line shows what the model believed (not just why the
        # router abstained). Same field as the execute paths attach.
        _attach_llm_context_to_result(_res, analysis)
        return _res
    # Should be unreachable (parse_verdict normalizes to one of the above),
    # but never silently drop — surface it so a new verdict can't go unhandled.
    logger.warning(f"[router] unhandled verdict {verdict!r} for {coin} — treating as no-op")
    return {"action": "unknown", "verdict": verdict, "result": None}


def _late_chase_corroboration(analysis: Dict[str, Any], config: Dict[str, Any],
                              side: str) -> Tuple[Optional[int], tuple, tuple]:
    """Count independent signals corroborating a late-trend-chase entry.

    Returns (n, names, shadow_names): n is the count of ALIGNED independent
    signals that COUNT toward the bar in [0, 3] (clamped to the available
    signals), or None when the dynamic-bar feature is disabled
    (`late_chase_dynamic_per_signal_drop` <= 0) — the gate
    then behaves exactly as before (fixed bar, and no signal fetches at all).

    Signals (independent pipelines, NOT the LLM's own confidence):
      chronos_aligned — get_chronos_signal_sync median sign agrees with side
                        (300s cache: a silent hit in steady state) AND clears
                        the model's own confidence floor (deadband below).
      timesfm_aligned — get_timesfm_signal_sync median sign agrees with side
                        (300s cache), same deadband requirement. COUNTED only when
                        `late_chase_timesfm_vote` is true; while the flag is
                        off an aligned read instead logs a
                        [COUNTERFACTUAL] line so the additive vote accrues a
                        sample before it can move the live bar (the
                        2026-09-02 two-model sweep backed the additive vote,
                        but the marginal release cohort was +10.24 with a
                        weak second split-half — shadow first). When counted,
                        it lowers the bar by its OWN knob
                        `late_chase_timesfm_drop` (a separate, weaker value —
                        default 0.0 = inert), NOT the shared
                        `late_chase_dynamic_per_signal_drop`. The 2026-09-04
                        grid replay found tf 0.03 releases only the
                        both-aligned conf-0.78 rung (+$3.18) while the shared
                        0.10 over-releases (falls to +$1.68); the weaker
                        separate knob is what makes the AND shape binding.

    Deadband (2026-09-14, REZ −$20.08 max_loss): a vote additionally requires
    |median_pct| >= min_conf_ratio x spread_pct — the SAME confidence floor the
    signal module uses to render its log flag. Before this, the gate tested raw
    median sign while the log read `NEUTRAL (ratio 0.17 < 0.25)`: a forecast the
    system itself declares "no opinion" (median inside its own p10-p90 band)
    still dropped the bar 0.90 → 0.81 and admitted a composite-0 entry. A
    NEUTRAL read now casts NO vote (fail-closed, like missing/counter-direction).
    Floor is hot-read from chronos_signal.min_conf_ratio / timesfm_signal.min_conf_ratio
    (default 0.25; explicit 0 disables the deadband = old sign-only behavior).

    A signal only votes when it is exactly True; missing/None/error/
    counter-direction counts as 0. Failures never raise (fail-closed: fewer
    votes, never a false allow).
    """
    gate = config.get("runner_entry_gate") or {}
    drop = float(gate.get("late_chase_dynamic_per_signal_drop", 0.0))
    if drop <= 0:
        return (None, (), ())

    def _clears_deadband(sig, cfg: Dict[str, Any], label: str) -> bool:
        """True when the signal's median is OUTSIDE its own uncertainty band.

        Mirrors chronos_signal/timesfm_signal `_format_signal_log`: below the
        floor that module logs NEUTRAL, so a vote here must not contradict it.
        Fail-closed: missing median/spread or a garbage floor config means no
        vote (except explicit 0 = deadband disabled, per resolve_min_conf_ratio).
        """
        try:
            floor = _chronos_resolve_min_conf_ratio(cfg)
            med = getattr(sig, "median_pct", None)
            spread = getattr(sig, "spread_pct", None)
            if med is None or not spread:
                return False
            ratio = abs(med) / spread
            if ratio < floor:
                logger.info(
                    f"[executor] late-chase corroboration: {label} vote "
                    f"suppressed — NEUTRAL (ratio {ratio:.2f} < {floor:.2f}); "
                    "no-opinion read must not lower the bar")
                return False
            return True
        except Exception as e:  # never let the deadband check raise
            logger.debug(f"[executor] late-chase corroboration: {label} "
                         f"deadband check failed (treating as no vote): {e}")
            return False

    names: list = []
    # Chronos alignment — median sign vs side AND outside its own band; None
    # median, error, or a NEUTRAL (in-the-band) read = no vote.
    try:
        sig = get_chronos_signal_sync(analysis.get("coin") or "unknown", side)
        m = sig.median_pct if sig.median_pct is not None else None
        if not sig.error and m is not None:
            aligned = (m > 0) if side == "long" else (m < 0)
            if aligned and _clears_deadband(
                    sig, config.get("chronos_signal") or {}, "chronos"):
                names.append("chronos_aligned")
    except Exception as e:
        logger.debug(f"[executor] late-chase corroboration: chronos read failed "
                     f"(non-fatal): {e}")
    # TimesFM-3 alignment — median sign vs side AND outside its own band.
    # Counted into the bar only when `late_chase_timesfm_vote` is true; while
    # the flag is off an aligned read lands in shadow_names so the caller can
    # log the would-rescue counterfactual (sample accrual; the live bar is
    # byte-identical either way). The deadband applies to BOTH paths — a
    # NEUTRAL tf read neither votes nor accrues a rescue sample.
    shadow_names: list = []
    vote_target = names if bool(gate.get("late_chase_timesfm_vote", False)) else shadow_names
    if (config.get("timesfm_signal") or {}).get("enabled", False):
        try:
            from hermes_trader.agents.timesfm_signal import get_timesfm_signal_sync
            tsig = get_timesfm_signal_sync(analysis.get("coin") or "unknown", side)
            tm = tsig.median_pct if tsig.median_pct is not None else None
            if not tsig.error and tm is not None:
                aligned = (tm > 0) if side == "long" else (tm < 0)
                if aligned and _clears_deadband(
                        tsig, config.get("timesfm_signal") or {}, "timesfm"):
                    vote_target.append("timesfm_aligned")
        except Exception as e:
            logger.debug(f"[executor] late-chase corroboration: timesfm read failed "
                         f"(non-fatal): {e}")
    return (len(names), tuple(names), tuple(shadow_names))


def _sidestep_bearish_block_reason(analysis: Dict[str, Any], config: Dict[str, Any]) -> str:
    """Block a PASS->LONG sidestep that is actually a BEARISH impulse.

    The sidestep upgrades an AI PASS to LONG when the composite/burst setup
    clears, but the composite triggers (pctMoveSpike, volumeSpike, shockDay,
    momentumBurst) are MAGNITUDE-based (`abs(move)`), so a violent SELLOFF — a
    big red candle on huge volume — fires them just as hard as a breakout and
    produces a high composite. With no direction check the sidestep buys the
    falling knife (upstream 51bc23b, xyz:SMSN 2026-06-29: bought the open of a
    big red breakdown bar, -9.3% ROE). The short runner gate already requires
    `downtrend`; this restores the symmetric requirement for the long sidestep.

    Bearish impulse = downtrend momentum fired AND uptrend did not, OR the 24h
    move is clearly negative (<= sidestep_bearish_move_pct) with no uptrend.
    Explicit uptrend momentum always wins (a real upside setup). Reads
    `daily_move_pct` off the analysis dict (perception attaches it); a missing
    value degrades the 24h-move clause to a no-op, leaving the downtrend clause
    as the guaranteed protection. Flag-gated (sidestep_require_bullish,
    default true), hot-read, reversible. Returns a reason to block, or "" to
    allow. Analysis-only — no network in the execute hot path.
    """
    gate = config.get("runner_entry_gate") or {}
    if not bool(gate.get("sidestep_require_bullish", True)):
        return ""
    if bool(analysis.get("uptrend_momentum_fired")):
        return ""  # explicit bullish momentum — a real upside setup, allow
    downtrend = bool(analysis.get("downtrend_momentum_fired"))
    move_pct = analysis.get("daily_move_pct")
    try:
        move_pct = None if move_pct is None else float(move_pct)
    except (TypeError, ValueError):
        move_pct = None
    try:
        min_neg = float(gate.get("sidestep_bearish_move_pct", -3.0))
    except (TypeError, ValueError):
        min_neg = -3.0
    bearish_move = move_pct is not None and move_pct <= min_neg
    if downtrend or bearish_move:
        why = ("downtrend momentum fired" if downtrend
               else f"24h move {move_pct:+.1f}% <= {min_neg:.1f}%")
        return (f"sidestep_bearish_blocked ({analysis.get('coin')}: {why}, "
                f"no uptrend — would buy a selloff, not a breakout)")
    return ""


def _late_chase_tape_read():
    """BTC trail24h tape read for the late-chase bypass tape sub-gate.

    Module-level indirection over market_regime.btc_tape_activity (the SAME
    cached 5m-candle read the quiet_tape gate consumes, 300s TTL) so tests
    can monkeypatch it exactly like get_chronos_signal_sync. Returns
    {"vol": %, "drift": %} or None on a data gap.
    """
    from hermes_trader.agents.market_regime import btc_tape_activity
    try:
        return btc_tape_activity()
    except Exception:
        return None


def _late_chase_tape_gate(gate: Dict[str, Any], coin: str, side: str,
                          conf: float, bar: float,
                          suppresses: bool) -> Tuple[bool, str]:
    """BTC-loudness meta-gate on the late-chase BYPASS (2026-09-13).

    Evidence (.hermes/specs/late-chase-btc-tape-gate.md): the shadow bypass
    cohort's P/L tracks BTC's tape — entries taken while BTC trail24h vol
    < 1.5% netted +$109 (n=81) while vol >= 1.5% netted −$80 (n=70), and the
    entire 09-10/09-11 drawdown (−$114, 12 max_loss stops) sat in the loud
    bucket; signed drift < −1% was −$25 vs +$30 above +1%. So the bypass is
    ALLOWED only when BTC is calm-or-not-falling:

        allow = vol < late_chase_tape_vol_max_pct AND drift > late_chase_tape_drift_min_pct

    (Opposite polarity to quiet_tape, which BLOCKS entries on a quiet tape —
    this gate consumes the raw read, not that verdict.)

    Only consulted when conf >= bar (a bypass candidate): with the master
    flag off it is never called and behavior is byte-identical. Deny
    suppresses the bypass even when `bypass_late_trend_chase: true` — the
    late-chase block reason then stands, exactly as a sub-bar conf. A data
    gap FAILS SAFE TO DENY for a live bypass (the extra evidence is missing;
    unlike quiet_tape's gap-passes rule, here absence of evidence is not a
    reason to release). Shadow mode never changes the outcome — it appends
    `TAPE=allow|deny|gap(...)` to the caller's log line so the accrual
    carries the verdict + raw reads for offline replay.

    Returns (allowed, tape_note) — tape_note is "" when the gate is off.
    """
    if not bool(gate.get("late_chase_tape_gate", False)):
        return True, ""
    vol_max = float(gate.get("late_chase_tape_vol_max_pct", 1.5))
    drift_min = float(gate.get("late_chase_tape_drift_min_pct", -1.0))
    tape = _late_chase_tape_read()
    if tape is None or tape.get("vol") is None or tape.get("drift") is None:
        note = f" TAPE=gap(fail-safe deny, allow-band vol<{vol_max:.2f}, drift>{drift_min:.2f})"
        if suppresses:
            logger.warning(
                f"[gate][TAPE] late_chase_bypass DENIED for {coin} "
                f"{side.upper()}: conf {conf:.2f} >= bar {bar:.2f} but no "
                f"BTC tape read — fail-safe deny (allow-band vol<{vol_max:.2f}, "
                f"drift>{drift_min:.2f})")
        return False, note
    vol, drift = float(tape["vol"]), float(tape["drift"])
    allow = vol < vol_max and drift > drift_min
    reads = f"vol {vol:.2f}%, drift {drift:+.2f}%"
    if allow:
        return True, f" TAPE=allow({reads})"
    note = (f" TAPE=deny({reads}, allow-band vol<{vol_max:.2f}, "
            f"drift>{drift_min:.2f})")
    if suppresses:
        logger.warning(
            f"[gate][TAPE] late_chase_bypass DENIED for {coin} "
            f"{side.upper()}: conf {conf:.2f} >= bar {bar:.2f} but BTC tape "
            f"{reads} outside allow-band (vol<{vol_max:.2f}, "
            f"drift>{drift_min:.2f}) — blocked")
    return False, note


def _late_chase_composite_gate(gate: Dict[str, Any], coin: str, side: str,
                               conf: float, bar: float, score: float,
                               suppresses: bool) -> Tuple[bool, str]:
    """Composite-score floor on the late-chase bypass (2026-09-14).

    Counterfactual evidence (scratch/_late_chase_composite_cf.py, shadow cohort
    09-05→09-13, n=582 scans / 148 executable): bypass admits with composite
    score == 0 netted −$77.97 (n=15, 5 max_loss stops) and were negative on
    EVERY day they traded, while composite > 0 netted +$74.74 (n=133). A
    zero-composite late-chase admit means NO trigger fired at all — no
    breakout, burst, mover, volume spike, slow burn: a bare LLM verdict
    against a bar lowered by one forecast vote (REZ 09-13 22:46, −$20.08).

    Semantics mirror `_late_chase_tape_gate`: consulted ONLY at bypass
    candidates (conf >= bar); floor <= 0 or absent ⇒ (True, "") with no
    behavior change (byte-identical to pre-feature). A deny suppresses even a
    LIVE bypass (the standard late-chase block reason then stands, byte-
    identical for parsers) and logs a loud [gate][COMPOSITE] DENIED line.
    Shadow accrual lines carry COMPOSITE=allow|deny so the cohort keeps its
    counterfactual record. Fail-safe: score is already `or 0` normalized by
    the caller; a missing composite reads as 0 = deny side when the floor is
    on (deliberate — the floor asks for positive evidence of setup).

    Returns (allowed, comp_note); comp_note is "" when the floor is off.
    """
    floor = float(gate.get("late_chase_bypass_min_composite", 0.0) or 0.0)
    if floor <= 0:
        return True, ""
    if score >= floor:
        return True, f" COMPOSITE=allow(score {score:.1f})"
    note = (f" COMPOSITE=deny(score {score:.1f} < floor {floor:.1f})")
    if suppresses:
        logger.warning(
            f"[gate][COMPOSITE] late_chase_bypass DENIED for {coin} "
            f"{side.upper()}: conf {conf:.2f} >= bar {bar:.2f} but composite "
            f"score {score:.1f} < floor {floor:.1f} — blocked")
    return False, note


# B.27 shadow accrual (2026-09-21): per-coin throttle for the volume-accumulation
# probe so a repeatedly-blocked coin can't spend candleSnapshot weight every scan.
_LC_VA_LAST: Dict[str, float] = {}
_LC_VA_LOCK = threading.Lock()


def _late_chase_volume_accrual(gate: Dict[str, Any], coin: str, side: str,
                               conf: float, bar: float) -> None:
    """Log-only volume-accumulation accrual on late-chase BLOCKS (B.27 shadow).

    Replay finding (.hermes/WATCHLIST.md §B.27): the late-chase blocked cohort's
    runway is NOT entry-limited — at volume-accumulation release points a median
    +5–6% 24h run typically remains — but the scalp exit forfeits it, and only
    ~18% of strict-threshold releases chase an exhausted move. The candidate
    admission rule (never live here): vol_last / mean(prior 96 finalized 5m
    volumes) >= `late_chase_va_ratio_min` counts as fresh-impulse-equivalent,
    PAIRED with the dormant dsl_exit trend_ride policy as the exit.

    This function NEVER changes the block/pass decision — it is called only on
    the block path and logs one `[gate][SHADOW] late_chase_volume_accumulation
    WOULD RELEASE ...` line when the ratio clears the floor, so the admit cohort
    accrues forward data for the promote/die call (offline mirror joins scalp-
    vs-trend_ride deltas from candles; nothing is simulated live). Flag off /
    key absent => no fetch, byte-identical behavior. Throttled per coin
    (`late_chase_va_per_coin_cooldown_min`, default 30) — the scan re-researches
    the same blocked coin every few minutes and candleSnapshot weight is shared
    with the live scanner. Any error fails closed: no line, block unchanged.
    """
    if not bool(gate.get("late_chase_volume_shadow", False)):
        return
    ratio_min = float(gate.get("late_chase_va_ratio_min", 5.0) or 0.0)
    if ratio_min <= 0:
        return
    cooldown_s = float(gate.get("late_chase_va_per_coin_cooldown_min", 30) or 30) * 60.0
    now = time.time()
    with _LC_VA_LOCK:
        if now - _LC_VA_LAST.get(coin, 0.0) < cooldown_s:
            return
        # Reserve before the fetch (single-flight; on error the throttle still
        # applies — a coin whose candles fail shouldn't retry every scan).
        _LC_VA_LAST[coin] = now
    try:
        # 98 bars => >=97 finalized after dropping the still-forming candle.
        candles = fetch_hl_candles(coin, "5m", 98)
        if not candles or len(candles) < 97:
            return
        vols = [float(c["v"]) for c in candles[:-1]]  # finalized only
        if len(vols) < 97:
            return
        window, cur = vols[-97:-1], vols[-1]
        mean = sum(window) / len(window)
        if mean <= 0:
            return
        ratio = cur / mean
        if ratio < ratio_min:
            return
        logger.warning(
            f"[gate][SHADOW] late_chase_volume_accumulation WOULD RELEASE "
            f"{coin} {side.upper()}: vol_ratio {ratio:.1f}x >= {ratio_min:.1f}x "
            f"(96-bar accumulation floor) conf {conf:.2f} < bar {bar:.2f} — "
            f"NOT releasing (shadow accrual, B.27); live rule stands")
    except Exception as e:  # never let the accrual raise into the gate path
        logger.debug(f"[executor] late-chase volume-accumulation accrual "
                     f"failed for {coin}: {e}")


def _runner_entry_block_reason(analysis: Dict[str, Any], config: Dict[str, Any]) -> str:
    """Block entries that are not fresh runner setups.

    The live ledger's repeated loss mode is not "no runners exist"; it is broad
    admission of late trend-only names and whale-only PASS upgrades. This gate
    keeps execution focused on fresh impulse setups: volume plus breakout/burst,
    backed by either 1h structure or a strong composite score.

    Daily movers are NOT exempt from the late-chase check: the
    `structured_daily_mover` admission only satisfies the final structure
    requirement below. Movers that are uptrend-only (no fresh impulse) hit the
    same conf-vs-bar dynamic test as any other uptrend candidate (2026-09-12,
    VVV −$18.81 max_loss; the old exemption let the 28-trade mover cohort
    (−$77.97, −$2.78/trade) in with zero gate coverage).
    """
    gate = config.get("runner_entry_gate") or {}
    if not bool(gate.get("enabled", False)):
        return ""

    coin = analysis.get("coin") or ""
    is_hip3 = ":" in coin
    side = (analysis.get("side") or "").lower()
    conf = float(analysis.get("confidence", 0) or 0)
    score = float(analysis.get("composite_score", 0) or 0)
    min_conf = float(gate.get("min_confidence", 0.70))
    min_score = float(gate.get("min_composite", 30.0))
    min_hip3_score = float(gate.get("min_hip3_composite", 50.0))

    volume = bool(analysis.get("volume_spike_fired"))
    breakout = bool(analysis.get("breakout_fired"))
    burst = bool(analysis.get("momentum_burst_fired"))
    daily_mover = bool(analysis.get("daily_mover_fired"))
    uptrend = bool(analysis.get("uptrend_momentum_fired"))
    downtrend = bool(analysis.get("downtrend_momentum_fired"))
    slow_count = int(analysis.get("slow_burn_count", 0) or 0)
    whale = bool(analysis.get("whale_signal"))
    forced = "[structural override]" in (analysis.get("reasoning") or "")

    fresh_impulse = (volume and (breakout or burst)) or (burst and score >= min_score)

    # Cohort admission (B.46): model+cohort-scoped entry path for promoted
    # signal cohorts whose edge demonstrably lives OUTSIDE the general
    # population's requirements — the fade-short archive read (326 eps):
    # conf p50 0.42 / max 0.58 (0/34 reach the 0.65 floor; the low-conf
    # band is where the 92% WR lives) and 1/60 pass structured_short (fade
    # setups have no fresh impulse BY CONSTRUCTION). Key is
    # "<answering model>/<cohort tag>" so the exemption is welded to the
    # gauntleted model: another model in the seat (or a tagless analysis or
    # an absent key) = byte-identical general path. The cohort floor is a
    # SANITY floor, not selectivity; below it the entry falls through to
    # the normal checks and logs the standard block reason. Shorts remain
    # subject to the global allow_shorts kill-switch. Every OTHER gate
    # (quiet-tape, band-counter-breach, late-chase sub-gates, cooldowns,
    # slots) still applies — this exempts the runner gate only.
    cohort_tag = str(analysis.get("cohort_tag") or "").strip().lower()
    if cohort_tag:
        adm = gate.get("cohort_admissions") or {}
        adm_key = f"{str(analysis.get('primary_model') or '').strip().lower()}/{cohort_tag}"
        if adm_key in adm and (side != "short" or bool(gate.get("allow_shorts", False))):
            try:
                cohort_floor = float(adm[adm_key])
            except (TypeError, ValueError):
                cohort_floor = None  # malformed -> general path (fail-open)
            if cohort_floor is not None and conf >= cohort_floor:
                logger.info(
                    f"[gate][COHORT] ADMIT {coin} {side.upper()}: {adm_key} "
                    f"conf {conf:.2f} >= cohort floor {cohort_floor:.2f} — "
                    f"runner-gate shape checks exempted (other gates still apply)"
                )
                return ""

    if conf < min_conf:
        return f"runner_gate_blocked (confidence {conf:.2f} < {min_conf:.2f})"

    if side == "short":
        if not bool(gate.get("allow_shorts", False)):
            return "runner_gate_blocked (shorts disabled)"
        short_min_score = float(gate.get("min_short_composite", min_score))
        short_min_conf = float(gate.get("min_short_confidence", min_conf))
        if conf < short_min_conf:
            return f"runner_gate_blocked (short confidence {conf:.2f} < {short_min_conf:.2f})"
        structured_short = (
            downtrend
            or (score >= short_min_score and (slow_count >= 1 or fresh_impulse))
            or (fresh_impulse and score >= min_score)
        )
        if not structured_short:
            return (f"runner_gate_blocked (short needs downtrend momentum or "
                    f"fresh impulse+structure; score={score:.0f}, slow={slow_count})")
        return ""

    if side != "long":
        return ""

    structured_daily_mover = (
        daily_mover
        and conf >= float(gate.get("mover_min_confidence", 0.80))
        and score >= float(gate.get("mover_min_composite", 45.0))
        and (slow_count >= 1 or volume or breakout or burst)
    )
    structured_runner = fresh_impulse and (slow_count >= 1 or score >= min_score)

    if is_hip3:
        en = config.get("signal_enforcement") or {}
        gex_cfg = config.get("gex_signal") or {}
        if (
            bool(en.get("enabled", False))
            and bool(en.get("veto", True))
            and bool(en.get("gex_veto", True))
            and bool(gex_cfg.get("enabled", True))
        ):
            try:
                from hermes_trader.agents.options_gex import gex_override_caution
                near = float(gex_cfg.get("caution_near_wall_pct", 1.0))
                suppress, why = gex_override_caution(
                    coin, "long", near_wall_pct=near, allow_fetch=False
                )
                if suppress:
                    if bool(gex_cfg.get("shadow_mode", False)):
                        logger.info(f"[executor] GEX entry veto [SHADOW - not blocked] "
                                    f"on {coin}: {why}")
                    else:
                        return f"runner_gate_blocked ({why})"
            except Exception as e:
                logger.debug(f"[executor] GEX entry veto check failed for {coin}: {e}")
    if is_hip3 and score < min_hip3_score:
        return (f"runner_gate_blocked (HIP-3 composite {score:.0f} "
                f"< {min_hip3_score:.0f})")
    if forced and whale and not fresh_impulse:
        return "runner_gate_blocked (whale-only forced override; no fresh breakout/burst)"
    if uptrend and not fresh_impulse:
        # 2026-09-12 (VVV −$18.81 max_loss): the `structured_daily_mover` term
        # used to exempt 24h-mover admits from this late-chase test entirely —
        # the blind spot behind the 28-trade mover cohort (−$77.97, 10W/28,
        # −$2.78/trade vs the book's −$0.35, 2026-09-06→11 sweep). Movers now
        # hit the SAME conf-vs-bar dynamic test; their structure admission
        # (`structured_daily_mover`) still counts toward the final
        # fresh-impulse-or-structure requirement below.
        # Dynamic confidence bar: independent-signal corroboration lowers the
        # late-chase bypass bar so a 0.78 LLM with two aligned signals can
        # enter what a bare 0.90 bar (never reached; LLM tops out ~0.78)
        # would block forever. Clamped to min_confidence so corroboration can
        # never undercut the hard confidence floor. Feature off (drop <= 0 or
        # key absent) → the fixed bar, byte-identical to before.
        fixed_bar = float(gate.get("bypass_late_trend_chase_min_conf", 0))
        drop = float(gate.get("late_chase_dynamic_per_signal_drop", 0.0))
        tf_vote = bool(gate.get("late_chase_timesfm_vote", False))
        tf_drop = float(gate.get("late_chase_timesfm_drop", 0.0))
        corr, corr_names, corr_shadow = _late_chase_corroboration(analysis, config, side)
        if corr is None:
            bar, bar_note = fixed_bar, ""
        else:
            # Per-vote drop: the timesfm vote (only present in corr_names when
            # tf_vote is on) lowers the bar by its OWN `late_chase_timesfm_drop`;
            # the chronos vote keeps the shared `drop`. This keeps the tf vote a
            # separate, weaker lever (0.03) so it releases only the
            # both-aligned rung instead of the shared 0.10's over-release.
            weighted_drop = (
                sum(tf_drop if nm == "timesfm_aligned" else drop for nm in corr_names)
                if tf_vote else corr * drop
            )
            bar = max(fixed_bar - weighted_drop, min_conf)
            bar_note = (f" (dynamic bar {bar:.2f} from {fixed_bar:.2f}, "
                        f"{corr} signal{'s' if corr != 1 else ''} aligned: "
                        f"{'+'.join(corr_names) if corr_names else 'none'})")
        # Tri-state bypass (2026-09-06): `bypass_late_trend_chase` true = live
        # bypass (existing behavior); false + `bypass_late_trend_chase_shadow_mode`
        # true = SHADOW — the trade is still blocked (byte-identical reason, so
        # external log parsers keep working) but a loud [gate][SHADOW] line
        # accrues the would-bypass decision for the promote/die call; both
        # false/absent = off (fail-safe default, pre-feature behavior). The
        # bool is authoritative: shadow only matters when the bool is false.
        # Rationale: the 2026-09-05 24h day review attributed n=20 chase-bypass
        # entries at −$67.64 (incl. both max_loss stops, both conf 0.78 vs the
        # dynamic 0.77 bar) vs normal entries at +$37.15 — the relaxation
        # admits exactly the late-top class it should guard; shadow accrues the
        # conf-vs-bar + coin distribution for replay before re-enabling.
        _bt = gate.get("bypass_late_trend_chase", False)
        _bs = bool(gate.get("bypass_late_trend_chase_shadow_mode", False))
        tape_note = ""
        if conf >= bar:
            bypassed = bool(_bt)
            # BTC-loudness meta-gate on the bypass (2026-09-13, spec
            # .hermes/specs/late-chase-btc-tape-gate.md): consulted ONLY at
            # bypass candidates (conf >= bar); master flag off ⇒ returns
            # (True, "") with no fetch — byte-identical to before. A DENY
            # suppresses even a live bypass; shadow mode never changes the
            # outcome, it only annotates the accrual line with TAPE=verdict.
            _allowed, tape_note = _late_chase_tape_gate(
                gate, coin, side, conf, bar, suppresses=bypassed)
            if bypassed and not _allowed:
                bypassed = False
            # Composite-score floor (2026-09-14): a zero-composite candidate
            # has NO setup evidence at all — see helper docstring for the
            # cohort numbers. Runs after the tape gate so both annotations
            # land on shadow accruals; floor off ⇒ no-op, byte-identical.
            _allowed_c, comp_note = _late_chase_composite_gate(
                gate, coin, side, conf, bar, score, suppresses=bypassed)
            if bypassed and not _allowed_c:
                bypassed = False
            if bypassed:
                logger.info(f"[executor] late-trend chase bypassed on {coin} "
                            f"(conf {conf:.2f} >= bar {bar:.2f}){bar_note}"
                            f"{tape_note}{comp_note}")
                # B.27 scope-knob tag (2026-09-21): this entry got in via the
                # late-chase bypass — carried on the analysis dict so the open
                # path can hand it to select_exit_params(entry_class=...). Only
                # set when the bypass is LIVE (shadow accruals never tag);
                # harmless extra key if no regime_aware.scope is configured.
                analysis["_entry_class"] = "late_chase"
            elif _bs:
                logger.warning(
                    f"[gate][SHADOW] late_chase_bypass WOULD HAVE BYPASSED "
                    f"{coin} {side.upper()}: conf {conf:.2f} >= bar "
                    f"{bar:.2f}{bar_note}{tape_note}{comp_note} — NOT "
                    f"bypassing (shadow mode), live rule stands")
        else:
            bypassed = False
        if not bypassed:
            # Log-only counterfactual (never changes pass/fail): timesfm
            # aligned this late-chase entry but its vote is not counted
            # (late_chase_timesfm_vote off) — had it counted, the bar would
            # have been `tf_drop` lower (its own knob, NOT the shared drop)
            # and this block may have released. Strictly rescue-side: the
            # candidate bar is the live bar minus the tf contribution and is
            # always <= the live bar (tf_drop is a positive, smaller lever;
            # if the tf vote were ever sized above the shared drop, the
            # guard below keeps the marker off rather than ever ADD a block).
            # Accrue these against P/L before flipping the vote on
            # (2026-09-02 two-model sweep).
            if (corr is not None and corr_shadow and tf_drop > 0
                    and tf_drop <= drop
                    and conf >= max(bar - tf_drop, min_conf)):
                logger.warning(
                    f"[gate][COUNTERFACTUAL] late_chase block RESCUED by "
                    f"timesfm additive vote for {coin} {side.upper()}: conf "
                    f"{conf:.2f} < live bar {bar:.2f} but >= candidate bar "
                    f"{max(bar - tf_drop, min_conf):.2f} "
                    f"({'+'.join(corr_shadow)}) — live rule stands, vote-on "
                    f"rule would have passed")
            # B.27 shadow accrual (log-only; flag off => no-op, byte-identical):
            # volume-accumulation WOULD-RELEASE line for the promote/die cohort.
            _late_chase_volume_accrual(gate, coin, side, conf, bar)
            return (f"runner_gate_blocked (late trend-only chase; no fresh "
                    f"breakout/burst, conf {conf:.2f}, bar {bar:.2f}){bar_note}")
    else:
        bypassed = False

    # Only require fresh impulse structure if we didn't just bypass above.
    if not (fresh_impulse or structured_daily_mover):
        if not bypassed:
            if not (structured_runner or structured_daily_mover):
                return (f"runner_gate_blocked (needs volume+breakout/burst "
                        f"and structure; score={score:.0f}, slow={slow_count})")
    return ""


def _dv_trap_veto_reason(analysis: Dict[str, Any], config: Dict[str, Any]) -> str:
    """Decision-voice entry trap-veto (clef-gate scope Change 2).

    The Clef-family decision voice (decision_voice.py, /v1/systemone) answers
    a `trap` noul per scan — "is ENTERING this setup right now actively
    dangerous". Evidence: WATCHLIST §C.19 gauntlet + overlay costing — trap
    >= 0.40 veto removes -$3.98 of the gated walk book's -$4.68 (clef as
    ENTRY DECIDER was rejected; as trap-veto it is the whole win).

    Config `decision_voice_gate.trap_veto`:
      shadow_mode true  -> log WOULD-BE-BLOCKED, allow the trade (start here;
                           the counterfactual join prices it against real
                           close rows — the B.10/momentum_reentry pattern).
      shadow_mode false -> hard block; the reason string flows into the
                           existing runner_gate_blocked forensics path.
    Threshold lives in config, NOT baked in code (raw trap noul is logged,
    so every threshold variant is priced offline).

    FAIL-OPEN by doctrine: no dv row (observer off/failed) or a missing trap
    scalar = "no observation", never a block. Never raises.
    """
    try:
        gate = (config.get("decision_voice_gate") or {})
        if not bool(gate.get("enabled", False)):
            return ""
        tv = gate.get("trap_veto") or {}
        dv = analysis.get("decision_voice_at_entry") or {}
        trap = dv.get("trap")
        if trap is None:
            return ""  # fail-open: no observation
        threshold = float(tv.get("threshold", 0.45))
        trap = float(trap)
        if trap < threshold:
            return ""
        coin = analysis.get("coin") or "unknown"
        side = (analysis.get("side") or "long")
        conf = float(analysis.get("confidence", 0) or 0)
        score = float(analysis.get("composite_score", 0) or 0)
        if bool(tv.get("shadow_mode", True)):
            logger.warning(
                f"[gate][SHADOW] dv_trap_veto WOULD BE BLOCKED {coin} "
                f"{str(side).upper()}: trap {trap:.2f} >= {threshold:.2f}, "
                f"conf {conf:.2f}, composite {score:.0f}, "
                f"pid {analysis.get('perception_id', 'unknown')} — "
                f"NOT blocking (shadow accrual, clef-gate); live rule stands")
            return ""
        return (f"dv_trap_veto (trap {trap:.2f} >= {threshold:.2f})")
    except Exception as e:  # noqa: BLE001 — a broken gate never costs/blocks a trade
        logger.debug(f"[executor] dv_trap_veto gate failed for "
                     f"{analysis.get('coin')}: {e}")
        return ""


def _exit_type(reason: str) -> Optional[str]:
    """Coarse exit class from the leading token of a close reason string.

    "max_loss (7.05% spot / …)" → "max_loss"; "ai_close: …" → "ai_close";
    "operator_close:losing" → "operator_close"; "" → None.
    """
    if not reason:
        return None
    return reason.split(" ", 1)[0].split(":", 1)[0]


# Full-close booking dedupe (B.36 double-book class). coin_side_entry_px ->
# monotonic ts of the last BOOKED full close. A second booked full close of
# the SAME position (same coin/side/entry_px) within the window books
# NOTHING: on 2026-10-03 the killswitch flattened MON, the same-tick
# rehydrate re-synthesized the tracker from a stale position read, and the
# second flatten booked a SECOND CLOSE row — double-counted PnL feeding the
# daily-PnL comparator and the ledger. The reduce-only ORDER still goes to
# the exchange (flattening safety > ledger hygiene; a still-open position
# must get closed), only the BOOKING is deduped. Keyed on entry_px so a
# legitimate re-entry (different entry) books normally. Partial slices are
# NOT stamped — the DSL's follow-up close of the residual must book too.
# @_serialized already serializes callers, so no lock is needed.
_RECENTLY_CLOSED_WINDOW_S = 600.0
_recently_closed_full: Dict[str, float] = {}


def was_recently_booked_close(coin: str, side: str, entry_px: float) -> bool:
    """True when close_position_market booked a FULL close for this exact
    position (coin/side/entry_px) within the dedupe window. The stale-settle
    path consults this so a fill already booked by the executor's flatten is
    never booked a second time as an exchange_close — the actual §B.36 shape
    on 2026-10-03 was killswitch_daily_loss (20:12:20) + stale-settle
    exchange_close (20:17:42) for the SAME fill."""
    try:
        key = f"{coin}_{side}_{float(entry_px):.10g}"
    except (TypeError, ValueError):
        return False
    return (time.monotonic() - _recently_closed_full.get(key, 0.0)) < _RECENTLY_CLOSED_WINDOW_S


@_serialized
def close_position_market(coin: str, exit_reason: str = "") -> Dict[str, Any]:
    """Market-close any open perp position for `coin`. Deregisters the DSL tracker on success.

    `exit_reason` (optional, caller-supplied) is recorded verbatim on the
    outcome store and the append-only ledger so a CLOSE row answers "why did
    this exit?" without log archaeology (DSL reasons arrive e.g. as
    "max_loss (…% spot / …% ROE)"; kill-switch / rotation / operator / AI
    closes supply their own tokens). Empty string = reason unknown — the
    ledger row then carries exit_reason=null, exit_type=null (back-compat).

    Returns include `entry_px`, `fill_px`, and `realized_pnl_pct` (leveraged,
    net of taker fees) whenever the close fills with a parseable avgPx — so the
    trading loop can log the actual realized PnL instead of an estimate based
    on the pre-trade mid.

    A result with NO fill evidence (ok:True, no avgPx — the reduce-only order
    matched nothing) is verified against the exchange before the tracker is
    dropped: position still open → `noop: no_fill_position_still_open`,
    tracker + SL/TP kept; confirmed flat → `noop:
    no_fill_position_already_flat`. A no-op close never books realized PnL.
    """
    user = resolve_user_address()
    if not user:
        return {"ok": False, "coin": coin, "error": "no_user_address"}

    # include_hip3=True so we can resolve HIP-3 positions (xyz:MU, vntl:*, ...).
    # Without this every close call for a HIP-3 position would fall into the
    # `already_flat` branch even when the position is real.
    state = fetch_account_state(user, include_hip3=True)
    pos = next(
        (p for p in state.get("asset_positions", [])
         if p.get("position", {}).get("coin") == coin),
        None,
    )
    if not pos:
        # Already flat — drop any stale tracker so we don't keep retrying.
        deregister_position(coin, "long")
        deregister_position(coin, "short")
        return {"ok": True, "coin": coin, "noop": "already_flat"}

    try:
        szi = float(pos["position"].get("szi", "0") or 0)
        entry_px = float(pos["position"].get("entryPx") or 0)
    except (TypeError, ValueError):
        return {"ok": False, "coin": coin, "error": "bad_szi"}
    if szi == 0:
        deregister_position(coin, "long")
        deregister_position(coin, "short")
        return {"ok": True, "coin": coin, "noop": "zero_szi"}

    is_long = szi > 0
    side = "long" if is_long else "short"
    mid_price = get_hl_price(coin)
    if mid_price <= 0:
        return {"ok": False, "coin": coin, "error": f"invalid_price_for_{coin}"}

    # Look up tracker leverage before close so the realized PnL can be computed
    # at the right multiplier even after deregister.
    from hermes_trader.agents import dsl_exit
    tracker = dsl_exit._active_positions.get(f"{coin}_{side}")
    leverage = tracker.leverage if tracker else 1

    # reduce_only: a close must only FLATTEN. Without it, the $10-min size floor in
    # place_hl_order overshoots a sub-$10 position and flips it to the opposite side
    # (the BIRD short<->long churn loop). reduce_only makes HL ignore the excess.
    res = place_hl_order(is_buy=not is_long, size=abs(szi), mid_price=mid_price, coin=coin,
                         reduce_only=True)
    out: Dict[str, Any] = {**res, "coin": coin, "side": side,
                            "entry_px": entry_px, "leverage": leverage}

    if res.get("ok"):
        fill_px = res.get("avg_px")
        # A close with NO fill evidence (ok:True but no avgPx — the reduce-only
        # order matched nothing because the position was already gone) must be
        # verified against the exchange before it is treated as a close. Without
        # this, a no-op close of a position that's already flat still drops the
        # tracker, cancels the SL/TP bracket, and reports ok:True — and anything
        # downstream that books a realized row on that result would record a
        # close the exchange never saw (2026-08-23: the ARB-short rows with no
        # OPEN and no matching fill).
        if not (fill_px and entry_px > 0):
            post = fetch_account_state(user, include_hip3=True) or {}
            # A failed read looks exactly like a flat account (empty
            # asset_positions). Require positive equity to trust the read:
            # on a read failure the tracker + SL/TP bracket MUST stay —
            # rehydrate_from_exchange reconciles next cycle.
            post_equity = 0.0
            try:
                post_equity = float(post.get("equity") or 0)
            except (TypeError, ValueError):
                post_equity = 0.0
            if post_equity <= 0:
                logger.error(f"[executor] {coin} close: no fill evidence AND "
                             f"post-close account read failed/empty — cannot "
                             f"confirm flat; tracker + bracket KEPT (reconciles "
                             f"next cycle)")
                out["noop"] = "no_fill_postread_failed"
                return out
            post_pos = next(
                (p for p in post.get("asset_positions", [])
                 if p.get("position", {}).get("coin") == coin),
                None,
            )
            post_szi = 0.0
            if post_pos:
                try:
                    post_szi = float(post_pos["position"].get("szi", "0") or 0)
                except (TypeError, ValueError):
                    post_szi = 0.0
            if post_szi != 0:
                # Still open — the order simply didn't match. Keep the tracker
                # and its SL/TP; monitor_exits re-evaluates next tick.
                logger.warning(f"[executor] {coin} close: no fill evidence and "
                               f"position still {post_szi:g} — no-op, tracker kept")
                out["noop"] = "no_fill_position_still_open"
                return out
            # Confirmed flat: drop the stale tracker and stranded bracket.
            deregister_position(coin, side)
            cancel_open_orders_for_coin(coin)
            out["noop"] = "no_fill_position_already_flat"
            return out

        # Partial-fill check: an IOC can match less than the requested size if
        # the book thins between the L2 read and the fill. total_sz absent (0)
        # = size unknown → treat as a full close (legacy behavior).
        try:
            _filled_sz = float(res.get("total_sz") or 0)
        except (TypeError, ValueError):
            _filled_sz = 0.0
        _requested_sz = abs(szi)
        _partial = 0 < _filled_sz < _requested_sz
        _closed_sz = _filled_sz if _partial else _requested_sz
        if not _partial:
            # Full close — drop the tracker and clear the stranded reduce-only
            # SL/TP bracket so stale orders don't reject a future reduce-only
            # order on this coin.
            deregister_position(coin, side)
            cancel_open_orders_for_coin(coin)
            # BOOKING dedupe (B.36): a second booked full close of the SAME
            # position (coin/side/entry_px) within the window books nothing.
            # The order above still went to the exchange (flatten safety),
            # but the ledger/outcome-store row is skipped — the 2026-10-03
            # killswitch + rehydrate re-synth double-booked MON's close.
            _dedupe_key = f"{coin}_{side}_{entry_px:.10g}"
            _now_mono = time.monotonic()
            _prev_ts = _recently_closed_full.get(_dedupe_key, 0.0)
            if _now_mono - _prev_ts < _RECENTLY_CLOSED_WINDOW_S:
                logger.warning(
                    f"[executor] {coin} {side}: duplicate full close "
                    f"{_now_mono - _prev_ts:.0f}s after a booked close of the "
                    f"same position (entry {entry_px}) — order sent, NOT "
                    f"re-booking (B.36 dedupe)")
                out["noop"] = "duplicate_close_recent"
                return out
            _recently_closed_full[_dedupe_key] = _now_mono
            # prune stale stamps
            for _k in [k for k, ts in _recently_closed_full.items()
                       if _now_mono - ts >= _RECENTLY_CLOSED_WINDOW_S]:
                _recently_closed_full.pop(_k, None)
        if fill_px and entry_px > 0:
            # Book the realized slice at the actual fill price (avg_px), not the
            # pre-trade mid, on exactly what closed.
            # Spot move from the perspective of the position: long earns when
            # mark rises, short earns when mark falls.
            if is_long:
                spot_pct = (fill_px - entry_px) / entry_px * 100
            else:
                spot_pct = (entry_px - fill_px) / entry_px * 100
            # 2 round-trip taker fills at 2.5bps × leverage
            fees_pct = 0.025 * 2 * leverage
            out["fill_px"] = fill_px
            out["spot_pct"] = round(spot_pct, 4)
            out["realized_pnl_pct"] = round(spot_pct * leverage - fees_pct, 4)
            out["fees_pct"] = round(fees_pct, 4)
            # ── Trade-outcome store ─────────────────────────────────────────
            # Persist the realized exit so win-rate / payoff / risk-of-ruin /
            # Phase-3 stats have a real source (trades[].pnl was never written).
            # Single chokepoint → covers DSL, AI-close, and kill-switch exits.
            # Wrapped: a bookkeeping failure must never abort a close.
            try:
                _notional_entry = _closed_sz * entry_px
                _closed_at = int(time.time() * 1000)
                # Pull the entry-context snapshot (entry time + signals at entry +
                # enforcement) so this outcome row is self-contained for the forward
                # signal backtest. Empty {} for positions opened before this shipped.
                _ec = memory.pop_entry_context(coin, side)
                _entry_time = _ec.get("entry_time")
                _hold_min = (round((_closed_at - _entry_time) / 60000.0, 1)
                             if _entry_time else None)
                _gross_pnl_usd = _notional_entry * spot_pct / 100.0
                _fee_usd = _notional_entry * (fees_pct / max(leverage, 1)) / 100.0
                _funding_cost_usd = (
                    round(_ec["funding_rate_hr"]
                          * (_hold_min / 60.0 if _hold_min else 0)
                          * _notional_entry
                          * (1 if is_long else -1), 4)
                    if _ec.get("funding_rate_hr") is not None else None
                )
                _net_pnl_usd = _gross_pnl_usd - _fee_usd
                if _funding_cost_usd is not None:
                    _net_pnl_usd -= _funding_cost_usd
                memory.record_close({
                    "coin": coin, "side": side,
                    "entry_px": entry_px, "exit_px": fill_px,
                    "size_coin": _closed_sz, "notional_usd": round(_notional_entry, 4),
                    "spot_pct": out["spot_pct"],
                    "realized_pnl_pct": out["realized_pnl_pct"],   # leveraged, net fees
                    "realized_pnl_usd": round(_net_pnl_usd, 4),
                    "gross_pnl_usd": round(_gross_pnl_usd, 4),
                    "fee_usd": round(_fee_usd, 4),
                    "leverage": leverage,
                    "closed_at": _closed_at,
                    # why this exit happened (DSL reason verbatim, or the
                    # caller's token) + a coarse class for easy grouping:
                    # "max_loss (…% spot …)" → "max_loss".
                    "exit_reason": exit_reason or None,
                    "exit_type": _exit_type(exit_reason),
                    # forward-backtest fields:
                    "entry_time": _entry_time,
                    "hold_minutes": _hold_min,
                    "signals_at_entry": _ec.get("signals") or {},
                    "enforcement_at_entry": _ec.get("enforcement") or {},
                    "forced_override": _ec.get("forced_override"),
                    # execution-quality + regime (the audit data items a/c/d):
                    "entry_slip_bps": _ec.get("entry_slip_bps"),
                    "exit_slip_bps": (round((((fill_px - mid_price) / mid_price * 1e4)
                                             * (1 if is_long else -1)) * -1, 1)
                                      if (fill_px and mid_price) else None),
                    "regime_at_entry": _ec.get("regime"),
                    "is_hip3": ":" in coin,
                    # A/B duelist: the second model's verdict at entry (see
                    # research.py's duelist_at_entry). The duel report computes
                    # its hypothetical P&L from this + the realized fields.
                    "duelist_at_entry": _ec.get("duelist"),
                    # Decision voice at entry (verdict + trap/close_now
                    # scalars) — the clef-gate offline join prices the
                    # trap-veto / close-veto counterfactuals from this.
                    "decision_voice_at_entry": _ec.get("decision_voice"),
                    "perception_id": _ec.get("perception_id"),
                    # funding carry: rate_hr × hold_hrs × notional × side (long pays
                    # when rate>0). Estimate (entry-rate held constant over the hold).
                    "funding_cost_usd": _funding_cost_usd,
                })
                # Append-only ledger (separate from operational memory)
                try:
                    record_close(
                        coin=coin,
                        side=side,
                        entry_px=entry_px,
                        exit_px=fill_px,
                        notional_usd=round(_notional_entry, 4),
                        realized_pnl_pct=out["realized_pnl_pct"],
                        realized_pnl_usd=round(_net_pnl_usd, 4),
                        spot_pct=out["spot_pct"],
                        hold_minutes=_hold_min,
                        leverage=leverage,
                        fee_usd=round(_fee_usd, 4),
                        funding_cost_usd=_funding_cost_usd,
                        exit_reason=exit_reason or None,
                        exit_type=_exit_type(exit_reason),
                        perception_id=_ec.get("perception_id"),
                    )
                except Exception as _lc_e:
                    logger.debug(f"[executor] ledger record_close failed (non-fatal): {_lc_e}")
            except Exception as _rc_e:
                logger.warning(f"[outcome-store] record_close failed for {coin} (non-fatal): {_rc_e}")
            # Loss cooldown: a losing close arms an extended re-entry block on
            # this coin (config `loss_cooldown_min`, 0 = off). Anti-revenge rule:
            # TON was churned 3x in one day because the standard cooldown expired
            # and the AI re-bought the same falling name each time.
            # A max_loss (stop) close arms the LONGER `stop_loss_cooldown_min`
            # window when set — a stopped name is the falling-knife case the
            # plain cooldown was calibrated for, and re-entry into the same
            # regime is what compounds stop clusters (2026-09 ledger: top-5
            # stop coins held 53% of all stop losses).
            if out["realized_pnl_pct"] < 0:
                try:
                    _acfg = read_agent_config()
                    lc_min = float(_acfg.get("loss_cooldown_min", 0) or 0)
                    if _exit_type(exit_reason) == "max_loss":
                        lc_min = max(lc_min, float(_acfg.get("stop_loss_cooldown_min", 0) or 0))
                    if lc_min > 0:
                        until = int(time.time() * 1000 + lc_min * 60_000)
                        memory.set_loss_cooldown(coin, until)
                        logger.info(f"[executor] loss cooldown armed on {coin}: "
                                    f"{lc_min:.0f}min (closed {out['realized_pnl_pct']:.2f}%)")
                except Exception as e:
                    logger.warning(f"[executor] loss-cooldown arm failed for {coin}: {e}")
    return out
