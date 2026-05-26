"""Risk gates — every gate is a pure function returning {pass, reason?}.

All gates are evaluated; results are collected for telemetry (no short-circuit).
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

GateResult = Dict[str, Any]  # {pass: bool, reason?: str}


class GateContext:
    """Context passed to all risk gates."""
    def __init__(
        self,
        confidence: float,
        current_positions: List[Dict[str, Any]],
        trade_notional_usd: float,
        daily_pnl: float,
        market_volume_24h_usd: float,
        coin: str,
        trade_side: str,  # 'long' or 'short'
        has_binary_news_risk: bool,
        equity: float,
        total_open_notional: float,
    ):
        self.confidence = confidence
        self.current_positions = current_positions
        self.trade_notional_usd = trade_notional_usd
        self.daily_pnl = daily_pnl
        self.market_volume_24h_usd = market_volume_24h_usd
        self.coin = coin
        self.trade_side = trade_side
        self.has_binary_news_risk = has_binary_news_risk
        self.equity = equity
        self.total_open_notional = total_open_notional


def confidence_gate(ctx: GateContext, min_confidence: float) -> GateResult:
    if ctx.confidence >= min_confidence:
        return {"pass": True}
    return {"pass": False, "reason": f"confidence {ctx.confidence:.2f} < {min_confidence}"}


def max_concurrent_positions_gate(ctx: GateContext, max_concurrent: int) -> GateResult:
    if len(ctx.current_positions) < max_concurrent:
        return {"pass": True}
    return {"pass": False, "reason": f"max positions reached ({len(ctx.current_positions)}/{max_concurrent})"}


def per_trade_notional_cap_gate(ctx: GateContext, cap_usd: float) -> GateResult:
    if ctx.trade_notional_usd <= cap_usd:
        return {"pass": True}
    return {"pass": False, "reason": f"trade notional ${ctx.trade_notional_usd:.0f} exceeds cap ${cap_usd}"}


def daily_loss_kill_switch(ctx: GateContext, max_daily_loss: float) -> GateResult:
    if ctx.daily_pnl > max_daily_loss:
        return {"pass": True}
    return {"pass": False, "reason": f"daily loss killswitch triggered (PnL ${ctx.daily_pnl:.0f} <= ${max_daily_loss})"}


def market_liquidity_floor(
    ctx: GateContext,
    min_volume: float,
    min_volume_hip3: Optional[float] = None,
) -> GateResult:
    """Block trades on markets with insufficient 24h notional volume.

    HIP-3 tokenized-equity / commodity perps live on separate dexs and
    naturally carry less volume than BTC/ETH-style native markets (most
    `xyz:*` markets sit in the $1M–$50M range vs $1B+ for BTC). Applying
    the same 5M crypto floor incorrectly blocks adequately-liquid HIP-3
    markets like xyz:CRCL ($4.7M) and km:USTECH ($1.06M). When the coin
    is HIP-3 (colon-namespaced) and a separate `min_volume_hip3` is set,
    use that floor instead.
    """
    is_hip3 = ":" in (ctx.coin or "")
    floor = (min_volume_hip3 if (is_hip3 and min_volume_hip3 is not None) else min_volume)
    if ctx.market_volume_24h_usd >= floor:
        return {"pass": True}
    return {"pass": False, "reason": f"market 24h volume ${ctx.market_volume_24h_usd/1e6:.2f}M below floor ${floor/1e6:.2f}M"}


def coin_allowlist_gate(ctx: GateContext, allowlist: List[str], blocklist: List[str]) -> GateResult:
    if blocklist and ctx.coin in blocklist:
        return {"pass": False, "reason": f"{ctx.coin} is on the coin blocklist"}
    if allowlist and ctx.coin not in allowlist:
        return {"pass": False, "reason": f"{ctx.coin} not on the allowlist"}
    return {"pass": True}


def cooldown_gate(ctx: GateContext, last_trade_time: Optional[int], cooldown_min: float) -> GateResult:
    if last_trade_time is None:
        return {"pass": True}
    elapsed = (int(time.time() * 1000) - last_trade_time) / 60_000
    if elapsed >= cooldown_min:
        return {"pass": True}
    return {"pass": False, "reason": f"cooldown active ({int(cooldown_min - elapsed)}min remaining)"}


def opposite_direction_guard(ctx: GateContext) -> GateResult:
    existing = next((p for p in ctx.current_positions if p["coin"] == ctx.coin), None)
    if not existing:
        return {"pass": True}
    if existing["side"] != ctx.trade_side:
        return {"pass": False, "reason": f"opposite position exists ({ctx.coin} {existing['side']}) — no auto-flip"}
    return {"pass": True}


# Major crypto coins for correlation cap
_CRYPTO_COINS = frozenset([
    "BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX", "MATIC", "LINK",
    "DOT", "UNI", "ATOM", "NEAR", "FTM", "APT", "ARB", "OP", "INJ", "TIA",
    "SUI", "SEI", "WIF", "PEPE", "BONK", "FLOKI", "TRX", "LTC", "BCH", "ETC",
    "XLM", "ALGO", "AAVE", "MKR", "SNX", "CRV", "COMP", "YFI", "SUSHI", "1INCH",
])


def correlation_cap(ctx: GateContext, max_crypto_correlated: int) -> GateResult:
    # Only cap long correlation
    if ctx.trade_side != "long":
        return {"pass": True}
    existing_crypto_long = sum(
        1 for p in ctx.current_positions
        if p["coin"] in _CRYPTO_COINS and p["side"] == "long"
    )
    if existing_crypto_long < max_crypto_correlated:
        return {"pass": True}
    return {"pass": False, "reason": f"crypto long correlation cap reached ({existing_crypto_long}/{max_crypto_correlated})"}


def equity_risk_cap(ctx: GateContext, max_total_notional_pct: float) -> GateResult:
    max_notional = ctx.equity * max_total_notional_pct
    projected_notional = ctx.total_open_notional + ctx.trade_notional_usd
    if projected_notional <= max_notional:
        return {"pass": True}
    return {
        "pass": False,
        "reason": f"total notional ${projected_notional:.0f} would exceed {max_total_notional_pct*100:.0f}% of equity (${max_notional:.0f})",
    }


def market_regime_gate(ctx: GateContext, counter_regime_min_conf: float = 0.7) -> GateResult:
    """Block counter-regime trades unless conviction clears a higher bar.

    The per-coin scan + AI research pipeline is direction-agnostic about the
    macro: it'll fire a short on TSLA even while the equity-perp basket is
    grinding up, and that's how a bunch of low-conviction shorts got
    steamrolled in correlation rather than on per-coin merit.

    This gate looks up the regime for the right proxy (BTC for crypto, NVDA
    for equity, the coin's own 4h for commodities) and:
      - trade aligned with regime → pass
      - regime neutral             → pass (no opinion → don't override)
      - counter-trend trade        → require confidence >= counter_regime_min_conf
                                     (default 0.7), else block
    """
    from hermes_trader.agents.market_regime import detect_regime
    regime = detect_regime(ctx.coin)
    if regime == "neutral":
        return {"pass": True}
    aligned = (regime == "up" and ctx.trade_side == "long") or \
              (regime == "down" and ctx.trade_side == "short")
    if aligned:
        return {"pass": True}
    if ctx.confidence >= counter_regime_min_conf:
        return {"pass": True}  # high-conviction counter-trade allowed through
    return {
        "pass": False,
        "reason": (f"counter-regime {ctx.trade_side} vs {regime} trend — "
                   f"need conf >= {counter_regime_min_conf}, have {ctx.confidence:.2f}"),
    }


def news_blackout_gate(ctx: GateContext) -> GateResult:
    if not ctx.has_binary_news_risk:
        return {"pass": True}
    return {"pass": False, "reason": "binary news risk detected (Fed, earnings, hack within 2h) — standing down"}


def _cfg(config: Dict[str, Any], key: str, default: Any) -> Any:
    """Read a config value tolerating snake_case or camelCase keys."""
    if key in config:
        return config[key]
    parts = key.split("_")
    camel = parts[0] + "".join(p.capitalize() for p in parts[1:])
    return config[camel] if camel in config else default


def eval_all_gates(
    ctx: GateContext,
    config: Dict[str, Any],
    last_trade_time: Optional[int] = None,
) -> Dict[str, Any]:
    """Evaluate all 11 risk gates and collect results."""
    results = {}
    results["confidence"] = confidence_gate(ctx, _cfg(config, "min_ai_confidence", 0.8))
    results["max_concurrent"] = max_concurrent_positions_gate(ctx, config.get("max_concurrent", 3))
    results["notional_cap"] = per_trade_notional_cap_gate(ctx, config.get("max_trade_notional_usd", 300))
    results["daily_loss"] = daily_loss_kill_switch(ctx, config.get("max_daily_loss_usd", -100))
    results["liquidity"] = market_liquidity_floor(
        ctx,
        config.get("min_market_volume_usd", 5_000_000),
        config.get("min_hip3_volume_usd", 500_000),
    )
    results["coin_filter"] = coin_allowlist_gate(
        ctx,
        config.get("coin_allowlist", []),
        config.get("coin_blocklist", []),
    )
    results["cooldown"] = cooldown_gate(ctx, last_trade_time, config.get("cooldown_min", 60))
    results["opposite_guard"] = opposite_direction_guard(ctx)
    results["correlation"] = correlation_cap(ctx, int(config.get("max_crypto_long_correlated", 2)))
    results["equity_risk"] = equity_risk_cap(ctx, config.get("max_total_notional_pct", 1.0))  # Default 100% to allow trading with small accounts
    results["market_regime"] = market_regime_gate(
        ctx, _cfg(config, "counter_regime_min_conf", 0.7)
    )
    results["news"] = news_blackout_gate(ctx)

    block_reasons = []
    blocked = False
    for key, result in results.items():
        if not result.get("pass"):
            blocked = True
            block_reasons.append(result.get("reason", key))

    return {"results": results, "blocked": blocked, "block_reasons": block_reasons}
