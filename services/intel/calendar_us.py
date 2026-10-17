"""Deterministic US calendar generators for scheduled catalysts.

Every date here is computed from a rule, in `America/New_York`, run through
`zoneinfo` so DST transitions (EDT <-> EST) are handled by the stdlib, not
by us. The one thing stdlib cannot give us is WHICH day is a federal
holiday from a name alone, or whether an agency's irregular-looking release
rule is actually regular — both of those were verified live against the
primary source on 2026-10-17 (see each function's docstring for the exact
URL and what the fetch showed) rather than assumed.

Two different confidence levels below, and they are labeled:
  - `federal_holidays`, `eia_weekly_release`, `steo_release_date` are a
    GENERAL RULE, verified by reproducing a published list of real
    examples exactly (see tests/test_calendar.py — it asserts against the
    live EIA holiday-exception table and the live STEO "next release"
    date, both fetched 2026-10-17).
  - `api_weekly_release`, `baker_hughes_release`, `cftc_cot_release` are an
    ASSUMPTION: the base weekday/time is well known, but no published
    holiday-shift table was found for any of the three (API and Baker
    Hughes are industry-association releases with no equivalent of EIA's
    public exception table; CFTC publishes COT on a fixed weekly cadence
    but a from-source-verified holiday-shift table was not found in the
    time available). The shift-to-next-business-day fallback they use is
    reasonable but UNVERIFIED — flagged again in the intel report.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, NamedTuple
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
UTC = timezone.utc

MON, TUE, WED, THU, FRI, SAT, SUN = range(7)


def _nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    d += timedelta(days=offset)
    d += timedelta(weeks=n - 1)
    return d


def _last_weekday_of_month(year: int, month: int, weekday: int) -> date:
    first_of_next = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    last_day = first_of_next - timedelta(days=1)
    offset = (last_day.weekday() - weekday) % 7
    return last_day - timedelta(days=offset)


def _observed(d: date) -> date:
    """Federal observance: a holiday landing on Saturday is observed the
    Friday before; on Sunday, the Monday after. 5 U.S.C. 6103."""
    if d.weekday() == SAT:
        return d - timedelta(days=1)
    if d.weekday() == SUN:
        return d + timedelta(days=1)
    return d


def federal_holidays(year: int) -> Dict[date, str]:
    """Observed US federal holidays for `year`. For 2026 this produces:
    Jan 1 (New Year's), Jan 19 (MLK, a Monday), Feb 16 (Washington's
    Birthday, a Monday), May 25 (Memorial Day, a Monday), Jun 19
    (Juneteenth, a Friday), Jul 3 observed (Independence Day, Jul 4, falls
    on a Saturday in 2026), Sep 7 (Labor Day, a Monday), Oct 12 (Columbus
    Day, a Monday), Nov 11 (Veterans Day — a WEDNESDAY in 2026, not a
    Monday; it is a fixed calendar date, not an "nth Monday" holiday),
    Nov 26 (Thanksgiving, a Thursday), Dec 25 (Christmas, a Friday). Cross-
    checked against the EIA weekly-report exception table in
    `eia_weekly_release`'s docstring — including Veterans Day, which is
    exactly why that function checks the Wednesday itself for a holiday,
    not only the preceding Monday."""
    raw = [
        (date(year, 1, 1), "New Year's Day"),
        (_nth_weekday_of_month(year, 1, MON, 3), "Martin Luther King Jr. Day"),
        (_nth_weekday_of_month(year, 2, MON, 3), "Washington's Birthday"),
        (_last_weekday_of_month(year, 5, MON), "Memorial Day"),
        (date(year, 6, 19), "Juneteenth"),
        (date(year, 7, 4), "Independence Day"),
        (_nth_weekday_of_month(year, 9, MON, 1), "Labor Day"),
        (_nth_weekday_of_month(year, 10, MON, 2), "Columbus Day"),
        (date(year, 11, 11), "Veterans Day"),
        (_nth_weekday_of_month(year, 11, THU, 4), "Thanksgiving Day"),
        (date(year, 12, 25), "Christmas Day"),
    ]
    return {_observed(d): name for d, name in raw}


def is_federal_holiday(d: date) -> bool:
    """Checks `d`'s year and its neighbors — an observed date can fall in
    the adjacent calendar year (e.g. Jan 1 on a Saturday observes Dec 31)."""
    for y in (d.year - 1, d.year, d.year + 1):
        if d in federal_holidays(y):
            return True
    return False


class Release(NamedTuple):
    at_utc: datetime
    note: str  # e.g. "shifted from Wed for Veterans Day"


def _et_datetime(d: date, hour: int, minute: int) -> datetime:
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=ET)


def _weekly_dates(start: date, end: date, weekday: int) -> List[date]:
    first_offset = (weekday - start.weekday()) % 7
    d = start + timedelta(days=first_offset)
    out = []
    while d <= end:
        out.append(d)
        d += timedelta(days=7)
    return out


def eia_weekly_release(start: date, end: date) -> List[Release]:
    """EIA Weekly Petroleum Status Report. Standard: Wednesday 10:30 ET.
    Verified rule (eia.gov/petroleum/supply/weekly/schedule.php, fetched
    2026-10-17): the report shifts to Thursday 12:00 ET whenever a federal
    holiday falls on the Monday OR the Wednesday of release week. The live
    page's own 2026 exception table lists exactly six shifts — Jan 22
    (MLK, a Monday holiday), Feb 19 (Presidents, Monday), May 28 (Memorial,
    Monday), Sep 10 (Labor, Monday), Oct 15 (Columbus, Monday), and Nov 12
    (Veterans Day — Nov 11, 2026 is a WEDNESDAY, so this is the one
    exception year's table that only a Monday-OR-Wednesday check
    reproduces; a Monday-only rule misses it). Checked exhaustively
    against all 6 real entries with zero false positives and zero misses
    before writing this function — see
    tests/test_calendar.py::test_eia_weekly_matches_published_2026_exceptions."""
    out = []
    for wed in _weekly_dates(start, end, WED):
        monday = wed - timedelta(days=2)
        holiday = is_federal_holiday(monday) or is_federal_holiday(wed)
        if holiday:
            thu = wed + timedelta(days=1)
            out.append(Release(_et_datetime(thu, 12, 0).astimezone(UTC),
                                "shifted from Wed (federal holiday Mon or Wed)"))
        else:
            out.append(Release(_et_datetime(wed, 10, 30).astimezone(UTC), "standard Wed 10:30 ET"))
    return out


def steo_release_date(year: int, month: int) -> date:
    """EIA Short-Term Energy Outlook. Verified rule (eia.gov/outlooks/steo/
    release_schedule.php, fetched 2026-10-17 — quoted verbatim): "released ...
    on the first Tuesday following the first Thursday of each month. ... If
    a Federal holiday falls on the Monday before the normal release date the
    release is delayed until Wednesday." Reproduces the live page's own
    stated dates exactly: first Thursday of Oct 2026 is Oct 1 -> Oct 6
    (matches "Forecast Completed: October 1, 2026" / next table entry);
    first Thursday of Nov 2026 is Nov 5 -> Nov 10 (matches the live page's
    "Next Release Date: November 10, 2026" at fetch time)."""
    first_thursday = _nth_weekday_of_month(year, month, THU, 1)
    tuesday = first_thursday + timedelta(days=5)
    monday_before = tuesday - timedelta(days=1)
    if is_federal_holiday(monday_before):
        return tuesday + timedelta(days=1)
    return tuesday


def steo_releases(start: date, end: date) -> List[Release]:
    out = []
    y, m = start.year, start.month
    while date(y, m, 1) <= end:
        d = steo_release_date(y, m)
        if start <= d <= end:
            out.append(Release(_et_datetime(d, 10, 0).astimezone(UTC), "monthly STEO"))
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


def api_weekly_release(start: date, end: date) -> List[Release]:
    """American Petroleum Institute weekly stock report. Base: Tuesday
    16:30 ET (industry convention, widely reported — e.g. Reuters/EIA
    commentary consistently cites "API data due Tuesday afternoon"). No
    published holiday-exception table was found (API does not publish one
    the way EIA does); shifting to Wednesday when Tuesday is a federal
    holiday is this module's own UNVERIFIED fallback, not a sourced fact."""
    out = []
    for tue in _weekly_dates(start, end, TUE):
        if is_federal_holiday(tue):
            wed = tue + timedelta(days=1)
            out.append(Release(_et_datetime(wed, 16, 30).astimezone(UTC),
                                "ASSUMPTION: shifted from Tue (holiday), unverified"))
        else:
            out.append(Release(_et_datetime(tue, 16, 30).astimezone(UTC), "standard Tue 16:30 ET"))
    return out


def baker_hughes_release(start: date, end: date) -> List[Release]:
    """Baker Hughes weekly US rig count. Base: Friday 13:00 ET (published
    convention, bakerhughes.com/rig-count). Holiday-shift-to-Thursday
    fallback is this module's own UNVERIFIED assumption."""
    out = []
    for fri in _weekly_dates(start, end, FRI):
        if is_federal_holiday(fri):
            thu = fri - timedelta(days=1)
            out.append(Release(_et_datetime(thu, 13, 0).astimezone(UTC),
                                "ASSUMPTION: shifted from Fri (holiday), unverified"))
        else:
            out.append(Release(_et_datetime(fri, 13, 0).astimezone(UTC), "standard Fri 13:00 ET"))
    return out


def cftc_cot_release(start: date, end: date) -> List[Release]:
    """CFTC Commitments of Traders. Base: Friday 15:30 ET (cftc.gov
    publishes "as of the preceding Tuesday" data every Friday). Holiday
    shift-to-Thursday fallback is this module's own UNVERIFIED assumption."""
    out = []
    for fri in _weekly_dates(start, end, FRI):
        if is_federal_holiday(fri):
            thu = fri - timedelta(days=1)
            out.append(Release(_et_datetime(thu, 15, 30).astimezone(UTC),
                                "ASSUMPTION: shifted from Fri (holiday), unverified"))
        else:
            out.append(Release(_et_datetime(fri, 15, 30).astimezone(UTC), "standard Fri 15:30 ET"))
    return out


# ── hardcoded, source-verified one-off calendars ─────────────────────────
# These are not rule-computable (meeting schedules are set by committee,
# not by a formula) so they are hardcoded and dated with the exact source
# fetched to verify them, per the intel brief's explicit instruction.

# FOMC decision dates (day 2 of each 2-day meeting; press conference same
# day 14:00 ET). Verified https://www.federalreserve.gov/json/calendar.json
# fetched 2026-10-17 02:54 UTC — every 2026 "FOMC Meeting" entry:
#   Jan 27-28, Mar 17-18, Apr 28-29, Jun 16-17, Jul 28-29, Sep 15-16,
#   Oct 27-28, Dec 8-9 (decision = second day in each pair).
# Canonical published page: federalreserve.gov/monetarypolicy/fomccalendars.htm
FOMC_DECISION_DATES_2026: List[date] = [
    date(2026, 1, 28), date(2026, 3, 18), date(2026, 4, 29), date(2026, 6, 17),
    date(2026, 7, 29), date(2026, 9, 16), date(2026, 10, 28), date(2026, 12, 9),
]

# CPI release dates, 08:30 ET, "Reference Month -> Release Date" from the
# live bls.gov/schedule/news_release/cpi.htm table. Direct fetch of that
# URL returned HTTP 403 (Akamai bot block) on 2026-10-17; the table below
# is read from the Wayback Machine's snapshot of the SAME live page taken
# 2026-10-13T00:44:40Z (http://web.archive.org/web/20261013004440/
# https://www.bls.gov/schedule/news_release/cpi.htm), four days before
# this module was written — not a direct fetch, the closest verifiable
# substitute available in this environment, called out explicitly rather
# than silently hardcoded as if confirmed live.
CPI_RELEASE_DATES: List[date] = [
    date(2026, 11, 10),  # October 2026 CPI
    date(2026, 12, 10),  # November 2026 CPI
    date(2027, 1, 13),   # December 2026 CPI
    date(2027, 2, 11),   # January 2027 CPI
    date(2027, 3, 10),   # February 2027 CPI
]

# OPEC+ meetings are announced by the secretariat, not scheduled by rule.
# Per the intel brief: "next known: Nov 1 2026." Time of day is not fixed
# publicly in advance for these ministerial calls; 12:00 UTC is a labeled
# placeholder, not a sourced time.
OPEC_PLUS_MEETINGS: List[date] = [date(2026, 11, 1)]


def fomc_releases(start: date, end: date) -> List[Release]:
    return [Release(_et_datetime(d, 14, 0).astimezone(UTC), "FOMC decision + press conference")
            for d in FOMC_DECISION_DATES_2026 if start <= d <= end]


def cpi_releases(start: date, end: date) -> List[Release]:
    return [Release(_et_datetime(d, 8, 30).astimezone(UTC), "CPI release")
            for d in CPI_RELEASE_DATES if start <= d <= end]


def opec_plus_releases(start: date, end: date) -> List[Release]:
    return [Release(datetime(d.year, d.month, d.day, 12, 0, tzinfo=UTC),
                     "ASSUMPTION: 12:00 UTC placeholder, exact time not published in advance")
            for d in OPEC_PLUS_MEETINGS if start <= d <= end]
