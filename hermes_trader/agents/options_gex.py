"""FREE options-positioning signals — our own build of the Unusual Whales GEX /
max-pain / gamma-wall analytics, with NO paid feed.

Data source: CBOE's free delayed (~15min) options JSON — which already carries
per-contract gamma, delta, IV and open interest, so dealer gamma exposure is
DIRECTLY computable. 15-min delay is irrelevant for this signal: dealer gamma
positioning is a structural/daily map (where price gets pinned vs where it runs),
not a tick signal.

What it produces, for any equity/index underlying (and our xyz HIP-3 perps which
track them):
  - total GEX + regime: positive = dealers long gamma = PIN/mean-revert/low-vol;
    negative = dealers short gamma = TREND/amplify/squeeze-prone.
  - call wall (overhead resistance) + put wall (support) = the gamma strikes price
    tends to magnet to / stall at.
  - gamma flip = spot level separating the pin regime from the trend regime.
  - max pain = the expiry pin price.

PURE compute functions (testable) + a thin fetch. Nothing here trades; it's the
signal product. Wiring into perception/override is a separate, gated step.
"""

from __future__ import annotations

import json
import logging
import re
import ssl
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:                                  # proper CA bundle (Mac python often lacks one)
    import certifi
    _SSL = ssl.create_default_context(cafile=certifi.where())
except Exception:                     # pragma: no cover
    _SSL = ssl._create_unverified_context()

_OCC = re.compile(r"^([A-Z\^_.]+)(\d{6})([CP])(\d{8})$")
_CONTRACT_MULT = 100

# xyz HIP-3 perp namespace → real options underlying on CBOE.
# Strip "xyz:" and map the few that differ from their plain ticker; default = ticker.
_XYZ_TICKER = {
    "SP500": "_SPX", "SPX": "_SPX", "NDX": "_NDX", "GOLD": "GLD", "SILVER": "SLV",
    "OIL": "USO", "COPPER": "CPER",
}


def underlying_for(coin: str) -> str:
    """xyz:NVDA -> NVDA ; xyz:SP500 -> _SPX ; NVDA -> NVDA."""
    t = coin.split(":", 1)[1] if ":" in coin else coin
    return _XYZ_TICKER.get(t.upper(), t.upper())


@dataclass(frozen=True)
class OptRow:
    strike: float
    is_call: bool
    oi: float
    gamma: float
    delta: float
    expiry: str  # YYMMDD


@dataclass(frozen=True)
class GexReport:
    ticker: str
    spot: float
    total_gex: float          # $ per 1% move (millions), signed
    regime: str               # "pin_long_gamma" | "trend_short_gamma"
    gamma_flip: Optional[float]
    call_wall: Optional[float]   # overhead resistance strike
    put_wall: Optional[float]    # support strike
    max_pain: Optional[float]
    n_contracts: int
    note: str = ""


def parse_occ(sym: str) -> Optional[tuple]:
    m = _OCC.match(sym)
    if not m:
        return None
    root, yymmdd, cp, strike8 = m.groups()
    return root, yymmdd, (cp == "C"), int(strike8) / 1000.0


def rows_from_cboe(payload: Dict[str, Any]) -> tuple:
    """(spot, [OptRow]) from a CBOE delayed_quotes/options JSON payload."""
    data = payload.get("data", {}) or {}
    spot = float(data.get("current_price") or 0)
    rows: List[OptRow] = []
    for o in (data.get("options") or []):
        p = parse_occ(o.get("option", ""))
        if not p:
            continue
        _root, yymmdd, is_call, strike = p
        rows.append(OptRow(
            strike=strike, is_call=is_call,
            oi=float(o.get("open_interest") or 0),
            gamma=float(o.get("gamma") or 0),
            delta=float(o.get("delta") or 0),
            expiry=yymmdd,
        ))
    return spot, rows


def _gex_dollars(gamma: float, oi: float, spot: float) -> float:
    # $ gamma exposure per 1% move = gamma * OI * 100 * spot^2 * 0.01
    return gamma * oi * _CONTRACT_MULT * spot * spot * 0.01


def compute_gex(rows: List[OptRow], spot: float) -> Dict[str, Any]:
    """Net dealer GEX (SqueezeMetrics convention: call gamma +, put gamma −,
    i.e. dealers long calls / short puts). Per-strike + total + walls + flip.
    Returns dollars in MILLIONS."""
    if spot <= 0 or not rows:
        return {"total": 0.0, "by_strike": {}, "call_wall": None, "put_wall": None,
                "gamma_flip": None, "regime": "unknown"}
    by_strike: Dict[float, float] = {}
    call_gex: Dict[float, float] = {}
    put_gex: Dict[float, float] = {}
    for r in rows:
        g = _gex_dollars(r.gamma, r.oi, spot)
        if r.is_call:
            by_strike[r.strike] = by_strike.get(r.strike, 0.0) + g
            call_gex[r.strike] = call_gex.get(r.strike, 0.0) + g
        else:
            by_strike[r.strike] = by_strike.get(r.strike, 0.0) - g
            put_gex[r.strike] = put_gex.get(r.strike, 0.0) + g  # magnitude
    total = sum(by_strike.values())
    # Walls: biggest call-OI gamma above spot (resistance), biggest put gamma below (support)
    call_wall = max((k for k in call_gex if k >= spot),
                    key=lambda k: call_gex[k], default=None)
    put_wall = max((k for k in put_gex if k <= spot),
                   key=lambda k: put_gex[k], default=None)
    # Gamma flip: strike where cumulative net GEX (ascending strikes) crosses zero.
    flip = None
    cum = 0.0
    for k in sorted(by_strike):
        prev = cum
        cum += by_strike[k]
        if prev < 0 <= cum or prev > 0 >= cum:
            flip = k
            break
    M = 1e6
    return {
        "total": total / M,
        "by_strike": {k: v / M for k, v in by_strike.items()},
        "call_wall": call_wall, "put_wall": put_wall, "gamma_flip": flip,
        "regime": "pin_long_gamma" if total >= 0 else "trend_short_gamma",
    }


def compute_max_pain(rows: List[OptRow], nearest_expiry_only: bool = True) -> Optional[float]:
    """Strike that minimizes total option-holder intrinsic value (writers' pin)."""
    if not rows:
        return None
    if nearest_expiry_only:
        exp = min(r.expiry for r in rows)
        rows = [r for r in rows if r.expiry == exp]
    strikes = sorted({r.strike for r in rows})
    if not strikes:
        return None
    best_k, best_pain = None, None
    for K in strikes:
        pain = 0.0
        for r in rows:
            if r.is_call and K > r.strike:
                pain += r.oi * (K - r.strike)
            elif (not r.is_call) and K < r.strike:
                pain += r.oi * (r.strike - K)
        if best_pain is None or pain < best_pain:
            best_pain, best_k = pain, K
    return best_k


def fetch_cboe(ticker: str, timeout: float = 12.0) -> Optional[Dict[str, Any]]:
    url = f"https://cdn.cboe.com/api/global/delayed_quotes/options/{ticker}.json"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        return json.load(urllib.request.urlopen(req, timeout=timeout, context=_SSL))
    except Exception:
        return None


def gex_signal(coin_or_ticker: str) -> Optional[GexReport]:
    """End-to-end free GEX/max-pain report for an equity/index or xyz: perp."""
    ticker = underlying_for(coin_or_ticker)
    payload = fetch_cboe(ticker)
    if not payload:
        return None
    spot, rows = rows_from_cboe(payload)
    if spot <= 0 or not rows:
        return None
    g = compute_gex(rows, spot)
    mp = compute_max_pain(rows)
    return GexReport(
        ticker=ticker, spot=spot, total_gex=round(g["total"], 1), regime=g["regime"],
        gamma_flip=g["gamma_flip"], call_wall=g["call_wall"], put_wall=g["put_wall"],
        max_pain=mp, n_contracts=len(rows),
        note=("spot ABOVE gamma-flip → pin/mean-revert" if g["gamma_flip"] and spot >= g["gamma_flip"]
              else "spot BELOW gamma-flip → trend/squeeze-prone" if g["gamma_flip"]
              else ""),
    )


# ── Live wiring: TTL-cached signal + forced-override caution ──────────────────
# The CBOE fetch is a network call, so it must NEVER run on the per-execute hot
# path uncached (the ATR-sizing API-amplification lesson). Dealer gamma is a
# structural/daily map on a ~15-min delayed feed, so a 15-min cache is lossless.
_GEX_TTL_S = 900.0
_gex_cache: Dict[str, tuple] = {}      # ticker -> (epoch, GexReport|None)
_gex_lock = threading.Lock()


def gex_signal_cached(coin_or_ticker: str, ttl: float = _GEX_TTL_S,
                      allow_fetch: bool = True) -> Optional[GexReport]:
    """gex_signal() with a process-wide TTL cache. Caches misses too (as None)
    so a CBOE outage can't hammer the hot path. Thread-safe.

    allow_fetch=False = CACHE-ONLY: return a fresh cached value (or None) WITHOUT
    any network call — for the execute hot path, which must never fetch."""
    ticker = underlying_for(coin_or_ticker)
    now = time.time()
    with _gex_lock:
        hit = _gex_cache.get(ticker)
        if hit and (now - hit[0]) < ttl:
            return hit[1]
    if not allow_fetch:
        return None
    rep = None
    try:
        rep = gex_signal(coin_or_ticker)
    except Exception as e:                                # pragma: no cover
        logger.warning(f"[gex] fetch failed for {ticker}: {e}")
    with _gex_lock:
        _gex_cache[ticker] = (now, rep)
    return rep


def gex_override_caution(coin: str, side: str, near_wall_pct: float = 1.0,
                         ttl: float = _GEX_TTL_S, allow_fetch: bool = True) -> tuple:
    """Should a FORCED structural-override LONG on this xyz name be suppressed?

    Returns (suppress: bool, reason: str). Conservative + always-safe:
      - only meaningful for xyz: equity perps on the LONG side; everything else
        (crypto, shorts, no free options data) returns (False, "") so callers
        can consult it unconditionally.
      - suppresses ONLY the clearest chop-trap: dealers long gamma (pin regime),
        spot already in the pin zone (>= gamma_flip), AND spot jammed within
        `near_wall_pct`% below the call wall (overhead dealer resistance). That
        is a mean-revert pin, not a ripper — exactly the setup that turns a
        forced override into a dud. AI-conviction LONGs never reach this.
    """
    if ":" not in (coin or "") or (side or "").lower() != "long":
        return (False, "")
    rep = gex_signal_cached(coin, ttl=ttl, allow_fetch=allow_fetch)
    if not rep or rep.regime != "pin_long_gamma":
        return (False, "")
    if rep.gamma_flip is not None and rep.spot < rep.gamma_flip:
        return (False, "")  # below flip = trend/squeeze-prone → let it run
    if rep.call_wall and rep.spot > 0:
        gap_pct = (rep.call_wall - rep.spot) / rep.spot * 100.0
        if 0 <= gap_pct <= near_wall_pct:
            return (True, (f"GEX pin-trap: {rep.ticker} long-gamma, spot {rep.spot:g} "
                           f"jammed {gap_pct:.2f}% under call wall {rep.call_wall:g} "
                           f"(overhead dealer resistance)"))
    return (False, "")
