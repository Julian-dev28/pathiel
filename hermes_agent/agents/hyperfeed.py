"""Hyperfeed Discovery — replicate Hyperfeed MCP tool signatures using raw HL API.

The senpi-skills repo uses the hyperliquid-mcp plugin which exposes these tools:
  - leaderboard_get_markets
  - leaderboard_get_top_traders
  - leaderboard_get_trader_positions
  - discovery_get_top_traders
  - discovery_get_trader_state
  - market_get_asset_data
  - market_get_funding_regime
  - market_list_instruments

This module replicates their behavior using raw HL HTTP API calls,
so hermes-trader can use the same data without the MCP dependency.

Returns data in the same shapes that the senpi-skills producers expect.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

import requests

from hermes_agent.client.cache import get_global_cache
from hermes_agent.client.hl_client import (
    HL_API,
    _http_post,
    fetch_all_mids,
    fetch_hl_candles,
    fetch_universe,
)
from hermes_agent.client.universe import get_universe

logger = logging.getLogger(__name__)

# ── Known high-PnL trader wallets (curated from leaderboard) ─────
# Populated from HL leaderboard rankings. These addresses are the
# "Smart Money" universe that strategies like raptor/cheetah/jackal follow.
TRUSTED_WALLETS = set()


def _safe_float(val, default=0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


# ═══════════════════════════════════════════════════════════════
# Leaderboard tools
# ═══════════════════════════════════════════════════════════════


def leaderboard_get_markets(limit: int = 100) -> Dict[str, Any]:
    """Get the Hyperliquid SM leaderboard market rankings.
    
    Mirrors MCP: leaderboard_get_markets(limit=100)
    
    Returns top markets by open interest + volume, sorted by 24h notional
    volume. This is the senpi-skills standard market discovery feed.
    
    Response shape:
    {
        "markets": [
            {
                "asset": "BTC",
                "rank": 1,
                "oi": 27000.0,
                "volume_24h": 3277000000.0,
                "funding_rate": 0.00001,
                "prev_day_px": 80000.0,
                "mark_px": 79200.0,
                "mid_px": 79200.0,
            },
            ...
        ]
    }
    """
    universe = get_universe()
    perp = [m for m in universe if m.get("type") == "perp"]
    perp = sorted(perp, key=lambda x: x.get("dayNtlVlm", 0), reverse=True)[:limit]

    markets = []
    for rank, m in enumerate(perp, 1):
        markets.append({
            "asset": m["coin"],
            "rank": rank,
            "oi": m.get("openInterest", 0),
            "volume_24h": m.get("dayNtlVlm", 0),
            "funding_rate": m.get("funding", 0),
            "prev_day_px": m.get("prevDayPx", 0),
            "mark_px": m.get("markPx", 0),
            "mid_px": m.get("midPx", 0),
        })

    return {"markets": markets}


def leaderboard_get_top(time_frame: str = "DAILY",
                        sort_by: str = "PROFIT_AND_LOSS_UNREALIZED",
                        consistency: Optional[List[str]] = None,
                        limit: int = 10,
                        open_position_filter: bool = True) -> Dict[str, Any]:
    """Get top traders from the Hyperliquid leaderboard.
    
    Mirrors MCP: leaderboard_get_top(...)
    
    Since HL's leaderboard endpoint is not publicly exposed, this uses
    the curated TRUSTED_WALLETS set. Users should populate this from
    app.hyperliquid.xyz/leaderboard.
    
    Args:
        time_frame: "DAILY", "WEEKLY", "MONTHLY"
        sort_by: "PROFIT_AND_LOSS_UNREALIZED" | "RETURN_ON_INVESTMENT"
        consistency: ["ELITE", "RELIABLE"] — trader quality tiers
        limit: max entries
        open_position_filter: only return traders with open positions
    
    Returns top traders with their account values and positions.
    """
    results = []
    for addr in TRUSTED_WALLETS:
        try:
            state = _http_post("/info", {
                "type": "clearinghouseState",
                "user": addr,
            })
            if not state:
                continue

            margin = state.get("marginSummary", {})
            account_value = _safe_float(margin.get("accountValue", "0"))
            total_ntl = _safe_float(margin.get("totalNtlPos", "0"))

            positions = []
            for p in state.get("assetPositions", []):
                if not p.get("position"):
                    continue
                pos = p["position"]
                szi = _safe_float(pos.get("szi", "0"))
                if szi == 0:
                    continue
                positions.append({
                    "coin": pos.get("coin", ""),
                    "side": "long" if szi > 0 else "short",
                    "size": abs(szi),
                    "entry_price": _safe_float(pos.get("entryPx", "0")),
                    "unrealized_pnl": _safe_float(pos.get("unrealizedPnl", "0")),
                    "leverage": pos.get("leverage", {}).get("value", "0"),
                })

            if open_position_filter and not positions:
                continue

            results.append({
                "address": addr,
                "account_value": account_value,
                "total_ntl_pos": total_ntl,
                "positions": positions,
                "position_count": len(positions),
            })
        except Exception:
            pass

    results = sorted(results, key=lambda x: x["account_value"], reverse=True)[:limit]
    return {"traders": results}


def leaderboard_get_trader_positions(trader_id: str) -> Dict[str, Any]:
    """Get all open positions for a specific trader.
    
    Mirrors MCP: leaderboard_get_trader_positions(trader_id=...)
    
    Important: HL SDK's position data is nested: {position: {...}, type: "oneWay"}
    The senpi-skills v3.4 parser fix handles this correctly.
    
    Returns:
    {
        "positions": [
            {
                "coin": "BTC",
                "szi": 1.5,
                "entry_px": 79000.0,
                "leverage": {"value": "10.53"},
                "unrealized_pnl": 500.0,
                "side": "long",
            },
            ...
        ]
    }
    """
    state = _http_post("/info", {
        "type": "clearinghouseState",
        "user": trader_id,
    })
    if not state:
        return {"positions": []}

    positions = []
    for p in state.get("assetPositions", []):
        if not isinstance(p, dict):
            continue
        pos = p.get("position", p)
        szi = _safe_float(pos.get("szi", "0"))
        if szi == 0:
            continue
        
        leverage_obj = pos.get("leverage", {})
        if isinstance(leverage_obj, str):
            leverage_obj = {"value": leverage_obj}

        positions.append({
            "coin": pos.get("coin", ""),
            "szi": szi,
            "side": "long" if szi > 0 else "short",
            "entry_px": _safe_float(pos.get("entryPx", "0")),
            "leverage": leverage_obj,
            "unrealized_pnl": _safe_float(pos.get("unrealizedPnl", "0")),
        })

    return {"positions": positions}


# ═══════════════════════════════════════════════════════════════
# Discovery tools
# ═══════════════════════════════════════════════════════════════


def discovery_get_top_traders(
    time_frame: str = "MONTHLY",
    sort_by: str = "RETURN_ON_INVESTMENT",
    consistency: Optional[List[str]] = None,
    open_position_filter: bool = True,
    limit: int = 60,
) -> Dict[str, Any]:
    """Get top traders sorted by performance metrics.
    
    Mirrors MCP: discovery_get_top_traders(...)
    
    Since HL's leaderboard isn't a public API, this returns curated
    trader wallets. The real data pipeline is the HL website's
    internal leaderboard query — not exposed via info endpoint.
    
    Args:
        time_frame: "MONTHLY", "WEEKLY", "DAILY"
        sort_by: "RETURN_ON_INVESTMENT", "PROFIT_AND_LOSS_UNREALIZED"
        consistency: ["ELITE", "RELIABLE"] — optional quality filter
        open_position_filter: skip traders with no open positions
        limit: max traders to return
    
    Returns:
    {
        "data": {
            "traders": [
                {
                    "address": "0x...",
                    "pnl_usd": 123456.78,
                    "roi_pct": 115.2,
                    "win_rate": 42.5,
                    "total_trades": 150,
                    "avg_holding_time_hours": 12.5,
                    ...
                },
                ...
            ]
        }
    }
    """
    results = []
    for addr in TRUSTED_WALLETS:
        try:
            state = _http_post("/info", {
                "type": "clearinghouseState",
                "user": addr,
            })
            if not state:
                continue
            
            margin = state.get("marginSummary", {})
            account_value = _safe_float(margin.get("accountValue", "0"))
            
            # Estimate win rate from positions + unrealized pnl
            positions = state.get("assetPositions", [])
            open_positions = [p for p in positions 
                              if p.get("position", {}).get("szi") != "0"]
            
            # Get recent fills for more detail
            fills = _http_post("/info", {
                "type": "userFills",
                "user": addr,
                "limit": 100,
            }) or []
            
            winning_trades = sum(1 for f in fills 
                                 if _safe_float(f.get("closedPnl", "0")) > 0)
            total_trades = len(fills)
            
            # Fetch spot state for more context
            spot = _http_post("/info", {
                "type": "spotClearinghouseState",
                "user": addr,
            }) or {}
            spot_balances = spot.get("balances", [])
            total_spot_value = sum(
                _safe_float(b.get("total", "0")) 
                for b in spot_balances 
                if b.get("coin") in ("USDC", "USDT", "USD")
            )

            entry = {
                "address": addr,
                "pnl_usd": account_value,
                "roi_pct": account_value / max(1, (account_value - _safe_float(margin.get("totalNtlPos", "0")))) * 100 if account_value > 0 else 0,
                "win_rate": (winning_trades / total_trades * 100) if total_trades > 0 else 0,
                "total_trades": total_trades,
                "open_positions": len(open_positions),
                "total_spot_value": total_spot_value,
            }

            if open_position_filter and entry["open_positions"] == 0:
                continue

            results.append(entry)
        except Exception:
            pass

    # Sort by chosen metric
    sort_key = "roi_pct" if sort_by == "RETURN_ON_INVESTMENT" else "pnl_usd"
    results = sorted(results, key=lambda x: x.get(sort_key, 0), reverse=True)[:limit]
    return {"data": {"traders": results}}


def discovery_get_trader_state(trader_addresses: List[str]) -> Dict[str, Any]:
    """Get comprehensive state for multiple traders in one call.
    
    Mirrors MCP: discovery_get_trader_state(trader_addresses=...)
    
    Returns aggregated states for all requested addresses. Used by
    jackal/raptor to get win rates, ROI, PnL, and other trader metrics.
    
    Note: winRate in the MCP response is a 0-100 PERCENTAGE, not a 0-1 fraction.
    Jackal v1.8 bug was treating it as 0-1 fraction.
    
    Returns:
    {
        "data": {
            "traders": [
                {
                    "traderAddress": "0x...",
                    "accountValue": 123456.78,
                    "pnl_usd": 50000.0,
                    "roi_pct": 115.0,
                    "win_rate": 42.5,
                    "total_trades": 150,
                    ...
                },
                ...
            ]
        }
    }
    """
    traders = []
    for addr in trader_addresses:
        try:
            state = _http_post("/info", {
                "type": "clearinghouseState",
                "user": addr,
            })
            if not state:
                continue
            
            margin = state.get("marginSummary", {})
            account_value = _safe_float(margin.get("accountValue", "0"))
            total_ntl = _safe_float(margin.get("totalNtlPos", "0"))
            
            positions = []
            for p in state.get("assetPositions", []):
                if not p.get("position"):
                    continue
                pos = p["position"]
                szi = _safe_float(pos.get("szi", "0"))
                if szi == 0:
                    continue
                positions.append({
                    "coin": pos.get("coin", ""),
                    "side": "long" if szi > 0 else "short",
                    "size": abs(szi),
                    "entry_price": _safe_float(pos.get("entryPx", "0")),
                    "unrealized_pnl": _safe_float(pos.get("unrealizedPnl", "0")),
                    "leverage": pos.get("leverage", {}).get("value", "0"),
                })
            
            fills = _http_post("/info", {
                "type": "userFills",
                "user": addr,
                "limit": 500,
            }) or []
            
            winning = sum(1 for f in fills 
                          if _safe_float(f.get("closedPnl", "0")) > 0)
            total = len(fills)
            
            traders.append({
                "traderAddress": addr,
                "accountValue": account_value,
                "pnl_usd": account_value,
                "roi_pct": (account_value / max(1, account_value - total_ntl)) * 100 if total_ntl > 0 else 0,
                "win_rate": (winning / total * 100) if total > 0 else 0,
                "total_trades": total,
                "open_positions": len(positions),
                "positions": positions,
            })
        except Exception:
            pass
    
    return {"data": {"traders": traders}}


# ═══════════════════════════════════════════════════════════════
# Market data tools
# ═══════════════════════════════════════════════════════════════


def market_get_asset_data(asset: str, 
                          intervals: Optional[List[str]] = None) -> Dict[str, Any]:
    """Get comprehensive asset data: candles + orderbook + funding.
    
    Mirrors MCP: market_get_asset_data(asset="BTC", intervals=["5m", "15m", "1h"])
    
    Returns:
    {
        "data": {
            "asset": "BTC",
            "candles": {
                "5m": [...],
                "15m": [...],
                "1h": [...],
            },
            "funding_rate": 0.00001,
            "open_interest": 27000.0,
            "prev_day_px": 80000.0,
            "mid_px": 79200.0,
        }
    }
    """
    if intervals is None:
        intervals = ["5m", "15m", "1h", "4h"]
    
    # Get candles for each interval
    candles = {}
    for interval in intervals:
        try:
            candle_data = fetch_hl_candles(asset, interval, 100)
            candles[interval] = [
                {"t": c.t, "o": c.o, "h": c.h, "l": c.l, "c": c.c, "v": c.v}
                for c in candle_data
            ]
        except Exception:
            candles[interval] = []
    
    # Get asset context from universe
    universe = get_universe()
    asset_data = next((m for m in universe if m["coin"] == asset), {})
    
    return {
        "data": {
            "asset": asset,
            "candles": candles,
            "funding_rate": asset_data.get("funding", 0),
            "open_interest": asset_data.get("openInterest", 0),
            "prev_day_px": asset_data.get("prevDayPx", 0),
            "mid_px": asset_data.get("midPx", 0),
            "volume_24h": asset_data.get("dayNtlVlm", 0),
        }
    }


def market_get_funding_regime() -> Dict[str, Any]:
    """Get market-wide funding regime analysis.
    
    Mirrors MCP: market_get_funding_regime
    
    Identifies crowded trades:
    - LONG_CROWDED: funding > 0.0001 AND high OI
    - SHORT_CROWDED: funding < -0.0001 AND high OI
    - NEUTRAL: moderate funding
    
    Used by strategies like dog to time entries against crowded positioning.
    
    Returns:
    {
        "regime": "LONG_CROWDED" | "SHORT_CROWDED" | "NEUTRAL",
        "assets": [
            {
                "asset": "BTC",
                "funding_rate": 0.00015,
                "regime": "LONG_CROWDED",
                "oi": 27000.0,
            },
            ...
        ]
    }
    """
    universe = get_universe()
    assets = []
    long_crowded = 0
    short_crowded = 0
    
    for m in universe:
        funding = m.get("funding", 0)
        oi = m.get("openInterest", 0)
        
        if funding > 0.0001 and oi > 1e7:
            regime = "LONG_CROWDED"
            long_crowded += 1
        elif funding < -0.0001 and oi > 1e7:
            regime = "SHORT_CROWDED"
            short_crowded += 1
        else:
            regime = "NEUTRAL"
        
        assets.append({
            "asset": m["coin"],
            "funding_rate": funding,
            "regime": regime,
            "oi": oi,
            "volume_24h": m.get("dayNtlVlm", 0),
        })
    
    # Determine market-wide regime
    if long_crowded > short_crowded + 5:
        market_regime = "LONG_CROWDED"
    elif short_crowded > long_crowded + 5:
        market_regime = "SHORT_CROWDED"
    else:
        market_regime = "NEUTRAL"
    
    return {
        "regime": market_regime,
        "assets": sorted(assets, key=lambda x: x.get("funding_rate", 0), reverse=True),
    }


def market_list_instruments() -> Dict[str, Any]:
    """List all tradable instruments (perps + spot).
    
    Mirrors MCP: market_list_instruments
    
    Returns unified universe with metadata.
    """
    universe = get_universe()
    
    perps = [m for m in universe if m.get("type") == "perp"]
    spots = [m for m in universe if m.get("type") == "spot"]
    
    return {
        "instruments": [
            {
                "symbol": m["coin"].lstrip("@"),
                "type": m.get("type", "perp"),
                "max_leverage": m.get("maxLeverage", 0),
                "funding_rate": m.get("funding", 0),
                "open_interest": m.get("openInterest", 0),
                "volume_24h": m.get("dayNtlVlm", 0),
                "mid_price": m.get("midPx", 0),
                "prev_day_price": m.get("prevDayPx", 0),
            }
            for m in universe
        ],
        "counts": {"perps": len(perps), "spot": len(spots), "total": len(universe)}
    }


def market_get_mids() -> Dict[str, str]:
    """Get all current mid prices.
    
    Convenience wrapper around fetch_all_mids.
    """
    return fetch_all_mids()
