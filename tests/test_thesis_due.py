"""scripts/thesis_due.py: when the desk rewrites its thesis.

Operator, 2026-10-28: "Rewrite once a day, plus after each scheduled catalyst
(API on Tuesday, EIA on Wednesday, OPEC), after a position closes, and when
price moves 1.5% or more from the thesis entry." Before this the tick
rewrote only on staleness, a 1.5% headline or a hit target, and the Oct 25
thesis was still trading 2.7 days and one EIA report later.
"""
from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("thesis_due", ROOT / "scripts" / "thesis_due.py")
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)

T0 = datetime(2026, 10, 25, 23, 38, tzinfo=timezone.utc)


def _thesis(**kw):
    base = {"id": "oil-20261025T2338-m", "created_at": T0.isoformat(),
            "expires_at": (T0 + timedelta(days=7)).isoformat(),
            "call": {"side": "long", "entry_px": 110.40, "entry_zone": [110.0, 110.64]},
            "catalysts": [{"kind": "api_weekly", "title": "API weekly crude stock report", "at": "2026-10-27T20:30:00Z"},
                          {"kind": "eia_weekly", "title": "EIA Weekly Petroleum Status Report", "at": "2026-10-28T14:30:00Z"},
                          {"kind": "fomc", "title": "FOMC decision", "at": "2026-10-28T18:00:00Z"},
                          {"kind": "opec_meeting", "title": "OPEC+ meeting", "at": "2026-11-01T12:00:00Z"}]}
    base.update(kw)
    return base


def test_nothing_is_due_inside_the_first_day_with_no_event():
    out = M.refresh_due(_thesis(), T0 + timedelta(hours=5), mid=110.9)
    assert out["due"] is False and out["reasons"] == []


def test_a_day_old_thesis_is_due():
    out = M.refresh_due(_thesis(), T0 + timedelta(hours=24, minutes=5), mid=110.5)
    assert out["due"] and out["reasons"] == ["daily: thesis is 24.1h old"]


def test_api_eia_and_opec_trigger_after_they_print_but_fomc_does_not():
    # 15 minutes after the API print: due for it; EIA has not printed yet
    out = M.refresh_due(_thesis(), datetime(2026, 10, 27, 20, 46, tzinfo=timezone.utc), mid=110.5)
    assert any("API weekly" in r for r in out["reasons"]) and not any("EIA" in r for r in out["reasons"])
    # 10 minutes after: not settled yet
    early = M.refresh_due(_thesis(created_at=datetime(2026, 10, 27, 10, tzinfo=timezone.utc).isoformat()),
                          datetime(2026, 10, 27, 20, 40, tzinfo=timezone.utc), mid=110.5)
    assert not any("API" in r for r in early["reasons"])
    # after FOMC only (a thesis written after EIA): FOMC is not a rewrite trigger
    late = M.refresh_due(_thesis(created_at=datetime(2026, 10, 28, 15, tzinfo=timezone.utc).isoformat()),
                         datetime(2026, 10, 28, 18, 30, tzinfo=timezone.utc), mid=110.5)
    assert late["due"] is False


def test_a_catalyst_before_the_thesis_was_written_does_not_count():
    t = _thesis(created_at=datetime(2026, 10, 28, 15, 0, tzinfo=timezone.utc).isoformat())
    out = M.refresh_due(t, datetime(2026, 10, 28, 16, 30, tzinfo=timezone.utc), mid=110.5)
    assert out["due"] is False


def test_a_closed_position_triggers():
    out = M.refresh_due(_thesis(), T0 + timedelta(hours=3), mid=110.5,
                        closes_since=[{"coin": "xyz:BRENTOIL", "dir": "Close Long", "closedPnl": "19.89"}])
    assert out["due"] and "position closed" in out["reasons"][0]


def test_a_1_5_percent_move_from_the_entry_triggers_either_way():
    up = M.refresh_due(_thesis(), T0 + timedelta(hours=3), mid=112.10)        # +1.54% from 110.40
    down = M.refresh_due(_thesis(), T0 + timedelta(hours=3), mid=108.70)      # -1.54%
    small = M.refresh_due(_thesis(), T0 + timedelta(hours=3), mid=111.90)     # +1.36%
    assert up["due"] and down["due"] and small["due"] is False
    assert up["move_from_entry_pct"] == 1.54


def test_a_flat_thesis_measures_from_the_middle_of_its_zone():
    t = _thesis(call={"side": "flat", "entry_px": None, "entry_zone": [110.5, 111.0]})
    out = M.refresh_due(t, T0 + timedelta(hours=3), mid=112.45)                # 1.53% above 110.75
    assert out["due"] and out["move_from_entry_pct"] == 1.53
    assert M.refresh_due(t, T0 + timedelta(hours=3), mid=111.5)["due"] is False  # 0.68%


def test_never_more_than_one_rewrite_an_hour():
    out = M.refresh_due(_thesis(), T0 + timedelta(minutes=40), mid=125.0,
                        closes_since=[{"dir": "Close Long"}])
    assert out["due"] is False and "at most one rewrite an hour" in out["held_back"]


def test_an_expired_thesis_is_due():
    out = M.refresh_due(_thesis(), T0 + timedelta(days=7, minutes=1), mid=110.4)
    assert "expired" in out["reasons"]
