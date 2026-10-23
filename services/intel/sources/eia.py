"""EIA weekly crude stocks and natural gas storage changes, via the free
EIA v2 API (https://api.eia.gov/v2). Needs `EIA_API_KEY` — register free at
https://www.eia.gov/opendata/register.php. A missing key is reported
cleanly, same pattern as sources/policy.py's `fetch_congress_bills`, never
silently empty.

Verified live 2026-10-23 WITHOUT a key: both endpoints below return the
documented `{"error": {"code": "API_KEY_MISSING", ...}}` shape at HTTP
403 — confirms the missing-key path is really what EIA returns, not a
guess. No live key was available in this sandbox to capture a real
*successful* response, so the success-path fixture is built from EIA
v2's documented `/data/` endpoint schema (every `/data` query returns
`{"response": {"data": [{"period", "value", ...}, ...]}}`) rather than a
live capture — flagged exactly like policy.py flags its congress.gov
fixture for the same reason.

`fetch_crude_stocks` fires for the oil desk only. `fetch_natgas_storage`
also fires for the dedicated "gas" desk added on overhaul-2026-10-17
alongside this branch (services/strategies/desks/gas.py) — its own
primary edge is literally the weekly EIA natural gas storage number, so
excluding it here (its id/labels have no "oil"/"wti"/"brent"/"crude"
token, the thing `is_oil_desk` alone checks for) would miss the desk this
number matters to most. See `desk_rules.is_natgas_desk`.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from statistics import fmean
from typing import Any, Dict, List, Optional, Tuple

from contracts.strategies.models import DataPoint, DeskSpec
from services.intel import desk_rules, http_client
from services.intel.textnorm import stable_id

EIA_BASE = "https://api.eia.gov/v2"
CRUDE_STOCKS_URL = f"{EIA_BASE}/petroleum/stoc/wstk/data/"
NATGAS_STORAGE_URL = f"{EIA_BASE}/natural-gas/stor/wkly/data/"
CRUDE_STOCKS_SERIES = "WCRSTUS1"                 # weekly US crude stocks, thousand bbl
NATGAS_STORAGE_SERIES = "NW2_EPG0_SWO_R48_BCF"   # weekly Lower-48 working gas in storage, Bcf

# 4 prior changes to average + the latest change = 5 changes = 6 weekly levels.
TRAILING_WEEKS_FOR_AVG = 4
LEVELS_NEEDED = TRAILING_WEEKS_FOR_AVG + 2


def _api_key() -> Optional[str]:
    return os.environ.get("EIA_API_KEY")


def _fetch_series(url: str, series: str, api_key: str
                   ) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
    params = {
        "frequency": "weekly", "data[0]": "value", "facets[series][]": series,
        "sort[0][column]": "period", "sort[0][direction]": "desc",
        "length": LEVELS_NEEDED, "api_key": api_key,
    }
    data, err = http_client.fetch_json(url, params=params, timeout_s=8.0)
    if err:
        return None, err
    if not isinstance(data, dict) or not isinstance(data.get("response"), dict):
        return None, "bad_shape"
    rows = data["response"].get("data")
    if not isinstance(rows, list) or not rows:
        return None, "no_rows"
    return rows, None


def _safe_float(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _rows_to_datapoint(rows: List[Dict[str, Any]], *, label: str, unit: str,
                        unit_scale: float, dp_id_source: str) -> Optional[DataPoint]:
    """`rows` newest period first, each `{"period": "YYYY-MM-DD", "value": "..."}`.
    EIA's series are LEVELS (e.g. total barrels in storage); the "weekly
    change" the brief wants is value[t] - value[t-1], computed here."""
    levels = [_safe_float(r.get("value")) for r in rows]
    levels = [v for v in levels if v is not None]
    if len(levels) < 2:
        return None
    changes = [(levels[i] - levels[i + 1]) * unit_scale for i in range(len(levels) - 1)]
    latest_change, prior_change = changes[0], (changes[1] if len(changes) > 1 else None)
    trailing = changes[1:1 + TRAILING_WEEKS_FOR_AVG]
    avg4 = fmean(trailing) if trailing else None
    surprise = (latest_change - avg4) if avg4 is not None else None
    period = rows[0].get("period")
    try:
        as_of = datetime.strptime(period, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        as_of = datetime.now(timezone.utc)
    note = (f"surprise {surprise:+.2f} vs 4wk avg chg {avg4:+.2f}"
            if surprise is not None else "insufficient history for a 4wk average")
    return DataPoint(
        id=stable_id("eia", dp_id_source, period or ""),
        source="eia", label=f"{label} weekly change", value=latest_change,
        prior=prior_change, change=surprise, unit=unit, as_of=as_of, note=note,
        relevance=0.85 if surprise is not None and abs(surprise) > abs(avg4 or 0) else 0.7,
    )


def fetch_crude_stocks(spec: DeskSpec) -> Tuple[List[DataPoint], Optional[str]]:
    if not desk_rules.is_oil_desk(spec):
        return [], None
    api_key = _api_key()
    if not api_key:
        return [], "eia:crude_stocks:EIA_API_KEY_missing"
    rows, err = _fetch_series(CRUDE_STOCKS_URL, CRUDE_STOCKS_SERIES, api_key)
    if err:
        return [], f"eia:crude_stocks:{err}"
    dp = _rows_to_datapoint(rows, label="EIA crude stocks", unit="mm_bbl",
                             unit_scale=1.0 / 1000.0, dp_id_source="crude_stocks")
    if dp is None:
        return [], "eia:crude_stocks:insufficient_history"
    return [dp], None


def fetch_natgas_storage(spec: DeskSpec) -> Tuple[List[DataPoint], Optional[str]]:
    if not desk_rules.is_natgas_desk(spec):
        return [], None
    api_key = _api_key()
    if not api_key:
        return [], "eia:natgas_storage:EIA_API_KEY_missing"
    rows, err = _fetch_series(NATGAS_STORAGE_URL, NATGAS_STORAGE_SERIES, api_key)
    if err:
        return [], f"eia:natgas_storage:{err}"
    dp = _rows_to_datapoint(rows, label="EIA natural gas storage", unit="bcf",
                             unit_scale=1.0, dp_id_source="natgas_storage")
    if dp is None:
        return [], "eia:natgas_storage:insufficient_history"
    return [dp], None


ATOMIC_FETCHERS = (fetch_crude_stocks, fetch_natgas_storage)


def fetch_eia(spec: DeskSpec) -> Tuple[List[DataPoint], List[str]]:
    """Convenience sequential path — see sources/news.py's fetch_headlines
    docstring for why this exists alongside ATOMIC_FETCHERS."""
    out: List[DataPoint] = []
    failed: List[str] = []
    for fn in ATOMIC_FETCHERS:
        items, err = fn(spec)
        if err:
            failed.append(err)
        out.extend(items)
    return out, failed
