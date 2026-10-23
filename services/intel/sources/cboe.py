"""CBOE volatility term structure: VIX, VIX9D, VIX3M daily closes, and the
9D/3M slope as the fear-regime read (slope < 1 = contango/calm, >= 1 =
backwardation/fear — short-dated vol pricing above medium-dated). No key.

Verified live 2026-10-23: cdn.cboe.com/api/global/us_indices/daily_prices/
{VIX,VIX9D,VIX3M}_History.csv all 307-redirect to cdn-api.cboe.com and
return `DATE,OPEN,HIGH,LOW,CLOSE` CSV, `MM/DD/YYYY` dates, oldest-first
(VIX back to 1990, VIX9D to 2011, VIX3M to 2009) — httpx's
`follow_redirects=True` (services/intel/http_client.py) handles the
redirect transparently, no special-casing needed here.

Only fires for indices and AI-equity desks (`desk_rules.is_indices_desk` /
`is_equity_desk`) per the brief's desk-relevance mapping.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from contracts.strategies.models import DataPoint, DeskSpec
from services.intel import desk_rules, http_client
from services.intel.textnorm import stable_id

CBOE_CSV_URL_FMT = "https://cdn.cboe.com/api/global/us_indices/daily_prices/{sym}_History.csv"
SYMBOLS = ("VIX", "VIX9D", "VIX3M")
FEAR_SLOPE_THRESHOLD = 1.0


def parse_cboe_csv(text: str) -> List[Tuple[datetime, float]]:
    """[(date, close), ...] oldest-first, exactly the file's own row order —
    callers index from the end for "latest"/"prior". Tolerant of a
    trailing blank line; raises nothing a caller can't recover from (a
    genuinely malformed row is just skipped, consistent with every other
    parser in this service never sinking the whole packet on one bad row)."""
    out: List[Tuple[datetime, float]] = []
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        date_raw, close_raw = row.get("DATE"), row.get("CLOSE")
        if not date_raw or not close_raw:
            continue
        try:
            dt = datetime.strptime(date_raw.strip(), "%m/%d/%Y").replace(tzinfo=timezone.utc)
            close = float(close_raw)
        except (ValueError, TypeError):
            continue
        out.append((dt, close))
    return out


def fetch_symbol_series(symbol: str) -> Tuple[Optional[List[Tuple[datetime, float]]], Optional[str]]:
    url = CBOE_CSV_URL_FMT.format(sym=symbol)
    text, err = http_client.fetch_text(url, timeout_s=8.0)
    if err:
        return None, f"cboe:{symbol}:{err}"
    series = parse_cboe_csv(text)
    if len(series) < 2:
        return None, f"cboe:{symbol}:insufficient_rows"
    return series, None


def fetch_cboe(spec: DeskSpec) -> Tuple[List[DataPoint], Optional[str]]:
    if not (desk_rules.is_indices_desk(spec) or desk_rules.is_equity_desk(spec)):
        return [], None

    out: List[DataPoint] = []
    errors: List[str] = []
    latest: dict = {}  # symbol -> (as_of, close)
    for symbol in SYMBOLS:
        series, err = fetch_symbol_series(symbol)
        if err:
            errors.append(err)
            continue
        (_prior_dt, prior_close), (latest_dt, close) = series[-2], series[-1]
        change = close - prior_close
        latest[symbol] = (latest_dt, close)
        out.append(DataPoint(
            id=stable_id("cboe", symbol, latest_dt.isoformat()),
            source="cboe", label=symbol, value=close, prior=prior_close, change=change,
            unit="index_pts", as_of=latest_dt, note=f"day chg {change:+.2f}", relevance=0.55,
        ))

    if "VIX9D" in latest and "VIX3M" in latest and latest["VIX3M"][1] != 0:
        vix9d_dt, vix9d_close = latest["VIX9D"]
        _vix3m_dt, vix3m_close = latest["VIX3M"]
        slope = vix9d_close / vix3m_close
        regime = "backwardation (fear)" if slope >= FEAR_SLOPE_THRESHOLD else "contango (calm)"
        out.append(DataPoint(
            id=stable_id("cboe", "vix_term_slope", vix9d_dt.isoformat()),
            source="cboe", label="VIX term structure slope (9D/3M)", value=slope,
            prior=None, change=None, unit="ratio", as_of=vix9d_dt, note=regime,
            relevance=0.75 if slope >= FEAR_SLOPE_THRESHOLD else 0.5,
        ))

    return out, ("; ".join(errors) if errors else None)


ATOMIC_FETCHERS = (fetch_cboe,)
