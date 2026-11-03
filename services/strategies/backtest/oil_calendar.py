"""Oil calendar: deterministic timestamps for Brent's scheduled events,
2026-11-03 (docs/strategy/OIL_EVENT_TIMING_TEST_2026-11-03.md).

Operator, 2026-11-03: "sure" to testing whether Brent behaves differently
around its own scheduled events (crypto has none of these; the earlier
"trade only 07:00-21:00 UTC" session idea was rejected on crypto-style
reasoning, not this). Pure module, no network, no I/O: every function
takes a window and returns timestamps or True/False. The runnable study
lives at `services.strategies.evals.oil_event_timing_test`; the simulator
hook that turns these into a live-desk filter is `desk_tick_sim.Params.
event_blackout_windows` / `in_event_blackout`.

Four event families:

1. **EIA Weekly Petroleum Status Report** (`eia_report_times`): normally
   Wednesday 10:30 ET. Shifts to Thursday 12:00 ET in a week whose Monday
   OR whose Wednesday itself is a US federal holiday -- see "EIA holiday
   rule" below for how that was derived and checked.
2. **API inventory report** (`api_report_times`): Tuesday 16:30 ET, no
   holiday rule applied (API Weekly Statistical Bulletin releases are not
   on the US federal holiday calendar; verify by hand before trusting a
   holiday week here).
3. **OPEC+ / JMMC meetings** (`OPEC_EVENTS` / `opec_event_times`):
   hardcoded, sourced list, day-level only (no sourced release minute).
4. **Session structure** (`in_nymex_day_session`, `in_asia_session`;
   `desk_tick_sim.weekend_closed` covers the CME/ICE weekend window and is
   re-exported here as `weekend_closed` for one import site).

EIA holiday rule
-----------------
The EIA's own 2026 schedule page (eia.gov/petroleum/supply/weekly/
schedule.php, fetched 2026-11-04) lists six 2026 releases that move off
the normal Wednesday 10:30 ET slot, all to Thursday 12:00 ET:

    week of          holiday           shifted release
    2026-01-19        MLK Day           2026-01-22 (Thu)
    2026-02-16        Presidents Day    2026-02-19 (Thu)
    2026-05-25        Memorial Day      2026-05-28 (Thu)
    2026-09-07        Labor Day         2026-09-10 (Thu)
    2026-10-12        Columbus Day      2026-10-15 (Thu)
    2026-11-11        Veterans Day      2026-11-12 (Thu)  -- the holiday
                                         IS that week's Wednesday

Every one of those six is reproduced exactly by one rule: compute that
week's Monday and Wednesday: if either date is a US federal holiday
(New Year's Day, MLK Day = 3rd Mon of Jan, Presidents Day = 3rd Mon of
Feb, Memorial Day = last Mon of May, Juneteenth, Independence Day, Labor
Day = 1st Mon of Sep, Columbus Day = 2nd Mon of Oct, Veterans Day = Nov
11, Thanksgiving = 4th Thu of Nov, Christmas), shift Wed 10:30 ET to Thu
12:00 ET; verified against the six dates above with no exceptions needed
for any of them (`test_oil_calendar.py::test_eia_2026_matches_the_sourced_
schedule`). `us_federal_holidays` computes the calendar by the rule, not
a hardcoded date list, so it generalizes to any year without a new lookup
table.

**Known gap, intentionally not covered**: year-end releases sometimes
move for reasons this rule does not model -- e.g. the real EIA schedule
moved the week-ending-2024-12-27 release to Thursday 2025-01-02 at 11:00
ET (not 12:00), and a 2025-09-04 Labor Day week release printed at both
12:00 and 2:00 PM (a preliminary + final split some weeks use). Thanks-
giving week is NOT shifted by this rule (Thanksgiving is that week's
Thursday, not its Monday or Wednesday), which matches the sourced
schedule finding no Thanksgiving-week entry. The test window here (Sept
through early Nov 2026) touches only the Labor Day, Columbus Day and
Veterans Day shifts, all three sourced and reproduced exactly; nothing in
this window depends on the unmodeled year-end case.

OPEC+ / JMMC sourcing
---------------------
Checked by WebSearch, 2026-11-04. Day-level only -- no source gives a
release minute, so these are NOT run through the EIA/API-style precise-
timestamp machinery; `opec_event_times` returns a single UTC noon anchor
per date, documented as approximate in `OPEC_EVENTS`' own comments. Any
OPEC date not clearly sourced is left out on purpose (the operator's own
instruction: "if unsure of a date, leave it out and say so") -- see the
"left out" list below `OPEC_EVENTS`.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from services.strategies.backtest.desk_tick_sim import weekend_closed  # re-export, see module docstring

NEW_YORK = ZoneInfo("America/New_York")
HOUR_MS = 3_600_000
DAY_MS = 86_400_000

EIA_NORMAL_TIME = (10, 30)     # Wednesday 10:30 ET, the normal week
EIA_SHIFTED_TIME = (12, 0)     # Thursday 12:00 ET, a holiday-shifted week
API_TIME = (16, 30)            # Tuesday 16:30 ET, every week


# ── US federal holidays (computed by rule, not hardcoded dates) ───────────

def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The n-th (1-based) `weekday` (Monday=0) of `year`-`month`."""
    d = date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    d += timedelta(days=offset + 7 * (n - 1))
    assert d.month == month, f"no {n}-th weekday {weekday} in {year}-{month:02d}"
    return d


def _last_weekday(year: int, month: int, weekday: int) -> date:
    """The last `weekday` (Monday=0) of `year`-`month`."""
    if month == 12:
        nxt = date(year + 1, 1, 1)
    else:
        nxt = date(year, month + 1, 1)
    d = nxt - timedelta(days=1)
    d -= timedelta(days=(d.weekday() - weekday) % 7)
    return d


def us_federal_holidays(year: int) -> Dict[date, str]:
    """The US federal holidays this module's EIA rule checks against, for
    `year`. Fixed-date holidays are not shifted to the nearest weekday
    here (the EIA rule below only cares whether the ACTUAL date lands on
    the week's Monday or Wednesday, not on the federal in-lieu-of
    observance). Pure."""
    return {
        date(year, 1, 1): "New Year's Day",
        _nth_weekday(year, 1, 0, 3): "Martin Luther King Jr. Day",     # 3rd Monday of January
        _nth_weekday(year, 2, 0, 3): "Presidents Day",                 # 3rd Monday of February
        _last_weekday(year, 5, 0): "Memorial Day",                     # last Monday of May
        date(year, 6, 19): "Juneteenth",
        date(year, 7, 4): "Independence Day",
        _nth_weekday(year, 9, 0, 1): "Labor Day",                      # 1st Monday of September
        _nth_weekday(year, 10, 0, 2): "Columbus Day",                  # 2nd Monday of October
        date(year, 11, 11): "Veterans Day",
        _nth_weekday(year, 11, 3, 4): "Thanksgiving Day",              # 4th Thursday of November
        date(year, 12, 25): "Christmas Day",
    }


# ── EIA Weekly Petroleum Status Report ─────────────────────────────────────

def _wednesdays_touching(start_ms: int, end_ms: int) -> List[date]:
    """Every calendar date that is a Wednesday in New York local time,
    whose report window could fall in [start_ms, end_ms) once a one-day
    pad on each side absorbs the Thursday shift and any UTC/NY offset."""
    if end_ms <= start_ms:
        return []
    lo = datetime.fromtimestamp(start_ms / 1000.0, tz=timezone.utc).astimezone(NEW_YORK).date() - timedelta(days=2)
    hi = datetime.fromtimestamp((end_ms - 1) / 1000.0, tz=timezone.utc).astimezone(NEW_YORK).date() + timedelta(days=2)
    out = []
    d = lo
    while d <= hi:
        if d.weekday() == 2:   # Wednesday
            out.append(d)
        d += timedelta(days=1)
    return out


def eia_report_dt(wednesday: date) -> Tuple[datetime, str]:
    """The EIA release's timezone-aware New York datetime for the week
    whose normal report day is `wednesday`, and why ("normal" or
    "shifted: <holiday>, <date>" -- see the module docstring's "EIA
    holiday rule"). Pure."""
    monday = wednesday - timedelta(days=2)
    holidays = {**us_federal_holidays(monday.year), **us_federal_holidays(wednesday.year)}
    hit = holidays.get(monday) or holidays.get(wednesday)
    if hit is not None:
        on = monday if monday in holidays else wednesday
        thursday = wednesday + timedelta(days=1)
        h, m = EIA_SHIFTED_TIME
        return datetime(thursday.year, thursday.month, thursday.day, h, m, tzinfo=NEW_YORK), f"shifted: {hit}, {on}"
    h, m = EIA_NORMAL_TIME
    return datetime(wednesday.year, wednesday.month, wednesday.day, h, m, tzinfo=NEW_YORK), "normal"


def eia_report_times(start_ms: int, end_ms: int) -> List[Tuple[int, str]]:
    """Every EIA report timestamp (UTC ms) in [start_ms, end_ms), oldest
    first, paired with why it sits where it does. Pure."""
    out = []
    for wed in _wednesdays_touching(start_ms, end_ms):
        dt, why = eia_report_dt(wed)
        t = int(dt.astimezone(timezone.utc).timestamp() * 1000)
        if start_ms <= t < end_ms:
            out.append((t, why))
    return sorted(out)


# ── API inventory report ────────────────────────────────────────────────────

def api_report_times(start_ms: int, end_ms: int) -> List[int]:
    """Every Tuesday 16:30 ET timestamp (UTC ms) in [start_ms, end_ms),
    oldest first. No holiday rule (see module docstring). Pure."""
    if end_ms <= start_ms:
        return []
    lo = datetime.fromtimestamp(start_ms / 1000.0, tz=timezone.utc).astimezone(NEW_YORK).date() - timedelta(days=1)
    hi = datetime.fromtimestamp((end_ms - 1) / 1000.0, tz=timezone.utc).astimezone(NEW_YORK).date() + timedelta(days=1)
    out = []
    d = lo
    h, m = API_TIME
    while d <= hi:
        if d.weekday() == 1:   # Tuesday
            dt = datetime(d.year, d.month, d.day, h, m, tzinfo=NEW_YORK)
            t = int(dt.astimezone(timezone.utc).timestamp() * 1000)
            if start_ms <= t < end_ms:
                out.append(t)
        d += timedelta(days=1)
    return sorted(out)


# ── OPEC+ / JMMC meetings (hardcoded, sourced; WebSearch 2026-11-04) ───────
#
# Day-level only -- no source below gives a release minute, so each entry
# anchors at 12:00 UTC (a neutral midday mark, not a sourced release time).
# Left out on purpose (not clearly sourced to one date): the exact JMMC
# meeting numbering/cadence for 2026 (64th JMMC 2026-02-01 and 65th JMMC
# 2026-04-05 are sourced but fall outside this test's window; the 2026-08
# JMMC date reported by one wire only, "August 2, 2026", was not
# corroborated by a second source and is left out); the actual OUTCOME of
# the 2026-11-01 meeting (it is sourced only as the date the group itself
# named for its next meeting, in the 2026-10-04 statement -- the meeting
# itself post-dates this module's data).
OPEC_EVENTS: Tuple[Tuple[int, int, int, str], ...] = (
    (2026, 9, 6, "OPEC+ (7 of the 8 voluntary producers; Saudi Arabia, Russia, Iraq, Kuwait, Kazakhstan, Algeria, "
                "Oman) monthly virtual meeting: held October output flat at September levels. "
                "opec.org/pr-detail/613-6-september-2026.html"),
    (2026, 10, 4, "OPEC+ (same 7) monthly virtual meeting: held November output flat at September levels "
                 "(~31.01 mb/d combined); named 2026-11-01 as the next meeting. One wire also called it the "
                 "68th JMMC. opec.org/pr-detail/616-4-october-2026.html"),
    (2026, 11, 1, "OPEC+ next scheduled monthly meeting, per the 2026-10-04 statement's own next-meeting date. "
                 "Outcome not sourced (post-dates this module). opec.org/pr-detail/616-4-october-2026.html"),
)


def opec_event_times(start_ms: Optional[int] = None, end_ms: Optional[int] = None) -> List[Tuple[int, str]]:
    """`OPEC_EVENTS` as UTC-ms timestamps (12:00 UTC anchor) and their
    citation, oldest first, filtered to [start_ms, end_ms) when given.
    Pure."""
    out = []
    for y, m, d, why in OPEC_EVENTS:
        t = int(datetime(y, m, d, 12, 0, tzinfo=timezone.utc).timestamp() * 1000)
        if (start_ms is None or t >= start_ms) and (end_ms is None or t < end_ms):
            out.append((t, why))
    return sorted(out)


# ── blackout windows (feeds desk_tick_sim.Params.event_blackout_windows) ──

def blackout_windows(event_times_ms: Sequence[int], before_hours: float, after_hours: float,
                     merge: bool = True) -> Tuple[Tuple[int, int], ...]:
    """`[event - before_hours, event + after_hours)` per timestamp in
    `event_times_ms`, as UTC ms. `before_hours`/`after_hours` may be 0 (a
    one-sided window) but not negative. With `merge` (default), adjacent
    or overlapping windows collapse into one (two EIA weeks never overlap
    at realistic before/after hours, but the merge keeps the result valid
    even if a caller passes a wide window or a dense event list). Pure."""
    if before_hours < 0 or after_hours < 0:
        raise ValueError("before_hours/after_hours must be >= 0")
    before_ms, after_ms = int(before_hours * HOUR_MS), int(after_hours * HOUR_MS)
    windows = sorted((t - before_ms, t + after_ms) for t in event_times_ms)
    if not merge or not windows:
        return tuple(windows)
    out = [windows[0]]
    for start, end in windows[1:]:
        last_start, last_end = out[-1]
        if start <= last_end:
            out[-1] = (last_start, max(last_end, end))
        else:
            out.append((start, end))
    return tuple(out)


# ── session structure ───────────────────────────────────────────────────────
#
# NYMEX/ICE day-session hours: the historical open-outcry "day session"
# for NYMEX energy futures (WTI/Brent-linked), still the session commonly
# used for the daily settlement window, 09:00-14:30 New York time
# (CME Group's published RTH for energy products). Asia hours: the
# Singapore/Tokyo trading day that precedes London's open, 01:00-07:00 UTC
# (Singapore and Tokyo do not observe DST, so this is expressed in UTC,
# not a zoneinfo-converted local time, unlike the day session above).
NYMEX_DAY_SESSION_NY = ((9, 0), (14, 30))     # (open, close), hour, minute, New York local time
ASIA_SESSION_UTC = (1, 7)                     # (open, close) UTC hour, no DST


def in_nymex_day_session(t_ms: int) -> bool:
    """Is `t_ms` inside the NYMEX/ICE day session (09:00-14:30 New York
    local time), any day of the week? Pure; combine with `weekend_closed`
    to also exclude the weekend."""
    lt = datetime.fromtimestamp(t_ms / 1000.0, tz=timezone.utc).astimezone(NEW_YORK)
    (oh, om), (ch, cm) = NYMEX_DAY_SESSION_NY
    minute = lt.hour * 60 + lt.minute
    return oh * 60 + om <= minute < ch * 60 + cm


def in_asia_session(t_ms: int) -> bool:
    """Is `t_ms` inside Asia hours (01:00-07:00 UTC)? Pure."""
    hr = (t_ms // HOUR_MS) % 24
    lo, hi = ASIA_SESSION_UTC
    return lo <= hr < hi


__all__ = ["NEW_YORK", "EIA_NORMAL_TIME", "EIA_SHIFTED_TIME", "API_TIME", "us_federal_holidays", "eia_report_dt",
          "eia_report_times", "api_report_times", "OPEC_EVENTS", "opec_event_times", "blackout_windows",
          "NYMEX_DAY_SESSION_NY", "ASIA_SESSION_UTC", "in_nymex_day_session", "in_asia_session", "weekend_closed"]
