"""Hyperliquid universe loader.

Translation of lib/hl-universe.ts — fetches perp + spot meta from HL,
caches results for 1 hour.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from hermes_agent.client.hl_client import hl_call

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

EQUITY_PERP_COINS = {
    # US Tech / Growth
    "TSLA", "NVDA", "AAPL", "AMZN", "GOOGL", "MSFT", "META", "COIN", "MSTR",
    "INTC", "AMD", "NFLX", "ADBE", "CRM", "AVGO", "QCOM", "TXN", "MU", "SNPS",
    "SNDK", "LITE", "CRDO", "SMCI", "ARM", "PLTR", "SOFI", "HOOD", "RKLB",
}

COMMODITY_COINS = {
    "NATGAS", "CRCL", "SILVER", "COPPER", "GOLD", "URNM",
}

CACHE_TTL_MS = 60 * 60 * 1000  # 1 hour

# ── Cache ──────────────────────────────────────────────────────────────────────

_cache: Optional[Dict[str, Any]] = None


def _categorize(coin: str) -> str:
    """Determine market category from coin name."""
    if coin in COMMODITY_COINS:
        return "commodity"
    if coin in EQUITY_PERP_COINS:
        return "equity"
    return "crypto"


# ── Fetchers ───────────────────────────────────────────────────────────────────

def _fetch_perp_universe() -> List[Dict[str, Any]]:
    """Fetch perp universe from HL meta endpoint."""
    raw = hl_call("meta")
    universe = raw.get("universe", [])

    return [
        {
            "coin": u["name"],
            "type": "perp",
            "category": _categorize(u["name"]),
            "sz_decimals": u.get("szDecimals", 5),
            "max_leverage": u.get("maxLeverage", 1),
            "min_notional": float(u["minNtl"]) if u.get("minNtl") else None,
        }
        for u in universe
    ]


def _fetch_spot_universe() -> List[Dict[str, Any]]:
    """Fetch spot universe from HL spotMeta endpoint."""
    raw = hl_call("spotMeta")
    universe = raw.get("universe", [])
    tokens = raw.get("tokens", [])

    return [
        {
            "coin": u["name"],
            "type": "spot",
            "category": "crypto",
            "sz_decimals": u.get("szDecimals", 6) if isinstance(u.get("szDecimals"), int) else (
                tokens[u.get("index", 0)]["szDecimals"] if u.get("index") is not None and u.get("index") < len(tokens) and "szDecimals" in tokens[u.get("index", 0)] else 6
            ),
            "max_leverage": 1,
        }
        for u in universe
    ]


# ── Public API ─────────────────────────────────────────────────────────────────

def get_universe(force_refresh: bool = False) -> List[Dict[str, Any]]:
    """Get the full tradeable universe (perp + spot).

    Returns list of market dicts with {coin, type, category, sz_decimals, max_leverage, min_notional?}.
    Cached for 1 hour unless force_refresh=True.
    """
    global _cache

    now = int(time.time() * 1000)
    if not force_refresh and _cache and _cache.get("ttl", 0) > now:
        return _cache["value"]

    perps, spots = _fetch_perp_universe(), _fetch_spot_universe()

    all_markets = perps + spots
    _cache = {"value": all_markets, "ttl": now + CACHE_TTL_MS}
    logger.info(f"[universe] loaded {len(all_markets)} markets ({len(perps)} perp + {len(spots)} spot)")
    return all_markets


def get_market_by_coin(coin: str) -> Optional[Dict[str, Any]]:
    """Lookup a market by coin name from the cache."""
    if _cache is None:
        return None
    for m in _cache.get("value", []):
        if m["coin"] == coin:
            return m
    return None
