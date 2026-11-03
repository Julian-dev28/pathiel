"""Gate tests for services/strategies/backtest/oil_calendar.py
(OIL_EVENT_TIMING_TEST_2026-11-03.md). Pure, synthetic, no network.

Covers: the EIA holiday rule reproduces the EIA's own sourced 2026
schedule exactly (six shifted weeks, every other week normal), the DST
edge cases around the March/November transitions, the API report's plain
weekly cadence, the OPEC hardcoded list's filtering, blackout_windows'
window math and merge, and the session-structure helpers.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from services.strategies.backtest import oil_calendar as C

UTC = timezone.utc


def _ms(y, m, d, h=0, mi=0) -> int:
    return int(datetime(y, m, d, h, mi, tzinfo=UTC).timestamp() * 1000)


# ── EIA holiday rule vs the sourced 2026 schedule ──────────────────────────

# eia.gov/petroleum/supply/weekly/schedule.php, fetched 2026-11-04: every
# 2026 week that does NOT release Wednesday 10:30 ET, and why.
SOURCED_2026_SHIFTS = {
    (2026, 1, 21): (2026, 1, 22, "MLK"),
    (2026, 2, 18): (2026, 2, 19, "Presidents"),
    (2026, 5, 27): (2026, 5, 28, "Memorial"),
    (2026, 9, 9): (2026, 9, 10, "Labor"),
    (2026, 10, 14): (2026, 10, 15, "Columbus"),
    (2026, 11, 11): (2026, 11, 12, "Veterans"),
}


def test_eia_2026_matches_the_sourced_schedule():
    for (wy, wm, wd), (sy, sm, sd, label) in SOURCED_2026_SHIFTS.items():
        from datetime import date
        dt, why = C.eia_report_dt(date(wy, wm, wd))
        assert why != "normal", f"{wy}-{wm:02d}-{wd:02d} should shift ({label})"
        assert (dt.year, dt.month, dt.day, dt.hour, dt.minute) == (sy, sm, sd, 12, 0)


def test_every_other_2026_wednesday_is_normal_1030():
    from datetime import date
    sourced_wed = set(SOURCED_2026_SHIFTS)
    d = date(2026, 1, 7)   # first Wednesday of 2026
    checked = 0
    while d.year == 2026:
        dt, why = C.eia_report_dt(d)
        if (d.year, d.month, d.day) in sourced_wed:
            assert why != "normal"
        else:
            assert why == "normal", f"{d} unexpectedly shifted: {why}"
            assert (dt.hour, dt.minute) == (10, 30)
        checked += 1
        d = d.fromordinal(d.toordinal() + 7)
    assert checked > 50   # every Wednesday of the year, sanity floor


def test_eia_report_times_window_matches_dt_and_is_sorted():
    start, end = _ms(2026, 9, 1), _ms(2026, 11, 1)
    rows = C.eia_report_times(start, end)
    assert rows == sorted(rows)
    assert all(start <= t < end for t, _ in rows)
    # exactly the Sept/Oct Wednesdays (5 + 4), none dropped by the pad logic
    assert len(rows) == 9
    shifted = [why for _, why in rows if why != "normal"]
    assert len(shifted) == 2   # Labor Day week, Columbus Day week


def test_eia_empty_window():
    assert C.eia_report_times(100, 100) == []
    assert C.eia_report_times(200, 100) == []


# ── DST edge cases ──────────────────────────────────────────────────────────

def test_eia_dst_spring_forward_2026():
    # 2026 US DST starts Sunday 2026-03-08. The Wednesday after (03-11) is
    # a normal week: 10:30 ET must already read as EDT (UTC-4), 14:30 UTC.
    from datetime import date
    dt, why = C.eia_report_dt(date(2026, 3, 11))
    assert why == "normal"
    utc = dt.astimezone(UTC)
    assert (utc.hour, utc.minute) == (14, 30)


def test_eia_dst_fall_back_2026():
    # DST ends Sunday 2026-11-01. The Veterans Day week (shifted, Thursday
    # 2026-11-12) is already back on EST (UTC-5): 12:00 ET = 17:00 UTC.
    from datetime import date
    dt, why = C.eia_report_dt(date(2026, 11, 11))
    assert why != "normal"
    utc = dt.astimezone(UTC)
    assert (utc.hour, utc.minute) == (17, 0)


def test_eia_winter_vs_summer_utc_offset_differs():
    from datetime import date
    winter, _ = C.eia_report_dt(date(2026, 1, 7))     # EST, UTC-5
    summer, _ = C.eia_report_dt(date(2026, 7, 8))      # EDT, UTC-4
    assert winter.astimezone(UTC).hour == 15   # 10:30 EST -> 15:30 UTC
    assert summer.astimezone(UTC).hour == 14   # 10:30 EDT -> 14:30 UTC


def test_thanksgiving_week_is_not_shifted():
    # Thanksgiving (4th Thu of Nov) lands on the week's Thursday, not its
    # Monday or Wednesday -- the sourced schedule has no Thanksgiving-week
    # entry, and this rule must not invent one.
    from datetime import date
    dt, why = C.eia_report_dt(date(2026, 11, 25))   # the Wednesday before Thanksgiving
    assert why == "normal"


# ── API report ───────────────────────────────────────────────────────────

def test_api_report_times_every_tuesday_1630_et():
    start, end = _ms(2026, 9, 1), _ms(2026, 9, 30)
    rows = C.api_report_times(start, end)
    assert rows == sorted(rows)
    assert len(rows) == 5   # Sept 1/8/15/22/29, 2026 are all Tuesdays
    for t in rows:
        lt = datetime.fromtimestamp(t / 1000, tz=UTC).astimezone(C.NEW_YORK)
        assert lt.weekday() == 1 and (lt.hour, lt.minute) == (16, 30)


def test_api_report_times_empty_window():
    assert C.api_report_times(100, 100) == []


# ── OPEC+ hardcoded list ────────────────────────────────────────────────────

def test_opec_events_are_sourced_and_sorted():
    assert len(C.OPEC_EVENTS) >= 3
    for y, m, d, why in C.OPEC_EVENTS:
        assert isinstance(why, str) and ("opec.org" in why or "OPEC" in why)
    times = C.opec_event_times()
    assert times == sorted(times)


def test_opec_event_times_filters_window():
    start, end = _ms(2026, 10, 1), _ms(2026, 11, 2)
    rows = C.opec_event_times(start, end)
    assert len(rows) == 2   # Oct 4 and Nov 1 only
    for t, _ in rows:
        assert start <= t < end


# ── blackout_windows ────────────────────────────────────────────────────────

def test_blackout_windows_basic():
    events = [1_000_000]
    w = C.blackout_windows(events, before_hours=1.0, after_hours=2.0)
    assert w == ((1_000_000 - C.HOUR_MS, 1_000_000 + 2 * C.HOUR_MS),)


def test_blackout_windows_zero_sided():
    events = [1_000_000]
    w = C.blackout_windows(events, before_hours=0.0, after_hours=0.25)
    assert w == ((1_000_000, 1_000_000 + C.HOUR_MS // 4),)


def test_blackout_windows_rejects_negative():
    with pytest.raises(ValueError):
        C.blackout_windows([0], before_hours=-1.0, after_hours=0.0)


def test_blackout_windows_merges_overlaps():
    events = [0, 2 * C.HOUR_MS]   # two 2h-wide windows that just touch at +-1h
    w = C.blackout_windows(events, before_hours=1.0, after_hours=1.0, merge=True)
    assert w == ((-C.HOUR_MS, 3 * C.HOUR_MS),)
    w_nomerge = C.blackout_windows(events, before_hours=1.0, after_hours=1.0, merge=False)
    assert w_nomerge == ((-C.HOUR_MS, C.HOUR_MS), (C.HOUR_MS, 3 * C.HOUR_MS))


def test_blackout_windows_empty():
    assert C.blackout_windows([], before_hours=1.0, after_hours=1.0) == ()


# ── session structure ───────────────────────────────────────────────────────

def test_nymex_day_session():
    # 2026-09-02 is a Wednesday; 10:00 ET is inside the day session, 20:00
    # ET is not.
    inside = _ms(2026, 9, 2, 14, 0)    # 10:00 EDT = 14:00 UTC
    outside = _ms(2026, 9, 3, 0, 0)    # 20:00 EDT the prior day = 00:00 UTC
    assert C.in_nymex_day_session(inside)
    assert not C.in_nymex_day_session(outside)


def test_asia_session_utc_no_dst():
    assert C.in_asia_session(_ms(2026, 1, 15, 3, 0))     # 03:00 UTC, inside
    assert not C.in_asia_session(_ms(2026, 1, 15, 12, 0))  # 12:00 UTC, outside
    assert C.in_asia_session(_ms(2026, 7, 15, 3, 0))     # no DST shift either season


def test_weekend_closed_reexport_matches_desk_tick_sim():
    from services.strategies.backtest import desk_tick_sim as S
    t = _ms(2026, 10, 3, 12, 0)   # a Saturday
    assert C.weekend_closed(t) == S.weekend_closed(t) == True
