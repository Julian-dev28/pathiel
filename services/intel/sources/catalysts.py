"""Scheduled catalysts: deterministic calendar generators (calendar_us.py)
plus three live, best-effort checks (NHC Atlantic storms, crypto token
unlocks, equity earnings estimates).

`catalyst_kinds` on DeskSpec is treated as an ALLOWLIST OF DESK-SPECIFIC
generators, not an exhaustive gate — three kinds are added automatically
regardless of whether the desk's `catalyst_kinds` lists them:

  - "fomc" and "cpi" are always on. They move every desk's instruments,
    and the intel brief lists them as required infrastructure to build,
    not as oil-specific config.
  - "hurricane_atlantic" turns on for any oil-like desk (desk_rules.
    is_oil_desk), "token_unlock" for any crypto-like desk, "earnings" for
    any desk with an equity-shaped instrument — content-sniffed exactly
    the way sources/news.py gates oilprice.com and the crypto RSS feed.

This matters concretely for the real contracts/strategies/fixtures/
oil_spec.json fixture: its `catalyst_kinds` list is
`["eia_weekly","api_weekly","opec_meeting","cftc_cot","baker_hughes","fomc"]`
— it does not mention "cpi", "eia_steo", or "hurricane_atlantic" even
though the intel brief explicitly calls out CPI, STEO, and NHC storms as
required oil-desk coverage. Treating catalyst_kinds as a strict allowlist
would silently under-serve that exact fixture. Flagged again in the
intel service's final report under "for the integrator": desk-core may
want oil_spec.json's catalyst_kinds updated to list these explicitly.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from contracts.strategies.models import Catalyst, DeskSpec
from services.intel import calendar_us, desk_rules, http_client
from services.intel.textnorm import stable_id

EXTRA_LOOKAHEAD_DAYS = 14
NHC_CURRENT_STORMS_URL = "https://www.nhc.noaa.gov/CurrentStorms.json"
DEFILLAMA_EMISSIONS_URL = "https://defillama-datasets.llama.fi/emissionsIndex"
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL_FMT = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_UA = {"User-Agent": "pathiel-intel/1.0 (team.recoin@gmail.com)"}  # SEC asks for a contact UA
MIN_UNLOCK_PCT_CIRC = 1.0  # same floor pathiel/agents/unlock_recorder.py uses

# kind -> (generator(start,end)->List[Release], title, importance, source)
_CALENDAR_GENERATORS: Dict[str, Tuple] = {
    "eia_weekly": (calendar_us.eia_weekly_release, "EIA Weekly Petroleum Status Report", 3, "EIA"),
    "eia_gas_storage": (calendar_us.eia_gas_storage_release, "EIA Weekly Natural Gas Storage Report",
                       3, "EIA"),
    "api_weekly": (calendar_us.api_weekly_release, "API weekly crude stock report", 2, "API"),
    "baker_hughes": (calendar_us.baker_hughes_release, "Baker Hughes US rig count", 1, "Baker Hughes"),
    "cftc_cot": (calendar_us.cftc_cot_release, "CFTC Commitments of Traders", 2, "CFTC"),
    "fomc": (calendar_us.fomc_releases, "FOMC decision", 3, "Federal Reserve"),
    "cpi": (calendar_us.cpi_releases, "CPI release", 3, "BLS"),
    "eia_steo": (calendar_us.steo_releases, "EIA Short-Term Energy Outlook", 2, "EIA"),
    "opec_meeting": (calendar_us.opec_plus_releases, "OPEC+ meeting", 3, "OPEC"),
}
_ALWAYS_ON_KINDS = frozenset({"fomc", "cpi"})


def desk_window(spec: DeskSpec, now: datetime) -> Tuple[date, date]:
    start = now.date()
    end = (now + timedelta(days=spec.horizon_days + EXTRA_LOOKAHEAD_DAYS)).date()
    return start, end


def active_kinds(spec: DeskSpec) -> set:
    kinds = set(spec.catalyst_kinds) | _ALWAYS_ON_KINDS
    if desk_rules.is_oil_desk(spec):
        kinds.add("hurricane_atlantic")
    if desk_rules.is_crypto_desk(spec):
        kinds.add("token_unlock")
    if desk_rules.is_equity_desk(spec):
        kinds.add("earnings")
    return kinds


def calendar_catalysts(spec: DeskSpec, now: datetime) -> List[Catalyst]:
    """Pure, instant, no I/O — every date-rule-based catalyst in window."""
    start, end = desk_window(spec, now)
    kinds = active_kinds(spec)
    out: List[Catalyst] = []
    for kind, (gen_fn, title, importance, source) in _CALENDAR_GENERATORS.items():
        if kind not in kinds:
            continue
        for rel in gen_fn(start, end):
            if rel.at_utc < now:
                continue
            note = rel.note or ""
            expected = note if ("shift" in note.lower() or "assumption" in note.lower()
                                 or "placeholder" in note.lower()) else None
            out.append(Catalyst(
                id=stable_id("catalyst", kind, rel.at_utc.isoformat()),
                kind=kind, title=title, at=rel.at_utc, importance=importance,
                source=source, expected=expected,
            ))
    return out


# ── live: NHC active Atlantic storms (oil desks) ─────────────────────

def fetch_hurricane_catalysts(spec: DeskSpec, now: datetime) -> Tuple[List[Catalyst], Optional[str]]:
    if "hurricane_atlantic" not in active_kinds(spec):
        return [], None
    data, err = http_client.fetch_json(NHC_CURRENT_STORMS_URL)
    if err:
        return [], f"catalysts:nhc_storms:{err}"
    if not isinstance(data, dict):
        return [], "catalysts:nhc_storms:bad_shape"
    out = []
    for storm in data.get("activeStorms") or []:
        storm_id = str(storm.get("id") or "")
        if not storm_id.lower().startswith("al"):  # Atlantic basin only
            continue
        name = storm.get("name") or storm_id
        classification = storm.get("classification") or "?"
        intensity = storm.get("intensity")
        last_update = storm.get("lastUpdate")
        at = now
        if last_update:
            try:
                at = datetime.fromisoformat(last_update.replace("Z", "+00:00"))
            except ValueError:
                at = now
        importance = 3 if classification in ("HU", "MH") else 2
        out.append(Catalyst(
            id=stable_id("catalyst", "hurricane_atlantic", storm_id, str(last_update)),
            kind="hurricane_atlantic", title=f"Active Atlantic storm: {name} ({classification})",
            at=at, importance=importance, source="NHC",
            expected=f"{intensity}kt winds as of advisory {storm.get('publicAdvisory', {}).get('advNum', '?')}",
        ))
    return out, None


# ── live: crypto token unlocks ────────────────────────────────────────

def _load_unlock_map() -> Dict[str, str]:
    try:
        with open(desk_rules.UNLOCK_MAP_PATH) as fh:
            raw = json.load(fh)
        return {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}
    except Exception:
        return {}


def fetch_unlock_catalysts(spec: DeskSpec, now: datetime) -> Tuple[List[Catalyst], Optional[str]]:
    if "token_unlock" not in active_kinds(spec):
        return [], None
    desk_tickers = {t.upper() for t in desk_rules.instrument_tokens(spec)}
    name_to_sym = _load_unlock_map()
    data, err = http_client.fetch_json(DEFILLAMA_EMISSIONS_URL, timeout_s=20.0)
    if err:
        return [], f"catalysts:token_unlock:{err}"
    if not isinstance(data, dict):
        return [], "catalysts:token_unlock:bad_shape"
    start, end = desk_window(spec, now)
    window_start_ms = int(datetime(start.year, start.month, start.day,
                                    tzinfo=timezone.utc).timestamp() * 1000)
    window_end_ms = int(datetime(end.year, end.month, end.day,
                                  tzinfo=timezone.utc).timestamp() * 1000)
    merged: Dict[Tuple[str, int], float] = {}
    for row in data.get("data") or []:
        sym = name_to_sym.get(str(row.get("name") or ""))
        if not sym or sym.upper() not in desk_tickers:
            continue
        circ = _to_float(row.get("circSupply"))
        if circ <= 0:
            continue
        for ev in row.get("events") or []:
            ts_ms = int(_to_float(ev.get("timestamp")) * 1000)
            if ts_ms < window_start_ms or ts_ms > window_end_ms:
                continue
            toks = ev.get("noOfTokens")
            amt = sum(_to_float(x) for x in toks) if isinstance(toks, list) else 0.0
            if amt <= 0:
                continue
            pct = amt / circ * 100.0
            key = (sym, ts_ms // 86_400_000)
            merged[key] = merged.get(key, 0.0) + pct
    out = []
    for (sym, day_bucket), pct in merged.items():
        if pct < MIN_UNLOCK_PCT_CIRC:
            continue
        at = datetime.fromtimestamp(day_bucket * 86400, tz=timezone.utc)
        out.append(Catalyst(
            id=stable_id("catalyst", "token_unlock", sym, str(day_bucket)),
            kind="token_unlock", title=f"{sym} token unlock ({pct:.1f}% of circulating supply)",
            at=at, importance=3 if pct >= 5.0 else 2, source="DefiLlama",
            expected=f"{pct:.2f}% of circulating supply",
        ))
    return out, None


def _to_float(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


# ── live: equity earnings estimate (best-effort, e.g. AI desk) ──────

def _sec_cik_for(ticker: str, tickers_map: Dict[str, str]) -> Optional[str]:
    return tickers_map.get(ticker.upper())


def fetch_earnings_catalysts(spec: DeskSpec, now: datetime) -> Tuple[List[Catalyst], Optional[str]]:
    if "earnings" not in active_kinds(spec):
        return [], None
    tickers = [i.coin.split(":", 1)[-1].upper() for i in spec.instruments
               if i.coin.split(":", 1)[-1].isalpha()]
    if not tickers:
        return [], None
    data, err = http_client.fetch_json(SEC_TICKERS_URL, headers=SEC_UA, timeout_s=10.0)
    if err:
        return [], f"catalysts:earnings:sec_tickers:{err}"
    if not isinstance(data, dict):
        return [], "catalysts:earnings:sec_tickers:bad_shape"
    cik_map = {str(v.get("ticker", "")).upper(): str(v.get("cik_str", "")).zfill(10)
               for v in data.values() if isinstance(v, dict)}
    start, end = desk_window(spec, now)
    out: List[Catalyst] = []
    errors = []
    for ticker in tickers:
        cik = _sec_cik_for(ticker, cik_map)
        if not cik:
            continue
        sub, sub_err = http_client.fetch_json(
            SEC_SUBMISSIONS_URL_FMT.format(cik=cik), headers=SEC_UA, timeout_s=10.0)
        if sub_err:
            errors.append(f"catalysts:earnings:{ticker}:{sub_err}")
            continue
        if not isinstance(sub, dict):
            continue
        recent = (sub.get("filings") or {}).get("recent") or {}
        dates = sorted({d for f, d in zip(recent.get("form", []), recent.get("filingDate", []))
                        if f in ("10-Q", "10-K")}, reverse=True)
        if not dates:
            continue
        try:
            last = datetime.strptime(dates[0], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        # Quarterly cadence estimate, not a confirmed date — SEC EDGAR only
        # has PAST filings, no forward calendar. ~91 days is a quarter.
        est = last + timedelta(days=91)
        if start <= est.date() <= end:
            out.append(Catalyst(
                id=stable_id("catalyst", "earnings", ticker, dates[0]),
                kind="earnings", title=f"{ticker} earnings (estimated)",
                at=est, importance=2, source="SEC EDGAR",
                expected="estimated from last 10-Q/10-K filing + ~91d quarterly cadence, "
                         "not a confirmed date",
            ))
    return out, ("; ".join(errors) if errors else None)


ATOMIC_FETCHERS = (fetch_hurricane_catalysts, fetch_unlock_catalysts, fetch_earnings_catalysts)


def fetch_catalysts(spec: DeskSpec, now: datetime) -> Tuple[List[Catalyst], List[str]]:
    """Convenience sequential path — see sources/news.py's fetch_headlines
    docstring for why this exists alongside ATOMIC_FETCHERS."""
    out = calendar_catalysts(spec, now)
    failed: List[str] = []
    for fn in ATOMIC_FETCHERS:
        items, err = fn(spec, now)
        if err:
            failed.append(err)
        out.extend(items)
    out.sort(key=lambda c: c.at)
    return out, failed
