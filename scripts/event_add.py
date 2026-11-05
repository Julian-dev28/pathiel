#!/usr/bin/env python3
"""The one writer for the scanned-event overlay
($PATHIEL_STATE_DIR/intel/scanned_events.json).

Operator, 2026-11-05: "a scan every 12 hours to get new events and wire them
into our trading decision". The curated calendar
(services/intel/data/scheduled_events.json) stays hand-edited and committed;
everything a scan finds (scripts/event_scan.py, or the event-calendar skill's
research agent) goes through this script into the overlay, which
services/intel/scheduled_events.load() merges in. A curated row wins on a
(date, kind, title) conflict.

    python3 scripts/event_add.py --date 2026-11-10 --event "Senate Foreign Relations hearing: Iran" \\
        --kind congress_hearing --coins oil --impact 1 --verified --source https://...
    python3 scripts/event_add.py --json '{"date": ..., "at_utc": null, ...}'
    python3 scripts/event_add.py --list
    python3 scripts/event_add.py --prune-before 2026-11-01

Rules, all enforced here and tested in services/intel/tests/test_event_add.py:
  - a row is validated exactly like the curated file (scheduled_events.validate_event);
  - the same date + kind + normalized title updates the row in place (no duplicates);
  - impact 3 is refused unless --allow-impact-3 is passed. Impact 3 blocks new
    entries; the operator's rule keeps it to FOMC, CPI, OPEC+ ministerials, a
    national election, or a dated war/sanctions/peace deadline;
  - a row the curated file already has is reported and not written;
  - the write is atomic (tmp file + os.replace).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from services.intel import scheduled_events as S  # noqa: E402

OVERLAY_VERSION = 1
IMPACT_3_RULE = ("impact 3 blocks new entries; only FOMC, CPI, an OPEC+ ministerial, a national election "
                 "or a dated war/sanctions/peace deadline qualifies. Pass --allow-impact-3 if this is one.")
# Fields whose change counts as an update (scanned_at alone does not).
CONTENT_FIELDS = tuple(k for k in S.REQUIRED_FIELDS + S.OPTIONAL_FIELDS if k not in ("origin", "scanned_at"))


class ImpactRefused(ValueError):
    """Impact 3 without --allow-impact-3."""


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def load_overlay(path: Optional[str] = None) -> Dict[str, Any]:
    data = S.read_overlay(path)
    data.setdefault("version", OVERLAY_VERSION)
    return data


def save_overlay(data: Dict[str, Any], path: Optional[str] = None) -> str:
    """Atomic: write <path>.tmp, then os.replace. Rows sorted by date so the
    file reads like the curated one."""
    path = path or S.overlay_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    data = dict(data)
    data["version"] = OVERLAY_VERSION
    data["updated"] = utc_now()
    data["events"] = sorted(data.get("events") or [], key=lambda e: (e["date"], e.get("at_utc") or "", e["kind"]))
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh, indent=1, sort_keys=False)
        fh.write("\n")
    os.replace(tmp, path)
    return path


def _curated_keys(curated_path: str) -> set:
    try:
        with open(curated_path) as fh:
            return {S.event_key(e) for e in json.load(fh).get("events") or []}
    except (OSError, ValueError, KeyError):
        return set()


def add_event(overlay: Dict[str, Any], row: Dict[str, Any], *, allow_impact_3: bool = False,
              now: Optional[str] = None, curated_path: str = S.DATA_PATH) -> Tuple[str, Dict[str, Any]]:
    """Validate `row` and put it in `overlay` (in memory; the caller saves).
    Returns (status, stored_row) with status one of:
      added      new (date, kind, title)
      updated    same key, some content field changed
      unchanged  same key, same content
      curated    the curated file already has this key; nothing written
    Raises ValueError on a bad row, ImpactRefused on impact 3 without the flag."""
    row = dict(row)
    row.setdefault("at_utc", None)
    row.setdefault("verified", False)
    row["origin"] = S.SCAN_ORIGIN
    row["scanned_at"] = now or utc_now()
    S.validate_event(row)
    if row["impact"] == 3 and not allow_impact_3:
        raise ImpactRefused(f"{row['date']} {row['event']!r}: {IMPACT_3_RULE}")
    key = S.event_key(row)
    if key in _curated_keys(curated_path):
        return "curated", row
    events: List[Dict[str, Any]] = overlay.setdefault("events", [])
    for i, cur in enumerate(events):
        if S.event_key(cur) == key:
            changed = any(_differs(k, cur.get(k), row[k]) for k in CONTENT_FIELDS if k in row)
            if not changed:
                return "unchanged", cur
            # The new row replaces the old one field by field; a field the new
            # row leaves out (a note, before_h) survives. scanned_at = this scan.
            merged = dict(cur)
            merged.update(row)
            events[i] = merged
            return "updated", merged
    events.append(row)
    return "added", row


def _differs(field: str, old: Any, new: Any) -> bool:
    """A title that only changed case or punctuation is the same title."""
    if field == "event":
        return S.normalize_title(old or "") != S.normalize_title(new or "")
    return old != new


def remove_event(overlay: Dict[str, Any], key: Tuple[str, str, str]) -> bool:
    events = overlay.setdefault("events", [])
    n = len(events)
    overlay["events"] = [e for e in events if S.event_key(e) != key]
    return len(overlay["events"]) < n


def prune_before(overlay: Dict[str, Any], day: str) -> int:
    """Drop overlay rows dated before `day` (YYYY-MM-DD). Returns how many."""
    datetime.strptime(day, "%Y-%m-%d")
    events = overlay.setdefault("events", [])
    kept = [e for e in events if e["date"] >= day]
    overlay["events"] = kept
    return len(events) - len(kept)


def format_row(e: Dict[str, Any]) -> str:
    at = e.get("at_utc") or "all day"
    v = "verified" if e.get("verified") else "unverified"
    return (f"{e['date']} {at:<17} i{e['impact']} {e['kind']:<18} {','.join(e['coins']):<10} "
            f"{e['event'][:90]} [{v}]")


# ── CLI ─────────────────────────────────────────────────────────────────────

def _row_from_args(a: argparse.Namespace) -> Dict[str, Any]:
    if a.json:
        row = json.loads(a.json)
        if not isinstance(row, dict):
            raise ValueError("--json must be one object")
        return row
    row: Dict[str, Any] = {
        "date": a.date, "at_utc": a.at_utc, "event": a.event, "kind": a.kind,
        "coins": [c.strip() for c in (a.coins or "").split(",") if c.strip()],
        "impact": a.impact, "verified": bool(a.verified), "source": a.source,
    }
    if a.note:
        row["note"] = a.note
    if a.before_h is not None:
        row["before_h"] = a.before_h
    if a.after_h is not None:
        row["after_h"] = a.after_h
    return row


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--path", help="overlay file (default: $PATHIEL_STATE_DIR/intel/scanned_events.json)")
    p.add_argument("--list", action="store_true", help="print the overlay rows and exit")
    p.add_argument("--prune-before", metavar="DATE", help="drop overlay rows dated before DATE")
    p.add_argument("--json", help="the whole row as one JSON object")
    p.add_argument("--date")
    p.add_argument("--at-utc", dest="at_utc", help="YYYY-MM-DDTHH:MMZ; omit for all day or unknown")
    p.add_argument("--event")
    p.add_argument("--kind")
    p.add_argument("--coins", help="comma list from oil,metals (silver = the metals group, the old name)")
    p.add_argument("--impact", type=int)
    p.add_argument("--verified", action="store_true", help="an official page states the date")
    p.add_argument("--source")
    p.add_argument("--note")
    p.add_argument("--before-h", dest="before_h", type=float)
    p.add_argument("--after-h", dest="after_h", type=float)
    p.add_argument("--allow-impact-3", action="store_true", help=IMPACT_3_RULE)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    a = build_parser().parse_args(argv)
    path = a.path or S.overlay_path()
    overlay = load_overlay(path)
    if a.list:
        rows = overlay.get("events") or []
        print(f"{len(rows)} scanned row(s) in {path}")
        for e in sorted(rows, key=lambda e: (e["date"], e.get("at_utc") or "")):
            print(format_row(e))
        return 0
    if a.prune_before:
        n = prune_before(overlay, a.prune_before)
        save_overlay(overlay, path)
        print(f"pruned {n} row(s) before {a.prune_before}; {len(overlay['events'])} left in {path}")
        return 0
    try:
        row = _row_from_args(a)
        status, stored = add_event(overlay, row, allow_impact_3=a.allow_impact_3)
    except ImpactRefused as exc:
        print(f"REFUSED {exc}", file=sys.stderr)
        return 3
    except ValueError as exc:
        print(f"INVALID {exc}", file=sys.stderr)
        return 2
    if status in ("added", "updated"):
        save_overlay(overlay, path)
    print(f"{status.upper()} {format_row(stored)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
