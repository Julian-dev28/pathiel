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

Re-verified live 2026-10-23, four more codes added per the intel-hard-data
brief (every desk gets COT, not just oil/WTI):
  Silver      084691  "SILVER - COMMODITY EXCHANGE INC."              (COMEX,
              same exchange suffix pattern as Gold above — the metals desk's
              other instrument)
  Nat gas     023651  "NATURAL GAS - NEW YORK MERCANTILE EXCHANGE"    (NYMEX
              Henry Hub, the standard contract — not the thin "E-MINI
              NATURAL GAS" 023655 or any of the ICE basis contracts)
  S&P 500     13874A  "E-MINI S&P 500 STOCK INDEX - CHICAGO MERCANTILE
  e-mini              EXCHANGE" (not the sector e-minis or the MICRO
              contract, which are separate, much thinner series)
  Nasdaq-100  209742  "NASDAQ-100 STOCK INDEX (MINI) - CHICAGO MERCANTILE
  e-mini              EXCHANGE" (the standard e-mini; 209747's MICRO
              contract and the unconsolidated 209741 full-size series are
              both excluded the same way)
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from contracts.strategies.models import DeskSpec, PositioningItem
from services.intel import http_client
from services.intel.textnorm import stable_id

COT_URL = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"

# (match keywords against instrument label/coin, case-insensitive) -> (code, friendly label)
_CONTRACTS = (
    (("wti", "cl"), ("067651", "WTI non-commercial net long")),
    (("gold", "xau"), ("088691", "Gold non-commercial net long")),
    (("bitcoin", "btc"), ("133741", "CME Bitcoin non-commercial net long")),
    (("silver", "xag"), ("084691", "Silver non-commercial net long")),
    (("natgas", "nat gas"), ("023651", "Natural gas non-commercial net long")),
    (("sp500", "s&p 500"), ("13874A", "S&P 500 e-mini non-commercial net long")),
    (("nasdaq", "ndx"), ("209742", "Nasdaq-100 e-mini non-commercial net long")),
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


# 52 weekly reports (a year) plus the current one; every contract's percentile
# is "best-effort over however many weeks the API actually returns" — a
# freshly-listed or thin contract with fewer than 52 history rows still gets
# a percentile, just over a shorter window, rather than being skipped.
COT_LOOKBACK_REPORTS = 53


def _net_long(row: Dict[str, Any]) -> float:
    return _safe_float(row.get("noncomm_positions_long_all")) - \
        _safe_float(row.get("noncomm_positions_short_all"))


def _percentile_1y(latest_net_long: float, rows: List[Dict[str, Any]]) -> Optional[float]:
    if len(rows) < 2:
        return None
    series = [_net_long(r) for r in rows]
    return sum(1 for v in series if v <= latest_net_long) / len(series)


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
            "$limit": COT_LOOKBACK_REPORTS,
        })
        if err:
            errors.append(f"positioning:cftc_cot:{code}:{err}")
            continue
        if not isinstance(data, list) or not data:
            errors.append(f"positioning:cftc_cot:{code}:no_rows")
            continue
        row = data[0]
        net_long = _net_long(row)
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
            percentile_1y=_percentile_1y(net_long, data),
        ))
    return out, ("; ".join(errors) if errors else None)


# api.py fans this out to its own thread pool — see that module's 15s
# cold-build budget note. A tuple of one so the same fan-out code in
# api.py works uniformly across every sources/*.py module.
ATOMIC_FETCHERS = (fetch_cot,)
