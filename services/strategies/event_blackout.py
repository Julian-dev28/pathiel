"""Live EIA blackout for the oil desk: no NEW Brent/WTI entry from 2h before
to 2h after the weekly EIA petroleum report.

Operator, 2026-11-03, after docs/strategy/OIL_EVENT_TIMING_TEST_2026-11-03.md
("No new entries 2h either side of the Wednesday EIA report: won 5 of 9
sets, tied 4, never lost"): "DO THIS THEN".

What it blocks: a new position or a top-up on an oil coin while `now` sits
inside the window. What it never touches: an open position, its stop or
target, a close, or any non-oil coin. The report time comes from the same
pure calendar the backtest used (services.strategies.backtest.oil_calendar:
Wednesday 10:30 ET, Thursday 12:00 ET in a federal-holiday week), and the
window matches the backtest's `eia_2_2` variant exactly
(test_event_blackout.py pins both).

Enforced in place_bracket (scripts/pathiel-mcp-server.py), shown in the
desk brief's live-rule line, and honored by scripts/trigger_watch.py.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from services.strategies.backtest.oil_calendar import HOUR_MS, eia_report_times

BEFORE_H = 2.0
AFTER_H = 2.0
OIL_COINS = frozenset({"xyz:BRENTOIL", "xyz:CL"})


def now_ms() -> int:
    """The clock this module reads. Tests replace it."""
    return int(time.time() * 1000)


def _utc(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%MZ")


def eia_blackout(coin: str, at_ms: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """The active blackout for `coin` at `at_ms` (default: now), or None.
    Pure given `at_ms`. Window: [report - 2h, report + 2h)."""
    if coin not in OIL_COINS:
        return None
    t = now_ms() if at_ms is None else int(at_ms)
    before, after = int(BEFORE_H * HOUR_MS), int(AFTER_H * HOUR_MS)
    for report_ms, why in eia_report_times(t - after - HOUR_MS, t + before + HOUR_MS):
        start, end = report_ms - before, report_ms + after
        if start <= t < end:
            return {"coin": coin, "event": "EIA weekly petroleum report", "schedule": why,
                    "report_ms": report_ms, "start_ms": start, "end_ms": end,
                    "report_utc": _utc(report_ms), "until_utc": _utc(end),
                    "reason": f"EIA blackout: no new oil entries until {_utc(end)} "
                              f"(report {_utc(report_ms)}, 2h either side)"}
    return None


def next_eia_blackout(coin: str, at_ms: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """The next blackout window that has not ended yet (the active one if
    inside it), for display. None for a non-oil coin."""
    if coin not in OIL_COINS:
        return None
    t = now_ms() if at_ms is None else int(at_ms)
    before, after = int(BEFORE_H * HOUR_MS), int(AFTER_H * HOUR_MS)
    for report_ms, why in eia_report_times(t - after, t + 9 * 24 * HOUR_MS):
        if report_ms + after > t:
            return {"report_utc": _utc(report_ms), "start_utc": _utc(report_ms - before),
                    "until_utc": _utc(report_ms + after), "schedule": why}
    return None


def event_blackout(coin: str, at_ms: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """The active blackout for `coin`: the EIA window first, else an impact-3
    scheduled event (FOMC, CPI, OPEC+, an election; services/intel/
    scheduled_events.py), else None. What place_bracket, the brief and the
    price watcher check (operator, 2026-11-05: "AUDIT THE EVENT CALENDAR AND
    TAKE THEM INTO ACCOUNT"). Never touches an open position."""
    t = now_ms() if at_ms is None else int(at_ms)
    eia = eia_blackout(coin, t)
    if eia:
        return eia
    from services.intel import scheduled_events
    return scheduled_events.blackout(coin, t)


def upcoming_events(coin: str, hours: float = 72.0, at_ms: Optional[int] = None) -> list:
    """Scheduled events for `coin` in the next `hours` (EIA weekly not
    included: its window shows on the live-rule line)."""
    from services.intel import scheduled_events
    t = now_ms() if at_ms is None else int(at_ms)
    return [r for r in scheduled_events.market_events(t, t + int(hours * HOUR_MS), coin) if r["at_ms"] >= t
            or (r["window"] and r["window"]["end_ms"] > t)]


__all__ = ["AFTER_H", "BEFORE_H", "OIL_COINS", "eia_blackout", "event_blackout", "next_eia_blackout",
           "now_ms", "upcoming_events"]
