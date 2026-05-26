"""Hyperliquid universe loader.

Fetches perp + spot metadata with volume data from metaAndAssetCtxs and
spotMetaAndAssetCtxs. Returns volume-ranked markets for pre-filtering.

Uses metaAndAssetCtxs/spotMetaAndAssetCtxs endpoints (weight ~20 each)
instead of separate meta + allMids calls, so we get universe + volume +
mids in TWO HTTP POSTs total.

Caches results for 24 hours.

────────────────────────────────────────────────────────────────────────────
ENABLING HIP-3 (tokenized equity / commodity / index perps)
────────────────────────────────────────────────────────────────────────────
Hyperliquid hosts tokenized-equity, commodity, and index perps on separate
`perpDexs` (xyz, km, vntl, flx, hyna, abcd, cash, para, ...). Markets are
named `<dex>:<symbol>` — e.g. `xyz:NVDA`, `xyz:GOLD`, `xyz:SP500`,
`km:USOIL`. Per-trade leverage is set by each dex (xyz:NVDA = 20x,
xyz:SP500 = 50x, xyz:GOLD = 25x).

To enable HIP-3 scanning, set `enable_hip3: true` in `.agent-config.json`.
The flag is read at three points:

  1. **At loop start** (`scripts/trading_loop.py`): `get_universe(include_hip3=True)`
     fetches each registered HIP-3 dex's `metaAndAssetCtxs` once and merges
     the markets into the prefetched universe. Restart the loop to refresh
     the universe after toggling the flag.
  2. **Per scan cycle** (`agents/perception.py`): `fetch_all_mids(include_hip3=True)`
     adds one HTTP POST per HIP-3 dex (~8 total) so mid prices for HIP-3
     markets are available to the trigger filters.
  3. **At SDK init** (`client/exchange.py`): the Hyperliquid SDK's
     `Info` / `Exchange` are constructed with `perp_dexs=[""] + dex_names`
     so `name_to_asset` can resolve colon-namespaced coins. The empty
     string `""` is the sentinel for the main perp dex — DO NOT drop it,
     or BTC/ETH/etc. will start raising `KeyError` at order placement.

HIP-3 markets compete with native crypto perps for the top-N scan slots
(`HERMES_MAX_MARKETS`, default 60); volume-ranking surfaces the most
liquid stocks (NVDA, MU) and indices (SP500, XYZ100) alongside crypto.

Note: HIP-3 equity perps only trade during US equity hours. Outside those
hours volume drops to ~zero so the scanner naturally skips them; orders
sent off-hours will be rejected by HL. No special hours-gate is implemented.
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
_PERP_DEXS_CACHE_PATH = _CACHE_DIR / "perp_dexs.json"
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


# ── HIP-3 perp dex support ────────────────────────────────────────────────────
# Hyperliquid hosts tokenized-equity / commodity / index perps on separate
# `perpDexs` (xyz, km, vntl, flx, hyna, abcd, cash, para, ...). Each dex has
# its own universe accessed via metaAndAssetCtxs with a `dex` parameter and
# its own allMids likewise. Markets are named `<dex>:<symbol>`, e.g.
# `xyz:NVDA`, `km:GLDMINE`. See README "Enabling HIP-3 markets" below.


def list_hip3_dexes(force_refresh: bool = False) -> List[str]:
    """Names of all non-null HIP-3 perp dexes registered on Hyperliquid.

    First entry of /info?perpDexs is null (the main HL perp dex); the rest
    are HIP-3 sub-dexes. Cached for 24h.
    """
    cache = _load_json_cached(_PERP_DEXS_CACHE_PATH, _CACHE_TTL_SECS) if not force_refresh else None
    if cache is not None:
        return cache
    raw = _http_post("/info", {"type": "perpDexs"}) or []
    dexes = [d["name"] for d in raw if isinstance(d, dict) and d.get("name")]
    _save_json_cached(_PERP_DEXS_CACHE_PATH, dexes)
    return dexes


def _fetch_hip3_meta(dex: str, force_refresh: bool = False) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Fetch one HIP-3 perpDex universe + asset contexts, shape-compatible with `_fetch_perp_meta`."""
    cache_path = _CACHE_DIR / f"meta_{dex}.json"
    cache = _load_json_cached(cache_path, _CACHE_TTL_SECS) if not force_refresh else None
    if cache is not None:
        return cache[0], cache[1]
    data = _http_post("/info", {"type": "metaAndAssetCtxs", "dex": dex})
    if not (data and isinstance(data, list) and len(data) >= 2):
        return {}, {}
    meta, ctx = data[0], data[1]
    meta_dict: Dict[str, Any] = {}
    ctx_dict: Dict[str, Any] = {}
    for i, u in enumerate(meta.get("universe", [])):
        coin = u.get("name")
        if not coin:
            continue
        meta_dict[coin] = {
            "name": coin,
            "maxLeverage": u.get("maxLeverage", 1),
            "szDecimals": u.get("szDecimals", 5),
            "type": "perp",
            "dex": dex,
        }
        if i < len(ctx):
            ctx_dict[coin] = ctx[i]
    _save_json_cached(cache_path, (meta_dict, ctx_dict))
    return meta_dict, ctx_dict


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


def get_universe(force_refresh: bool = False, include_hip3: bool = False) -> List[Dict[str, Any]]:
    """Fetch the full market universe (perp + spot, optionally + HIP-3) with volume data.

    Args:
        force_refresh: ignore caches and re-fetch.
        include_hip3: when True, also fetches each registered HIP-3 perpDex
            (xyz/km/vntl/...) and merges its markets in. Each HIP-3 market dict
            gets `"dex": "<dex_name>"`; native crypto markets have `dex: None`.

    Returns list of dicts sorted by 24h volume (highest first):
    [
        {
            "coin": "BTC",
            "type": "perp",
            "dex": None,                  # None for native HL perp, "xyz" / "km" / ... for HIP-3
            "maxLeverage": 40,
            "szDecimals": 5,
            "dayNtlVlm": 3274603594.46,  # 24h volume in USDC
            "dayBaseVlm": 40576.83,
            "openInterest": 27824.85,
            ...
        },
        ...
    ]
    """
    perp_meta, perp_ctx = _fetch_perp_meta(force_refresh)
    spot_meta, spot_ctx = _fetch_spot_meta(force_refresh)

    # HIP-3: walk every non-null perpDex and merge its markets in.
    hip3_meta: Dict[str, Any] = {}
    hip3_ctx: Dict[str, Any] = {}
    if include_hip3:
        for dex in list_hip3_dexes(force_refresh):
            m, c = _fetch_hip3_meta(dex, force_refresh)
            hip3_meta.update(m)
            hip3_ctx.update(c)

    # Merge into unified list
    results = []
    all_coins = set(list(perp_meta.keys()) + list(spot_meta.keys()) + list(hip3_meta.keys()))

    for coin in all_coins:
        m = perp_meta.get(coin) or hip3_meta.get(coin) or spot_meta.get(coin, {})
        c = perp_ctx.get(coin) or hip3_ctx.get(coin) or spot_ctx.get(coin, {})
        
        def _f(v, d=0):
            return float(v) if v is not None else d
        
        asset = {
            "coin": coin,
            "type": m.get("type", "perp"),
            "dex": m.get("dex"),  # None for native HL perp / spot, "xyz" / "km" / ... for HIP-3
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
