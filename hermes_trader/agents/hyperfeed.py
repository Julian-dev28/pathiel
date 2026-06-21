"""Hyperliquid market and trader discovery built on the raw HL HTTP API.

Provides leaderboard, trader-discovery, and market-data lookups (candles,
funding regime, instrument list) with no external MCP dependency.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from hermes_trader.client.hl_client import _http_post, fetch_all_mids, fetch_hl_candles
from hermes_trader.client.universe import get_universe

logger = logging.getLogger(__name__)

# Wallets to treat as "smart money". Empty by default — populate to enable
# leaderboard_get_top / discovery_get_top_traders.
TRUSTED_WALLETS: set[str] = set()

# Funding-regime cache. Funding rates settle hourly so a 5-min TTL is safe
# and avoids hitting get_universe() on every risk-gate evaluation.
_FUNDING_REGIME_TTL_S = 300
_funding_regime_cache: Optional[Tuple[Dict[str, Any], float]] = None


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _get_universe(include_hip3: bool = False) -> List[Dict[str, Any]]:
    """Call get_universe with HIP-3 support while preserving older test stubs."""
    try:
        return get_universe(include_hip3=include_hip3)
    except TypeError:
        return get_universe()


# ═══════════════════════════════════════════════════════════════
# Leaderboard tools
# ═══════════════════════════════════════════════════════════════


def leaderboard_get_markets(limit: int = 100) -> Dict[str, Any]:
    """Top perp markets ranked by 24h notional volume.

    Returns {"markets": [{asset, rank, oi, volume_24h, funding_rate,
    prev_day_px, mark_px, mid_px}, ...]}.
    """
    universe = _get_universe(include_hip3=True)
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
        except Exception as e:
            logger.debug(f"[hyperfeed] leaderboard state failed for {addr}: {e}")

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
        except Exception as e:
            logger.debug(f"[hyperfeed] trader discovery failed for {addr}: {e}")

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
        except Exception as e:
            logger.debug(f"[hyperfeed] trader state failed for {addr}: {e}")

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
        except Exception as e:
            logger.debug(f"[hyperfeed] candles failed for {asset} {interval}: {e}")
            candles[interval] = []

    universe = _get_universe(include_hip3=(":" in asset))
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
    """Per-asset-class funding regime: flags LONG_CROWDED / SHORT_CROWDED / NEUTRAL.

    An asset is crowded when |funding| exceeds the threshold and open interest
    is high. The regime is computed PER ASSET CLASS (crypto / equity /
    commodity) because crypto funding crowding shouldn't gate oil or
    semiconductor longs — those have their own funding markets on the HIP-3
    dexes (xyz, km, vntl, ...) and need to be evaluated separately.

    Returns:
        {
            "regime": "SHORT_CROWDED",          # legacy market-wide regime (crypto)
            "regimes_by_class": {                # NEW: per-class breakdown
                "crypto":    "SHORT_CROWDED",
                "equity":    "NEUTRAL",
                "commodity": "LONG_CROWDED",
            },
            "assets": [...sorted by funding...],
        }

    The `regime` top-level field is kept for backward compatibility — it
    reflects the crypto class regime, which is what callers historically
    cared about. New code should consume `regimes_by_class`.

    Cached for 5 min — funding rates settle every hour, so the regime can't
    flip faster than that. Without the cache, the risk-gate caller would
    fetch the full universe on every trade attempt.
    """
    global _funding_regime_cache
    now = time.time()
    if _funding_regime_cache and (now - _funding_regime_cache[1]) < _FUNDING_REGIME_TTL_S:
        return _funding_regime_cache[0]
    result = _compute_funding_regime()
    _funding_regime_cache = (result, now)
    return result


def _compute_funding_regime() -> Dict[str, Any]:
    # Pull the full universe INCLUDING HIP-3 so equity and commodity perps
    # are visible to the regime classifier. Without include_hip3=True, oil
    # (xyz:CL), semis (xyz:ARM), gold (xyz:GOLD), etc. would silently be
    # excluded — and the gate would default them to a stale crypto regime.
    from hermes_trader.agents.market_regime import classify_asset
    universe = _get_universe(include_hip3=True)
    assets = []
    # Per-class counters: {"crypto": {"long": N, "short": M}, ...}
    counts: Dict[str, Dict[str, int]] = {
        "crypto":    {"long": 0, "short": 0},
        "equity":    {"long": 0, "short": 0},
        "commodity": {"long": 0, "short": 0},
    }

    for m in universe:
        funding = m.get("funding", 0)
        oi = m.get("openInterest", 0)
        coin = m["coin"]
        klass = classify_asset(coin)

        # OI threshold scales by class — HIP-3 markets are an order of
        # magnitude smaller than BTC/ETH, so the 1e7 crypto floor would
        # blank every equity/commodity perp's regime signal.
        oi_floor = 1e7 if klass == "crypto" else 1e6

        if funding > 0.0001 and oi > oi_floor:
            regime = "LONG_CROWDED"
            counts[klass]["long"] += 1
        elif funding < -0.0001 and oi > oi_floor:
            regime = "SHORT_CROWDED"
            counts[klass]["short"] += 1
        else:
            regime = "NEUTRAL"

        assets.append({
            "asset": coin,
            "asset_class": klass,
            "funding_rate": funding,
            "regime": regime,
            "oi": oi,
            "volume_24h": m.get("dayNtlVlm", 0),
        })

    # Compute per-class regime: dominant side beats the other by margin.
    def _decide(c: Dict[str, int]) -> str:
        if c["long"] > c["short"] + 5:
            return "LONG_CROWDED"
        if c["short"] > c["long"] + 5:
            return "SHORT_CROWDED"
        return "NEUTRAL"

    regimes_by_class = {k: _decide(v) for k, v in counts.items()}
    market_regime = regimes_by_class["crypto"]  # legacy field (crypto-only)

    return {
        "regime": market_regime,
        "regimes_by_class": regimes_by_class,
        "assets": sorted(assets, key=lambda x: x.get("funding_rate", 0), reverse=True),
    }


def market_list_instruments() -> Dict[str, Any]:
    """List all tradable instruments (perps + spot) with metadata."""
    universe = _get_universe(include_hip3=True)

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
    return fetch_all_mids(include_hip3=True)
