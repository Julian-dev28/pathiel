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

# Foreign / non-US-correlated stock indices. These trade on their own session
# and drivers (Korea, Japan, HK, Europe, India, Australia) and do NOT track the
# US SP500 proxy — gating them by US equity regime made them perennial losers
# (e.g. xyz:KR200 = KOSPI 200, repeatedly "trend-aligned with US-up" yet falling).
# Classified to use their OWN 4h trend (the commodity/own-trend path) instead.
_FOREIGN_INDICES = frozenset([
    "KR200", "KOSPI", "KOSPI200",
    "JP225", "NIKKEI", "N225",
    "HSI", "HANGSENG", "HK50",
    "DAX", "DAX40", "FTSE", "FTSE100", "CAC", "CAC40", "STOXX", "STOXX50", "ESTX50",
    "ASX", "ASX200", "SENSEX", "NIFTY", "NIFTY50",
])

CRYPTO_PROXY = "BTC"
# HIP-3 tokenized equity perp — xyz:SP500 is the highest-volume broad-market
# proxy ($194M 24h vol). Only resolves when enable_hip3 is on; falls back to
# crypto proxy when the candle fetch returns nothing.
EQUITY_PROXY = "xyz:SP500"

# Trend thresholds on 1h closes — 8 bars = 8h lookback so intraday
# rotations are caught (a slower 4h × 5-bar = 20h window was missing
# every relief rally and pinning regime to BTC's macro drift).
#
# Sensitivity raised 0.2%→0.1% (2026-05): a soft/chop tape was reading
# 'neutral' all day, and a neutral regime is a FREE PASS in market_regime_gate
# — so the gate gave zero trend discipline exactly when the book was filling
# with counter-trend dip-buy longs. A bearish EMA20<EMA50 cross that is only
# gently sloped is still a real downtrend; we want it to register as 'down' so
# counter-trend longs face the gate. Flat (slope≈0) still reads neutral.
_SLOPE_LOOKBACK = 8
_SLOPE_UP = 0.001       # +0.1% over 8 bars → 'up'
_SLOPE_DOWN = -0.001    # -0.1% over 8 bars → 'down'

REGIME_TTL_S = 300  # 5min cache — 1h trends can flip faster than 4h

_regime_cache: Dict[str, Tuple[Regime, float]] = {}

# Bare tickers that trade as native (main-dex) HL perps — authoritatively
# crypto. Built once from the universe and cached; lets `hyna:BTC`, `cash:ETH`,
# `flx:XMR` etc. resolve to crypto by their bare ticker rather than being
# swept into the HIP-3 equity default below.
_crypto_tickers_cache: frozenset[str] | None = None


def _native_crypto_tickers() -> frozenset[str]:
    global _crypto_tickers_cache
    if _crypto_tickers_cache is None:
        try:
            from hermes_trader.client.universe import get_universe
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


def classify_asset(coin: str) -> AssetClass:
    """Map a coin to its asset class, picking the trend proxy + funding-regime
    bucket. Resolution is by BARE ticker (the dex prefix is stripped), because
    HIP-3 venues are mixed: `xyz:`/`km:` are tokenized stocks/commodities, but
    `hyna:`/`cash:`/`flx:` also list crypto (`hyna:BTC`, `cash:ETH`).

    Order:
      1. commodity allowlist  → commodity
      2. equity allowlist     → equity
      3. native HL perp ticker (e.g. BTC, LINK, FARTCOIN) → crypto, so
         `hyna:LINK` is gated by BTC, not SP500
      4. any OTHER HIP-3 namespaced coin → equity — a tokenized stock the
         allowlist doesn't enumerate (xyz:SNDK, xyz:CBRS). The old code
         defaulted these to crypto and gated SanDisk by BTC's trend.
      5. bare unknown (no dex prefix) → crypto (main dex is all crypto)
    """
    raw = (coin or "")
    namespaced = ":" in raw
    bare = raw.split(":", 1)[-1].upper() if namespaced else raw.upper()
    if bare in _COMMODITY_COINS:
        return "commodity"
    if bare in _FOREIGN_INDICES:
        # Own-trend (commodity path): a foreign index follows its own market, not
        # the US SP500 proxy used for the `equity` class.
        return "commodity"
    if bare in _EQUITY_COINS:
        return "equity"
    if bare in _native_crypto_tickers():
        return "crypto"
    if namespaced:
        return "equity"
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
        # FIX #3 (2026-06-02 audit): equities previously ALL inherited one proxy
        # (xyz:SP500). That mis-gated individual names — e.g. a stock ripping while
        # SP500 was flat got "neutral/down" and its long was blocked. Now each
        # equity is gated by ITS OWN trend; SP500 is only the fallback when the
        # name's own candles are missing/thin (off-hours). Best of both: per-name
        # accuracy with a macro safety net.
        now = time.time()
        cached_own = _regime_cache.get(coin)
        if not force and cached_own and (now - cached_own[1]) < REGIME_TTL_S:
            if cached_own[0] != "neutral":
                return cached_own[0]
        else:
            own = _detect_for_proxy(coin)  # the coin's OWN 1h candles
            _regime_cache[coin] = (own, time.time())
            if own != "neutral":
                return own
        proxy = EQUITY_PROXY  # own-trend unclear/thin -> fall back to macro
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
