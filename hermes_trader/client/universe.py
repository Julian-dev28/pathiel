"""Hyperliquid universe loader.

Fetches perp + spot metadata with volume data from metaAndAssetCtxs and
spotMetaAndAssetCtxs. Returns volume-ranked markets for pre-filtering.

Uses metaAndAssetCtxs/spotMetaAndAssetCtxs endpoints (weight ~20 each)
instead of separate meta + allMids calls, so we get universe + volume +
mids in TWO HTTP POSTs total.

Caches results for 1 hour.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from hermes_trader.client.hl_client import _http_post

logger = logging.getLogger(__name__)

_CACHE_DIR = Path.home() / ".hermes" / "universe_cache"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)
_UNIVERSE_CACHE_PATH = _CACHE_DIR / "meta.json"
_SPOT_CACHE_PATH = _CACHE_DIR / "spot_meta.json"
_CACHE_TTL_SECS = 86_400  # 24 hours


def _load_json_cached(path: Path, ttl_secs: int) -> Optional[Any]:
    """Load a JSON file if it's fresh enough."""
    try:
        if path.exists() and (time.time() - path.stat().st_mtime) < ttl_secs:
            with open(path, 'r') as f:
                return json.load(f)
    except Exception:
        pass
    return None


def _save_json_cached(path: Path, data: Any) -> None:
    """Save JSON to a cache file."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w') as f:
            json.dump(data, f)
    except Exception as e:
        logger.warning(f"[universe] Failed to cache {path}: {e}")


def _fetch_perp_meta(force_refresh: bool = False) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Fetch perp metadata with asset context (volume, funding, etc.).
    
    Returns (meta_dict, asset_ctx_dict) where:
    - meta_dict: coin -> {name, maxLeverage, szDecimals, ...}
    - asset_ctx_dict: coin -> {dayNtlVlm, openInterest, funding, ...}
    """
    cache = _load_json_cached(_UNIVERSE_CACHE_PATH, _CACHE_TTL_SECS) if not force_refresh else None
    if cache is None:
        data = _http_post("/info", {"type": "metaAndAssetCtxs"})
        if data and isinstance(data, list) and len(data) >= 2:
            meta = data[0]
            ctx = data[1]
            meta_dict = {}
            ctx_dict = {}
            for i, u in enumerate(meta.get("universe", [])):
                coin = u["name"]
                meta_dict[coin] = {
                    "name": coin,
                    "maxLeverage": u.get("maxLeverage", 40),
                    "szDecimals": u.get("szDecimals", 5),
                    "type": "perp",
                }
                if i < len(ctx):
                    ctx_dict[coin] = ctx[i]
            _save_json_cached(_UNIVERSE_CACHE_PATH, (meta_dict, ctx_dict))
            return meta_dict, ctx_dict
        return {}, {}
    return cache[0], cache[1]


def _fetch_spot_meta(force_refresh: bool = False) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Fetch spot metadata with asset context.
    
    Returns (meta_dict, asset_ctx_dict) for spot assets.
    Spot coin names are prefixed with '@' internally — we strip that.
    """
    cache = _load_json_cached(_SPOT_CACHE_PATH, _CACHE_TTL_SECS) if not force_refresh else None
    if cache is None:
        data = _http_post("/info", {"type": "spotMetaAndAssetCtxs"})
        if data and isinstance(data, list) and len(data) >= 2:
            meta = data[0]
            ctx = data[1]
            meta_dict = {}
            ctx_dict = {}
            for i, u in enumerate(meta.get("universe", [])):
                # Spot names come as "@4", "@5" etc — use "name" if available
                coin = u.get("name", f"@{i}")
                if not coin.startswith("@"):
                    coin = f"@{coin}"  # Normalize spot prefix
                meta_dict[coin] = {
                    "name": coin,
                    "type": "spot",
                }
                if i < len(ctx):
                    ctx_dict[coin] = ctx[i]
            _save_json_cached(_SPOT_CACHE_PATH, (meta_dict, ctx_dict))
            return meta_dict, ctx_dict
        return {}, {}
    return cache[0], cache[1]


def get_universe(force_refresh: bool = False) -> List[Dict[str, Any]]:
    """Fetch the full market universe (perp + spot) with volume data.
    
    Returns list of dicts sorted by 24h volume (highest first):
    [
        {
            "coin": "BTC",
            "type": "perp",
            "maxLeverage": 40,
            "szDecimals": 5,
            "dayNtlVlm": 3274603594.46,  # 24h volume in USDC
            "dayBaseVlm": 40576.83,      # 24h volume in coin
            "openInterest": 27824.85,
            ...
        },
        ...
    ]
    """
    perp_meta, perp_ctx = _fetch_perp_meta(force_refresh)
    spot_meta, spot_ctx = _fetch_spot_meta(force_refresh)
    
    # Merge into unified list
    results = []
    all_coins = set(list(perp_meta.keys()) + list(spot_meta.keys()))
    
    for coin in all_coins:
        m = perp_meta.get(coin) or spot_meta.get(coin, {})
        c = perp_ctx.get(coin) or spot_ctx.get(coin, {})
        
        def _f(v, d=0):
            return float(v) if v is not None else d
        
        asset = {
            "coin": coin,
            "type": m.get("type", "perp"),
            "maxLeverage": m.get("maxLeverage"),
            "szDecimals": m.get("szDecimals"),
            "dayNtlVlm": _f(c.get("dayNtlVlm")),
            "dayBaseVlm": _f(c.get("dayBaseVlm")),
            "openInterest": _f(c.get("openInterest")),
            "funding": _f(c.get("funding")),
            "prevDayPx": _f(c.get("prevDayPx")),
            "oraclePx": _f(c.get("oraclePx")),
            "markPx": _f(c.get("markPx")),
            "midPx": _f(c.get("midPx")),
        }
        results.append(asset)
    
    # Sort by 24h volume descending
    results.sort(key=lambda x: x["dayNtlVlm"], reverse=True)
    return results


def get_universe_top_n(n: int = 100, force_refresh: bool = False) -> List[Dict[str, Any]]:
    """Get top N markets by 24h volume.
    
    This is the pre-filtered list for scanning — avoids hitting rate limits
    by only fetching candles for the most liquid markets.
    """
    universe = get_universe(force_refresh)
    return universe[:n]


def get_market_by_coin(coin: str) -> Optional[Dict[str, Any]]:
    """Get a single market by coin name."""
    for m in get_universe():
        if m["coin"] == coin:
            return m
    return None
