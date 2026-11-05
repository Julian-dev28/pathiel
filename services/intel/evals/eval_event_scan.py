#!/usr/bin/env python3
"""Eval for the 12-hour event scan (read only; may hit the network).

Two checks:
  1. Run scripts/event_scan.py in dry-run mode against the live APIs and
     print every row it would add or update. Nothing is written. Without
     CONGRESS_API_KEY the congress sources are skipped and the line says so.
  2. Merge safety: every curated row for the next 7 days is still present
     after services/intel/scheduled_events.load() merges the overlay. A scan
     may add rows; it must never hide a curated one (the midterms lesson).

    PATHIEL_STATE_DIR=... python3 -m services.intel.evals.eval_event_scan

Exit 0 when the merge check passes, 1 when a curated row went missing.
`missing_curated` is pure and gate-tested (tests/test_event_scan.py).
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from services.intel import scheduled_events as S  # noqa: E402


def missing_curated(curated: List[Dict[str, Any]], merged: List[Dict[str, Any]], today: date,
                    days: int = 7) -> List[Dict[str, Any]]:
    """Pure. Curated rows dated in [today, today + days] whose (date, kind,
    normalized title) is absent from the merged rows."""
    lo, hi = today.isoformat(), (today + timedelta(days=days)).isoformat()
    have = {S.event_key(e) for e in merged}
    return [e for e in curated if lo <= e["date"] <= hi and S.event_key(e) not in have]


def main() -> int:
    spec = importlib.util.spec_from_file_location("event_scan", os.path.join(ROOT, "scripts", "event_scan.py"))
    scan = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scan)
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    from _state_env import load_env_local
    load_env_local()

    now = datetime.now(timezone.utc)
    out = scan.scan(now, dry_run=True)
    print(f"dry run against the live APIs ({len(out['results'])} row(s) it would touch):")
    for status, src, row in out["results"]:
        print(f"  {status.upper():<9} {src:<17} {scan.event_add.format_row(row)}")
    print(out["line"])

    with open(S.DATA_PATH) as fh:
        curated = json.load(fh)["events"]
    merged = S.load()["events"]
    missing = missing_curated(curated, merged, now.date())
    window = [e for e in curated if now.date().isoformat() <= e["date"] <= (now.date() + timedelta(days=7)).isoformat()]
    print(f"merge check: {len(window)} curated row(s) in the next 7 days, {len(missing)} missing, "
          f"{sum(1 for e in merged if e.get('origin') == S.SCAN_ORIGIN)} scanned row(s) in the overlay "
          f"({S.overlay_path()})")
    for e in missing:
        print(f"  MISSING {e['date']} {e['kind']} {e['event']}")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
