"""FRED rates/dollar series — DGS10 (10y nominal yield), DFII10 (10y real
yield), T10YIE (10y breakeven inflation), DTWEXBGS (broad trade-weighted
dollar index) — via the free FRED API. Needs `FRED_API_KEY` — register
free at https://fred.stlouisfed.org/docs/api/api_key.html. A missing key
is reported cleanly, never silently empty, same pattern as
sources/policy.py's `fetch_congress_bills` and sources/eia.py.

Verified live 2026-10-23 WITHOUT a key: FRED returns
`{"error_code": 400, "error_message": "Bad Request. Variable api_key is
not set. ..."}` — confirms the missing-key path, not a guess. No live
key was available in this sandbox to capture a real *successful*
response; the success-path fixture is built from FRED's documented
`/fred/series/observations` schema (`{"observations": [{"date",
"value"}, ...]}`, missing trading days are "." per FRED's own docs) —
flagged the same way eia.py and policy.py flag their no-key fixtures.

Only fires for metals and indices desks (`desk_rules.is_metals_desk` /
`is_indices_desk`) per the brief's desk-relevance mapping.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from contracts.strategies.models import DataPoint, DeskSpec
from services.intel import desk_rules, http_client
from services.intel.textnorm import stable_id

FRED_OBSERVATIONS_URL = "https://api.stlouisfed.org/fred/series/observations"
# series_id -> (label, unit)
SERIES: Dict[str, Tuple[str, str]] = {
    "DGS10": ("10y Treasury yield", "pct"),
    "DFII10": ("10y real yield (TIPS)", "pct"),
    "T10YIE": ("10y breakeven inflation", "pct"),
    "DTWEXBGS": ("Broad dollar index", "index_pts"),
}
CHANGE_LOOKBACK_OBS = 5   # "5-day change" per the brief — 5 trading-day observations back
OBS_LIMIT = 15            # enough recent obs to skip holiday "." gaps and still find 5 valid


def _api_key() -> Optional[str]:
    return os.environ.get("FRED_API_KEY")


def _safe_float(v: Any) -> Optional[float]:
    if v in (None, ".", ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fetch_series(series_id: str, api_key: str) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
    data, err = http_client.fetch_json(FRED_OBSERVATIONS_URL, params={
        "series_id": series_id, "api_key": api_key, "file_type": "json",
        "sort_order": "desc", "limit": OBS_LIMIT,
    }, timeout_s=8.0)
    if err:
        return None, err
    if not isinstance(data, dict) or not isinstance(data.get("observations"), list):
        return None, "bad_shape"
    return data["observations"], None


def _to_datapoint(series_id: str, observations: List[Dict[str, Any]]) -> Optional[DataPoint]:
    label, unit = SERIES[series_id]
    valid = [(o.get("date"), _safe_float(o.get("value"))) for o in observations]
    valid = [(d, v) for d, v in valid if v is not None]
    if not valid:
        return None
    latest_date, latest_value = valid[0]
    prior_value = valid[CHANGE_LOOKBACK_OBS][1] if len(valid) > CHANGE_LOOKBACK_OBS else None
    change = (latest_value - prior_value) if prior_value is not None else None
    try:
        as_of = datetime.strptime(latest_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        as_of = datetime.now(timezone.utc)
    note = (f"5d change {change:+.3f}" if change is not None
            else "insufficient history for a 5d change")
    return DataPoint(
        id=stable_id("fred", series_id, latest_date or ""),
        source="fred", label=label, value=latest_value, prior=prior_value,
        change=change, unit=unit, as_of=as_of, note=note, relevance=0.6,
    )


def fetch_fred(spec: DeskSpec) -> Tuple[List[DataPoint], Optional[str]]:
    if not (desk_rules.is_metals_desk(spec) or desk_rules.is_indices_desk(spec)):
        return [], None
    api_key = _api_key()
    if not api_key:
        return [], "fred:FRED_API_KEY_missing"
    out: List[DataPoint] = []
    errors: List[str] = []
    for series_id in SERIES:
        observations, err = fetch_series(series_id, api_key)
        if err:
            errors.append(f"fred:{series_id}:{err}")
            continue
        dp = _to_datapoint(series_id, observations)
        if dp is not None:
            out.append(dp)
        else:
            errors.append(f"fred:{series_id}:no_valid_observations")
    return out, ("; ".join(errors) if errors else None)


ATOMIC_FETCHERS = (fetch_fred,)
