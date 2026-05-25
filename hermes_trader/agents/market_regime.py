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
from typing import Dict, Literal, Optional, Tuple

from hermes_trader.client.hl_client import fetch_hl_candles
from hermes_trader.indicators.math import ema

logger = logging.getLogger(__name__)

AssetClass = Literal["crypto", "equity", "commodity"]
Regime = Literal["up", "down", "neutral"]

# HL single-stock perps. Curated rather than auto-discovered because the
# universe shifts and we want a stable classifier — adds latency to no new
# coin, just a one-line update when HL lists more.
_EQUITY_COINS = frozenset([
    "TSLA", "NVDA", "AAPL", "MSFT", "GOOGL", "GOOG", "META", "AMZN",
    "NFLX", "AMD", "INTC", "SPY", "QQQ", "COIN", "MSTR", "HOOD", "PLTR",
    "DIS", "JPM", "BA", "WMT", "XOM",
])

# HL commodity perps — names vary across the API; cover the obvious aliases.
_COMMODITY_COINS = frozenset([
    "NATGAS", "GAS", "NGAS", "OIL", "BRENT", "WTI",
    "GOLD", "SILVER", "COPPER", "PLATINUM", "PALLADIUM",
])

CRYPTO_PROXY = "BTC"
EQUITY_PROXY = "NVDA"  # if HL lists SPY/QQQ later, prefer those

# Trend thresholds — small numbers because we're on 4h closes; even a 0.1%
# slope per 5 bars (20h) is a meaningful directional move at crypto vol.
_SLOPE_LOOKBACK = 5
_SLOPE_UP = 0.001       # +0.1% over 5 bars → 'up' candidate
_SLOPE_DOWN = -0.001    # -0.1% over 5 bars → 'down' candidate

REGIME_TTL_S = 600  # 10min cache; 4h trends don't flip faster

_regime_cache: Dict[str, Tuple[Regime, float]] = {}


def classify_asset(coin: str) -> AssetClass:
    """Map a coin to its asset class. Default is 'crypto' for everything not
    explicitly listed as equity or commodity — the universe is crypto-heavy
    and that fallback is safe (BTC trend is the right gate for them)."""
    c = (coin or "").upper()
    if c in _EQUITY_COINS:
        return "equity"
    if c in _COMMODITY_COINS:
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
        candles = fetch_hl_candles(proxy, interval="4h", count=100)
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
        proxy = coin.upper()
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
