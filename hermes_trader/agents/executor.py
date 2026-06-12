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
    get_hl_atr,
    get_hl_price,
    get_max_leverage,
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


def maybe_execute(analysis: Dict[str, Any]) -> Dict[str, Any]:
    """Execute an analysis through risk gates and into the market."""
    config = read_agent_config()
    mode = str(config.get("mode", "OFF"))

    if mode == "OFF":
        return {
            "executed": False, "mode": mode,
            "analysis_id": analysis["id"], "reason": "mode_off",
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
    override_composite = float(config.get("force_execute_composite", 40))
    override_min_slow_burn = int(config.get("force_execute_slow_burn_count", 2))
    # whale_force_execute gates whether a whale signal alone can upgrade a PASS.
    whale_fired = bool(analysis.get("whale_signal")) and bool(config.get("whale_force_execute", True))
    slow_burn_strong = (
        float(analysis.get("composite_score", 0) or 0) >= override_composite
        and int(analysis.get("slow_burn_count", 0) or 0) >= override_min_slow_burn
    )
    # Breakout force-execute (O'Neil rule, added 2026-06-12): a 20-period-high
    # break WITH a volume spike and composite >= bar is an objectively strong
    # setup — the AI hedged these to PASS 21x on XPL while it ran +32% (38
    # researches, zero LONG verdicts, no gate ever blocked it). LONG path only:
    # forced shorts are the audit's worst bucket (AI shorts 0/8).
    breakout_strong = (
        bool(config.get("breakout_force_execute", True))
        and bool(analysis.get("breakout_fired"))
        and bool(analysis.get("volume_spike_fired"))
        and float(analysis.get("composite_score", 0) or 0) >= override_composite
    )
    # A PASS produced by a FAILED LLM call (402/timeout → ai_down) is an error
    # code, not a hedged opinion — upgrading it trades blind with no AI judgment
    # behind the entry AND no working AI close behind the exit. Refuse the
    # structural/whale upgrade on those unless the operator explicitly opts out
    # via override_requires_ai=false (reversible).
    _ai_down_block = bool(analysis.get("ai_down")) and \
        bool(config.get("override_requires_ai", True))
    if analysis.get("verdict") == "PASS" \
            and (slow_burn_strong or whale_fired or breakout_strong) \
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
    if analysis.get("verdict") == "PASS" and (slow_burn_strong or whale_fired or breakout_strong):
        trigger = ("whale-accumulation" if whale_fired
                   else f"composite={analysis.get('composite_score'):.0f}+{analysis.get('slow_burn_count')} slow-burn"
                   if slow_burn_strong
                   else "breakout+volume (O'Neil)")
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
    _lc_remaining = memory.loss_cooldown_remaining_min(analysis["coin"])
    if _lc_remaining > 0:
        return {
            "executed": False, "mode": mode,
            "analysis_id": analysis["id"],
            "reason": (f"loss_cooldown ({analysis['coin']} closed at a loss recently — "
                       f"{_lc_remaining:.0f}min remaining)"),
        }

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

    # Per-trade size = equity × fraction × leverage × conviction_multiplier.
    # The multiplier scales size by AI confidence so high-conviction trades
    # bet bigger and low-conviction trades bet smaller — same average
    # exposure across many trades, but asymmetric per-trade. Disabled by
    # setting "conviction_sizing": false in config.
    base_fraction = float(config.get("equity_fraction_per_trade", 0.01))
    if bool(config.get("conviction_sizing", True)):
        conf = float(analysis.get("confidence", 0) or 0)
        tiers = _parse_conviction_tiers(config.get("conviction_tiers"))
        conviction_mult = _conviction_multiplier(conf, tiers)
        # Whale-signal boost: when smart-money accumulation is flagged on this
        # coin, bet bigger to capitalize. Multiplies on top of the confidence
        # tier and clamps so a whale + high-conf trade can't exceed 2× base.
        if analysis.get("whale_signal"):
            whale_mult = float(config.get("whale_size_multiplier", 1.3))
            conviction_mult = min(conviction_mult * whale_mult, 2.0)
    else:
        conviction_mult = 1.0
    equity_fraction = base_fraction * conviction_mult
    leverage = min(int(config.get("leverage", HL_LEVERAGE)),
                   get_max_leverage(analysis["coin"]))
    trade_notional = equity * equity_fraction * leverage
    # Clamp to the per-trade notional ceiling so an oversized conviction bet is
    # SIZED DOWN to the cap rather than REJECTED by the notional gate — we want
    # smaller positions, not fewer trades. (kelly_size already clamps; this
    # conviction-sizing path did not, so a >cap bet was dropped entirely.)
    _notional_cap = float(config.get("max_trade_notional_usd", 0) or 0)
    if _notional_cap > 0 and trade_notional > _notional_cap:
        logger.info(f"[executor] notional ${trade_notional:.0f} > cap "
                    f"${_notional_cap:.0f} — clamping to cap")
        trade_notional = _notional_cap

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
        # whale_regime_bypass gates whether a whale signal can bypass the
        # counter-regime gate. Default True; set False to keep the size
        # boost + override but require regime alignment.
        whale_signal_fired=bool(analysis.get("whale_signal")) and bool(config.get("whale_regime_bypass", True)),
        peak_daily_pnl=memory.peak_daily_pnl(),
    )

    gate_output = eval_all_gates(ctx, config, last_trade_time)

    if gate_output["blocked"]:
        # Don't write blocked attempts to memory._trades — the cooldown gate
        # keys off the most recent trade-by-coin and would self-perpetuate.
        # Visibility comes from the `execute` event in the session log.
        return {
            "executed": False, "mode": mode,
            "analysis_id": analysis["id"],
            "blocked_by": gate_output["block_reasons"],
            "gate_results": gate_output["results"],
        }

    if not os.environ.get("HYPERLIQUID_PRIVATE_KEY"):
        return {
            "executed": False, "mode": mode,
            "analysis_id": analysis["id"],
            "reason": "private_key_missing",
        }

    coin = analysis["coin"]
    is_buy = trade_side == "long"

    # Fetch live mid — never use the (possibly stale) analysis entry price.
    mid_price = get_hl_price(coin)
    if mid_price <= 0:
        return {"executed": False, "mode": mode, "analysis_id": analysis["id"],
                "reason": f"invalid_price_for_{coin}"}

    # Coin size from the leverage-inclusive notional. No coin-count cap: the
    # dollar amount is already bounded by trade_notional (and the notional
    # risk gate); a fixed "100 coins" cap wrongly shrank cheap coins below
    # HL's $10 minimum. place_hl_order enforces that $10 floor at size precision.
    size_in_coin = trade_notional / mid_price

    position_notional = trade_notional

    # ATR drives the backup exchange stop. A coin with too little candle history
    # (e.g. a brand-new HIP-3 listing) returns atr<=0 — research even emits
    # stop_px/tp_px = 0.0 for it. We must NOT place a blind position there: with
    # whale/structural force-execute now able to upgrade a PASS, a 0-ATR coin
    # would trade with no computable stop. One retry (covers a transient candle
    # flake), then refuse — a skipped trade costs $0; a stopless one doesn't.
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

    # Register the position with the DSL tracker; it re-evaluates the exit
    # floor on every scan tick (loss protection -> profit locking).
    dsl_config = config.get("dsl_exit", {})
    # phase2_tiers is optional in config; when present it OVERRIDES the class
    # default ladder so profit-locking tightness is tunable without code edits.
    _tiers_raw = dsl_config.get("phase2_tiers")
    _tiers = [RetraceTier(**t) for t in _tiers_raw] if _tiers_raw else None
    _atr_cfg = dsl_config.get("atr_stop", {}) or {}
    policy = ExitPolicy(
        max_loss_pct=dsl_config.get("max_loss_pct", 2.5),
        max_loss_roe_pct=dsl_config.get("max_loss_roe_pct", 50.0),
        protect_pct=dsl_config.get("protect_pct", 1.5),
        retrace_threshold=dsl_config.get("retrace_threshold", 0.30),
        hard_timeout_minutes=dsl_config.get("hard_timeout_minutes", 180.0),
        breakeven_trigger_pct=dsl_config.get("breakeven_trigger_pct", 0.0),
        breakeven_lock_pct=dsl_config.get("breakeven_lock_pct", 0.0),
        atr_stop_enabled=bool(_atr_cfg.get("enabled", False)),
        atr_stop_mult=float(_atr_cfg.get("atr_mult", 1.5)),
        atr_stop_floor_pct=float(_atr_cfg.get("floor_pct", 1.0)),
        atr_stop_ceiling_pct=float(_atr_cfg.get("ceiling_pct", 4.0)),
        phase2_tiers=_tiers if _tiers else ExitPolicy().phase2_tiers,
    )
    # ATR as % of entry — captured once here so the DSL stop width is stable
    # for the life of the trade (the atr_stop feature scales off this).
    entry_atr_pct = (atr / mid_price * 100) if mid_price > 0 else 0.0
    register_position(coin, trade_side, mid_price, policy=policy, leverage=leverage,
                      entry_atr_pct=entry_atr_pct)
    logger.info(f"[executor] Registered DSL exit for {coin} {trade_side} @ {mid_price} ({leverage}x)")

    memory.record_trade({
        "id": str(uuid.uuid4()),
        "analysis_id": analysis["id"],
        "coin": coin,
        "side": trade_side,
        "entry_px": mid_price,
        "size_usd": position_notional,
        "order_id": order_res.get("order_id"),
        "executed_at": int(time.time() * 1000),
    })

    # Backup exchange stop-loss bracket — fires server-side (instantly, between our
    # 60s DSL checks) to cap the gap-throughs the DSL loop misses. DSL is still the
    # primary/normal exit; this is the fast safety net.
    sl_atr_mult = float(config.get("sl_atr_mult", _DEFAULT_SL_ATR_MULT))
    sl_missing = False
    if atr > 0 and size_in_coin > 0:
        sl_px = mid_price - atr * sl_atr_mult if is_buy else mid_price + atr * sl_atr_mult
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
        tp_px_trig = mid_price + atr * TP_ATR_MULT if is_buy else mid_price - atr * TP_ATR_MULT
        tp_size = size_in_coin * tp_scale_fraction
        tp_res = place_hl_trigger_order(is_buy, tp_size, tp_px_trig, "tp", coin)
        if tp_res.get("ok"):
            logger.info(f"[executor] Placed TP scale-out {tp_scale_fraction:.0%} "
                        f"at {tp_px_trig} ({TP_ATR_MULT}x ATR)")
        else:
            logger.error(f"[executor] TP scale-out FAILED for {coin}: {tp_res.get('error')}")

    final_sl = (mid_price - atr * sl_atr_mult) if is_buy else (mid_price + atr * sl_atr_mult) if atr > 0 else stop_px
    final_tp = (mid_price + atr * TP_ATR_MULT) if is_buy else (mid_price - atr * TP_ATR_MULT) if atr > 0 else tp_px

    return {
        "executed": True, "mode": mode,
        "analysis_id": analysis["id"],
        "order_id": order_res.get("order_id"),
        "gate_results": gate_output["results"],
        "size_usd": position_notional,
        "entry_px": mid_price,
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
        # A hedging AI PASS can still carry a structural-override HINT: a whale
        # accumulation signal, or a strong slow-burn composite. maybe_execute
        # owns the real override decision (and re-checks whale_force_execute +
        # all gates, no-opping cleanly if it doesn't hold), but it's only ever
        # reached via this router — so route a hinted PASS to it instead of
        # dropping it. Without this the force-execute-on-PASS code was dead:
        # the AI hedges to PASS on exactly the contrarian whale setups it's for.
        has_whale = bool(analysis.get("whale_signal"))
        slow_burn_hint = (
            float(analysis.get("composite_score", 0) or 0) >= 40
            and int(analysis.get("slow_burn_count", 0) or 0) >= 2
        )
        breakout_hint = (
            bool(analysis.get("breakout_fired"))
            and bool(analysis.get("volume_spike_fired"))
            and float(analysis.get("composite_score", 0) or 0) >= 40
        )
        if has_whale or slow_burn_hint or breakout_hint:
            return {"action": "execute", "verdict": "PASS",
                    "result": execute_fn(analysis)}
        return {"action": "none", "verdict": "PASS", "result": None}
    # Should be unreachable (parse_verdict normalizes to one of the above),
    # but never silently drop — surface it so a new verdict can't go unhandled.
    logger.warning(f"[router] unhandled verdict {verdict!r} for {coin} — treating as no-op")
    return {"action": "unknown", "verdict": verdict, "result": None}


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
