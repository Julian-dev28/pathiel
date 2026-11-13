"""Check: no call in the desk journal is still pending past the point it
should have been graded.

    python3 -m services.strategies.evals.grading_lag_check [--state-dir DIR] [--grace-hours N] [--json]

Reads every thesis under $PATHIEL_STATE_DIR/strategies/<desk>/theses/ and
fails (exit 1) when one of these is true:

    stale    expires_at + grace < now and grade is None
             (the horizon passed; the hourly sweep in pathiel/server.py
             should have written at least "expired"/"untriggered")
    lagging  the call carries no grade, its horizon is still open, and the
             sweep has not written ANY grade on that desk in the last
             `--grace-hours` hours even though grades exist for calls made
             AFTER it (a grader that stopped mid-journal)

Read only: it never grades, never writes. The operator's question of
2026-11-13 ("no pending should be there right? right") is what this
catches: before that day the grader only graded at the 7-day horizon, so a
call stopped out within hours sat "pending" for a week. That is now graded
the hour it resolves (services/strategies/grader.py); this check watches
the part that is deterministic without candles -- horizon passed, still
pending -- and the review-trades skill runs it daily.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

DEFAULT_GRACE_HOURS = 2.0   # the sweep runs hourly; two misses is a stopped sweep, not a slow one


def _parse(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    d = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def load_theses(state_dir: str) -> Dict[str, List[Dict[str, Any]]]:
    """{desk_id: [raw thesis dicts]} for every desk with a theses/ directory;
    the raw JSON, not the model, so a contract change never hides a row."""
    base = os.path.join(state_dir, "strategies")
    out: Dict[str, List[Dict[str, Any]]] = {}
    if not os.path.isdir(base):
        return out
    for desk in sorted(os.listdir(base)):
        d = os.path.join(base, desk, "theses")
        if not os.path.isdir(d):
            continue
        rows = []
        for name in sorted(os.listdir(d)):
            if not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(d, name)) as fh:
                    rows.append(json.load(fh))
            except (OSError, ValueError):
                continue
        out[desk] = rows
    return out


def find_stale(theses_by_desk: Dict[str, Sequence[Dict[str, Any]]], *, now: datetime,
               grace: timedelta = timedelta(hours=DEFAULT_GRACE_HOURS)) -> List[Dict[str, Any]]:
    """Calls past their horizon (plus grace) with no grade. Pure."""
    stale = []
    for desk, rows in theses_by_desk.items():
        for t in rows:
            exp = _parse(t.get("expires_at"))
            if exp is not None and t.get("grade") is None and exp + grace < now:
                stale.append({"desk": desk, "id": t.get("id"), "expires_at": t.get("expires_at"),
                              "overdue_hours": round((now - exp).total_seconds() / 3600.0, 1)})
    return stale


def find_lagging(theses_by_desk: Dict[str, Sequence[Dict[str, Any]]], *, now: datetime,
                 grace: timedelta = timedelta(hours=DEFAULT_GRACE_HOURS)) -> List[Dict[str, Any]]:
    """Ungraded calls inside their horizon on a desk where the newest grade
    was written more than `grace` ago AND a newer call than this one is
    already graded. A newer call graded while an older one is not is normal
    for an hour (the older one has simply not resolved); it is a stopped
    grader when nothing has been written since. Pure."""
    lagging = []
    for desk, rows in theses_by_desk.items():
        graded_at = [_parse((t.get("grade") or {}).get("graded_at")) for t in rows if t.get("grade")]
        graded_at = [g for g in graded_at if g is not None]
        if not graded_at or max(graded_at) + grace > now:
            continue
        newest_graded_created = max((_parse(t.get("created_at")) for t in rows if t.get("grade")), default=None)
        for t in rows:
            created = _parse(t.get("created_at"))
            if (t.get("grade") is None and created is not None and newest_graded_created is not None
                    and created < newest_graded_created):
                lagging.append({"desk": desk, "id": t.get("id"), "created_at": t.get("created_at"),
                                "last_grade_written": max(graded_at).isoformat()})
    return lagging


def run(state_dir: str, *, now: Optional[datetime] = None,
        grace_hours: float = DEFAULT_GRACE_HOURS) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    grace = timedelta(hours=grace_hours)
    theses = load_theses(state_dir)
    stale = find_stale(theses, now=now, grace=grace)
    lagging = find_lagging(theses, now=now, grace=grace)
    n = sum(len(v) for v in theses.values())
    pending = sum(1 for v in theses.values() for t in v if t.get("grade") is None)
    return {"ok": not stale and not lagging, "theses": n, "pending": pending,
            "stale": stale, "lagging": lagging, "as_of": now.isoformat()}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--state-dir", default=os.environ.get("PATHIEL_STATE_DIR"),
                    help="defaults to $PATHIEL_STATE_DIR")
    ap.add_argument("--grace-hours", type=float, default=DEFAULT_GRACE_HOURS)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if not args.state_dir:
        print("grading_lag_check: set PATHIEL_STATE_DIR or pass --state-dir", file=sys.stderr)
        return 2
    report = run(args.state_dir, grace_hours=args.grace_hours)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"GRADING {'OK' if report['ok'] else 'FAIL'} theses={report['theses']} pending={report['pending']} "
              f"stale={len(report['stale'])} lagging={len(report['lagging'])}")
        for row in report["stale"]:
            print(f"  stale   {row['desk']} {row['id']} horizon passed {row['overdue_hours']}h ago, no grade")
        for row in report["lagging"]:
            print(f"  lagging {row['desk']} {row['id']} ungraded; last grade written {row['last_grade_written']}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
