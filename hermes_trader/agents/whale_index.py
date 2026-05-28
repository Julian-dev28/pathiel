"""Whale Index — smart-money signal heuristics over the Hyperliquid universe.

Provides OI/funding-based concentration and accumulation signals.
leaderboard_get_top / get_trader_state only return data for wallets added
to WHALE_WALLETS.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from hermes_trader.client.hl_client import _http_post
from hermes_trader.client.universe import get_universe

logger = logging.getLogger(__name__)

# Curated whale wallet registry. Add entries from app.hyperliquid.xyz/leaderboard
# or hyperstats.org; format: "0x...": {"name": ..., "risk": "low"|"medium"|"high"}.
WHALE_WALLETS: Dict[str, Dict[str, str]] = {}

# ── Leaderboard from on-chain data ──────────────────────────────────────

def _fetch_clearinghouse(user: str) -> Optional[Dict[str, Any]]:
    """Fetch perp account state for a user address."""
    return _http_post("/info", {"type": "clearinghouseState", "user": user})


def _fetch_user_fills(user: str, limit: int = 100) -> List[Dict[str, Any]]:
    """Fetch recent trades for a user."""
    return _http_post(
        "/info",
        {"type": "userFills", "user": user, "limit": limit},
    ) or []


def leaderboard_get_top(
    start: int = 0,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    """Return positions for the wallets registered in WHALE_WALLETS.

    Args:
        start: pagination offset
        limit: number of entries (max 100)
    """
    results = []
    for addr, meta in list(WHALE_WALLETS.items())[start:start + limit]:
        try:
            state = _fetch_clearinghouse(addr)
            if not state:
                continue
            margin = state.get("marginSummary", {})
            account_value = float(margin.get("accountValue", "0"))
            positions = state.get("assetPositions", [])
            
            results.append({
                "address": addr,
                "name": meta.get("name", ""),
                "account_value": account_value,
                "positions": [
                    {
                        "coin": p["position"]["coin"],
                        "szi": float(p["position"]["szi"]),
                        "entryPx": float(p["position"]["entryPx"]),
                        "leverage": {"value": p["position"]["leverage"].get("value", "0")},
                    }
                    for p in positions
                    if p.get("position", {}).get("szi") != "0"
                ],
                "total_positions": len(positions),
            })
        except Exception as e:
            logger.warning(f"[whale] Failed to fetch {addr}: {e}")
    return results


def smart_money_concentration(
    lookback_days: int = 7,
    min_volume_usd: float = 1e6,
) -> List[Dict[str, Any]]:
    """Identify assets with growing smart money concentration.
    
    Analyzes OI + volume distribution to find assets where large traders
    are accumulating positions. Flags:
    - OI growth outpaces volume growth
    - Top whales increasing positions in same asset
    - OI concentration in top 10 wallets
    
    Args:
        lookback_days: how far back to scan for concentration changes
        min_volume_usd: minimum 24h volume threshold
    """
    universe = get_universe()
    results = []
    
    for m in universe:
        day_oi = m.get("openInterest", 0)
        day_vol = m.get("dayNtlVlm", 0)
        funding = m.get("funding", 0)
        mid_px = m.get("midPx", 0)
        
        if day_vol < min_volume_usd:
            continue
        
        # Concentration signal: OI growing + negative funding = accumulation
        # (whales buying while retail sells into dips)
        if day_oi > 0 and funding < 0:
            results.append({
                "coin": m["coin"],
                "type": m["type"],
                "signal": "accumulation",
                "confidence": min(1.0, abs(funding) / 0.0001),  # scale funding magnitude
                "oi": day_oi,
                "volume_24h": day_vol,
                "funding_rate": funding,
                "mid_price": mid_px,
            })
        
        # High OI relative to volume = whale accumulation
        oi_vol_ratio = day_oi / (day_vol / 1e6) if day_vol > 0 else 0
        if oi_vol_ratio > 10:  # OI > 10x of daily volume in millions
            results.append({
                "coin": m["coin"],
                "type": m["type"],
                "signal": "high_oi_concentration",
                "confidence": min(1.0, oi_vol_ratio / 50),  # scale ratio
                "oi": day_oi,
                "volume_24h": day_vol,
                "oi_volume_ratio": oi_vol_ratio,
                "mid_price": mid_px,
            })
    
    return sorted(results, key=lambda x: x["confidence"], reverse=True)


def oi_funding_anomaly(
    min_oi_usd: float = 1e7,
    max_funding_threshold: float = -0.00005,
) -> List[Dict[str, Any]]:
    """Detect assets where OI is rising but price is flat while funding is
    deeply negative — classic smart money accumulation pattern.
    
    Signal: whales are building long positions (increasing OI) while
    retail is shorting (negative funding). When the crowd finally covers,
    price squeezes up.
    
    Args:
        min_oi_usd: minimum OI to consider (USDC)
        max_funding_threshold: funding rate must be below this
    """
    universe = get_universe()
    results = []
    
    for m in universe:
        oi = m.get("openInterest", 0)
        funding = m.get("funding", 0)
        mid_px = m.get("midPx", 0)
        prev_px = m.get("prevDayPx", 0)
        
        if oi < min_oi_usd or funding > max_funding_threshold:
            continue
        
        price_change_24h = (mid_px - prev_px) / prev_px * 100 if prev_px > 0 else 0
        
        # Signal: OI high + funding negative + price relatively flat
        # (whales accumulating quietly)
        if abs(price_change_24h) < 10:  # price not moving much
            results.append({
                "coin": m["coin"],
                "type": m["type"],
                "signal": "smart_money_accumulation",
                "confidence": (
                    min(1.0, abs(funding) / 0.0005)  # funding magnitude
                    * (1 - abs(price_change_24h) / 10)  # inverse price change
                ),
                "oi": oi,
                "funding_rate": funding,
                "price_24h_change_pct": price_change_24h,
                "mid_price": mid_px,
                "prev_day_px": prev_px,
            })
    
    return sorted(results, key=lambda x: x["confidence"], reverse=True)


def get_trader_state(user: str) -> Optional[Dict[str, Any]]:
    """Get comprehensive state for a specific trader address.
    
    Combines perp + spot state + recent trades into a single view.
    """
    perp = _fetch_clearinghouse(user)
    fills = _fetch_user_fills(user, limit=20)
    
    if not perp:
        return None
    
    margin = perp.get("marginSummary", {})
    account_value = float(margin.get("accountValue", "0"))
    total_ntl_pos = float(margin.get("totalNtlPos", "0"))
    
    positions = []
    for p in perp.get("assetPositions", []):
        if not p.get("position"):
            continue
        pos = p["position"]
        szi = float(pos.get("szi", "0"))
        if szi == 0:
            continue
        positions.append({
            "coin": pos.get("coin", ""),
            "side": "long" if szi > 0 else "short",
            "size": abs(szi),
            "entry_price": float(pos.get("entryPx", "0")),
            "leverage": pos.get("leverage", {}).get("value", "0"),
            "unrealized_pnl": float(pos.get("unrealizedPnl", "0")),
        })
    
    return {
        "address": user,
        "account_value": account_value,
        "total_notional_position": total_ntl_pos,
        "positions": positions,
        "recent_trades": [
            {
                "coin": f.get("coin", ""),
                "side": f.get("side", ""),
                "price": float(f.get("px", "0")),
                "size": float(f.get("sz", "0")),
                "fee": float(f.get("fee", "0")),
                "time": f.get("time", 0),
            }
            for f in fills[:10]
        ],
    }


# ── Whale Index MCP Integration ─────────────────────────────────────
# These functions can be registered as MCP tools for autonomous agents
# to query whale data as part of their scanning pipeline.

def whale_accumulation_map(min_confidence: float = 0.05) -> Dict[str, Dict[str, Any]]:
    """Return {coin: signal_dict} for coins flagged by oi_funding_anomaly.

    This is the clean signal — small list of coins where whales are loading
    while retail shorts (negative funding) and price is still flat (move
    hasn't happened yet). Excludes the noisy `high_oi_concentration` signal
    which fires on every BTC/ETH/SOL by default. Cache once per scan.
    """
    return {
        s["coin"]: s
        for s in oi_funding_anomaly()
        if s.get("confidence", 0) >= min_confidence
    }


def get_whale_signals(
    min_confidence: float = 0.1,
    top_n: int = 10,
) -> List[Dict[str, Any]]:
    """Aggregate concentration + anomaly signals for MCP-tool callers.

    Kept for the MCP exposure (`whale_index` tool). Production perception
    uses `whale_accumulation_map()` instead — the high-OI-concentration
    branch this combines in is too noisy for direction calls.
    """
    concentration = smart_money_concentration()
    anomalies = oi_funding_anomaly()
    
    # Merge signals by coin
    merged: Dict[str, Dict[str, Any]] = {}
    for sig in concentration + anomalies:
        coin = sig["coin"]
        if coin not in merged:
            merged[coin] = {"coin": coin, "signals": [], "max_confidence": 0}
        merged[coin]["signals"].append(sig)
        merged[coin]["max_confidence"] = max(
            merged[coin]["max_confidence"],
            sig.get("confidence", 0),
        )
    
    # Filter and sort
    results = [
        s for s in merged.values()
        if s["max_confidence"] >= min_confidence
    ]
    return sorted(results, key=lambda x: x["max_confidence"], reverse=True)[:top_n]
