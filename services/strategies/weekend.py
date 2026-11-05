"""The oil futures weekend, one definition for the live desk and the sim.

Operator, 2026-11-05: "weekend flat". The window is Friday 16:00 to Sunday
18:00 New York time (the CME/ICE close to reopen): Fri 21:00Z to Sun 23:00Z
in US winter, Fri 20:00Z to Sun 22:00Z in US summer; zoneinfo does the
switch. The xyz HIP-3 perps trade straight through it, CME is shut, and
weekend headlines gap on the Monday reopen.

Live rule (BRENT_STOP_BUFFER_TEST, study B rerun 2026-11-05 at 6% risk,
variant `wk_flat`): no NEW Brent/WTI entry or top-up inside the window, and
an open oil position is closed at the window open. Beat live on 5 of 8 Brent
datasets, lost 1, geo 1.033, plateau; tied the two thesis datasets exactly.
Silver was worse with it (0 of 3, geo 0.80) and gold is untested, so
`OIL_COINS` only.

`weekend_closed(t_ms)` is the definition `desk_tick_sim` imports (its
`Params.weekend_off = "flatten"` is the backtest twin of this rule); the
live gate (`event_blackout.event_blackout`), the brief, `trigger_watch.py`
and `scripts/weekend.py` read `oil_weekend` / `next_oil_weekend` below.
Everything here is pure: the caller passes the clock.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")
WEEKEND_CLOSE_NY = (4, 16 * 60)     # Friday 16:00 New York (weekday, minute of day)
WEEKEND_OPEN_NY = (6, 18 * 60)      # Sunday 18:00 New York
OIL_COINS = frozenset({"xyz:BRENTOIL", "xyz:CL"})
DAY = timedelta(days=1)


def weekend_closed(t_ms: int) -> bool:
    """True when `t_ms` falls in the oil futures weekend: Friday 16:00 to
    Sunday 18:00 New York time (Fri 21:00 to Sun 23:00 UTC in winter, Fri
    20:00 to Sun 22:00 UTC in summer; zoneinfo does the switch). Pure."""
    lt = datetime.fromtimestamp(t_ms / 1000.0, tz=timezone.utc).astimezone(NEW_YORK)
    wd, minute = lt.weekday(), lt.hour * 60 + lt.minute
    if wd == 5:
        return True
    if wd == WEEKEND_CLOSE_NY[0]:
        return minute >= WEEKEND_CLOSE_NY[1]
    if wd == WEEKEND_OPEN_NY[0]:
        return minute < WEEKEND_OPEN_NY[1]
    return False


def _ny_ms(day, minute_of_day: int) -> int:
    """`day` (a date) at `minute_of_day` New York wall time, in ms UTC."""
    local = datetime(day.year, day.month, day.day, minute_of_day // 60, minute_of_day % 60, tzinfo=NEW_YORK)
    return int(local.timestamp() * 1000)


def weekend_window(t_ms: int) -> Tuple[int, int]:
    """[start_ms, end_ms) of the weekend window `t_ms` sits in, or of the
    next one when `t_ms` is a weekday. `weekend_closed(t)` is exactly
    `start <= t < end` (test_weekend.py walks a year to pin it)."""
    lt = datetime.fromtimestamp(t_ms / 1000.0, tz=timezone.utc).astimezone(NEW_YORK)
    friday = lt.date() - timedelta(days=(lt.weekday() - WEEKEND_CLOSE_NY[0]) % 7)   # the Friday on or before
    start, end = _ny_ms(friday, WEEKEND_CLOSE_NY[1]), _ny_ms(friday + 2 * DAY, WEEKEND_OPEN_NY[1])
    if t_ms >= end:                                      # Sunday evening or later: next week's window
        friday += 7 * DAY
        start, end = _ny_ms(friday, WEEKEND_CLOSE_NY[1]), _ny_ms(friday + 2 * DAY, WEEKEND_OPEN_NY[1])
    return start, end


def _utc(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%MZ")


def next_oil_weekend(coin: str, at_ms: int) -> Optional[Dict[str, Any]]:
    """The weekend window that has not ended yet (the active one when inside
    it) for an oil coin, for display and for the watcher. None for any other
    coin. `active` says whether `at_ms` is inside it."""
    if coin not in OIL_COINS:
        return None
    start, end = weekend_window(int(at_ms))
    return {"coin": coin, "kind": "weekend", "start_ms": start, "end_ms": end,
            "start_utc": _utc(start), "until_utc": _utc(end), "active": start <= int(at_ms) < end}


def oil_weekend(coin: str, at_ms: int) -> Optional[Dict[str, Any]]:
    """The active weekend window for an oil coin at `at_ms`, or None. The
    same shape as the other blackouts (`reason`, `until_utc`, `kind`) plus
    `flatten: True`: unlike the EIA, event and headline windows this one also
    closes an open position (the desk-tick agent does it, via
    scripts/weekend.py). Pure."""
    nxt = next_oil_weekend(coin, at_ms)
    if not nxt or not nxt["active"]:
        return None
    return {"coin": coin, "event": "oil futures weekend", "kind": "weekend", "flatten": True,
            "start_ms": nxt["start_ms"], "end_ms": nxt["end_ms"],
            "start_utc": nxt["start_utc"], "until_utc": nxt["until_utc"],
            "reason": f"Weekend flat: no new oil entries until {nxt['until_utc']} (oil futures weekend)"}


__all__ = ["DAY", "NEW_YORK", "OIL_COINS", "WEEKEND_CLOSE_NY", "WEEKEND_OPEN_NY", "next_oil_weekend",
           "oil_weekend", "weekend_closed", "weekend_window"]
