"""Hyperliquid market and trader discovery built on the raw HL HTTP API.

Provides leaderboard, trader-discovery, and market-data lookups (candles,
funding regime, instrument list) with no external MCP dependency.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

import requests

from pathiel.client.hl_client import _http_post, fetch_all_mids, fetch_hl_candles
from pathiel.client.universe import get_universe

logger = logging.getLogger(__name__)

# ── asset classification ─────────────────────────────────────────────────────
# Moved out of `pathiel/agents/market_regime.py` 2026-10-23 (wave 2): that
# module's own trend detector (`detect_regime`, EMA20/EMA50 on 1h closes) fed
# `risk_gates.market_regime_gate`, a gate in the now-retired autonomous
# executor. `classify_asset` is the one piece of it this module still needs,
# for `_compute_funding_regime`'s per-asset-class funding buckets — inlined
# here rather than keeping a whole module alive for one function.

# HL single-stock perps. Curated rather than auto-discovered because the
# universe shifts and we want a stable classifier — adds latency to no new
# coin, just a one-line update when HL lists more.
_EQUITY_COINS = frozenset([
    # Single-name equities
    "TSLA", "NVDA", "AAPL", "MSFT", "GOOGL", "GOOG", "META", "AMZN",
    "NFLX", "AMD", "INTC", "SPY", "QQQ", "COIN", "MSTR", "HOOD", "PLTR",
    "DIS", "JPM", "BA", "WMT", "XOM", "MU", "ARM", "BABA", "SKHX",
    # Broad-market indices (HIP-3: xyz:SP500, xyz:XYZ100, km:US500, km:USTECH, km:SMALL2000)
    "SP500", "US500", "XYZ100", "USTECH", "QQQ", "DJI", "NDX", "SMALL2000", "USENERGY",
])

# HL commodity perps — names vary across the API; cover the obvious aliases
# including HIP-3 namespaced equivalents (xyz:CL = crude, xyz:BRENTOIL, km:USOIL, etc.).
_COMMODITY_COINS = frozenset([
    "NATGAS", "GAS", "NGAS",
    "OIL", "USOIL", "BRENT", "BRENTOIL", "WTI", "CL",
    "GOLD", "SILVER", "COPPER", "PLATINUM", "PALLADIUM", "ALUMINIUM",
])

# Foreign / non-US-correlated stock indices. These trade on their own session
# and drivers (Korea, Japan, HK, Europe, India, Australia) and do NOT track the
# US SP500 proxy. Classified to use their OWN trend instead (same bucket as
# commodities: "own trend", not a shared proxy).
_FOREIGN_INDICES = frozenset([
    "KR200", "KOSPI", "KOSPI200",
    "JP225", "NIKKEI", "N225",
    "HSI", "HANGSENG", "HK50",
    "DAX", "DAX40", "FTSE", "FTSE100", "CAC", "CAC40", "STOXX", "STOXX50", "ESTX50",
    "ASX", "ASX200", "SENSEX", "NIFTY", "NIFTY50",
])

# Bare tickers that trade as native (main-dex) HL perps — authoritatively
# crypto. Built once from the universe and cached; lets `hyna:BTC`, `cash:ETH`,
# `flx:XMR` etc. resolve to crypto by their bare ticker rather than being
# swept into the HIP-3 equity default below.
_crypto_tickers_cache: Optional[frozenset] = None


def _native_crypto_tickers() -> frozenset:
    global _crypto_tickers_cache
    if _crypto_tickers_cache is None:
        try:
            uni = get_universe()  # main dex only — every perp here is crypto
            tickers = frozenset(
                m["coin"].upper() for m in uni
                if m.get("type") == "perp" and ":" not in m.get("coin", "")
            )
            if tickers:  # only cache a real result; retry next call on failure
                _crypto_tickers_cache = tickers
            return tickers
        except Exception:
            return frozenset()
    return _crypto_tickers_cache


def classify_asset(coin: str) -> str:
    """Map a coin to its asset class ("crypto" | "equity" | "commodity") for
    the funding-regime bucket. Resolution is by BARE ticker (the dex prefix is
    stripped), because HIP-3 venues are mixed: `xyz:`/`km:` are tokenized
    stocks/commodities, but `hyna:`/`cash:`/`flx:` also list crypto
    (`hyna:BTC`, `cash:ETH`).

    Order:
      1. commodity allowlist  → commodity
      2. foreign index        → commodity (own-trend bucket, not US equity)
      3. equity allowlist     → equity
      4. native HL perp ticker (e.g. BTC, LINK, FARTCOIN) → crypto, so
         `hyna:LINK` is gated by its own class, not swept into equity
      5. any OTHER HIP-3 namespaced coin → equity — a tokenized stock the
         allowlist doesn't enumerate (xyz:SNDK, xyz:CBRS).
      6. bare unknown (no dex prefix) → crypto (main dex is all crypto)
    """
    raw = (coin or "")
    namespaced = ":" in raw
    bare = raw.split(":", 1)[-1].upper() if namespaced else raw.upper()
    if bare in _COMMODITY_COINS:
        return "commodity"
    if bare in _FOREIGN_INDICES:
        return "commodity"
    if bare in _EQUITY_COINS:
        return "equity"
    if bare in _native_crypto_tickers():
        return "crypto"
    if namespaced:
        return "equity"
    return "crypto"

# Hyperliquid's public leaderboard: the JSON its own UI renders. Not part of
# /info, so it is a plain GET against the stats host.
_LEADERBOARD_URL = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
_LEADERBOARD_TTL_S = 600
_LEADERBOARD_TIMEOUT_S = 60
_leaderboard_cache: Optional[Tuple[List[Dict[str, Any]], float]] = None
_WINDOWS = {"DAILY": "day", "WEEKLY": "week", "MONTHLY": "month", "ALL_TIME": "allTime"}
_SORT_METRICS = {"PROFIT_AND_LOSS_UNREALIZED": "pnl", "PROFIT_AND_LOSS": "pnl",
                 "RETURN_ON_INVESTMENT": "roi"}
_ROI_MIN_ACCOUNT_VALUE = 10_000.0
_MAX_LIMIT = 100
_MAX_POSITION_LOOKUPS = 200
_LOOKUP_WORKERS = 8

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


def _fetch_leaderboard_rows() -> List[Dict[str, Any]]:
    """Every row of Hyperliquid's public leaderboard, normalized.

    HL serves the leaderboard its own UI renders as a static JSON document on
    stats-data.hyperliquid.xyz (not /info). It is ~40MB and ~47k rows, so it is
    fetched at most once per _LEADERBOARD_TTL_S and served from memory between.
    Each window maps to {"pnl", "roi", "vlm"} floats; roi is a fraction.
    """
    resp = requests.get(_LEADERBOARD_URL, timeout=_LEADERBOARD_TIMEOUT_S)
    resp.raise_for_status()
    rows = (resp.json() or {}).get("leaderboardRows") or []
    out: List[Dict[str, Any]] = []
    for r in rows:
        addr = str(r.get("ethAddress") or "").lower()
        if not addr:
            continue
        windows: Dict[str, Dict[str, float]] = {}
        for pair in r.get("windowPerformances") or []:
            if isinstance(pair, (list, tuple)) and len(pair) == 2 and isinstance(pair[1], dict):
                windows[str(pair[0])] = {k: _safe_float(pair[1].get(k)) for k in ("pnl", "roi", "vlm")}
        out.append({
            "address": addr,
            "display_name": r.get("displayName"),
            "account_value": _safe_float(r.get("accountValue")),
            "windows": windows,
        })
    return out


def _leaderboard_rows() -> List[Dict[str, Any]]:
    """Cached leaderboard. A failed refresh serves the stale copy rather than
    nothing: rankings move slowly and an empty answer reads as "no traders"."""
    global _leaderboard_cache
    now = time.time()
    if _leaderboard_cache and now - _leaderboard_cache[1] < _LEADERBOARD_TTL_S:
        return _leaderboard_cache[0]
    try:
        rows = _fetch_leaderboard_rows()
    except Exception as e:
        if _leaderboard_cache:
            logger.warning(f"[hyperfeed] leaderboard refresh failed, serving stale: {e}")
            return _leaderboard_cache[0]
        raise
    _leaderboard_cache = (rows, now)
    return rows


def _rank_leaderboard(time_frame: str, sort_by: str,
                      min_account_value: float) -> List[Dict[str, Any]]:
    """Leaderboard rows ranked by one window's pnl or roi, best first.

    Raises ValueError on an unknown time_frame or sort_by: silently falling
    back to a default ranks traders by something the caller did not ask for.
    """
    window = _WINDOWS.get(str(time_frame).upper())
    if window is None:
        raise ValueError(f"time_frame must be one of {sorted(_WINDOWS)}, got {time_frame!r}")
    metric = _SORT_METRICS.get(str(sort_by).upper())
    if metric is None:
        raise ValueError(f"sort_by must be one of {sorted(_SORT_METRICS)}, got {sort_by!r}")
    rows = [r for r in _leaderboard_rows()
            if window in r["windows"] and r["account_value"] >= min_account_value]
    rows.sort(key=lambda r: r["windows"][window][metric], reverse=True)
    return rows


def _open_positions(addr: str) -> Optional[List[Dict[str, Any]]]:
    """Open main-dex perp positions for one address, or None if the call failed."""
    try:
        state = _http_post("/info", {"type": "clearinghouseState", "user": addr})
    except Exception as e:
        logger.debug(f"[hyperfeed] clearinghouseState failed for {addr}: {e}")
        return None
    if not state:
        return None
    positions = []
    for p in state.get("assetPositions", []) or []:
        pos = (p or {}).get("position") or {}
        szi = _safe_float(pos.get("szi", "0"))
        if szi == 0:
            continue
        lev = pos.get("leverage") or {}
        positions.append({
            "coin": pos.get("coin", ""),
            "side": "long" if szi > 0 else "short",
            "size": abs(szi),
            "entry_price": _safe_float(pos.get("entryPx", "0")),
            "position_value": _safe_float(pos.get("positionValue", "0")),
            "unrealized_pnl": _safe_float(pos.get("unrealizedPnl", "0")),
            "leverage": lev.get("value", "0") if isinstance(lev, dict) else lev,
        })
    return positions


def _top_with_positions(ranked: List[Dict[str, Any]], limit: int,
                        open_position_filter: bool) -> List[Tuple[Dict[str, Any], List[Dict[str, Any]]]]:
    """Walk the ranking, attach live positions, keep the first `limit`.

    With the filter on, flat accounts are skipped, so this walks past `limit`.
    The walk is capped at _MAX_POSITION_LOOKUPS so a ranking full of flat
    accounts costs a bounded number of /info calls, not one per leaderboard row.
    """
    picked: List[Tuple[Dict[str, Any], List[Dict[str, Any]]]] = []
    budget = ranked[:_MAX_POSITION_LOOKUPS] if open_position_filter else ranked[:limit]
    batch = max(1, min(_LOOKUP_WORKERS * 2, limit))
    with ThreadPoolExecutor(max_workers=_LOOKUP_WORKERS, thread_name_prefix="hf-lb") as pool:
        for i in range(0, len(budget), batch):
            chunk = budget[i:i + batch]
            for row, positions in zip(chunk, pool.map(lambda r: _open_positions(r["address"]), chunk)):
                if positions is None:
                    positions = []
                if open_position_filter and not positions:
                    continue
                picked.append((row, positions))
                if len(picked) >= limit:
                    return picked
    return picked


def leaderboard_get_top(time_frame: str = "DAILY",
                        sort_by: str = "PROFIT_AND_LOSS_UNREALIZED",
                        consistency: Optional[List[str]] = None,
                        limit: int = 10,
                        open_position_filter: bool = True,
                        min_account_value: float = 0.0) -> Dict[str, Any]:
    """Top traders from Hyperliquid's public leaderboard, with live positions.

    Ranked by `sort_by` over `time_frame` (DAILY/WEEKLY/MONTHLY/ALL_TIME).
    PROFIT_AND_LOSS_UNREALIZED is kept as the name callers already send; the
    leaderboard's pnl is HL's window pnl (realized + unrealized change), which
    is what that name has always meant on the HL UI. `consistency` is accepted
    for signature compatibility and ignored: the leaderboard has no such field.
    """
    limit = max(1, min(_MAX_LIMIT, int(limit)))
    ranked = _rank_leaderboard(time_frame, sort_by, float(min_account_value or 0))
    window = _WINDOWS[str(time_frame).upper()]
    traders = []
    for rank, (row, positions) in enumerate(_top_with_positions(ranked, limit, open_position_filter), 1):
        w = row["windows"][window]
        traders.append({
            "rank": rank,
            "address": row["address"],
            "display_name": row["display_name"],
            "account_value": row["account_value"],
            "window": window,
            "pnl_usd": w["pnl"],
            "roi_pct": w["roi"] * 100,
            "volume_usd": w["vlm"],
            "positions": positions,
            "position_count": len(positions),
        })
    return {"source": "hyperliquid_leaderboard", "time_frame": str(time_frame).upper(),
            "sort_by": str(sort_by).upper(), "traders": traders}


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
    min_account_value: Optional[float] = None,
) -> Dict[str, Any]:
    """Top traders with pnl/roi for every window, ranked by `sort_by` over `time_frame`.

    ROI rankings default to a $10k account-value floor (_ROI_MIN_ACCOUNT_VALUE):
    without it the top of an ROI board is dust accounts that doubled $20.
    Pass min_account_value=0 to see them anyway.
    """
    if min_account_value is None:
        min_account_value = _ROI_MIN_ACCOUNT_VALUE if str(sort_by).upper() == "RETURN_ON_INVESTMENT" else 0.0
    limit = max(1, min(_MAX_LIMIT, int(limit)))
    ranked = _rank_leaderboard(time_frame, sort_by, float(min_account_value))
    window = _WINDOWS[str(time_frame).upper()]
    traders = []
    for row, positions in _top_with_positions(ranked, limit, open_position_filter):
        w = row["windows"][window]
        traders.append({
            "address": row["address"],
            "display_name": row["display_name"],
            "account_value": row["account_value"],
            "pnl_usd": w["pnl"],
            "roi_pct": w["roi"] * 100,
            "volume_usd": w["vlm"],
            "windows": row["windows"],
            "open_positions": len(positions),
            "positions": positions,
        })
    return {"data": {"source": "hyperliquid_leaderboard", "time_frame": str(time_frame).upper(),
                     "sort_by": str(sort_by).upper(),
                     "min_account_value": min_account_value, "traders": traders}}


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

            # pnl_usd used to be account_value and roi_pct a ratio of equity to
            # equity-minus-notional: two numbers with the right names and no
            # relation to profit. Both are now the open book's unrealized pnl.
            unrealized = sum(p["unrealized_pnl"] for p in positions)
            base = account_value - unrealized
            traders.append({
                "traderAddress": addr,
                "accountValue": account_value,
                "total_ntl_pos": total_ntl,
                "pnl_usd": unrealized,
                "pnl_scope": "unrealized, open positions only",
                "roi_pct": (unrealized / base * 100) if base > 0 else 0,
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
