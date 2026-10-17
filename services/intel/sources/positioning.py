"""CFTC Commitments of Traders via the public Socrata API. HL funding/OI
live in DeskAnalytics already (services/strategies) — duplicating those
here would violate the one-concern-one-directory rule, so this module is
COT only.

Verified live 2026-10-17: publicreporting.cftc.gov/resource/6dca-aqww.json
(Legacy Futures Only Reports) — no key, HTTP 200. Contract codes confirmed
by querying the same dataset for `market_and_exchange_names`:
  WTI    067651  "WTI-PHYSICAL - NEW YORK MERCANTILE EXCHANGE"   (given)
  Gold   088691  "GOLD - COMMODITY EXCHANGE INC."                (found)
  BTC    133741  "BITCOIN - CHICAGO MERCANTILE EXCHANGE"         (found;
         CME's cash-settled contract — the one the brief means by
         "CME bitcoin")
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional, Tuple

from contracts.strategies.models import DeskSpec, PositioningItem
from services.intel import http_client
from services.intel.textnorm import stable_id

COT_URL = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"

# (match keywords against instrument label/coin, case-insensitive) -> (code, friendly label)
_CONTRACTS = (
    (("wti", "cl"), ("067651", "WTI non-commercial net long")),
    (("gold", "xau"), ("088691", "Gold non-commercial net long")),
    (("bitcoin", "btc"), ("133741", "CME Bitcoin non-commercial net long")),
)


def _matched_contracts(spec: DeskSpec) -> List[Tuple[str, str]]:
    hay_tokens = set()
    for i in spec.instruments:
        hay_tokens.add(i.label.lower())
        hay_tokens.add(i.coin.split(":", 1)[-1].lower())
    out = []
    seen_codes = set()
    for keywords, (code, label) in _CONTRACTS:
        if code in seen_codes:
            continue
        if any(kw in tok for kw in keywords for tok in hay_tokens):
            out.append((code, label))
            seen_codes.add(code)
    return out


def _safe_float(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def fetch_cot(spec: DeskSpec) -> Tuple[List[PositioningItem], Optional[str]]:
    contracts = _matched_contracts(spec)
    if not contracts:
        return [], None
    out: List[PositioningItem] = []
    errors = []
    for code, label in contracts:
        data, err = http_client.fetch_json(COT_URL, params={
            "$where": f"cftc_contract_market_code='{code}'",
            "$order": "report_date_as_yyyy_mm_dd DESC",
            "$limit": 1,
        })
        if err:
            errors.append(f"positioning:cftc_cot:{code}:{err}")
            continue
        if not isinstance(data, list) or not data:
            errors.append(f"positioning:cftc_cot:{code}:no_rows")
            continue
        row = data[0]
        net_long = _safe_float(row.get("noncomm_positions_long_all")) - \
            _safe_float(row.get("noncomm_positions_short_all"))
        net_long_change = _safe_float(row.get("change_in_noncomm_long_all")) - \
            _safe_float(row.get("change_in_noncomm_short_all"))
        report_date = row.get("report_date_as_yyyy_mm_dd")
        as_of = datetime.now(timezone.utc)
        if report_date:
            try:
                as_of = datetime.strptime(report_date[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                pass
        out.append(PositioningItem(
            id=stable_id("positioning", "cftc_cot", code, report_date or ""),
            source="cftc_cot", label=label, value=net_long, change=net_long_change, as_of=as_of,
        ))
    return out, ("; ".join(errors) if errors else None)


# api.py fans this out to its own thread pool — see that module's 15s
# cold-build budget note. A tuple of one so the same fan-out code in
# api.py works uniformly across every sources/*.py module.
ATOMIC_FETCHERS = (fetch_cot,)


def fetch_positioning(spec: DeskSpec, now: datetime) -> Tuple[List[PositioningItem], List[str]]:
    items, err = fetch_cot(spec)
    return items, ([err] if err else [])
