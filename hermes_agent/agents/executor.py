"""Auto-executor: validates through risk gates, sizes via Kelly, executes LIVE.

Translation of lib/agent/executor.ts. Integrates DSL exit engine for
two-phase trailing stops (loss protection → profit locking).
"""

from __future__ import annotations

import logging
import math
import os
import re
import time
import uuid
from typing import Any, Dict, List, Optional

from hermes_agent.agents.config_store import read_agent_config
from hermes_agent.agents.dsl_exit import (
    ExitPolicy, register_position, unregister_position,
    get_tracker, check_all_positions,
)
from hermes_agent.agents.memory import memory
from hermes_agent.agents.risk_gates import eval_all_gates, GateContext
from hermes_agent.client.exchange import (
    HL_LEVERAGE,
    get_coin_index,
    get_hl_atr,
    get_hl_price,
    place_hl_order,
    place_hl_trigger_order,
    set_leverage,
)
from hermes_agent.client.hl_client import fetch_account_state

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
    """Calculate trade size using half-Kelly criterion."""
    p = confidence
    q = 1 - p
    b = reward_risk_ratio
    f_star = max(0, (p * b - q) / b) if b != 0 else 0
    half_kelly = f_star / 2
    notional = half_kelly * equity
    return min(notional, max_trade_notional)


def maybe_execute(analysis: Dict[str, Any]) -> Dict[str, Any]:
    """Execute an analysis through risk gates and into the market."""
    # DEBUG: Log analysis keys to find the bug
    import json
    debug_info = f"[EXECUTOR] Analysis keys: {list(analysis.keys())}\n"
    debug_info += f"[EXECUTOR] Analysis: {json.dumps(analysis, indent=2)}\n"
    with open('/tmp/hermes_executor_debug.log', 'w') as f:
        f.write(debug_info)
    
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

    # Fetch account state
    user = (
        os.environ.get("HYPERLIQUID_MASTER_ADDRESS")
        or os.environ.get("HYPERLIQUID_WALLET_ADDRESS", "")
    )
    try:
        state = fetch_account_state(user)
    except Exception as e:
        with open('/tmp/hermes_executor_error.log', 'w') as f:
            f.write(f"fetch_account_state failed: {e}")
        raise
    
    try:
        equity = state["equity"]
        total_open_notional = state["total_ntl"]
    except KeyError as e:
        with open('/tmp/hermes_executor_error.log', 'w') as f:
            f.write(f"state keys: {list(state.keys())}\nError: {e}")
        raise

    memory.track_daily_pnl(equity)
    daily_pnl = memory.get_daily_pnl()

    # DEBUG: Log asset_positions format
    import json
    pos_debug = f"[EXECUTOR] asset_positions count: {len(state['asset_positions'])}\n"
    for i, p in enumerate(state['asset_positions']):
        pos_debug += f"[EXECUTOR] Position {i}: keys={list(p.keys())}\n"
        if 'position' in p:
            pos_debug += f"[EXECUTOR] Position {i}['position'] keys={list(p['position'].keys())}\n"
    with open('/tmp/hermes_executor_positions.log', 'w') as f:
        f.write(pos_debug)
    
    positions = [
        {
            "coin": p["position"]["coin"],
            "side": "long" if float(p["position"]["szi"]) > 0 else "short",
            "size_usd": abs(float(p["position"]["szi"])) * (analysis.get("entry_px") or 0),
        }
        for p in state["asset_positions"]
    ]

    # Kelly sizing
    entry_px = analysis.get("entry_px")
    tp_px = analysis.get("tp_px")
    stop_px = analysis.get("stop_px")

    if tp_px and stop_px and entry_px:
        reward = abs(tp_px - entry_px)
        risk = abs(entry_px - stop_px)
        reward_risk = reward / risk if risk != 0 else 1.0
    else:
        reward_risk = 1.0

    # FORCE small test size to debug "invalid size" error
    # Hardcode to 0.001 XMR (minimum size) at current mid price
    size = 0.001  # Minimum XMR size
    trade_notional = size * (entry_px or 2600)  # Approximate $ notional

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

    # Fetch live mid — never use stale analysis entryPx
    mid_price = get_hl_price(coin)
    if mid_price <= 0:
        return {"executed": False, "mode": mode, "analysis_id": analysis["id"],
                "reason": f"invalid_price_for_{coin}"}

    # Kelly gives margin amount; multiply by leverage for position notional
    position_notional = trade_notional * HL_LEVERAGE
    # HARDCODE: 10 XRP (minimum $10 value: 10 * 1.42 = $14.24)
    size_in_coin = 10.0

    asset_idx, _ = get_coin_index(coin)
    atr = get_hl_atr("4h", 14, coin)

    set_leverage(coin, HL_LEVERAGE)
    order_res = place_hl_order(is_buy, size_in_coin, mid_price, coin, asset_idx)

    if not order_res.get("ok"):
        return {
            "executed": False, "mode": mode, "analysis_id": analysis["id"],
            "reason": f"order_failed: {order_res.get('error', 'unknown')}",
            "gate_results": gate_output["results"],
        }

    # ── DSL exit engine integration ───────────────────────────────
    # Register the position with the DSL tracker for dynamic stop management.
    # DSL will monitor price on every scan tick and trigger exits when
    # conditions are met (loss protection → profit locking).
    dsl_config = config.get("dsl_exit", {})
    policy = ExitPolicy(
        max_loss_pct=dsl_config.get("max_loss_pct", 2.5),
        protect_pct=dsl_config.get("protect_pct", 1.5),
        retrace_threshold=dsl_config.get("retrace_threshold", 0.30),
        hard_timeout_minutes=dsl_config.get("hard_timeout_minutes", 180.0),
    )
    register_position(coin, trade_side, mid_price, policy=policy)
    logger.info(f"[executor] Registered DSL exit for {coin} {trade_side} @ {mid_price}")

    # Track trade in memory
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

    # Also place exchange SL brackets as a safety net (ATR-based)
    if atr > 0 and size_in_coin > 0:
        sl_px = mid_price - atr * SL_ATR_MULT if is_buy else mid_price + atr * SL_ATR_MULT
        tp_px_live = mid_price + atr * TP_ATR_MULT if is_buy else mid_price - atr * TP_ATR_MULT
        # Note: These trigger orders are a backup — DSL is the primary exit engine
        place_hl_trigger_order(is_buy, size_in_coin, sl_px, "sl", asset_idx, coin)
        logger.info(f"[executor] Placed backup SL at {sl_px}")

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
    """Check all DSL-tracked positions for exit conditions.

    Called from the scan loop. Returns list of positions that should be closed.
    The caller (daemon loop) then executes the close via exchange API.
    """
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
