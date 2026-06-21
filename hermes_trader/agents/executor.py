"""Auto-executor: validates through risk gates, sizes the trade, executes LIVE.

Integrates the DSL exit engine for two-phase trailing stops
(loss protection -> profit locking).
"""

from __future__ import annotations

import logging
import os
import re
import time
import uuid
from typing import Any, Dict, List

from hermes_trader.agents.config_store import read_agent_config
from hermes_trader.agents.dsl_exit import (
    ExitPolicy,
    RetraceTier,
    active_position_coins,
    check_all_positions,
    deregister_position,
    register_position,
)
from hermes_trader.agents.memory import memory
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
from hermes_trader.client.hl_client import fetch_account_state, resolve_user_address

logger = logging.getLogger(__name__)

# Backup server-side stop multiplier. RETUNED 2026-06-02 (microscope audit): was
# 3.5 -> ~5.5% spot on median names, far too wide to catch anything. The data showed
# 54% of max_loss exits GAP PAST the 1.2% DSL cap (median realized -1.56%, worst -3.6%)
# because the DSL loop only checks every 60s. A tighter server-side backup fires
# INSTANTLY at the exchange between our scans, catching the gap cluster. 1.5x ATR sits
# ~2.4% on median names (above the 1.2% DSL so DSL still fires first on normal exits,
# but tight enough to cap the gap-throughs that were the asymmetry killer). Config-tunable.
_DEFAULT_SL_ATR_MULT = 1.5
TP_ATR_MULT = 1.0

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


def _get_daily_move_pct(coin: str) -> float | None:
    """Current 24h move from the universe cache, used as an override chase guard."""
    try:
        from hermes_trader.client.universe import get_universe
        for m in get_universe(include_hip3=(":" in coin)):
            if m.get("coin") != coin:
                continue
            prev = float(m.get("prevDayPx") or 0)
            cur = float(m.get("midPx") or m.get("markPx") or 0)
            if prev > 0 and cur > 0:
                return (cur - prev) / prev * 100
            return None
    except Exception as e:
        logger.debug(f"[executor] daily move lookup failed for {coin}: {e}")
    return None


def _analysis_daily_move_pct(analysis: Dict[str, Any]) -> float | None:
    for key in ("daily_move_pct", "move_24h_pct", "daily_mover_pct"):
        val = analysis.get(key)
        if val is None:
            continue
        try:
            return float(val)
        except (TypeError, ValueError):
            continue
    return _get_daily_move_pct(str(analysis.get("coin") or ""))


def _sidestep_extension_block_reason(analysis: Dict[str, Any], config: Dict[str, Any]) -> str:
    """Block PASS->LONG sidestep on already-parabolic daily movers."""
    try:
        max_extension = float(config.get("override_max_daily_extension_pct", 30.0) or 0.0)
    except (TypeError, ValueError):
        max_extension = 30.0
    if max_extension <= 0:
        return ""
    move_pct = _analysis_daily_move_pct(analysis)
    if move_pct is None:
        return ""
    if move_pct >= max_extension:
        return (f"sidestep_extension_blocked ({analysis.get('coin')}: "
                f"24h move {move_pct:+.1f}% >= {max_extension:.1f}% ceiling)")
    return ""


def _late_chase_relax_ok(analysis: Dict[str, Any], config: Dict[str, Any], coin: str) -> bool:
    """Validated pocket (2026-06-21 edge_extension.py): trend-aligned 'late chase' entries
    (uptrend, no fresh breakout/burst) are +EV / OOS-robust ONLY on LIQUID coins in the
    20-30% daily-extension band (+0.15-0.20%/t, ~60% win). The same chase is -EV elsewhere
    (>30% ext, and -1.27% GROSS on low-liquidity coins, which reverse). So the late-chase
    gate may pass ONLY inside that measured pocket. Config-gated; caller handles shadow_mode."""
    rc = config.get("late_chase_relax") or {}
    if not bool(rc.get("enabled", False)):
        return False
    ext = _analysis_daily_move_pct(analysis)
    if ext is None:
        return False
    lo = float(rc.get("min_ext_pct", 20.0))
    hi = float(rc.get("max_ext_pct", 30.0))
    if not (lo <= ext <= hi):
        return False
    try:
        vol = _get_market_volume_24h(coin)
    except Exception:
        return False
    return vol >= float(rc.get("min_volume_usd", 5_000_000.0))


def _backup_sl_price(
    entry_px: float,
    atr_abs: float,
    is_long_position: bool,
    sl_atr_mult: float,
    leverage: float,
    max_frac_of_liq: float,
) -> tuple[float, bool]:
    """Server-side disaster stop capped inside an approximate liquidation buffer."""
    atr_dist = max(0.0, float(atr_abs or 0.0) * max(0.0, float(sl_atr_mult or 0.0)))
    dist = atr_dist
    capped = False
    if entry_px > 0 and leverage > 0 and max_frac_of_liq > 0:
        max_dist = entry_px * (float(max_frac_of_liq) / float(leverage))
        if max_dist > 0 and (dist <= 0 or dist > max_dist):
            dist = max_dist
            capped = True
    if is_long_position:
        return max(0.0, entry_px - dist), capped
    return entry_px + dist, capped


def _asset_notional_multiplier(coin: str, config: Dict[str, Any]) -> float:
    """Exposure scale by asset bucket, clamped to [0, 1].

    This is deliberately a sizing adjustment, not a gate: weak buckets can keep
    trading exceptional setups, but with less dollar risk attached.
    """
    raw = config.get("asset_notional_multiplier", {}) or {}
    if not isinstance(raw, dict):
        return 1.0
    key = "hip3" if ":" in (coin or "") else "crypto"
    try:
        mult = float(raw.get(key, 1.0))
    except (TypeError, ValueError):
        return 1.0
    return max(0.0, min(mult, 1.0))


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

    # Narrow TA sidestep: route an AI PASS only when the scanner already found a
    # fresh composite/burst setup. Broad whale/composite/breakout/slow-burn force
    # routes were negative in the realized ledger and have been removed.
    _runner_cfg = config.get("runner_entry_gate") or {}
    sidestep_bar = float(_runner_cfg.get("min_composite", 30.0))
    ta_sidestep_strong = (
        bool(config.get("ta_sidestep_force_execute", False))
        and (
            float(analysis.get("composite_score", 0) or 0) >= sidestep_bar
            or bool(analysis.get("momentum_burst_fired"))
        )
    )
    if analysis.get("verdict") == "PASS" \
            and ta_sidestep_strong \
            and bool(analysis.get("ai_down")):
        logger.info(
            f"[executor] TA sidestep SKIPPED on {analysis['coin']}: "
            f"AI research is DOWN (failure-PASS, not an opinion) — no blind upgrade"
        )
        return {
            "executed": False, "mode": mode,
            "analysis_id": analysis["id"],
            "reason": "override_blocked_ai_down (research failed; PASS is an error, not a verdict)",
        }
    if analysis.get("verdict") == "PASS" and ta_sidestep_strong:
        _ext_block = _sidestep_extension_block_reason(analysis, config)
        if _ext_block:
            logger.info(f"[executor] TA sidestep SKIPPED on {analysis['coin']}: {_ext_block}")
            return {
                "executed": False, "mode": mode,
                "analysis_id": analysis["id"],
                "reason": _ext_block,
            }

    # Keep the signal veto on sidestep upgrades before they enter the normal
    # runner gate. No signal boost is applied; the threshold is fixed to the
    # runner gate's normal min_composite.
    _enf = None
    try:
        from hermes_trader.agents.shadow_signals import enforce_signals
        _enf = enforce_signals(analysis["coin"], "long", config)
    except Exception as _enf_e:
        logger.debug(f"[executor] signal enforcement failed (non-fatal): {_enf_e}")
    if analysis.get("verdict") == "PASS" \
            and ta_sidestep_strong \
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

    if analysis.get("verdict") == "PASS" and ta_sidestep_strong:
        # Williams volume-confirm on the OVERRIDE path: a PASS->LONG upgrade only fires
        # WITH volume. Surgical (not a global filter — that over-filtered): targets the
        # documented no-volume-override leak (ledger: no-vol overrides -$24 / n=28 vs
        # vol-confirmed overrides +$16 / n=12). Flag-gated + hot-read; fails open.
        _vc = config.get("override_volume_confirm") or {}
        if bool(_vc.get("enabled", False)):
            if not _volume_confirmed(analysis.get("coin") or "",
                                     float(_vc.get("min_ratio", 1.5)),
                                     int(_vc.get("lookback", 20))):
                logger.info(
                    f"[executor] TA sidestep SKIPPED on {analysis['coin']}: override lacks "
                    f"volume confirmation (< {float(_vc.get('min_ratio', 1.5))}x avg) — the no-volume-override leak")
                return {
                    "executed": False, "mode": mode,
                    "analysis_id": analysis["id"],
                    "reason": "override_no_volume_confirm (no-volume sidestep override blocked)",
                }
        _conf_floor = float(config.get("min_ai_confidence", 0.70))
        logger.info(
            f"[executor] TA sidestep on {analysis['coin']}: "
            f"AI PASS but composite/burst setup cleared → upgrading to LONG conf {_conf_floor:.2f}"
        )
        analysis = dict(analysis)
        analysis["verdict"] = "LONG"
        analysis["side"] = "long"
        analysis["confidence"] = max(_conf_floor, float(analysis.get("confidence", 0) or 0))
        analysis["sidestep_override"] = True
        analysis["reasoning"] = (
            "[TA sidestep] " + (analysis.get("reasoning", "") or "")
        )[:500]

    # Safety guard: a PASS that did NOT qualify for TA sidestep must
    # never reach order placement (trade_side defaults to "long" downstream, so
    # an un-upgraded PASS would otherwise silently fire a long). route_verdict
    # only sends a PASS here when an override HINT applies — this is the real
    # check that no-ops cleanly when the override doesn't actually hold.
    if analysis.get("verdict") == "PASS":
        return {
            "executed": False, "mode": mode,
            "analysis_id": analysis["id"], "reason": "pass_no_override",
        }

    runner_block = _runner_entry_block_reason(analysis, config)
    if runner_block:
        return {
            "executed": False, "mode": mode,
            "analysis_id": analysis["id"], "reason": runner_block,
        }

    # PTJ 200-day-MA trend-regime filter — only trade WITH the daily trend. Validated on
    # our realized ledger (edge_legends.py): trend-aligned-only flipped the book -$23->+$15,
    # win 46%->53%, halved max DD. Flag-gated; external-alpha exempt.
    trend_block = _trend_filter_block_reason(analysis, config)
    if trend_block:
        return {
            "executed": False, "mode": mode,
            "analysis_id": analysis["id"], "reason": trend_block,
        }

    # Loss cooldown: refuse re-entry on a coin whose last close was a LOSS and
    # whose extended block hasn't expired (armed in close_position_market).
    _lc_remaining = memory.loss_cooldown_remaining_min(analysis["coin"])
    if _lc_remaining > 0:
        return {
            "executed": False, "mode": mode,
            "analysis_id": analysis["id"],
            "reason": (f"loss_cooldown ({analysis['coin']} closed at a loss recently — "
                       f"{_lc_remaining:.0f}min remaining)"),
        }

    # Per-coin re-entry cap: block re-entering the SAME coin more than N times in a
    # rolling window — limits the churn/fee-bleed that makes win-and-reenter -EV
    # (backtested 2026-06-21: fee-dominated; JUP churned 5x in a day, net negative).
    # Config-gated + hot-read; the SELECTION layer (AI/gates) is the real edge, this is
    # just a guardrail against pathological over-churn of one name.
    _rc = config.get("reentry_cap") or {}
    if bool(_rc.get("enabled", False)):
        try:
            _cap = int(_rc.get("max_per_coin", 3))
            _win_h = float(_rc.get("window_hours", 24.0))
            _n_recent = memory.count_entries_since(
                analysis["coin"], time.time() * 1000 - _win_h * 3_600_000)
            if _cap > 0 and _n_recent >= _cap:
                return {
                    "executed": False, "mode": mode,
                    "analysis_id": analysis["id"],
                    "reason": (f"reentry_cap ({analysis['coin']} {_n_recent} entries in "
                               f"{_win_h:.0f}h >= cap {_cap} — anti-churn)"),
                }
        except Exception as _e:
            logger.warning(f"[executor] reentry_cap check failed (non-fatal): {_e}")

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
        deq = st.get("dex_equity") or {}
        dav = st.get("dex_available") or {}
        if _target_dex:
            eq = float(deq.get(_target_dex, 0) or 0)
            av = float(dav.get(_target_dex, 0) or 0)
        else:
            eq = float(deq.get("", st.get("equity")) or 0)
            av = float(dav.get("", st.get("available")) or 0)
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
    # Per-account sizing equity: a trade is FUNDED by ONE account (main for crypto,
    # the specific HIP-3 dex for colon coins), so size the risk budget against THAT
    # account's equity — NOT the aggregate. Sizing on aggregate over-sizes a trade vs
    # the balance that actually funds it (e.g. $170 agg but only $60 on main → positions
    # ~3x too big → main saturates after 1-2 trades, blocking every other mover). This
    # keeps sizing proportional to fundable capital at ANY account size (works at $20).
    # Falls back to aggregate if the per-dex breakdown is missing (degraded read).
    _sz_dex = coin.split(":", 1)[0] if ":" in (coin or "") else ""
    size_equity = float((state.get("dex_equity") or {}).get(_sz_dex, agg_equity) or agg_equity)
    if size_equity <= 0:
        size_equity = agg_equity
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

    coin = analysis["coin"]
    leverage = min(int(config.get("leverage", HL_LEVERAGE)),
                   get_max_leverage(coin))
    _notional_cap = float(config.get("max_trade_notional_usd", 0) or 0)
    # External-alpha trades size to their own (smaller) cap — these edges are real but
    # thin/regime-volatile, so they ride at reduced notional until live data confirms.
    _ext_notional = float(analysis.get("external_alpha_notional", 0) or 0)
    if _ext_notional > 0:
        _notional_cap = min(_notional_cap, _ext_notional) if _notional_cap > 0 else _ext_notional
    # SHORTS ride at a small dedicated cap — newly-enabled, validated only in the current
    # down-regime (edge_shorts.py: trend-aligned shorts +0.52%/trade, 73% win, OOS-robust;
    # late/down-mover shorts lose). Trend filter gates them to downtrends. Small until live
    # data confirms. 0 = use the normal cap.
    _short_notional = float(config.get("short_notional_usd", 0) or 0)
    if (analysis.get("side") == "short") and _short_notional > 0:
        _notional_cap = min(_notional_cap, _short_notional) if _notional_cap > 0 else _short_notional
    _atr_sizing = config.get("atr_risk_sizing", {}) or {}
    _atr_sizing_enabled = bool(_atr_sizing.get("enabled", False))
    mid_price = 0.0
    atr = 0.0
    size_in_coin = 0.0

    if _atr_sizing_enabled:
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
        _sizing_basis = str(_atr_sizing.get("sizing_basis", "backup_stop") or "backup_stop").lower()
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
            _lev_cap = min(get_max_leverage(coin), int(config.get("leverage", HL_LEVERAGE)))
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
                config_max_leverage=int(config.get("leverage", HL_LEVERAGE)),
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
        # equity × fraction × leverage. Keep this deterministic; confidence and
        # whale multipliers were removed because they changed exposure without
        # improving realized EV.
        base_fraction = float(config.get("equity_fraction_per_trade", 0.01))
        trade_notional = size_equity * base_fraction * leverage
        # Clamp to the per-trade notional ceiling so an oversized fallback bet is
        # SIZED DOWN to the cap rather than REJECTED by the notional gate.
        if _notional_cap > 0 and trade_notional > _notional_cap:
            logger.info(f"[executor] notional ${trade_notional:.0f} > cap "
                        f"${_notional_cap:.0f} — clamping to cap")
            trade_notional = _notional_cap

    _asset_mult = _asset_notional_multiplier(coin, config)
    if _asset_mult < 1.0:
        _before_mult = trade_notional
        trade_notional *= _asset_mult
        logger.info(
            f"[executor] asset notional multiplier {coin}: "
            f"${_before_mult:.0f} × {_asset_mult:.2f} = ${trade_notional:.0f}"
        )

    # Normalize to the exact HL-valid entry size BEFORE risk gates. The order
    # layer enforces a $10.50 minimum and coin-size precision; if we wait until
    # place_hl_order() to apply that, the gates, DSL tracker, memory, and SL/TP
    # brackets all believe a smaller position exists than the one actually sent.
    if mid_price <= 0:
        mid_price = get_hl_price(coin)
        if mid_price <= 0:
            return {"executed": False, "mode": mode, "analysis_id": analysis["id"],
                    "reason": f"invalid_price_for_{coin}"}
    try:
        min_notional = min_entry_notional_usd(coin, mid_price)
        if min_notional > 0 and trade_notional < min_notional:
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

    recent_trades = memory.get_recent_trades(10)
    last_trade = next(
        (t for t in recent_trades if t.get("coin") == analysis["coin"]),
        None,
    )
    last_trade_time = last_trade.get("executed_at") if last_trade else None

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
        slow_burn_fired=bool(analysis.get("slow_burn_fired", False)),
        peak_daily_pnl=memory.peak_daily_pnl(),
    )

    gate_output = eval_all_gates(ctx, config, last_trade_time)

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
                        _cr = close_position_market(_d.evict_coin)
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
        return {
            "executed": False, "mode": mode,
            "analysis_id": analysis["id"],
            "blocked_by": gate_output["block_reasons"],
            "gate_results": gate_output["results"],
        }

    if shadow_mode:
        return {
            "executed": False, "mode": mode,
            "analysis_id": analysis["id"],
            "reason": "shadow_mode_would_execute",
            "gate_results": gate_output["results"],
            "size_usd": trade_notional,
        }

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
    entry_px = filled_px if filled_px > 0 else mid_price
    if filled_size > 0:
        size_in_coin = filled_size
    position_notional = abs(size_in_coin) * entry_px

    # Register the position with the DSL tracker; it re-evaluates the exit
    # floor on every scan tick (loss protection -> profit locking).
    dsl_config = config.get("dsl_exit", {})
    # phase2_tiers is optional in config; when present it OVERRIDES the class
    # default ladder so profit-locking tightness is tunable without code edits.
    _ex_protect = float(dsl_config.get("protect_pct", 1.5))
    _ex_retrace = float(dsl_config.get("retrace_threshold", 0.30))
    _tiers_raw = dsl_config.get("phase2_tiers")
    _tiers = [RetraceTier(**t) for t in _tiers_raw] if _tiers_raw else None
    _noise_cfg = dsl_config.get("noise_band", {}) or {}
    # ATR-scaled stop wiring — was MISSING here, so a dsl_exit.atr_stop config block was
    # silently ignored (the stop stayed fixed/tight no matter the config). Wired now: the
    # vol-scaled stop is the fix for the tight-stop whipsaw (validated: tight 0.4% stop
    # whipsaws volatile movers out before they run — EIGEN, the all-longs-negative finding,
    # the vol-stop replay). Clamp + the ROE cap still bound the per-trade loss.
    _atr_stop_cfg = dsl_config.get("atr_stop", {}) or {}
    logger.info(f"[executor] exit policy = base protect={_ex_protect} retrace={_ex_retrace} "
                f"atr_stop={'on' if _atr_stop_cfg.get('enabled') else 'off'}")
    policy = ExitPolicy(
        max_loss_pct=dsl_config.get("max_loss_pct", 2.5),
        max_loss_roe_pct=dsl_config.get("max_loss_roe_pct", 50.0),
        protect_pct=_ex_protect,
        retrace_threshold=_ex_retrace,
        hard_timeout_minutes=dsl_config.get("hard_timeout_minutes", 180.0),
        breakeven_trigger_pct=dsl_config.get("breakeven_trigger_pct", 0.0),
        breakeven_lock_pct=dsl_config.get("breakeven_lock_pct", 0.0),
        stale_flat_timeout_minutes=float(dsl_config.get("stale_flat_timeout_minutes", 0.0) or 0.0),
        consecutive_breaches_required=int(dsl_config.get("consecutive_breaches_required", 1) or 1),
        noise_band_enabled=bool(_noise_cfg.get("enabled", False)),
        noise_band_atr_mult=float(_noise_cfg.get("atr_mult", 1.0)),
        atr_stop_enabled=bool(_atr_stop_cfg.get("enabled", False)),
        atr_stop_mult=float(_atr_stop_cfg.get("atr_mult", 1.5)),
        atr_stop_floor_pct=float(_atr_stop_cfg.get("floor_pct", 1.0)),
        atr_stop_ceiling_pct=float(_atr_stop_cfg.get("ceiling_pct", 4.0)),
        phase2_tiers=_tiers if _tiers else ExitPolicy().phase2_tiers,
    )
    # ATR as % of entry is captured for volatility-relative persisted policies
    # such as the noise band.
    entry_atr_pct = (atr / entry_px * 100) if entry_px > 0 else 0.0
    register_position(coin, trade_side, entry_px, policy=policy, leverage=leverage,
                      entry_atr_pct=entry_atr_pct)
    logger.info(f"[executor] Registered DSL exit for {coin} {trade_side} @ {entry_px} ({leverage}x)")
    # Forward-shadow the wider VOL ATR-stop (no live effect) — paper-tracks this entry
    # with the 2.0x ATR stop to validate the vol-stop hypothesis live. Gated + wrapped.
    try:
        from hermes_trader.agents.volstop_shadow import record_entry as _vs_record
        _vs_record(coin, entry_px, trade_side, leverage, entry_atr_pct, config)
    except Exception as _vse:
        logger.debug(f"[volstop-shadow] entry hook failed: {_vse}")

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
        try:
            from hermes_trader.agents.market_regime import detect_regime as _detect_regime
            _entry_regime = _detect_regime(coin)
        except Exception:
            _entry_regime = "unknown"
        memory.record_entry_context(coin, trade_side, {
            "entry_time": _entry_ts,
            "arrival_mid": _arr_mid,
            "entry_fill": _fill,
            "entry_slip_bps": _slip_bps,
            "funding_rate_hr": _funding_hr,
            "regime": _entry_regime,
            "signals": _entry_sig,
            "enforcement": ({"veto": _enf.veto, "veto_reason": _enf.veto_reason,
                             "boost": _enf.boost, "boost_reason": _enf.boost_reason}
                            if _enf is not None else {}),
            "override_bar": sidestep_bar,
            "forced_override": bool(analysis.get("sidestep_override")),
        })
    except Exception as _ec_e:
        logger.debug(f"[executor] entry-context capture failed (non-fatal): {_ec_e}")

    # Backup exchange stop-loss bracket — fires server-side (instantly, between our
    # 60s DSL checks) to cap the gap-throughs the DSL loop misses. DSL is still the
    # primary/normal exit; this is the fast safety net.
    sl_atr_mult = float(config.get("sl_atr_mult", _DEFAULT_SL_ATR_MULT))
    backup_sl_max_frac = float(config.get("backup_sl_max_frac_of_liq", 0.60) or 0.0)
    sl_missing = False
    backup_sl_px = stop_px
    if atr > 0 and size_in_coin > 0:
        sl_px, sl_capped = _backup_sl_price(
            entry_px=entry_px,
            atr_abs=atr,
            is_long_position=is_buy,
            sl_atr_mult=sl_atr_mult,
            leverage=leverage,
            max_frac_of_liq=backup_sl_max_frac,
        )
        backup_sl_px = sl_px
        sl_res = place_hl_trigger_order(is_buy, size_in_coin, sl_px, "sl", coin)
        if not sl_res.get("ok"):
            # One retry after a beat — observed failures are transient 429s; a
            # position with no server-side stop carries the full gap-through
            # risk between 60s DSL checks, so a single retry is cheap insurance.
            time.sleep(2)
            sl_res = place_hl_trigger_order(is_buy, size_in_coin, sl_px, "sl", coin)
        if sl_res.get("ok"):
            cap_note = (
                f", capped at {backup_sl_max_frac:.0%} of ~liq buffer"
                if sl_capped else ""
            )
            logger.info(f"[executor] Placed backup SL at {sl_px} "
                        f"({sl_atr_mult}x ATR{cap_note})")
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

    final_sl = backup_sl_px if atr > 0 else stop_px
    final_tp = (entry_px + atr * TP_ATR_MULT) if is_buy else (entry_px - atr * TP_ATR_MULT) if atr > 0 else tp_px

    return {
        "executed": True, "mode": mode,
        "analysis_id": analysis["id"],
        "order_id": order_res.get("order_id"),
        "gate_results": gate_output["results"],
        "size_usd": position_notional,
        "entry_px": entry_px,
        "stop_px": final_sl,
        "tp_px": final_tp,
        "dsl_registered": True,
        "sl_missing": sl_missing,
    }


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


def route_verdict(analysis: Dict[str, Any], *, execute_fn=None, close_fn=None) -> Dict[str, Any]:
    """Route an analysis to the right action based on its verdict.

    Pure routing logic with the side-effecting functions injected, so EVERY
    verdict path is unit-testable. This exists because the dropped-CLOSE bug
    hid inside the trading loop's inline `if verdict in (...)` — orchestration
    that couldn't be tested. Now the loop calls this and just logs the result.

    Returns {"action": <str>, "verdict": <str>, "result": <dict|None>}:
      - LONG / SHORT  → action="execute", result = execute_fn(analysis)
      - CLOSE         → action="close",   result = close_fn(coin)
      - PASS          → action="none"
      - anything else → action="unknown" (logged loudly; never silently dropped)
    """
    execute_fn = execute_fn or maybe_execute
    close_fn = close_fn or close_position_market
    verdict = (analysis.get("verdict") or "").upper()
    coin = analysis.get("coin")

    if verdict in ("LONG", "SHORT"):
        return {"action": "execute", "verdict": verdict, "result": execute_fn(analysis)}
    if verdict == "CLOSE":
        return {"action": "close", "verdict": verdict, "result": close_fn(coin)}
    if verdict == "PASS":
        # A hedging AI PASS can still carry a narrow TA-sidestep hint. The
        # executor owns the real upgrade decision and every risk gate; this
        # router only forwards PASS verdicts that could qualify.
        _rv_cfg = read_agent_config()
        _gate = _rv_cfg.get("runner_entry_gate") or {}
        _bar = float(_gate.get("min_composite", 30.0))
        sidestep_hint = (
            bool(_rv_cfg.get("ta_sidestep_force_execute", False))
            and (
                float(analysis.get("composite_score", 0) or 0) >= _bar
                or bool(analysis.get("momentum_burst_fired"))
            )
        )
        if sidestep_hint:
            return {"action": "execute", "verdict": "PASS",
                    "result": execute_fn(analysis)}
        return {"action": "none", "verdict": "PASS", "result": None}
    # Should be unreachable (parse_verdict normalizes to one of the above),
    # but never silently drop — surface it so a new verdict can't go unhandled.
    logger.warning(f"[router] unhandled verdict {verdict!r} for {coin} — treating as no-op")
    return {"action": "unknown", "verdict": verdict, "result": None}


# Daily-MA alignment cache: coin -> (ts, +1 uptrend / -1 downtrend / 0 unknown).
# Re-fetched at most every _TREND_TTL_S so a burst of entries doesn't refetch daily
# candles per trade. Daily MA barely moves intraday, so a 1h TTL is plenty fresh.
_trend_ma_cache: Dict[str, Any] = {}
_TREND_TTL_S = 3600.0


def _daily_ma_direction(coin: str, period: int) -> int:
    """+1 if last daily close > the EMA(period), -1 if below, 0 if insufficient history.
    Cached per coin. This is the PTJ 200-day-MA trend regime, validated on our own
    realized closes (scripts/edge_legends.py): trading only WITH this trend turned the
    book -$23 -> +$15 and halved max drawdown."""
    now = time.time()
    hit = _trend_ma_cache.get(coin)
    if hit and (now - hit[0]) < _TREND_TTL_S:
        return hit[1]
    direction = 0
    try:
        from hermes_trader.client.hl_client import fetch_hl_candles
        from hermes_trader.indicators.math import candle_val, ema
        cd = fetch_hl_candles(coin, "1d", max(period + 30, 60))
        if len(cd) >= max(20, period // 4):
            closes = [candle_val(c, "c") for c in cd]
            ma = ema(closes, min(period, len(closes)))
            if ma:
                direction = 1 if closes[-1] > ma[-1] else -1
    except Exception as e:
        logger.debug(f"[trend-filter] daily MA fetch failed for {coin}: {e}")
        direction = 0
    _trend_ma_cache[coin] = (now, direction)
    return direction


def _volume_confirmed(coin: str, min_ratio: float, lookback: int = 20) -> bool:
    """Williams volume-confirmation: latest 1h bar volume >= min_ratio x the trailing
    `lookback`-bar average. Validated on our ledger (edge_combo.py): override entries
    WITH volume>=1.5x netted +$16 (n=12) while override entries WITHOUT volume netted
    -$24 (n=28) — volume cleanly separates the good overrides from the leak. On a fetch
    failure returns True (don't block on missing data — fail open)."""
    try:
        from hermes_trader.client.hl_client import fetch_hl_candles
        from hermes_trader.indicators.math import candle_val
        cd = fetch_hl_candles(coin, "1h", lookback + 5)
        if len(cd) < lookback + 1:
            return True
        avg = sum(candle_val(c, "v") for c in cd[-lookback - 1:-1]) / lookback
        return avg <= 0 or candle_val(cd[-1], "v") >= avg * min_ratio
    except Exception as e:
        logger.debug(f"[vol-confirm] fetch failed for {coin}: {e}")
        return True


def _trend_filter_block_reason(analysis: Dict[str, Any], config: Dict[str, Any]) -> str:
    """PTJ 200-day-MA trend-regime filter: block entries fighting the daily trend.
    Flag-gated + hot-read. External-alpha edges are exempt (separately validated). A
    coin with too little daily history is 'unknown' and only blocked when
    block_unknown=true (default false — don't penalize fresh HIP-3 listings, which are
    our +EV bucket)."""
    tf = config.get("trend_filter_200ma") or {}
    if not bool(tf.get("enabled", False)):
        return ""
    if analysis.get("external_alpha"):              # separate validated edge — exempt
        return ""
    side = (analysis.get("side") or "").lower()
    if side not in ("long", "short"):
        return ""
    period = int(tf.get("period", 200))
    direction = _daily_ma_direction(analysis.get("coin") or "", period)
    if direction == 0:
        if bool(tf.get("block_unknown", False)):
            return f"trend_filter ({analysis.get('coin')}: insufficient daily history for {period}d MA)"
        return ""
    want = 1 if side == "long" else -1
    if direction != want:
        trend = "uptrend" if direction == 1 else "downtrend"
        return (f"trend_filter ({side} fights the daily {period}d-MA {trend} — "
                f"counter-trend entries bleed)")
    return ""


def _runner_entry_block_reason(analysis: Dict[str, Any], config: Dict[str, Any]) -> str:
    """Block entries that are not fresh runner setups.

    The live ledger's repeated loss mode is not "no runners exist"; it is broad
    admission of late trend-only names and whale-only PASS upgrades. This gate
    keeps execution focused on fresh impulse setups: volume plus breakout/burst,
    backed by either 1h structure or a strong composite score.
    """
    gate = config.get("runner_entry_gate") or {}
    if not bool(gate.get("enabled", False)):
        return ""

    # External-alpha signals (smart_money copy / basis_gap) are a DIFFERENT alpha
    # source — validated OOS on their own (scripts/edge_smartmoney_walkfwd.py,
    # edge_basis_gap.py), not on the candle-impulse the runner gate measures. They
    # legitimately bypass THIS alpha filter; every SAFETY gate downstream
    # (loss-cooldown, eval_all_gates kill-switch/caps/margin, sizing, liquidation
    # stop) still applies unchanged.
    if analysis.get("external_alpha"):
        return ""

    coin = analysis.get("coin") or ""
    is_hip3 = ":" in coin
    side = (analysis.get("side") or "").lower()
    conf = float(analysis.get("confidence", 0) or 0)
    score = float(analysis.get("composite_score", 0) or 0)
    min_conf = float(gate.get("min_confidence", 0.70))
    min_score = float(gate.get("min_composite", 30.0))
    min_crypto_score = float(gate.get("min_crypto_composite", 20.0))
    min_hip3_score = float(gate.get("min_hip3_composite", 50.0))

    volume = bool(analysis.get("volume_spike_fired"))
    breakout = bool(analysis.get("breakout_fired"))
    burst = bool(analysis.get("momentum_burst_fired"))
    daily_mover = bool(analysis.get("daily_mover_fired"))
    uptrend = bool(analysis.get("uptrend_momentum_fired"))
    downtrend = bool(analysis.get("downtrend_momentum_fired"))
    slow_count = int(analysis.get("slow_burn_count", 0) or 0)
    # Shock-day gap+continuation counts as fresh impulse (backtested OOS-robust but THIN —
    # liquid-coins-only; AI + all gates still adjudicate). Flag-gated for instant revert.
    shock = bool(analysis.get("shock_day_fired")) and bool(gate.get("shock_day_fresh_impulse", False))

    fresh_impulse = (volume and (breakout or burst or shock)) or (burst and score >= min_score) or (shock and breakout)
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
    # shock-day is its own structure (the impulse bar) — let it satisfy the structure clause
    # so a shock setup the AI confirms can trade (still needs volume or breakout via
    # fresh_impulse; trend filter + caps + kill still apply). Narrow + flag-gated.
    structured_runner = fresh_impulse and (slow_count >= 1 or score >= min_score or shock)

    if (not is_hip3
            and structured_runner
            and not burst
            and not shock                       # shock setups are exempt from the composite floor
            and score < min_crypto_score):
        return (f"runner_gate_blocked (crypto composite {score:.0f} "
                f"< {min_crypto_score:.0f} for fresh non-burst setup)")

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
    if is_hip3 and score < min_hip3_score and not structured_daily_mover:
        return (f"runner_gate_blocked (HIP-3 composite {score:.0f} "
                f"< {min_hip3_score:.0f})")
    if uptrend and not (fresh_impulse or structured_daily_mover):
        if _late_chase_relax_ok(analysis, config, coin):
            _rc = config.get("late_chase_relax") or {}
            _ext = _analysis_daily_move_pct(analysis)
            if bool(_rc.get("shadow_mode", True)):
                logger.info(f"[executor] late-chase-relax [SHADOW] would admit {coin} "
                            f"(ext {_ext:.1f}%, liquid 20-30% pocket) — still blocked")
            else:
                logger.info(f"[executor] late-chase-relax ADMIT {coin} "
                            f"(ext {_ext:.1f}%, liquid 20-30% pocket — backtested +EV)")
                return ""   # validated +EV pocket passes the runner gate
        return "runner_gate_blocked (late trend-only chase; no fresh breakout/burst)"
    if not (structured_runner or structured_daily_mover):
        return (f"runner_gate_blocked (needs volume+breakout/burst and structure; "
                f"score={score:.0f}, slow={slow_count})")
    return ""


def close_position_market(coin: str) -> Dict[str, Any]:
    """Market-close any open perp position for `coin`. Deregisters the DSL tracker on success.

    Returns include `entry_px`, `fill_px`, and `realized_pnl_pct` (leveraged,
    net of taker fees) whenever the close fills with a parseable avgPx — so the
    trading loop can log the actual realized PnL instead of an estimate based
    on the pre-trade mid.
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
        deregister_position(coin, side)
        # Cancel the now-stranded reduce-only SL/TP trigger bracket so stale
        # orders don't pile up and reject a future reduce-only order on this coin.
        cancel_open_orders_for_coin(coin)
        fill_px = res.get("avg_px")
        if fill_px and entry_px > 0:
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
                _notional_entry = abs(szi) * entry_px
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
                    "size_coin": abs(szi), "notional_usd": round(_notional_entry, 4),
                    "spot_pct": out["spot_pct"],
                    "realized_pnl_pct": out["realized_pnl_pct"],   # leveraged, net fees
                    "realized_pnl_usd": round(_net_pnl_usd, 4),
                    "gross_pnl_usd": round(_gross_pnl_usd, 4),
                    "fee_usd": round(_fee_usd, 4),
                    "leverage": leverage,
                    "closed_at": _closed_at,
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
                    # funding carry: rate_hr × hold_hrs × notional × side (long pays
                    # when rate>0). Estimate (entry-rate held constant over the hold).
                    "funding_cost_usd": _funding_cost_usd,
                })
            except Exception as _rc_e:
                logger.warning(f"[outcome-store] record_close failed for {coin} (non-fatal): {_rc_e}")
            # Loss cooldown: a losing close arms an extended re-entry block on
            # this coin (config `loss_cooldown_min`, 0 = off). Anti-revenge rule:
            # TON was churned 3x in one day because the standard cooldown expired
            # and the AI re-bought the same falling name each time.
            if out["realized_pnl_pct"] < 0:
                try:
                    lc_min = float(read_agent_config().get("loss_cooldown_min", 0) or 0)
                    if lc_min > 0:
                        until = int(time.time() * 1000 + lc_min * 60_000)
                        memory.set_loss_cooldown(coin, until)
                        logger.info(f"[executor] loss cooldown armed on {coin}: "
                                    f"{lc_min:.0f}min (closed {out['realized_pnl_pct']:.2f}%)")
                except Exception as e:
                    logger.warning(f"[executor] loss-cooldown arm failed for {coin}: {e}")
    return out


def record_external_position_close(stale: Dict[str, Any], user: str | None = None) -> Dict[str, Any]:
    """Record a close for a DSL-tracked position that vanished outside our close path."""
    coin = str(stale.get("coin") or "")
    side = str(stale.get("side") or "long")
    if not coin:
        return {"ok": False, "error": "missing_coin"}
    user = user or resolve_user_address()
    entry_px = float(stale.get("entry_px") or 0)
    leverage = float(stale.get("leverage") or 1)
    opened_at_ms = int(float(stale.get("opened_at") or 0) * 1000)
    now_ms = int(time.time() * 1000)

    fill = None
    try:
        from hermes_trader.client.hl_client import _http_post
        fills = _http_post("/info", {"type": "userFills", "user": user, "limit": 100}) or []
        coin_fills = [f for f in fills if f.get("coin") == coin]
        if opened_at_ms:
            coin_fills = [
                f for f in coin_fills
                if int(float(f.get("time") or f.get("t") or 0)) >= opened_at_ms
            ] or coin_fills
        close_like = [
            f for f in coin_fills
            if any(tok in str(f.get("dir") or f.get("crossed") or "").lower()
                   for tok in ("close", "liquid", "stop", "take profit", "tp", "sl"))
        ]
        fill = (close_like or coin_fills or [None])[0]
    except Exception as e:
        logger.warning(f"[outcome-store] fill lookup failed for vanished {coin}: {e}")

    estimated = fill is None
    try:
        exit_px = float((fill or {}).get("px") or (fill or {}).get("avgPx") or 0)
    except (TypeError, ValueError):
        exit_px = 0.0
    if exit_px <= 0:
        exit_px = get_hl_price(coin)
        estimated = True
    try:
        size_coin = abs(float((fill or {}).get("sz") or 0))
    except (TypeError, ValueError):
        size_coin = 0.0
    trade = next(
        (t for t in memory.get_recent_trades(100)
         if t.get("coin") == coin and t.get("side") == side),
        {},
    )
    notional_entry = (
        size_coin * entry_px
        if size_coin > 0 and entry_px > 0
        else float(trade.get("size_usd") or 0)
    )
    if notional_entry <= 0 and entry_px > 0:
        notional_entry = entry_px

    is_long = side != "short"
    spot_pct = 0.0
    if entry_px > 0 and exit_px > 0:
        spot_pct = ((exit_px - entry_px) / entry_px * 100
                    if is_long else (entry_px - exit_px) / entry_px * 100)
    try:
        closed_pnl = (fill or {}).get("closedPnl")
        realized_pnl_usd = float(closed_pnl) if closed_pnl is not None else None
    except (TypeError, ValueError):
        realized_pnl_usd = None
    try:
        fee_usd = abs(float((fill or {}).get("fee") or 0))
    except (TypeError, ValueError):
        fee_usd = 0.0
    if realized_pnl_usd is None:
        realized_pnl_usd = notional_entry * spot_pct / 100.0 if notional_entry > 0 else 0.0
        estimated = True
    else:
        realized_pnl_usd -= fee_usd
    margin = notional_entry / max(leverage, 1.0) if notional_entry > 0 else 0.0
    realized_pnl_pct = (realized_pnl_usd / margin * 100.0) if margin > 0 else spot_pct * leverage
    dir_text = str((fill or {}).get("dir") or "").lower()
    liquidated = "liquid" in dir_text

    _ec = memory.pop_entry_context(coin, side)
    _entry_time = _ec.get("entry_time") or opened_at_ms or None
    _hold_min = round((now_ms - _entry_time) / 60000.0, 1) if _entry_time else None
    row = {
        "coin": coin, "side": side,
        "entry_px": entry_px, "exit_px": exit_px,
        "size_coin": size_coin or None,
        "notional_usd": round(notional_entry, 4),
        "spot_pct": round(spot_pct, 4),
        "realized_pnl_pct": round(realized_pnl_pct, 4),
        "realized_pnl_usd": round(realized_pnl_usd, 4),
        "fee_usd": round(fee_usd, 4),
        "leverage": leverage,
        "closed_at": now_ms,
        "entry_time": _entry_time,
        "hold_minutes": _hold_min,
        "signals_at_entry": _ec.get("signals") or {},
        "enforcement_at_entry": _ec.get("enforcement") or {},
        "forced_override": _ec.get("forced_override"),
        "regime_at_entry": _ec.get("regime"),
        "is_hip3": ":" in coin,
        "external_close": True,
        "liquidated": liquidated,
        "estimated": estimated,
        "reason": "liquidation" if liquidated else "external_position_vanished",
    }
    memory.record_close(row)
    if realized_pnl_pct < 0:
        try:
            lc_min = float(read_agent_config().get("loss_cooldown_min", 0) or 0)
            if lc_min > 0:
                memory.set_loss_cooldown(coin, int(time.time() * 1000 + lc_min * 60_000))
        except Exception as e:
            logger.warning(f"[outcome-store] loss-cooldown arm failed for vanished {coin}: {e}")
    logger.warning(
        f"[outcome-store] recorded vanished {coin} {side}: "
        f"pnl ${realized_pnl_usd:+.2f}, roe {realized_pnl_pct:+.2f}%"
        f"{' [LIQUIDATION]' if liquidated else ''}{' [estimated]' if estimated else ''}"
    )
    return {"ok": True, **row}
