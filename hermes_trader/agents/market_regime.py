"""Per-asset-class market regime detection — feeds the market_regime risk gate.

Classifies each coin into crypto / equity / commodity, then picks the right
trend proxy:

  crypto    → BTC 4h trend (everything in crypto correlates to BTC)
  equity    → NVDA 4h trend (most-liquid HL single-stock perp; proxy for
              risk-on/off in the tradfi single-stock basket; switch via
              EQUITY_PROXY)
  commodity → the coin's own 4h trend (commodities aren't correlated to each
              other — gas, silver, copper, oil all move on their own drivers)

Trend itself is EMA20 vs EMA50 on 4h closes, with a short slope check on the
fast EMA so we don't whipsaw at the cross. Three states: 'up', 'down',
'neutral' (no strong direction → gate stays out of the way).

Regimes are cached per-proxy for `REGIME_TTL_S` (default 10 min) so the gate
doesn't re-fetch candles for every trade attempt in a scan cycle.
"""
from __future__ import annotations

import logging
import time
from typing import Dict, Literal, Tuple

from hermes_trader.client.hl_client import fetch_hl_candles
from hermes_trader.indicators.math import ema

logger = logging.getLogger(__name__)

AssetClass = Literal["crypto", "equity", "commodity"]
Regime = Literal["up", "down", "neutral"]

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

CRYPTO_PROXY = "BTC"
# HIP-3 tokenized equity perp — xyz:SP500 is the highest-volume broad-market
# proxy ($194M 24h vol). Only resolves when enable_hip3 is on; falls back to
# crypto proxy when the candle fetch returns nothing.
EQUITY_PROXY = "xyz:SP500"

# Trend thresholds on 1h closes — 8 bars = 8h lookback so intraday
# rotations are caught (a slower 4h × 5-bar = 20h window was missing
# every relief rally and pinning regime to BTC's macro drift).
_SLOPE_LOOKBACK = 8
_SLOPE_UP = 0.002       # +0.2% over 8 bars → 'up'
_SLOPE_DOWN = -0.002    # -0.2% over 8 bars → 'down'

REGIME_TTL_S = 300  # 5min cache — 1h trends can flip faster than 4h

_regime_cache: Dict[str, Tuple[Regime, float]] = {}


def classify_asset(coin: str) -> AssetClass:
    """Map a coin to its asset class. Default is 'crypto' for everything not
    explicitly listed as equity or commodity — the universe is crypto-heavy
    and that fallback is safe (BTC trend is the right gate for them).

    HIP-3 namespaced coins (e.g. `xyz:NVDA`, `km:US500`) drop the dex prefix
    before matching, so `xyz:NVDA` lands in `_EQUITY_COINS` and `xyz:GOLD` in
    `_COMMODITY_COINS`. Without this strip every HIP-3 trade would be gated
    by BTC's trend, which is plainly wrong for stocks/commodities."""
    raw = (coin or "")
    bare = raw.split(":", 1)[-1].upper() if ":" in raw else raw.upper()
    if bare in _EQUITY_COINS:
        return "equity"
    if bare in _COMMODITY_COINS:
        return "commodity"
    return "crypto"


def _trend_from_closes(closes: list[float]) -> Regime:
    """EMA20 vs EMA50 + slope. Returns 'neutral' if the series is too short
    or the move isn't clear enough either way (the deliberate sit-out case)."""
    if len(closes) < 50:
        return "neutral"
    fast = ema(closes, 20)
    slow = ema(closes, 50)
    if len(fast) < _SLOPE_LOOKBACK + 1 or len(slow) < 1:
        return "neutral"
    f_now, s_now = fast[-1], slow[-1]
    f_prev = fast[-(_SLOPE_LOOKBACK + 1)]
    if f_prev == 0:
        return "neutral"
    slope = (f_now - f_prev) / abs(f_prev)
    if f_now > s_now and slope > _SLOPE_UP:
        return "up"
    if f_now < s_now and slope < _SLOPE_DOWN:
        return "down"
    return "neutral"


def _detect_for_proxy(proxy: str) -> Regime:
    """Network path — fetch candles for `proxy`, compute trend.
    Wrapped by `detect_regime` for caching."""
    try:
        candles = fetch_hl_candles(proxy, interval="1h", count=100)
        if not candles:
            return "neutral"
        closes = [float(c.c) for c in candles]
        return _trend_from_closes(closes)
    except Exception as e:
        logger.warning(f"[regime] candle fetch failed for {proxy}: {e}")
        return "neutral"


def detect_regime(coin: str, *, force: bool = False) -> Regime:
    """Return the regime applicable to a trade in `coin`. Cached for TTL.

    `force=True` bypasses the cache (used by tests + the operator console)."""
    klass = classify_asset(coin)
    if klass == "commodity":
        # HIP-3 commodity names are case-sensitive (`xyz:GOLD`, never `XYZ:GOLD`).
        # For non-HIP-3 bare names there isn't a working commodity perp on the
        # main dex, but `upper()` on a bare name was the prior convention.
        proxy = coin if ":" in coin else coin.upper()
    elif klass == "equity":
        proxy = EQUITY_PROXY
    else:
        proxy = CRYPTO_PROXY

    now = time.time()
    cached = _regime_cache.get(proxy)
    if not force and cached and (now - cached[1]) < REGIME_TTL_S:
        return cached[0]
    regime = _detect_for_proxy(proxy)
    _regime_cache[proxy] = (regime, now)
    return regime


def regime_snapshot() -> Dict[str, Dict[str, object]]:
    """Operator-facing summary: every cached proxy + its regime + cache age."""
    now = time.time()
    return {
        proxy: {"regime": regime, "age_s": round(now - ts, 1)}
        for proxy, (regime, ts) in _regime_cache.items()
    }
