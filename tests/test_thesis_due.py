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


def test_api_eia_opec_and_fomc_trigger_after_they_print():
    # 15 minutes after the API print: due for it; EIA has not printed yet
    out = M.refresh_due(_thesis(), datetime(2026, 10, 27, 20, 46, tzinfo=timezone.utc), mid=110.5)
    assert any("API weekly" in r for r in out["reasons"]) and not any("EIA" in r for r in out["reasons"])
    # 10 minutes after: not settled yet
    early = M.refresh_due(_thesis(created_at=datetime(2026, 10, 27, 10, tzinfo=timezone.utc).isoformat()),
                          datetime(2026, 10, 27, 20, 40, tzinfo=timezone.utc), mid=110.5)
    assert not any("API" in r for r in early["reasons"])
    # after FOMC only (a thesis written after EIA): since 2026-11-05 FOMC is a
    # rewrite trigger too (the midterm result moved Brent 9% and the desk reacted late)
    late = M.refresh_due(_thesis(created_at=datetime(2026, 10, 28, 15, tzinfo=timezone.utc).isoformat()),
                         datetime(2026, 10, 28, 18, 30, tzinfo=timezone.utc), mid=110.5)
    assert late["due"] is True and late["reasons"] == ["catalyst passed: FOMC decision at 2026-10-28 18:00Z"]
    # a lower-impact release the thesis lists (PPI) is still not one
    ppi = _thesis(created_at=datetime(2026, 10, 28, 15, tzinfo=timezone.utc).isoformat(),
                  catalysts=[{"kind": "ppi", "title": "US PPI", "at": "2026-10-28T18:00:00Z"}])
    assert M.refresh_due(ppi, datetime(2026, 10, 28, 18, 30, tzinfo=timezone.utc), mid=110.5)["due"] is False


def test_a_catalyst_before_the_thesis_was_written_does_not_count():
    t = _thesis(created_at=datetime(2026, 10, 28, 15, 0, tzinfo=timezone.utc).isoformat())
    out = M.refresh_due(t, datetime(2026, 10, 28, 16, 30, tzinfo=timezone.utc), mid=110.5)
    assert out["due"] is False


def test_a_closed_position_triggers():
    out = M.refresh_due(_thesis(), T0 + timedelta(hours=3), mid=110.5,
                        closes_since=[{"coin": "xyz:BRENTOIL", "dir": "Close Long", "closedPnl": "19.89"}])
    assert out["due"] and "position closed" in out["reasons"][0]


def test_a_1_5_percent_move_since_writing_triggers_either_way():
    up = M.refresh_due(_thesis(), T0 + timedelta(hours=3), mid=112.10, px_at_writing=110.40)    # +1.54%
    down = M.refresh_due(_thesis(), T0 + timedelta(hours=3), mid=108.70, px_at_writing=110.40)  # -1.54%
    small = M.refresh_due(_thesis(), T0 + timedelta(hours=3), mid=111.90, px_at_writing=110.40)  # +1.36%
    assert up["due"] and down["due"] and small["due"] is False
    assert up["move_since_written_pct"] == 1.54


def test_a_wait_thesis_does_not_loop_on_its_own_dip_zone():
    """2026-10-28: the flat thesis's buy zone (110.50 to 111.00) sat 1.5% under
    Brent at 112.45. Measured from the zone it would rewrite every hour."""
    t = _thesis(call={"side": "flat", "entry_px": None, "entry_zone": [110.5, 111.0]})
    out = M.refresh_due(t, T0 + timedelta(hours=3), mid=112.45, px_at_writing=112.24)
    assert out["due"] is False and out["move_since_written_pct"] == 0.19
    # no price at writing: the price reason is skipped, never guessed
    assert M.refresh_due(t, T0 + timedelta(hours=3), mid=112.45)["due"] is False


def test_never_more_than_one_rewrite_an_hour():
    out = M.refresh_due(_thesis(), T0 + timedelta(minutes=40), mid=125.0,
                        closes_since=[{"dir": "Close Long"}])
    assert out["due"] is False and "at most one rewrite an hour" in out["held_back"]


def test_an_expired_thesis_is_due():
    out = M.refresh_due(_thesis(), T0 + timedelta(days=7, minutes=1), mid=110.4)
    assert "expired" in out["reasons"]


# ── a closed 1h candle through the thesis stop invalidates it (2026-11-02) ──
def _bar(open_utc, close):
    return {"t": int(open_utc.timestamp() * 1000), "c": close}


def _oil_long_0119():
    """The live oil thesis of 2026-11-02 ~01:19Z: long 111.70, stop 110.83."""
    w = datetime(2026, 11, 2, 1, 19, tzinfo=timezone.utc)
    return _thesis(created_at=w.isoformat(), expires_at=(w + timedelta(days=1)).isoformat(),
                   catalysts=[], call={"side": "long", "entry_px": 111.70, "stop_px": 110.83})


def test_the_0300_close_below_the_stop_makes_the_live_thesis_due():
    """Regression: the 02:00-03:00Z bar closed 110.17 under the 110.83 stop and
    no reason fired (the move was 1.43%), so the watcher kept waking the tick
    to buy a dead long at 2.3R."""
    t = _oil_long_0119()
    bars = [_bar(datetime(2026, 11, 2, 1, tzinfo=timezone.utc), 111.41),   # opened before the thesis
            _bar(datetime(2026, 11, 2, 2, tzinfo=timezone.utc), 110.17),
            _bar(datetime(2026, 11, 2, 3, tzinfo=timezone.utc), 110.02)]   # still forming at 03:03
    out = M.refresh_due(t, datetime(2026, 11, 2, 3, 3, tzinfo=timezone.utc), mid=110.105,
                        px_at_writing=111.70, hour_closes=bars)
    assert out["due"] and out["reasons"] == ["invalidated: 1h close 110.17 at 03:00Z below the thesis stop 110.83"]


def test_a_forming_bar_or_a_close_above_the_stop_does_not_invalidate():
    t = _oil_long_0119()
    at = datetime(2026, 11, 2, 2, 40, tzinfo=timezone.utc)
    forming = [_bar(datetime(2026, 11, 2, 2, tzinfo=timezone.utc), 110.20)]    # bar closes at 03:00
    assert M.refresh_due(t, at, mid=110.4, px_at_writing=111.70, hour_closes=forming)["due"] is False
    held = [_bar(datetime(2026, 11, 2, 2, tzinfo=timezone.utc), 111.14)]
    later = datetime(2026, 11, 2, 3, 5, tzinfo=timezone.utc)
    assert M.refresh_due(t, later, mid=110.4, px_at_writing=111.70, hour_closes=held)["due"] is False


def test_a_short_is_invalidated_by_a_close_above_its_stop():
    w = datetime(2026, 11, 2, 2, 19, tzinfo=timezone.utc)
    t = _thesis(created_at=w.isoformat(), catalysts=[],
                call={"side": "short", "entry_px": 50.15, "stop_px": 50.65})
    bars = [_bar(datetime(2026, 11, 2, 3, tzinfo=timezone.utc), 50.70)]
    out = M.refresh_due(t, datetime(2026, 11, 2, 4, 2, tzinfo=timezone.utc), mid=50.6,
                        px_at_writing=50.1, hour_closes=bars)
    assert any(r.startswith("invalidated: 1h close 50.7 at 04:00Z above the thesis stop 50.65") for r in out["reasons"])


def test_a_flat_thesis_or_one_without_a_stop_is_never_invalidated():
    w = datetime(2026, 11, 2, 1, tzinfo=timezone.utc)
    bars = [_bar(datetime(2026, 11, 2, 2, tzinfo=timezone.utc), 1.0)]
    for call in ({"side": "flat"}, {"side": "long", "entry_px": 111.7, "stop_px": None}):
        t = _thesis(created_at=w.isoformat(), catalysts=[], call=call)
        assert M.invalidating_close(t, bars, datetime(2026, 11, 2, 4, tzinfo=timezone.utc)) is None


def test_the_one_rewrite_an_hour_gap_still_holds_an_invalidation_back():
    t = _oil_long_0119()
    bars = [_bar(datetime(2026, 11, 2, 1, 20, tzinfo=timezone.utc), 110.0)]
    out = M.refresh_due(t, datetime(2026, 11, 2, 2, 0, tzinfo=timezone.utc), mid=110.0,
                        px_at_writing=111.70, hour_closes=bars)
    assert out["due"] is False and "at most one rewrite an hour" in out["held_back"]


def test_the_eval_counts_hours_a_broken_thesis_stayed_live():
    from services.strategies.evals.invalidated_thesis_hours import dead_hours
    t1 = _oil_long_0119()
    t2 = _thesis(id="next", created_at=datetime(2026, 11, 2, 3, 48, tzinfo=timezone.utc).isoformat(),
                 catalysts=[], call={"side": "long", "entry_px": 110.0, "stop_px": 109.55})
    bars = [_bar(datetime(2026, 11, 2, 2, tzinfo=timezone.utc), 110.17),
            _bar(datetime(2026, 11, 2, 3, tzinfo=timezone.utc), 110.02),
            _bar(datetime(2026, 11, 2, 4, tzinfo=timezone.utc), 109.92)]
    res = dead_hours([t1, t2], bars)
    assert res[0]["invalidated"].startswith("invalidated: 1h close 110.17 at 03:00Z")
    assert res[0]["live_hours_after_break"] == 0.8      # 03:00Z break to the 03:48Z rewrite
    assert res[1]["invalidated"] is None
