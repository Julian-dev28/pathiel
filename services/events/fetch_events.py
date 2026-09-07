#!/usr/bin/env python3
"""Scheduled-event calendar for the xyz (HIP-3) universe.

WHAT THIS IS FOR
The operator asked whether public-policy and scheduled-event data can be turned
into edge: bills, wartime measures, oil/gold catalysts, token unlocks, funding
rounds. The honest constraint came first, and it is a POWER constraint, not an
access one - most of that data is fetchable, and almost none of it is testable
on a 72-day panel:

    FOMC meetings in the panel window      8   (and only ~2 are decisions)
    executive orders on any one topic      ~0-3
    a named bill's passage                 1, by definition

You cannot backtest a one-off. A strategy fitted to n=1 is a story. So this
module fetches the one scheduled event type that IS dense enough to measure -
corporate earnings, ~190 of them in the same window across 65 mapped names -
and treats everything else as calendar context for a human, not as signal.

SOURCES, AND WHICH NEED A KEY
  SEC EDGAR         no key   8-K / 10-Q filing dates  <- the testable one
  Federal Reserve   no key   FOMC + Fed speaker calendar
  Federal Register  no key   executive orders, rules, proposed rules
  Congress.gov      FREE KEY api.congress.gov/sign-up - bill status and votes
  EIA               FREE KEY eia.gov/opendata - weekly petroleum, direct CL/BRENTOIL
  FRED              FREE KEY fred.stlouisfed.org - macro series

The keyed three are wired to read from the environment and skip cleanly when a
key is absent, so adding one later needs no code change.

    python services/events/fetch_events.py --refresh
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

import requests

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / ".state" / "events_calendar.json"
UA = {"User-Agent": "pathia-research/1.0 (team.recoin@gmail.com)"}
SEC_RATE_S = 0.12          # SEC asks for <= 10 req/s


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(UA)
    return s


def xyz_names() -> List[str]:
    sys.path.insert(0, str(ROOT / "research" / "regime_2026_09"))
    from C1_session_structure import load_panel
    return sorted({c.split(":", 1)[1] for c in load_panel() if c.startswith("xyz:")})


def ticker_to_cik(s: requests.Session) -> Dict[str, str]:
    j = s.get("https://www.sec.gov/files/company_tickers.json", timeout=30).json()
    return {v["ticker"].upper(): str(v["cik_str"]).zfill(10) for v in j.values()}


def earnings_dates(s: requests.Session, names: List[str]) -> Dict[str, List[str]]:
    """8-K and 10-Q filing dates per ticker.

    A filing date is not exactly the announcement timestamp, but 10-Q and the
    8-K carrying Item 2.02 ARE the earnings release, and the panel is hourly -
    a few hours of imprecision is well inside its resolution. Being explicit
    about that beats implying a precision the data does not have.
    """
    cik = ticker_to_cik(s)
    out: Dict[str, List[str]] = {}
    mapped = [(n, cik[n]) for n in names if n in cik]
    for i, (n, c) in enumerate(mapped, 1):
        try:
            j = s.get(f"https://data.sec.gov/submissions/CIK{c}.json", timeout=25).json()
            r = j.get("filings", {}).get("recent", {})
            out[n] = sorted({d for f, d in zip(r.get("form", []), r.get("filingDate", []))
                             if f in ("8-K", "10-Q")})
        except Exception as e:
            print(f"  [warn] {n}: {type(e).__name__}", file=sys.stderr)
        if i % 20 == 0:
            print(f"  ...{i}/{len(mapped)} tickers", file=sys.stderr)
        time.sleep(SEC_RATE_S)
    return out


def fed_calendar(s: requests.Session) -> List[dict]:
    r = s.get("https://www.federalreserve.gov/json/calendar.json", timeout=25)
    d = json.loads(r.content.decode("utf-8-sig"))       # the feed carries a BOM
    evs = d.get("mtgitems") or d.get(next(iter(d)))
    return [{"month": e.get("month"), "days": e.get("days"), "title": e.get("title"),
             "type": e.get("type")} for e in evs]


def federal_register(s: requests.Session, terms: List[str]) -> List[dict]:
    """Executive orders and rules matching topics that plausibly move the xyz
    complex (energy, tariffs, defence, mining). Context, not signal - see the
    module docstring on why one-off policy cannot be backtested here."""
    out = []
    for term in terms:
        try:
            j = s.get("https://www.federalregister.gov/api/v1/documents.json",
                      params={"per_page": 20, "order": "newest",
                              "conditions[term]": term,
                              "fields[]": ["title", "publication_date", "type",
                                           "html_url"]},
                      timeout=25).json()
            for d in j.get("results", []):
                d["matched_term"] = term
                out.append(d)
        except Exception as e:
            print(f"  [warn] federal_register {term}: {type(e).__name__}", file=sys.stderr)
    return out


def keyed_sources() -> Dict[str, str]:
    """Report which optional keys are present. Absent keys are skipped, never
    faked - a calendar that silently omits a source is worse than one that says
    it is missing."""
    return {
        "CONGRESS_API_KEY": "api.congress.gov/sign-up (bills, votes)",
        "EIA_API_KEY": "eia.gov/opendata/register.php (weekly petroleum -> CL/BRENTOIL)",
        "FRED_API_KEY": "fred.stlouisfed.org/docs/api/api_key.html (macro series)",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    if CACHE.exists() and not a.refresh:
        data = json.loads(CACHE.read_text())
        print(f"  cached {CACHE} ({len(data.get('earnings', {}))} tickers)")
        return 0

    s = _session()
    names = xyz_names()
    print(f"  {len(names)} xyz markets; fetching SEC filing dates...", file=sys.stderr)
    data = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "earnings": earnings_dates(s, names),
        "fed": fed_calendar(s),
        "federal_register": federal_register(
            s, ["crude oil", "tariff", "export control", "critical minerals",
                "semiconductor", "defense production"]),
    }
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(data, indent=1))

    n_e = sum(len(v) for v in data["earnings"].values())
    print(f"\n  earnings dates   : {n_e} across {len(data['earnings'])} tickers")
    print(f"  fed calendar     : {len(data['fed'])} entries")
    print(f"  federal register : {len(data['federal_register'])} documents")
    missing = [k for k in keyed_sources() if not os.environ.get(k)]
    if missing:
        print("\n  OPTIONAL KEYS NOT SET (sources skipped, not faked):")
        for k in missing:
            print(f"    {k:<18} {keyed_sources()[k]}")
    print(f"\n  -> {CACHE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
