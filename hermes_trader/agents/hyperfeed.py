"""Hyperliquid market and trader discovery built on the raw HL HTTP API.

Provides leaderboard, trader-discovery, and market-data lookups (candles,
funding regime, instrument list) with no external MCP dependency.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from hermes_trader.client.hl_client import _http_post, fetch_all_mids, fetch_hl_candles
from hermes_trader.client.universe import get_universe

logger = logging.getLogger(__name__)

# Wallets to treat as "smart money". Empty by default — populate to enable
# leaderboard_get_top / discovery_get_top_traders.
TRUSTED_WALLETS: set[str] = set()


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


# ═══════════════════════════════════════════════════════════════
# Leaderboard tools
# ═══════════════════════════════════════════════════════════════


def leaderboard_get_markets(limit: int = 100) -> Dict[str, Any]:
    """Top perp markets ranked by 24h notional volume.

    Returns {"markets": [{asset, rank, oi, volume_24h, funding_rate,
    prev_day_px, mark_px, mid_px}, ...]}.
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
    """Top traders with their account values and positions.

    Returns empty unless TRUSTED_WALLETS is populated (HL's leaderboard
    endpoint is not publicly exposed).
    """
    results: List[Dict[str, Any]] = []
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
    """All open perp positions for a specific trader.

    HL position data is nested: {"position": {...}, "type": "oneWay"} —
    the position object is unwrapped before reading fields.
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
    """Top traders with PnL / ROI / win-rate metrics, sorted by `sort_by`.

    Returns empty unless TRUSTED_WALLETS is populated.
    """
    results: List[Dict[str, Any]] = []
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

    sort_key = "roi_pct" if sort_by == "RETURN_ON_INVESTMENT" else "pnl_usd"
    results = sorted(results, key=lambda x: x.get(sort_key, 0), reverse=True)[:limit]
    return {"data": {"traders": results}}


def discovery_get_trader_state(trader_addresses: List[str]) -> Dict[str, Any]:
    """Aggregated state (PnL, ROI, win rate, positions) for several traders.

    win_rate is a 0-100 percentage, not a 0-1 fraction.
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
    """Comprehensive asset data: multi-interval candles plus funding/OI context."""
    if intervals is None:
        intervals = ["5m", "15m", "1h", "4h"]

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
    """Market-wide funding regime: flags LONG_CROWDED / SHORT_CROWDED / NEUTRAL.

    An asset is crowded when |funding| exceeds the threshold and open interest
    is high; the market regime is the dominant side across all assets.
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
    """List all tradable instruments (perps + spot) with metadata."""
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
    """Get all current mid prices."""
    return fetch_all_mids()
