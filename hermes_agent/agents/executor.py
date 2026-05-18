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

from hermes_agent.agents.config_store import read_agent_config
from hermes_agent.agents.dsl_exit import ExitPolicy, check_all_positions, register_position
from hermes_agent.agents.memory import memory
from hermes_agent.agents.risk_gates import GateContext, eval_all_gates
from hermes_agent.client.exchange import (
    HL_LEVERAGE,
    get_hl_atr,
    get_hl_price,
    place_hl_order,
    place_hl_trigger_order,
    set_leverage,
)
from hermes_agent.client.hl_client import fetch_account_state, resolve_user_address

logger = logging.getLogger(__name__)

SL_ATR_MULT = 3.5
TP_ATR_MULT = 1.0

# Default 24h volumes for coins not in the major list
_MAJOR_VOLUMES = {
    "BTC": 1e8, "ETH": 1e8, "SOL": 1e8, "BNB": 1e8,
    "XRP": 1e8, "DOGE": 1e8, "ADA": 1e8, "AVAX": 1e8,
}


def _get_market_volume_24h(coin: str) -> float:
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


def maybe_execute(analysis: Dict[str, Any]) -> Dict[str, Any]:
    """Execute an analysis through risk gates and into the market."""
    config = read_agent_config()
    mode = str(config.get("mode", "OFF"))

    if mode == "OFF":
        return {
            "executed": False, "mode": mode,
            "analysis_id": analysis["id"], "reason": "mode_off",
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
    state = fetch_account_state(user)
    equity = state["equity"]
    total_open_notional = state["total_ntl"]

    memory.track_daily_pnl(equity)
    daily_pnl = memory.get_daily_pnl()

    positions = [
        {
            "coin": p["position"]["coin"],
            "side": "long" if float(p["position"]["szi"]) > 0 else "short",
            "size_usd": abs(float(p["position"]["szi"])) * (analysis.get("entry_px") or 0),
        }
        for p in state["asset_positions"]
    ]

    entry_px = analysis.get("entry_px")
    tp_px = analysis.get("tp_px")
    stop_px = analysis.get("stop_px")

    # Notional sized off equity (1% * leverage), overriding any AI-provided value.
    trade_notional = equity * 0.01 * HL_LEVERAGE

    recent_trades = memory.get_recent_trades(10)
    last_trade = next(
        (t for t in recent_trades if t.get("coin") == analysis["coin"]),
        None,
    )
    last_trade_time = last_trade.get("executed_at") if last_trade else None

    has_binary_news = bool(
        analysis.get("news_context")
        and re.search(
            r"fed|fomc|cpi|rate|earnings|hack|exploit|SEC",
            analysis["news_context"],
            re.IGNORECASE,
        )
    )

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
        equity=equity,
        total_open_notional=total_open_notional,
    )

    gate_output = eval_all_gates(ctx, config, last_trade_time)

    if gate_output["blocked"]:
        memory.record_trade({
            "id": str(uuid.uuid4()),
            "analysis_id": analysis["id"],
            "coin": analysis["coin"],
            "side": trade_side,
            "entry_px": entry_px or 0,
            "size_usd": 0,
            "executed_at": int(time.time() * 1000),
        })
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

    # Size in coin from the leverage-inclusive notional, floored at HL's $10
    # minimum order value and capped at 100 coins to bound very cheap assets.
    size_in_coin = trade_notional / mid_price
    min_size_by_value = 10.0 / mid_price
    size_in_coin = max(size_in_coin, min_size_by_value)
    size_in_coin = min(size_in_coin, 100.0)

    position_notional = trade_notional

    atr = get_hl_atr("4h", 14, coin)

    set_leverage(coin, HL_LEVERAGE)
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
    policy = ExitPolicy(
        max_loss_pct=dsl_config.get("max_loss_pct", 2.5),
        protect_pct=dsl_config.get("protect_pct", 1.5),
        retrace_threshold=dsl_config.get("retrace_threshold", 0.30),
        hard_timeout_minutes=dsl_config.get("hard_timeout_minutes", 180.0),
    )
    register_position(coin, trade_side, mid_price, policy=policy)
    logger.info(f"[executor] Registered DSL exit for {coin} {trade_side} @ {mid_price}")

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

    # Backup exchange stop-loss bracket — DSL is the primary exit engine.
    if atr > 0 and size_in_coin > 0:
        sl_px = mid_price - atr * SL_ATR_MULT if is_buy else mid_price + atr * SL_ATR_MULT
        sl_res = place_hl_trigger_order(is_buy, size_in_coin, sl_px, "sl", coin)
        if sl_res.get("ok"):
            logger.info(f"[executor] Placed backup SL at {sl_px}")
        else:
            logger.error(f"[executor] Backup SL FAILED for {coin}: {sl_res.get('error')}")

    final_sl = (mid_price - atr * SL_ATR_MULT) if is_buy else (mid_price + atr * SL_ATR_MULT) if atr > 0 else stop_px
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
    }


def monitor_exits(mids: Dict[str, float]) -> List[Dict[str, Any]]:
    """Check all DSL-tracked positions and return those that should be closed."""
    exits = check_all_positions(mids)
    return [
        {
            "coin": v.coin,
            "side": v.phase,
            "reason": v.reason,
            "unrealized_pct": v.unrealized_pct,
        }
        for v in exits
    ]
