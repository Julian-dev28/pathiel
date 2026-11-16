#!/usr/bin/env python3
"""Do the desks read only Polymarket markets that are still live?

2026-11-16: Polymarket kept "US x Iran Effective Ceasefire by November 8?"
listed as active and open eight days after its endDate (it waits for
resolution). Its 24% -> 32% drift woke the oil desk through the watcher,
started a 30-minute entry blackout and fed a thesis rewrite. The fix drops a
market whose endDate has passed (services/intel/sources/predictions.py) and
the watcher skips an expired row (headline_watch.odds_alerts).

This eval hits the live Polymarket search for each desk's real queries (the
same call the thesis and trigger_watch make; no LLM) and checks two things:

  1. no market the desks receive has an endDate at or before now, and
  2. the raw search still returns expired-but-open markets, i.e. the filter
     is doing work (reported, not failed: Polymarket may resolve them all).

Periodic lane: the market list changes daily.

    .venv/bin/python -m services.intel.evals.eval_odds_expiry
    .venv/bin/python -m services.intel.evals.eval_odds_expiry --desk oil

Exit 0 when every desk passes, 1 otherwise.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from typing import List

from services.intel import http_client
from services.intel.sources import predictions
from services.strategies.desks import get_spec


def _raw_expired_open(queries: List[str], now: datetime) -> List[str]:
    """Markets the raw search lists as active + open whose endDate has passed."""
    out = []
    for q in queries:
        data, err = http_client.fetch_json(predictions.POLYMARKET_SEARCH_URL,
                                           params={"q": q, "limit_per_type": 15})
        if err or not isinstance(data, dict):
            continue
        for ev in data.get("events") or []:
            for m in ev.get("markets") or []:
                end = predictions._parse_end(m.get("endDate"))
                if m.get("active") and not m.get("closed") and end is not None and end <= now:
                    out.append((m.get("question") or "")[:90])
    return sorted(set(out))


def check(desk: str) -> bool:
    now = datetime.now(timezone.utc)
    spec = get_spec(desk)
    odds, err = predictions.fetch_polymarket(spec, now=now)
    expired = [o.question[:90] for o in odds if o.closes_at is not None and o.closes_at <= now]
    raw = _raw_expired_open(list(spec.prediction_market_queries or []), now)
    ok = not expired and err is None
    print(f"ODDS-EXPIRY {desk} {'PASS' if ok else 'FAIL'} served={len(odds)} "
          f"expired_served={len(expired)} expired_filtered={len(raw)}"
          + (f" err={err}" if err else ""))
    for q in expired:
        print(f"  served but expired: {q}")
    for q in raw[:5]:
        print(f"  filtered: {q}")
    return ok


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--desk", action="append", help="oil, metals (default both)")
    a = ap.parse_args(argv)
    desks = a.desk or ["oil", "metals"]
    return 0 if all([check(d) for d in desks]) else 1


if __name__ == "__main__":
    sys.exit(main())
