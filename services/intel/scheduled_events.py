"""Scheduled market events the rule generators in calendar_us.py cannot
compute: elections, jobs/PCE/GDP/PPI releases, OPEC and IEA monthly reports,
funding deadlines, the new Congress, holidays, contract expiries, China data.

Operator, 2026-11-04/05, after the midterm results dropped Brent 9% with no
election on the desk's calendar: "I NEED YOU TO AUDIT THE EVENT CALENDAR AND
TAKE THEM INTO ACCOUNT WHEN YOU MAKE DECISIONS".

The data lives in data/scheduled_events.json (researched, sourced, a
`verified` flag per row, a `valid_through` date). This module only reads it.
Pure given the file and the clock passed in. FOMC decisions, CPI, OPEC+
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
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Dict, List, Optional

DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "scheduled_events.json")
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


@lru_cache(maxsize=4)
def load(path: str = DATA_PATH) -> Dict[str, Any]:
    with open(path) as fh:
        return json.load(fh)


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
            "note": ev.get("note"), "window": win, "origin": "scheduled_events.json"}


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
    """'Fri 11-06 13:30Z US jobs report (Oct NFP)' style, with a blackout tag."""
    when = datetime.fromtimestamp(r["at_ms"] / 1000, tz=timezone.utc)
    t = when.strftime("%a %m-%d") + ("" if r["all_day"] else when.strftime(" %H:%MZ"))
    tag = " [blackout]" if r["window"] else ""
    unv = "" if r["verified"] else " (unverified)"
    return f"{t} {r['title']}{tag}{unv}"


__all__ = ["BLACKOUT_IMPACT", "COIN_GROUPS", "DATA_PATH", "blackout", "calendar_warning", "coins_for",
           "load", "market_events", "short_line"]
