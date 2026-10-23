"""SEC EDGAR for the AI-equities desk: Form 4 insider buy/sell activity in
the last 14 days (count + net direction), and an estimated next-earnings
date from the 10-Q/10-K filing cadence. No key — SEC asks only for a
contact User-Agent, which every request here sends.

Verified live 2026-10-23 against NVDA (CIK 0001045810):
  company_tickers.json              ticker -> CIK map, 799KB, HTTP 200.
  cgi-bin/browse-edgar?...&type=4    per-issuer Form 4 filing list (Atom),
  &owner=include                    gives filing-date + accession-number +
                                     an -index.htm link per filing, but NOT
                                     the buy/sell transaction code — that
                                     only lives inside the filing's own XML.
  .../{accession}/index.json         lists that filing's actual files
                                     (predictable, documented JSON; the XML
                                     filename itself is NOT predictable —
                                     every filing agent names it
                                     differently, e.g. "wk-form4_....xml" —
                                     so index.json is read to find it rather
                                     than guessed).
  .../{accession}/{the xml file}     the real ownership XML:
                                     nonDerivativeTransaction entries each
                                     carry transactionCoding.transactionCode
                                     ("P" = open-market buy, "S" = open-
                                     market sale, among others — grants
                                     ("A"), gifts ("G"), and option
                                     exercises ("M") are excluded from the
                                     buy/sell count below since they are
                                     not a discretionary market view).

Deliberately bounded: at most MAX_FORM4_PER_TICKER filings get their XML
fetched per ticker (2 extra HTTP calls each on top of the one Atom-feed
call), same trade-off services/intel/sources/catalysts.py already accepts
for its own per-ticker SEC loop — a busy desk can blow this source's own
12s fetch budget and show up as `timeout_budget_exceeded`, which never
blocks the rest of the packet (services/intel/api.py's per-source thread
isolation), rather than silently truncating results with no signal.

CIK-map and submissions-cadence logic intentionally duplicates a slice of
services/intel/sources/catalysts.py's `fetch_earnings_catalysts` rather
than importing from it — same call services/intel/env.py's docstring
already made for services/trend_engine/env.py: two sources that may be
edited in parallel by two different agents should not share a file.
catalysts.py emits a Catalyst for the calendar; this module emits a
DataPoint for the HARD DATA block — related numbers, different contracts.

Only fires for equity-shaped desks (`desk_rules.is_equity_desk`) per the
brief's desk-relevance mapping — the AI desk sees EDGAR and CBOE.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from contracts.strategies.models import DataPoint, DeskSpec
from services.intel import desk_rules, http_client
from services.intel.textnorm import stable_id

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL_FMT = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_FORM4_BROWSE_URL = "https://www.sec.gov/cgi-bin/browse-edgar"
SEC_ARCHIVES_FMT = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_nodash}/{path}"
SEC_UA = {"User-Agent": "pathiel-intel/1.0 (team.recoin@gmail.com)"}  # SEC asks for a contact UA

FORM4_WINDOW_DAYS = 14
MAX_FORM4_PER_TICKER = 5
EARNINGS_CADENCE_DAYS = 91  # ~1 quarter; see catalysts.py's identical assumption
_OPEN_MARKET_BUY, _OPEN_MARKET_SELL = "P", "S"

def fetch_cik_map(*, timeout_s: float = 10.0) -> Tuple[Optional[Dict[str, str]], Optional[str]]:
    data, err = http_client.fetch_json(SEC_TICKERS_URL, headers=SEC_UA, timeout_s=timeout_s)
    if err:
        return None, f"edgar:sec_tickers:{err}"
    if not isinstance(data, dict):
        return None, "edgar:sec_tickers:bad_shape"
    out = {str(v.get("ticker", "")).upper(): str(v.get("cik_str", "")).zfill(10)
           for v in data.values() if isinstance(v, dict)}
    return out, None


def _tickers(spec: DeskSpec) -> List[str]:
    return [i.coin.split(":", 1)[-1].upper() for i in spec.instruments
            if i.coin.split(":", 1)[-1].isalpha()]


# ── Form 4 insider buys/sells ─────────────────────────────────────────

def fetch_form4_entries(cik: str, *, count: int = 20) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
    """Recent Form 4 (not 4/A) filings for one issuer CIK, from the Atom
    browse feed — [{accession, filing_date}]."""
    data, err = http_client.fetch_text(SEC_FORM4_BROWSE_URL, params={
        "action": "getcompany", "CIK": cik, "type": "4", "dateb": "", "owner": "include",
        "count": count, "output": "atom",
    }, headers=SEC_UA, timeout_s=8.0)
    if err:
        return None, f"edgar:form4_browse:{cik}:{err}"
    entries = re.findall(r"<entry>.*?</entry>", data or "", re.S)
    out = []
    for e in entries:
        form = re.search(r'term="([^"]+)"', e)
        acc = re.search(r"<accession-number>([^<]+)</accession-number>", e)
        date = re.search(r"<filing-date>([^<]+)</filing-date>", e)
        if not (form and acc and date):
            continue
        if form.group(1) != "4":   # exclude 4/A amendments — avoid double-counting
            continue
        out.append({"accession": acc.group(1), "filing_date": date.group(1)})
    return out, None


def _accession_nodash(accession: str) -> str:
    return accession.replace("-", "")


def fetch_form4_xml_path(cik: str, accession: str) -> Tuple[Optional[str], Optional[str]]:
    cik_int = str(int(cik))
    nodash = _accession_nodash(accession)
    url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{nodash}/index.json"
    data, err = http_client.fetch_json(url, headers=SEC_UA, timeout_s=6.0)
    if err:
        return None, f"edgar:form4_index:{accession}:{err}"
    items = ((data or {}).get("directory") or {}).get("item") or []
    for item in items:
        name = item.get("name", "")
        if name.endswith(".xml"):
            return name, None
    return None, f"edgar:form4_index:{accession}:no_xml_found"


_TXN_CODE_RE = re.compile(r"<transactionCode>([A-Z])</transactionCode>")


def count_buys_and_sells(xml_text: str) -> Tuple[int, int]:
    """Count P (open-market buy) / S (open-market sale) transactionCode
    occurrences in a Form 4's raw XML. A plain regex, not a full XML
    parser, on purpose: this file's own schema is simple and stable
    (verified against a real capture, see tests/fixtures), and every
    other row (grants, gifts, option exercises) is explicitly out of
    scope for "insider buys/sells" — regex keeps that filter a one-liner
    instead of a tree walk."""
    codes = _TXN_CODE_RE.findall(xml_text)
    return codes.count(_OPEN_MARKET_BUY), codes.count(_OPEN_MARKET_SELL)


def fetch_form4_activity_for_ticker(ticker: str, cik: str, now: datetime
                                     ) -> Tuple[Optional[DataPoint], List[str]]:
    errors: List[str] = []
    entries, err = fetch_form4_entries(cik)
    if err:
        return None, [err]
    cutoff = now - timedelta(days=FORM4_WINDOW_DAYS)
    recent = []
    for e in entries:
        try:
            fdate = datetime.strptime(e["filing_date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except (KeyError, ValueError):
            continue
        if fdate >= cutoff:
            recent.append((fdate, e["accession"]))
    recent.sort(key=lambda t: t[0], reverse=True)
    recent = recent[:MAX_FORM4_PER_TICKER]

    buys = sells = 0
    inspected = 0
    for _fdate, accession in recent:
        xml_name, idx_err = fetch_form4_xml_path(cik, accession)
        if idx_err:
            errors.append(idx_err)
            continue
        xml_url = SEC_ARCHIVES_FMT.format(cik_int=str(int(cik)),
                                          accession_nodash=_accession_nodash(accession),
                                          path=xml_name)
        xml_text, xml_err = http_client.fetch_text(xml_url, headers=SEC_UA, timeout_s=6.0)
        if xml_err:
            errors.append(f"edgar:form4_xml:{accession}:{xml_err}")
            continue
        b, s = count_buys_and_sells(xml_text)
        buys += b
        sells += s
        inspected += 1

    if not recent:
        return None, errors  # clean: nothing filed in the window, not a failure

    net = float(buys - sells)
    note = (f"{buys} buys, {sells} sells across {inspected}/{len(recent)} Form 4 filings "
            f"in the last {FORM4_WINDOW_DAYS}d (open-market P/S only)")
    dp = DataPoint(
        id=stable_id("edgar", "form4", ticker, now.date().isoformat()),
        source="sec_edgar", label=f"{ticker} insider Form 4 net (buys-sells)",
        value=net, prior=None, change=None, unit="count", as_of=now, note=note,
        relevance=0.75 if net != 0 else 0.45,
    )
    return dp, errors


# ── next earnings date (10-Q/10-K cadence estimate) ───────────────────

def estimated_earnings_date(submissions: Dict[str, Any]) -> Optional[Tuple[datetime, str]]:
    """(estimated_at, last_filing_date_str) from the latest 10-Q/10-K plus
    ~1 quarter, or None if the submissions payload has neither. SEC EDGAR
    only has PAST filings — there is no forward filings calendar here, so
    this is an estimate, always labeled as one in the DataPoint's note."""
    recent = ((submissions or {}).get("filings") or {}).get("recent") or {}
    dates = sorted({d for f, d in zip(recent.get("form", []), recent.get("filingDate", []))
                    if f in ("10-Q", "10-K")}, reverse=True)
    if not dates:
        return None
    try:
        last = datetime.strptime(dates[0], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return last + timedelta(days=EARNINGS_CADENCE_DAYS), dates[0]


def fetch_next_earnings_for_ticker(ticker: str, cik: str, now: datetime
                                    ) -> Tuple[Optional[DataPoint], Optional[str]]:
    data, err = http_client.fetch_json(SEC_SUBMISSIONS_URL_FMT.format(cik=cik),
                                        headers=SEC_UA, timeout_s=8.0)
    if err:
        return None, f"edgar:submissions:{ticker}:{err}"
    if not isinstance(data, dict):
        return None, f"edgar:submissions:{ticker}:bad_shape"
    est = estimated_earnings_date(data)
    if est is None:
        return None, None
    est_at, last_filing_date = est
    days_until = (est_at - now).total_seconds() / 86400.0
    return DataPoint(
        id=stable_id("edgar", "earnings", ticker, last_filing_date),
        source="sec_edgar", label=f"{ticker} next earnings (estimated)",
        value=days_until, prior=None, change=None, unit="days", as_of=now,
        note=(f"estimated {est_at.date().isoformat()} from last 10-Q/10-K "
              f"({last_filing_date}) + ~{EARNINGS_CADENCE_DAYS}d cadence, not confirmed"),
        relevance=0.6,
    ), None


def fetch_edgar(spec: DeskSpec, now: datetime) -> Tuple[List[DataPoint], Optional[str]]:
    if not desk_rules.is_equity_desk(spec):
        return [], None
    tickers = _tickers(spec)
    if not tickers:
        return [], None
    cik_map, err = fetch_cik_map()
    if err:
        return [], err
    out: List[DataPoint] = []
    errors: List[str] = []
    for ticker in tickers:
        cik = cik_map.get(ticker)
        if not cik:
            continue
        dp, f4_errors = fetch_form4_activity_for_ticker(ticker, cik, now)
        errors.extend(f4_errors)
        if dp is not None:
            out.append(dp)
        earnings_dp, e_err = fetch_next_earnings_for_ticker(ticker, cik, now)
        if e_err:
            errors.append(e_err)
        if earnings_dp is not None:
            out.append(earnings_dp)
    return out, ("; ".join(errors) if errors else None)


ATOMIC_FETCHERS = (fetch_edgar,)
