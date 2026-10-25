"""Hyperliquid market data on the raw HL HTTP API: candles, funding regime,
instrument list, mids. The MCP server's `market_*` tools route here.

2026-10-25: the leaderboard and trader-discovery half of this module
(`leaderboard_*`, `discovery_*`) went with the bot-era MCP tools that were
the only callers (cut from the tool list 2026-10-23).
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from pathiel.client.hl_client import fetch_all_mids, fetch_hl_candles
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
