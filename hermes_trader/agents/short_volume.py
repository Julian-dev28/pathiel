"""FREE short-interest pressure signal — our own build of the Unusual Whales
"short volume / dark-pool short" analytics, with NO paid feed.

Data source: FINRA's free daily Reg SHO short-sale volume files. FINRA publishes,
every trading day, the consolidated (CNMS) short vs total executed volume per
symbol — the raw input behind every "short volume %" product. Free, no auth:
    https://cdn.finra.org/equity/regsho/daily/CNMSshvol{YYYYMMDD}.txt
Pipe-delimited: Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market

What it produces, for any equity (and our xyz HIP-3 perps that track one):
  - short_volume_ratio = ShortVolume / TotalVolume for the latest available day.
  - regime: a HIGH ratio (crowded short / heavy short-side flow) is squeeze FUEL
    for a long — exactly the ripper setup; a LOW ratio = little short pressure.
  - a short series (last N days) so a RISING ratio (shorts piling in) is visible.

NOTE: this is daily EXECUTED short volume (a flow proxy), not bi-monthly reported
short INTEREST. It's the same series UW surfaces as "short volume" and updates
daily, which is what we want for a live squeeze read.

PURE compute functions (testable) + thin cached fetch. Nothing here trades; it's
the signal product. Wiring into perception/override is a separate, gated step.
"""

from __future__ import annotations

import logging
import ssl
import threading
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from hermes_trader.agents.options_gex import underlying_for  # xyz: -> underlying

logger = logging.getLogger(__name__)

try:
    import certifi
    _SSL = ssl.create_default_context(cafile=certifi.where())
except Exception:                     # pragma: no cover
    _SSL = ssl._create_unverified_context()

_BASE = "https://cdn.finra.org/equity/regsho/daily/CNMSshvol{date}.txt"


@dataclass(frozen=True)
class ShortVolDay:
    date: str            # YYYYMMDD
    symbol: str
    short_vol: float
    short_exempt: float
    total_vol: float

    @property
    def ratio(self) -> float:
        return (self.short_vol + self.short_exempt) / self.total_vol if self.total_vol > 0 else 0.0


@dataclass(frozen=True)
class ShortVolReport:
    symbol: str
    date: str
    ratio: float            # latest-day short volume / total
    regime: str             # "crowded_short_squeeze_fuel" | "neutral" | "light_short"
    trend: str              # "rising" | "falling" | "flat" | "n/a"
    series: List[float]     # oldest -> newest ratios
    note: str = ""


def parse_finra_shvol(text: str, want_symbol: Optional[str] = None) -> List[ShortVolDay]:
    """Parse one FINRA CNMS daily short-volume file. If `want_symbol` is given,
    return only that symbol's row(s)."""
    rows: List[ShortVolDay] = []
    want = want_symbol.upper() if want_symbol else None
    for line in text.splitlines():
        parts = line.split("|")
        if len(parts) < 5 or parts[0].strip().lower() == "date":
            continue                       # header / footer / blank
        date, sym = parts[0].strip(), parts[1].strip().upper()
        if not date.isdigit():
            continue                       # footer line ("Records: N")
        if want and sym != want:
            continue
        try:
            rows.append(ShortVolDay(
                date=date, symbol=sym,
                short_vol=float(parts[2] or 0),
                short_exempt=float(parts[3] or 0),
                total_vol=float(parts[4] or 0),
            ))
        except (ValueError, IndexError):
            continue
    return rows


def classify_short_regime(ratio: float) -> str:
    # FINRA consolidated short volume routinely runs ~40-50% market-wide, so the
    # squeeze-relevant band is the UPPER tail.
    if ratio >= 0.60:
        return "crowded_short_squeeze_fuel"
    if ratio <= 0.35:
        return "light_short"
    return "neutral"


def _trend(series: List[float]) -> str:
    if len(series) < 2:
        return "n/a"
    d = series[-1] - series[0]
    if d > 0.03:
        return "rising"
    if d < -0.03:
        return "falling"
    return "flat"


def build_report(symbol: str, days: List[ShortVolDay]) -> Optional[ShortVolReport]:
    """Assemble a report from per-day rows (oldest->newest expected; we sort)."""
    days = sorted([d for d in days if d.symbol == symbol.upper()], key=lambda d: d.date)
    if not days:
        return None
    series = [round(d.ratio, 4) for d in days]
    latest = days[-1]
    regime = classify_short_regime(latest.ratio)
    return ShortVolReport(
        symbol=symbol.upper(), date=latest.date, ratio=round(latest.ratio, 4),
        regime=regime, trend=_trend(series), series=series,
        note=("crowded short — squeeze fuel for a long" if regime == "crowded_short_squeeze_fuel"
              else "little short pressure" if regime == "light_short" else ""),
    )


# ── thin cached fetch ────────────────────────────────────────────────────────
_CACHE_TTL_S = 3600.0          # the file is daily; an hour is plenty
_cache: Dict[str, tuple] = {}  # symbol -> (epoch, ShortVolReport|None)
_lock = threading.Lock()


def _fetch_day(date: str, timeout: float = 12.0) -> Optional[str]:
    url = _BASE.format(date=date)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL) as r:
            return r.read().decode("utf-8", "replace")
    except Exception:
        return None


def short_volume_signal(coin_or_ticker: str, lookback_days: int = 5,
                        ttl: float = _CACHE_TTL_S,
                        allow_fetch: bool = True) -> Optional[ShortVolReport]:
    """Free short-volume report for an equity/index or xyz: perp. Walks back from
    today over `lookback_days` trading days, skipping weekends/holidays (missing
    files just 404 and are skipped). Cached per symbol.

    allow_fetch=False = CACHE-ONLY (return a fresh cached value or None, no network)."""
    symbol = underlying_for(coin_or_ticker)
    # FINRA files key on the plain ticker, not CBOE's "_SPX" index form.
    symbol = symbol.lstrip("_")
    now = time.time()
    with _lock:
        hit = _cache.get(symbol)
        if hit and (now - hit[0]) < ttl:
            return hit[1]
    if not allow_fetch:
        return None

    rows: List[ShortVolDay] = []
    d = datetime.now(timezone.utc).date()
    checked = 0
    while len(rows) < lookback_days and checked < lookback_days + 6:
        if d.weekday() < 5:            # Mon-Fri only
            txt = _fetch_day(d.strftime("%Y%m%d"))
            if txt:
                rows.extend(parse_finra_shvol(txt, want_symbol=symbol))
        d -= timedelta(days=1)
        checked += 1

    rep = build_report(symbol, rows) if rows else None
    with _lock:
        _cache[symbol] = (now, rep)
    return rep
