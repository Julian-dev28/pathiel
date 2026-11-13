"""Check: no call in the desk journal is still pending past the point it
should have been graded, and the sweep that grades them is alive.

    python3 -m services.strategies.evals.grading_lag_check [--state-dir DIR] [--grace-hours N] [--as-of ISO] [--json]

Reads every thesis under $PATHIEL_STATE_DIR/strategies/<desk>/theses/ plus
the grader's heartbeat $PATHIEL_STATE_DIR/strategies/grade_sweep.json
(written by grader.grade_all_due at the end of every sweep that finished)
and fails (exit 1) when one of these is true:

    stale    expires_at + grace < now and grade is None
             (the horizon passed; the hourly sweep in pathiel/server.py
             should have written at least "expired"/"untriggered")
    sweep    calls are pending and the heartbeat is missing or older than
             `--grace-hours` (the sweep stopped, or the dashboard server
             runs code older than the heartbeat and needs a restart)
    missed   a call pending in the journal, created more than grace
             before the last sweep, that the sweep did not list as open:
             it never read that call (a desk directory it skipped, a row
             it could not parse)

Read only: it never grades, never writes. The operator's question of
2026-11-13 ("no pending should be there right? right") is what this
catches. The first version of this check (same day) had a "lagging" rule
instead of the heartbeat: no grade written in 2h while a newer call than
a pending one was graded. That is exactly what a journal looks like when
nothing resolved for two hours -- six open Brent longs (stops 87.58,
targets 94.278 / 97.667) sat correctly ungraded while Brent ran 89.5 to
94.6 between them, and the rule called the sweep stopped. The journal
alone cannot tell a stopped sweep from a quiet one; the heartbeat can.
The review-trades skill runs this daily.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

DEFAULT_GRACE_HOURS = 2.0   # the sweep runs hourly; two misses is a stopped sweep, not a slow one
SWEEP_FILE = "grade_sweep.json"   # == services.strategies.journal.SWEEP_FILE; kept literal so this stays import-free


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


def load_sweep(state_dir: str) -> Optional[Dict[str, Any]]:
    """The grader's last heartbeat, raw; None when no sweep ever finished on
    this state dir (or the file is unreadable, which counts the same)."""
    path = os.path.join(state_dir, "strategies", SWEEP_FILE)
    try:
        with open(path) as fh:
            rec = json.load(fh)
    except (OSError, ValueError):
        return None
    return rec if isinstance(rec, dict) else None


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


def sweep_status(sweep: Optional[Dict[str, Any]], *, now: datetime, pending: int,
                 grace: timedelta = timedelta(hours=DEFAULT_GRACE_HOURS)) -> Dict[str, Any]:
    """Whether the sweep is alive: {"ok", "swept_at", "age_hours", "reason"}.
    With calls pending, a missing heartbeat or one older than `grace` is a
    stopped sweep. With nothing pending the heartbeat is not required (a
    fresh state dir, a desk with every call graded). Pure."""
    swept_at = _parse((sweep or {}).get("swept_at"))
    age_hours = round((now - swept_at).total_seconds() / 3600.0, 1) if swept_at is not None else None
    if pending == 0:
        return {"ok": True, "swept_at": sweep.get("swept_at") if sweep else None,
                "age_hours": age_hours, "reason": None}
    if swept_at is None:
        return {"ok": False, "swept_at": None, "age_hours": None,
                "reason": "no heartbeat: no sweep has finished on this state dir "
                          "(dashboard server on pre-heartbeat code, or never started)"}
    if swept_at + grace < now:
        return {"ok": False, "swept_at": sweep.get("swept_at"), "age_hours": age_hours,
                "reason": f"last sweep finished {age_hours}h ago; the hourly sweep stopped"}
    return {"ok": True, "swept_at": sweep.get("swept_at"), "age_hours": age_hours, "reason": None}


def find_missed(theses_by_desk: Dict[str, Sequence[Dict[str, Any]]], sweep: Optional[Dict[str, Any]], *,
                grace: timedelta = timedelta(hours=DEFAULT_GRACE_HOURS)) -> List[Dict[str, Any]]:
    """Pending calls the last sweep never read: created more than `grace`
    before `swept_at`, still ungraded, and absent from the heartbeat's
    `open` list. A sweep that read a call either graded it (no longer
    pending) or listed it open; anything else it skipped. Without a
    heartbeat there is nothing to compare against (sweep_status already
    fails that case). Pure."""
    swept_at = _parse((sweep or {}).get("swept_at"))
    if swept_at is None:
        return []
    seen = {(o.get("desk"), o.get("id")) for o in (sweep or {}).get("open", []) if isinstance(o, dict)}
    missed = []
    for desk, rows in theses_by_desk.items():
        for t in rows:
            created = _parse(t.get("created_at"))
            if (t.get("grade") is None and created is not None and created + grace < swept_at
                    and (desk, t.get("id")) not in seen):
                missed.append({"desk": desk, "id": t.get("id"), "created_at": t.get("created_at"),
                               "swept_at": (sweep or {}).get("swept_at")})
    return missed


def run(state_dir: str, *, now: Optional[datetime] = None,
        grace_hours: float = DEFAULT_GRACE_HOURS) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    grace = timedelta(hours=grace_hours)
    theses = load_theses(state_dir)
    sweep = load_sweep(state_dir)
    n = sum(len(v) for v in theses.values())
    pending = sum(1 for v in theses.values() for t in v if t.get("grade") is None)
    stale = find_stale(theses, now=now, grace=grace)
    status = sweep_status(sweep, now=now, pending=pending, grace=grace)
    missed = find_missed(theses, sweep, grace=grace)
    return {"ok": not stale and status["ok"] and not missed, "theses": n, "pending": pending,
            "stale": stale, "sweep": status, "missed": missed, "as_of": now.isoformat()}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--state-dir", default=os.environ.get("PATHIEL_STATE_DIR"),
                    help="defaults to $PATHIEL_STATE_DIR")
    ap.add_argument("--grace-hours", type=float, default=DEFAULT_GRACE_HOURS)
    ap.add_argument("--as-of", default=None, metavar="ISO",
                    help="evaluate as of this UTC time instead of now (a replay of a past state dir)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if not args.state_dir:
        print("grading_lag_check: set PATHIEL_STATE_DIR or pass --state-dir", file=sys.stderr)
        return 2
    report = run(args.state_dir, now=_parse(args.as_of), grace_hours=args.grace_hours)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        sw = report["sweep"]
        sweep_word = ("none" if sw["age_hours"] is None else f"{sw['age_hours']}h ago") + ("" if sw["ok"] else " STOPPED")
        print(f"GRADING {'OK' if report['ok'] else 'FAIL'} theses={report['theses']} pending={report['pending']} "
              f"stale={len(report['stale'])} missed={len(report['missed'])} sweep={sweep_word}")
        for row in report["stale"]:
            print(f"  stale   {row['desk']} {row['id']} horizon passed {row['overdue_hours']}h ago, no grade")
        if not sw["ok"]:
            print(f"  sweep   {sw['reason']}; {report['pending']} pending call(s) wait on it")
        for row in report["missed"]:
            print(f"  missed  {row['desk']} {row['id']} created {row['created_at']}, not read by the sweep of {row['swept_at']}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
