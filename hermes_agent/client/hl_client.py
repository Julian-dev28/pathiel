"""Hyperliquid info API client using official SDK.

Wraps hyperliquid.info.Info for async-style usage in our FastAPI app.
Provides high-level methods for market data fetching.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

import requests
from hyperliquid.info import Info
from hermes_agent.models.types import Candle

logger = logging.getLogger(__name__)

HL_API = "https://api.hyperliquid.xyz"
_MS_PER_CANDLE: Dict[str, int] = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}


def _make_info() -> Info:
    """Create an Info client instance."""
    return Info()


def hl_call(action: str, **kwargs: Any) -> Any:
    """Direct POST to the HL info endpoint for unsupported actions.
    
    Uses requests directly to avoid SDK post() URL construction bug.
    """
    payload = {"action": action, **kwargs}
    resp = requests.post(HL_API, json=payload, timeout=10)
    resp.raise_for_status()
    return resp.json()


def fetch_hl_candles(
    coin: str,
    interval: str = "5m",
    count: int = 100,
) -> List[Candle]:
    """Fetch candles from Hyperliquid.
    
    Translation of fetchHLCandles from lib/hl-client.ts.
    Returns list of Candle objects with float values.
    """
    ms = _MS_PER_CANDLE.get(interval, 300_000)
    end_time = int(time.time() * 1000)
    start_time = end_time - ms * count

    info = _make_info()
    raw = info.candles_snapshot(coin, interval, start_time, end_time)

    if not isinstance(raw, list):
        return []

    return [
        Candle(
            t=c["t"],
            o=float(c["o"]),
            h=float(c["h"]),
            l=float(c["l"]),
            c=float(c["c"]),
            v=float(c.get("v", "0")),
        )
        for c in raw
    ]


def fetch_account_state(user: str) -> Dict[str, Any]:
    """Fetch perp + spot account state for a user.
    
    Translation of fetchAccountState from lib/hl-client.ts.
    Returns {equity, total_ntl, spot_balances, asset_positions}.
    """
    info = _make_info()
    
    # Use direct POST for clearinghouse state (not exposed by SDK)
    try:
        perp = hl_call("clearinghouseState", user=user)
    except Exception as e:
        logger.error(f"Failed to fetch perp state: {e}")
        perp = {}
    
    try:
        spot = hl_call("spotClearinghouseState", user=user)
    except Exception as e:
        logger.error(f"Failed to fetch spot state: {e}")
        spot = {}

    margin_summary = perp.get("marginSummary", {})
    perp_equity = float(margin_summary.get("accountValue", "0"))
    total_ntl = float(margin_summary.get("totalNtlPos", "0"))

    raw_balances = spot.get("balances", []) or []
    spot_balances = [
        b for b in raw_balances
        if b.get("coin", "") in ("USDC", "USDT", "USD")
    ]
    
    raw_positions = perp.get("assetPositions", []) or []
    asset_positions = [
        p for p in raw_positions
        if float(p.get("position", {}).get("szi", "0")) != 0
    ]

    spot_usdc = 0.0
    for b in spot_balances:
        if b.get("coin") == "USDC":
            spot_usdc = float(b.get("total", "0"))

    equity = perp_equity if perp_equity > 0 else spot_usdc

    return {
        "equity": equity,
        "total_ntl": total_ntl,
        "spot_balances": spot_balances,
        "asset_positions": asset_positions,
    }


def fetch_all_mids() -> Dict[str, str]:
    """Get all mid prices from Hyperliquid."""
    info = _make_info()
    return info.all_mids()


def fetch_funding_history(coin: str, start_time: int, end_time: Optional[int] = None) -> List[Dict[str, Any]]:
    """Fetch funding rate history for a coin."""
    info = _make_info()
    if end_time is None:
        end_time = int(time.time() * 1000)
    return info.funding_history(coin, start_time, end_time)


def fetch_universe(force_refresh: bool = False) -> Dict[str, Any]:
    """Fetch the full market universe (perp + spot meta).
    
    Returns dict with 'perp' and 'spot' keys, each containing the raw metadata.
    """
    info = _make_info()
    perp_meta = info.meta()
    spot_meta = info.spot_meta()
    return {"perp": perp_meta, "spot": spot_meta}
