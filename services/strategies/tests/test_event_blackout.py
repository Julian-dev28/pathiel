"""Live EIA blackout (services/strategies/event_blackout.py), operator
2026-11-03: "DO THIS THEN" to the eia_2_2 filter of
docs/strategy/OIL_EVENT_TIMING_TEST_2026-11-03.md."""
from __future__ import annotations

from datetime import datetime, timezone

from services.strategies import event_blackout as EB
from services.strategies.backtest import oil_calendar as OC


def _ms(*parts):
    return int(datetime(*parts, tzinfo=timezone.utc).timestamp() * 1000)


# Wednesday 2026-11-04, the first EIA report after US DST ended (11-01):
# 10:30 EST = 15:30Z, so the window is 13:30Z to 17:30Z.
REPORT = _ms(2026, 11, 4, 15, 30)


def test_window_edges_on_the_first_post_dst_wednesday():
    assert EB.eia_blackout("xyz:BRENTOIL", _ms(2026, 11, 4, 13, 29)) is None
    hit = EB.eia_blackout("xyz:BRENTOIL", _ms(2026, 11, 4, 13, 30))
    assert hit and hit["report_ms"] == REPORT and hit["until_utc"] == "2026-11-04 17:30Z"
    assert EB.eia_blackout("xyz:CL", _ms(2026, 11, 4, 17, 29)) is not None
    assert EB.eia_blackout("xyz:BRENTOIL", _ms(2026, 11, 4, 17, 30)) is None   # end is open


def test_summer_report_sits_an_hour_earlier_in_utc():
    # 2026-10-28, still EDT: 10:30 ET = 14:30Z
    assert EB.eia_blackout("xyz:BRENTOIL", _ms(2026, 10, 28, 12, 30))["report_utc"] == "2026-10-28 14:30Z"
    assert EB.eia_blackout("xyz:BRENTOIL", _ms(2026, 10, 28, 16, 30)) is None


def test_holiday_week_moves_the_window_to_thursday_noon():
    # Columbus Day week 2026: Thursday 10-15 12:00 EDT = 16:00Z
    assert EB.eia_blackout("xyz:BRENTOIL", _ms(2026, 10, 14, 14, 30)) is None
    hit = EB.eia_blackout("xyz:BRENTOIL", _ms(2026, 10, 15, 15, 0))
    assert hit and hit["report_utc"] == "2026-10-15 16:00Z" and hit["schedule"].startswith("shifted")


def test_silver_and_other_coins_never_black_out():
    t = _ms(2026, 11, 4, 15, 30)
    for coin in ("xyz:SILVER", "xyz:GOLD", "BTC", ""):
        assert EB.eia_blackout(coin, t) is None
        assert EB.next_eia_blackout(coin, t) is None


def test_next_window_for_display():
    nxt = EB.next_eia_blackout("xyz:BRENTOIL", _ms(2026, 11, 3, 22, 0))
    assert nxt == {"report_utc": "2026-11-04 15:30Z", "start_utc": "2026-11-04 13:30Z",
                   "until_utc": "2026-11-04 17:30Z", "schedule": "normal"}


def test_clock_default_reads_now_ms(monkeypatch):
    monkeypatch.setattr(EB, "now_ms", lambda: REPORT)
    assert EB.eia_blackout("xyz:BRENTOIL") is not None
    monkeypatch.setattr(EB, "now_ms", lambda: REPORT + 3 * OC.HOUR_MS)
    assert EB.eia_blackout("xyz:BRENTOIL") is None


def test_live_window_is_exactly_the_backtested_eia_2_2():
    """Every minute of 2026 Sept-Nov: live blocks iff the backtest's
    eia_2_2 windows contain it. Drift here would make the live rule a
    different rule from the one that passed the test."""
    start, end = _ms(2026, 9, 1), _ms(2026, 11, 30)
    windows = OC.blackout_windows([t for t, _ in OC.eia_report_times(start, end)], 2.0, 2.0)
    step = 15 * 60_000
    for t in range(start + 4 * OC.HOUR_MS, end - 4 * OC.HOUR_MS, step):
        sim = any(a <= t < b for a, b in windows)
        assert (EB.eia_blackout("xyz:BRENTOIL", t) is not None) == sim, t
