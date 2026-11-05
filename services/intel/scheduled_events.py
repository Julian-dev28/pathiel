"""Scheduled market events the rule generators in calendar_us.py cannot
compute: elections, jobs/PCE/GDP/PPI releases, OPEC and IEA monthly reports,
funding deadlines, the new Congress, holidays, contract expiries, China data.

Operator, 2026-11-04/05, after the midterm results dropped Brent 9% with no
election on the desk's calendar: "I NEED YOU TO AUDIT THE EVENT CALENDAR AND
TAKE THEM INTO ACCOUNT WHEN YOU MAKE DECISIONS".

The data lives in data/scheduled_events.json (researched, sourced, a
`verified` flag per row, a `valid_through` date) plus an overlay the
12-hour scan writes at $PATHIEL_STATE_DIR/intel/scanned_events.json
(scripts/event_scan.py, scripts/event_add.py; operator 2026-11-05: "a scan
every 12 hours to get new events and wire them into our trading decision").
`load()` merges both; a curated row wins on a (date, kind, title) conflict,
and the cache is keyed on both files' mtimes so a running MCP server or
trigger_watch sees a scan on its next call. This module only reads them.
Pure given the files and the clock passed in. FOMC decisions, CPI, OPEC+
meetings, EIA, API, Baker Hughes and CFTC stay in calendar_us.py;
`market_events` merges both into one list.

Two consumers:
  - services/intel/sources/catalysts.py: every row in the desk's window
    becomes a Catalyst, so the thesis writer sees it.
  - services/strategies/event_blackout.py: impact-3 rows (and FOMC, CPI,
    OPEC+) block new entries in their window (BLACKOUT_IMPACT).
"""
from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from services.intel import env

DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "scheduled_events.json")
OVERLAY_NAME = "scanned_events.json"
CURATED_ORIGIN = "scheduled_events.json"
SCAN_ORIGIN = "scan"
FILE_ORIGINS = frozenset({CURATED_ORIGIN, SCAN_ORIGIN})
HOUR_MS = 3_600_000
DAY_MS = 24 * HOUR_MS

# Which pathiel coins an event's "coins" group covers.
COIN_GROUPS: Dict[str, frozenset] = {
    "oil": frozenset({"xyz:BRENTOIL", "xyz:CL"}),
    "silver": frozenset({"xyz:SILVER"}),
}

# An impact-3 event blocks new entries from BEFORE_H before to AFTER_H after
# it: the window the EIA test adopted (OIL_EVENT_TIMING_TEST_2026-11-03.md,
# eia_2_2: won 5 of 9 sets, tied 4, never lost). A row may widen it with its
# own before_h/after_h (the midterms: from 24h before the polls closed to
# 18h after). An all-day row with no time covers its whole UTC day.
BLACKOUT_IMPACT = 3
BEFORE_H = 2.0
AFTER_H = 2.0

# Rule-generated events that block too (calendar_us.py): kind -> (title, coins).
GENERATED_BLOCKING = {
    "fomc": ("FOMC decision", ("oil", "silver")),
    "cpi": ("US CPI release", ("oil", "silver")),
    "opec_meeting": ("OPEC+ meeting", ("oil",)),
}


def _parse_utc(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _utc(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%MZ")


# ── the row schema ──────────────────────────────────────────────────────────

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_AT_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}Z")
_KIND_RE = re.compile(r"[a-z][a-z0-9_]*")
REQUIRED_FIELDS = ("date", "at_utc", "event", "kind", "coins", "impact", "verified", "source")
OPTIONAL_FIELDS = ("note", "before_h", "after_h", "origin", "scanned_at")
IMPACTS = (1, 2, 3)


def validate_event(e: Any) -> Dict[str, Any]:
    """Check one row against the file's schema; raise ValueError naming the
    first problem, else return the row. The curated file test, event_add.py
    and the scan all go through this one check."""
    if not isinstance(e, dict):
        raise ValueError(f"row is not an object: {e!r}")
    missing = [k for k in REQUIRED_FIELDS if k not in e]
    if missing:
        raise ValueError(f"missing {', '.join(missing)}")
    unknown = sorted(set(e) - set(REQUIRED_FIELDS) - set(OPTIONAL_FIELDS))
    if unknown:
        raise ValueError(f"unknown field(s) {', '.join(unknown)}")
    d = e["date"]
    if not (isinstance(d, str) and _DATE_RE.fullmatch(d)):
        raise ValueError(f"date must be YYYY-MM-DD, got {d!r}")
    try:
        datetime.strptime(d, "%Y-%m-%d")
    except ValueError:
        raise ValueError(f"date is not a real day: {d!r}") from None
    at = e["at_utc"]
    if at is not None:
        if not (isinstance(at, str) and _AT_RE.fullmatch(at)):
            raise ValueError(f"at_utc must be YYYY-MM-DDTHH:MMZ or null, got {at!r}")
        try:
            _parse_utc(at)
        except ValueError:
            raise ValueError(f"at_utc is not a real time: {at!r}") from None
    if not (isinstance(e["event"], str) and e["event"].strip()):
        raise ValueError("event must be a non-empty title")
    if not (isinstance(e["kind"], str) and _KIND_RE.fullmatch(e["kind"])):
        raise ValueError(f"kind must match [a-z][a-z0-9_]*, got {e['kind']!r}")
    coins = e["coins"]
    if not (isinstance(coins, list) and coins and set(coins) <= set(COIN_GROUPS)):
        raise ValueError(f"coins must be a non-empty list from {sorted(COIN_GROUPS)}, got {coins!r}")
    if type(e["impact"]) is not int or e["impact"] not in IMPACTS:
        raise ValueError(f"impact must be 1, 2 or 3, got {e['impact']!r}")
    if not isinstance(e["verified"], bool):
        raise ValueError(f"verified must be true or false, got {e['verified']!r}")
    if not (isinstance(e["source"], str) and e["source"].strip()):
        raise ValueError("source must be a non-empty string (a URL or a page)")
    if e.get("note") is not None and not isinstance(e["note"], str):
        raise ValueError("note must be a string")
    for k in ("before_h", "after_h"):
        v = e.get(k)
        if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0):
            raise ValueError(f"{k} must be a number of hours >= 0, got {v!r}")
    if e.get("origin") is not None and e["origin"] not in FILE_ORIGINS:
        raise ValueError(f"origin must be one of {sorted(FILE_ORIGINS)}, got {e['origin']!r}")
    if e.get("scanned_at") is not None and not isinstance(e["scanned_at"], str):
        raise ValueError("scanned_at must be a string")
    return e


def normalize_title(s: str) -> str:
    """Lowercase, punctuation out, one space between words: the dedup key."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).split())


def event_key(e: Dict[str, Any]) -> Tuple[str, str, str]:
    """(date, kind, normalized title): the identity a row is deduped on."""
    return (e["date"], e["kind"], normalize_title(e["event"]))


# ── the files ───────────────────────────────────────────────────────────────

def overlay_path() -> str:
    """$PATHIEL_STATE_DIR/intel/scanned_events.json, resolved on every call
    so the MCP server, trigger_watch and a test's tmp_path all agree."""
    return os.path.join(env.state_dir(), "intel", OVERLAY_NAME)


def _sig(path: str) -> Optional[Tuple[int, int]]:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def read_overlay(path: Optional[str] = None) -> Dict[str, Any]:
    """The scan overlay as written by scripts/event_add.py; an empty one when
    the file is missing or unreadable (a missing overlay is the normal case
    on a fresh checkout)."""
    path = path or overlay_path()
    try:
        with open(path) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {"version": 1, "events": [], "state": {}}
    if not isinstance(data, dict):
        return {"version": 1, "events": [], "state": {}}
    data.setdefault("events", [])
    data.setdefault("state", {})
    return data


def merge(curated: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    """Pure. The curated file's dict with the overlay's rows appended, except
    where a curated row has the same (date, kind, normalized title): the
    curated row wins. Every overlay row carries origin "scan"."""
    out = dict(curated)
    rows = list(curated.get("events") or [])
    taken = {event_key(e) for e in rows}
    scanned = 0
    for e in overlay.get("events") or []:
        try:
            validate_event(e)
        except ValueError:
            continue
        if event_key(e) in taken:
            continue
        taken.add(event_key(e))
        rows.append(dict(e, origin=SCAN_ORIGIN))
        scanned += 1
    out["events"] = rows
    out["scanned"] = scanned
    return out


_CACHE: Dict[Tuple[str, str], Tuple[Tuple[Any, Any], Dict[str, Any]]] = {}


def load(path: str = DATA_PATH, overlay: Optional[str] = None) -> Dict[str, Any]:
    """The curated file merged with the scan overlay. Cached on both files'
    (mtime, size), so a long-running process re-reads within one call of a
    scan writing, and never re-parses when nothing changed."""
    overlay = overlay or overlay_path()
    key = (path, overlay)
    sig = (_sig(path), _sig(overlay))
    hit = _CACHE.get(key)
    if hit and hit[0] == sig:
        return hit[1]
    with open(path) as fh:
        curated = json.load(fh)
    data = merge(curated, read_overlay(overlay))
    _CACHE[key] = (sig, data)
    return data


def coins_for(groups: List[str]) -> frozenset:
    out: set = set()
    for g in groups:
        out |= COIN_GROUPS.get(g, frozenset())
    return frozenset(out)


def _window(at_ms: int, all_day: bool, impact: int, before_h: Optional[float],
            after_h: Optional[float]) -> Optional[Dict[str, int]]:
    if impact < BLACKOUT_IMPACT:
        return None
    if all_day:
        start, end = at_ms, at_ms + DAY_MS
        return {"start_ms": start - int((before_h or 0) * HOUR_MS), "end_ms": end + int((after_h or 0) * HOUR_MS)}
    b = BEFORE_H if before_h is None else before_h
    a = AFTER_H if after_h is None else after_h
    return {"start_ms": at_ms - int(b * HOUR_MS), "end_ms": at_ms + int(a * HOUR_MS)}


def _row(ev: Dict[str, Any]) -> Dict[str, Any]:
    all_day = not ev.get("at_utc")
    at = (datetime.strptime(ev["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc) if all_day
          else _parse_utc(ev["at_utc"]))
    at_ms = _ms(at)
    win = _window(at_ms, all_day, int(ev["impact"]), ev.get("before_h"), ev.get("after_h"))
    return {"title": ev["event"], "kind": ev["kind"], "impact": int(ev["impact"]),
            "coins": coins_for(ev["coins"]), "at_ms": at_ms, "all_day": all_day,
            "verified": bool(ev.get("verified")), "source": ev.get("source", ""),
            "note": ev.get("note"), "window": win,
            "origin": SCAN_ORIGIN if ev.get("origin") == SCAN_ORIGIN else CURATED_ORIGIN,
            "scanned_at": ev.get("scanned_at")}


def _generated(start: date, end: date) -> List[Dict[str, Any]]:
    from services.intel import calendar_us
    gens = {"fomc": calendar_us.fomc_releases, "cpi": calendar_us.cpi_releases,
            "opec_meeting": calendar_us.opec_plus_releases}
    out = []
    for kind, fn in gens.items():
        title, groups = GENERATED_BLOCKING[kind]
        for rel in fn(start, end):
            at_ms = _ms(rel.at_utc)
            # OPEC+ has no published time (calendar_us uses a 12:00Z placeholder):
            # block its whole UTC day instead of a guessed 4h.
            all_day = kind == "opec_meeting"
            if all_day:
                at_ms -= at_ms % DAY_MS
            out.append({"title": title, "kind": kind, "impact": 3, "coins": coins_for(list(groups)),
                        "at_ms": at_ms, "all_day": all_day, "verified": True, "source": "calendar_us.py",
                        "note": rel.note, "window": _window(at_ms, all_day, 3, None, None),
                        "origin": "calendar_us.py"})
    return out


def market_events(start_ms: int, end_ms: int, coin: Optional[str] = None,
                  path: str = DATA_PATH) -> List[Dict[str, Any]]:
    """Every event (file rows plus the generated FOMC/CPI/OPEC+) whose time or
    blackout window touches [start_ms, end_ms), for `coin` when given, sorted
    by time. Pure given the file."""
    rows = [_row(e) for e in load(path)["events"]]
    d0 = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).date() - timedelta(days=2)
    d1 = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc).date() + timedelta(days=2)
    rows += _generated(d0, d1)
    out = []
    for r in rows:
        if coin is not None and coin not in r["coins"]:
            continue
        lo = r["window"]["start_ms"] if r["window"] else r["at_ms"]
        hi = r["window"]["end_ms"] if r["window"] else r["at_ms"] + (DAY_MS if r["all_day"] else 1)
        if hi > start_ms and lo < end_ms:
            out.append(r)
    return sorted(out, key=lambda r: r["at_ms"])


def blackout(coin: str, at_ms: int, path: str = DATA_PATH) -> Optional[Dict[str, Any]]:
    """The impact-3 event window `at_ms` sits in for `coin`, or None."""
    for r in market_events(at_ms - 3 * DAY_MS, at_ms + 3 * DAY_MS, coin, path):
        w = r["window"]
        if w and w["start_ms"] <= at_ms < w["end_ms"]:
            return {"coin": coin, "event": r["title"], "kind": r["kind"], "start_ms": w["start_ms"],
                    "end_ms": w["end_ms"], "until_utc": _utc(w["end_ms"]),
                    "reason": f"Event blackout: no new entries until {_utc(w['end_ms'])} "
                              f"({r['title']}, impact 3)"}
    return None


def calendar_warning(now_ms: int, path: str = DATA_PATH, warn_days: int = 14) -> Optional[str]:
    """A line when the file runs out within `warn_days` (or already has)."""
    vt = datetime.strptime(load(path)["valid_through"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    left = (_ms(vt) - now_ms) / DAY_MS
    if left < warn_days:
        return (f"event calendar ends {vt.date()}: refresh services/intel/data/scheduled_events.json "
                f"(the event-calendar skill)")
    return None


def short_line(r: Dict[str, Any]) -> str:
    """'Fri 11-06 13:30Z US jobs report (Oct NFP)' style, with a blackout tag;
    a row the scan found ends with "(scan)" so the operator can tell it from
    the curated file."""
    when = datetime.fromtimestamp(r["at_ms"] / 1000, tz=timezone.utc)
    t = when.strftime("%a %m-%d") + ("" if r["all_day"] else when.strftime(" %H:%MZ"))
    tag = " [blackout]" if r["window"] else ""
    unv = "" if r["verified"] else " (unverified)"
    scan = " (scan)" if r.get("origin") == SCAN_ORIGIN else ""
    return f"{t} {r['title']}{tag}{unv}{scan}"


__all__ = ["BLACKOUT_IMPACT", "COIN_GROUPS", "CURATED_ORIGIN", "DATA_PATH", "FILE_ORIGINS", "SCAN_ORIGIN",
           "blackout", "calendar_warning", "coins_for", "event_key", "load", "market_events", "merge",
           "normalize_title", "overlay_path", "read_overlay", "short_line", "validate_event"]
