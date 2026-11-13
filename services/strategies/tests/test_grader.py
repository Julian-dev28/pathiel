"""Unit tests for grader.py: entry fill, target-first / stop-first / neither /
flat-call grading, the hour-of-touch resolution (win / loss / expired /
untriggered) on the real Brent tape, Brier math, idempotent grade_all_due,
regrade, and TrackRecord aggregation."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from contracts.strategies.models import DeskSpec, Grade, Instrument, PairSpec, Scenario
from pathiel.models.types import Candle
from services.strategies import grader as G
from services.strategies import journal
from services.strategies.tests.conftest import make_candles, make_thesis

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
HOUR = timedelta(hours=1)


def _fetcher(series_by_coin: dict):
    def fetch(coin, interval, count):
        return series_by_coin[coin]
    return fetch


# ── direction sign ─────────────────────────────────────────────────────────

def test_direction_sign():
    assert G._direction_sign("long") == 1.0
    assert G._direction_sign("pair") == 1.0
    assert G._direction_sign("short") == -1.0
    assert G._direction_sign("flat") is None


# ── target / stop ordering ─────────────────────────────────────────────────

def test_grade_thesis_target_first_long():
    thesis = make_thesis(side="long", instrument="XYZ", entry_px=100.0, stop_px=90.0,
                         targets=[110.0], horizon_days=1, created_at=T0, scenarios=[])
    closes = [100.0, 102.0, 105.0, 108.0, 111.0, 109.0, 107.0] + [107.0] * 18
    fetcher = _fetcher({"XYZ": make_candles(closes, start=T0, step=HOUR)})

    grade = G.grade_thesis(thesis, hourly_fetcher=fetcher, now=thesis.expires_at)
    assert grade.hit_target_first is True
    assert grade.hit_stop_first is False
    assert grade.direction_hit is True
    assert grade.mae_pct == pytest.approx(0.0)        # never traded below entry
    assert grade.mfe_pct == pytest.approx(0.11, abs=1e-9)


def test_grade_thesis_stop_first_long():
    thesis = make_thesis(side="long", instrument="XYZ", entry_px=100.0, stop_px=90.0,
                         targets=[110.0], horizon_days=1, created_at=T0, scenarios=[])
    closes = [100.0, 98.0, 95.0, 91.0, 89.0, 92.0, 95.0] + [95.0] * 18
    fetcher = _fetcher({"XYZ": make_candles(closes, start=T0, step=HOUR)})

    grade = G.grade_thesis(thesis, hourly_fetcher=fetcher, now=thesis.expires_at)
    assert grade.hit_stop_first is True
    assert grade.hit_target_first is False
    assert grade.mae_pct == pytest.approx(-0.11, abs=1e-9)


def test_grade_thesis_neither_hit():
    thesis = make_thesis(side="long", instrument="XYZ", entry_px=100.0, stop_px=90.0,
                         targets=[110.0], horizon_days=1, created_at=T0, scenarios=[])
    closes = [100.0, 103.0, 97.0, 104.0, 96.0, 102.0, 98.0] + [101.0] * 18
    fetcher = _fetcher({"XYZ": make_candles(closes, start=T0, step=HOUR)})

    grade = G.grade_thesis(thesis, hourly_fetcher=fetcher, now=thesis.expires_at)
    assert grade.hit_target_first is False
    assert grade.hit_stop_first is False
    assert grade.direction_hit is True  # ended at 101, above entry 100


def test_grade_thesis_short_side_sign_flip():
    """Target and stop sit on the OPPOSITE sides of entry for a short, and
    a price DROP is the profitable direction — the sign has to flip
    consistently through target/stop geometry AND ret_pct/direction_hit."""
    thesis = make_thesis(side="short", instrument="XYZ", entry_px=100.0, stop_px=110.0,
                         targets=[90.0], horizon_days=1, created_at=T0, scenarios=[])
    closes = [100.0, 97.0, 94.0, 91.0, 89.0, 92.0, 95.0] + [95.0] * 18
    fetcher = _fetcher({"XYZ": make_candles(closes, start=T0, step=HOUR)})

    grade = G.grade_thesis(thesis, hourly_fetcher=fetcher, now=thesis.expires_at)
    assert grade.hit_target_first is True     # price fell to the short's target first
    assert grade.hit_stop_first is False
    assert grade.direction_hit is True        # price ended below entry -> short profited
    assert grade.ret_pct > 0


def test_grade_thesis_flat_call_has_no_direction():
    thesis = make_thesis(side="flat", instrument="XYZ", entry_px=100.0, stop_px=None,
                         targets=[], horizon_days=1, created_at=T0, scenarios=[])
    closes = [100.0, 103.0, 95.0, 105.0] + [100.0] * 20
    fetcher = _fetcher({"XYZ": make_candles(closes, start=T0, step=HOUR)})

    grade = G.grade_thesis(thesis, hourly_fetcher=fetcher, now=thesis.expires_at)
    assert grade.direction_hit is None
    assert grade.hit_target_first is None
    assert grade.hit_stop_first is None
    assert grade.ret_pct == 0.0


def test_grade_thesis_no_candle_data_is_ungradeable():
    thesis = make_thesis(instrument="XYZ", horizon_days=1, created_at=T0)
    fetcher = _fetcher({"XYZ": []})
    grade = G.grade_thesis(thesis, hourly_fetcher=fetcher, now=thesis.expires_at)
    assert grade.direction_hit is None
    assert "no candle data" in grade.notes[0]


def test_grade_thesis_pair_side_uses_synthetic_spread():
    spec = DeskSpec(id="tt", name="T", tagline="t",
                    instruments=[Instrument(coin="A", label="A", role="primary"),
                               Instrument(coin="B", label="B", role="secondary")],
                    pairs=[PairSpec(label="A-B", long="A", short="B")])
    thesis = make_thesis(side="pair", instrument="A-B", entry_px=10.0, stop_px=5.0,
                         targets=[20.0], horizon_days=1, created_at=T0, scenarios=[])
    # spread = A - B: starts at 110-100=10, rises to 120-100=20 (target) before any drop.
    a = make_candles([110.0, 112.0, 115.0, 118.0, 120.0, 119.0] + [119.0] * 19, start=T0, step=HOUR)
    b = make_candles([100.0] * 25, start=T0, step=HOUR)
    fetcher = _fetcher({"A": a, "B": b})

    grade = G.grade_thesis(thesis, hourly_fetcher=fetcher, spec=spec, now=thesis.expires_at)
    assert grade.hit_target_first is True
    assert grade.direction_hit is True


# ── Brier ──────────────────────────────────────────────────────────────────

def _scenarios():
    return [
        Scenario(name="A", probability=0.5, move_lo_pct=0.0, move_hi_pct=0.05, description="A"),
        Scenario(name="B", probability=0.3, move_lo_pct=0.05, move_hi_pct=0.5, description="B"),
        Scenario(name="C", probability=0.2, move_lo_pct=-0.5, move_hi_pct=0.0, description="C"),
    ]


def test_brier_hand_computed_contained_bucket():
    # 0.10 falls in bucket B [0.05, 0.5) -> y = [0,1,0]
    brier = G._brier(_scenarios(), 0.10)
    expected = (0.5 - 0) ** 2 + (0.3 - 1) ** 2 + (0.2 - 0) ** 2
    assert brier == pytest.approx(expected)
    assert brier == pytest.approx(0.78)


def test_brier_perfect_call_is_zero():
    scenarios = [Scenario(name="A", probability=1.0, move_lo_pct=-1.0, move_hi_pct=1.0, description="A")]
    assert G._brier(scenarios, 0.5) == pytest.approx(0.0)


def test_brier_outside_every_bucket_uses_nearest_by_edge_distance():
    # -0.9 is below every bucket; nearest edge is C's lo=-0.5 (dist 0.4),
    # vs A's lo=0.0 (dist 0.9) and B's lo=0.05 (dist 0.95) -> assigned to C.
    brier = G._brier(_scenarios(), -0.9)
    expected = (0.5 - 0) ** 2 + (0.3 - 0) ** 2 + (0.2 - 1) ** 2
    assert brier == pytest.approx(expected)


def test_brier_none_without_scenarios_or_return():
    assert G._brier([], 0.1) is None
    assert G._brier(_scenarios(), None) is None


# ── find_scenario_bucket (public, also used by services.thesis.scorecard) ──

def test_find_scenario_bucket_contained_case():
    bucket = G.find_scenario_bucket(_scenarios(), 0.10)
    assert bucket.name == "B"


def test_find_scenario_bucket_nearest_edge_fallback():
    bucket = G.find_scenario_bucket(_scenarios(), -0.9)
    assert bucket.name == "C"


def test_find_scenario_bucket_empty_scenarios_is_none():
    assert G.find_scenario_bucket([], 0.1) is None


def test_find_scenario_bucket_matches_brier_s_own_choice():
    """_brier must keep scoring the exact bucket this function returns --
    a refactor divergence here would silently change every Grade.brier."""
    scenarios = _scenarios()
    for raw_ret in (0.10, -0.9, 0.5, -1.0, 0.0):
        bucket = G.find_scenario_bucket(scenarios, raw_ret)
        expected = sum((s.probability - (1.0 if s is bucket else 0.0)) ** 2 for s in scenarios)
        assert G._brier(scenarios, raw_ret) == pytest.approx(expected)


# ── grade_all_due: idempotent, respects expiry ─────────────────────────────

def test_grade_all_due_idempotent():
    created = T0
    thesis = make_thesis(desk_id="oil", thesis_id="due1", instrument="XYZ",
                         entry_px=100.0, stop_px=90.0, targets=[110.0],
                         horizon_days=1, created_at=created, scenarios=[])
    journal.save_thesis(thesis)
    now = created + timedelta(days=2)  # well past expiry
    closes = [100.0] * 26
    fetcher = _fetcher({"XYZ": make_candles(closes, start=created, step=HOUR)})

    n1 = G.grade_all_due(hourly_fetcher=fetcher, now=now)
    assert n1 == 1
    graded = journal.get_thesis("oil", "due1")
    assert graded.grade is not None

    n2 = G.grade_all_due(hourly_fetcher=fetcher, now=now)
    assert n2 == 0  # nothing left to grade


def test_grade_all_due_skips_an_open_unresolved_call():
    """Inside its horizon, filled, neither stop nor target touched: the
    sweep reads it (to know it has not resolved) but writes nothing."""
    created = T0
    thesis = make_thesis(desk_id="oil", thesis_id="not-due", instrument="XYZ", entry_px=100.0,
                         stop_px=90.0, targets=[110.0], horizon_days=30, created_at=created)
    journal.save_thesis(thesis)
    closes = [100.0, 101.0, 99.0, 102.0] + [100.5] * 22
    fetcher = _fetcher({"XYZ": make_candles(closes, start=created, step=HOUR)})
    n = G.grade_all_due(hourly_fetcher=fetcher, now=created + timedelta(days=1))
    assert n == 0
    assert journal.get_thesis("oil", "not-due").grade is None


def test_grade_all_due_skips_a_call_written_in_the_future():
    """A thesis whose created_at is after `now` has no window yet (clock skew,
    a replay with an early `now`): nothing to read, nothing to write."""
    thesis = make_thesis(desk_id="oil", thesis_id="future", horizon_days=1, created_at=T0 + timedelta(days=1))
    journal.save_thesis(thesis)
    calls = []

    def fetcher(coin, interval, count):
        calls.append(coin)
        return []
    assert G.grade_all_due(hourly_fetcher=fetcher, now=T0) == 0
    assert calls == []


# ── a call resolves the hour its stop or target is touched ────────────────
#
# Regression for the Desk page showing every call since 11-06 as "pending"
# (operator, 2026-11-13: "no pending should be there right? right"). Before
# the fix grade_all_due only graded at expires_at (7 days), so a call whose
# stop was hit within hours stayed pending for a week. The fixture is the
# real 1h Brent tape; the two calls are the real oil-desk theses the operator
# pasted, with their journaled entry/stop/target.

_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "brent_1h_ohlc_2026-11-03_to_11-13.json")
with open(_FIXTURE) as _fh:
    BRENT_1H = [Candle(t=r[0], o=r[1], h=r[2], l=r[3], c=r[4], v=r[5]) for r in json.load(_fh)["rows"]]
NOW_11_13 = datetime(2026, 11, 13, 1, 30, tzinfo=timezone.utc)   # inside every call's 7-day horizon


def _brent_fetcher(coin, interval, count):
    assert coin == "xyz:BRENTOIL" and interval == "1h"
    return BRENT_1H


def _real_call(thesis_id, created, entry, stop, targets):
    return make_thesis(desk_id="oil", thesis_id=thesis_id, created_at=created, horizon_days=7,
                       side="long", instrument="xyz:BRENTOIL", entry_px=entry, stop_px=stop,
                       targets=targets, scenarios=[])


# oil-20261109T1120-m: "Long Brent 93.90 ... stop 92.36, target 97.67". Brent fell to ~88.
CALL_11_09 = _real_call("oil-20261109T1120-m", datetime(2026, 11, 9, 11, 20, tzinfo=timezone.utc),
                        93.9, 92.36, [97.667, 98.43])
# oil-20261110T0855-m: "Long Brent at 89.00 on the 20d low 88.64, stop 88.14, target 91.857".
# Brent printed 88.08 (13:00 bar) before it went above 92.
CALL_11_10 = _real_call("oil-20261110T0855-m", datetime(2026, 11, 10, 8, 55, tzinfo=timezone.utc),
                        89.0, 88.14, [91.857, 96.776])
# oil-20261111T0056-m: "Long Brent 89.90, stop 88.88, target 91.857" -- filled 11-12 01:00, target 13:00.
CALL_11_11 = _real_call("oil-20261111T0056-m", datetime(2026, 11, 11, 0, 56, tzinfo=timezone.utc),
                        89.9, 88.88, [91.857, 97.667])


def test_resolved_calls_are_graded_inside_their_horizon_regression():
    """The bug: both calls expire 11-16/11-17; at 11-13 01:30 grade_all_due
    wrote nothing and the page said "pending" for calls whose stops were
    hit on 11-09 and 11-10. Now both are graded as losses the same sweep."""
    for t in (CALL_11_09, CALL_11_10):
        journal.save_thesis(t)
    assert CALL_11_09.expires_at > NOW_11_13 and CALL_11_10.expires_at > NOW_11_13

    assert G.grade_all_due(hourly_fetcher=_brent_fetcher, now=NOW_11_13) == 2

    g9 = journal.get_thesis("oil", CALL_11_09.id).grade
    assert g9 is not None and g9.result == "loss"
    assert g9.triggered_at == datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)   # low 92.986 <= 93.90
    assert g9.resolved_at == datetime(2026, 11, 9, 14, 0, tzinfo=timezone.utc)    # low 92.003 <= 92.36
    assert g9.outcome_px == 92.36 and g9.hit_stop_first is True and g9.direction_hit is False
    assert g9.ret_pct == pytest.approx(92.36 / 93.9 - 1.0)

    g10 = journal.get_thesis("oil", CALL_11_10.id).grade
    assert g10 is not None and g10.result == "loss"
    assert g10.triggered_at == datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)
    assert g10.resolved_at == datetime(2026, 11, 10, 13, 0, tzinfo=timezone.utc)  # the 88.08 print
    assert g10.mae_pct == pytest.approx(88.08 / 89.0 - 1.0)

    # idempotent: the next hourly sweep finds nothing left
    assert G.grade_all_due(hourly_fetcher=_brent_fetcher, now=NOW_11_13) == 0


def test_a_target_touch_grades_a_win_at_the_target_price():
    journal.save_thesis(CALL_11_11)
    assert G.grade_all_due(hourly_fetcher=_brent_fetcher, now=NOW_11_13) == 1
    g = journal.get_thesis("oil", CALL_11_11.id).grade
    assert g.result == "win" and g.hit_target_first is True and g.direction_hit is True
    assert g.triggered_at == datetime(2026, 11, 12, 1, 0, tzinfo=timezone.utc)
    assert g.resolved_at == datetime(2026, 11, 12, 13, 0, tzinfo=timezone.utc)
    assert g.outcome_px == 91.857
    assert g.ret_pct == pytest.approx(91.857 / 89.9 - 1.0)
    assert g.brier is None                       # horizon not reached: no horizon-return score


def test_an_early_grade_is_a_mid_flight_read_until_it_resolves():
    """grade_thesis on an open call: result None, ret_pct at the latest
    close, so a caller can show "open" without the sweep saving it."""
    # oil-20261110T1555-m: filled 11-10 16:00; 87.58 and 94.278 both untouched through 11-13 01:00
    call = _real_call("open-call", datetime(2026, 11, 10, 15, 55, tzinfo=timezone.utc), 89.4, 87.58, [94.278, 98.43])
    g = G.grade_thesis(call, hourly_fetcher=_brent_fetcher, now=NOW_11_13)
    assert g.result is None and g.resolved_at is None
    assert g.triggered_at == datetime(2026, 11, 10, 16, 0, tzinfo=timezone.utc)
    assert g.outcome_px == BRENT_1H[-1].c and g.hit_stop_first is False and g.hit_target_first is False
    assert "open" in g.notes[0]
    assert not G.is_final(g, call, NOW_11_13)


def test_a_dip_limit_that_never_fills_is_untriggered_at_the_horizon():
    """A long limit well under the tape never fills: no trade, no direction
    to score, result "untriggered" once the horizon passes, pending before."""
    call = _real_call("never-filled", datetime(2026, 11, 10, 8, 55, tzinfo=timezone.utc), 80.0, 79.0, [85.0])
    before = G.grade_thesis(call, hourly_fetcher=_brent_fetcher, now=NOW_11_13)
    assert before.result is None and before.triggered_at is None
    assert not G.is_final(before, call, NOW_11_13)

    at_horizon = G.grade_thesis(call, hourly_fetcher=_brent_fetcher, now=call.expires_at)
    assert at_horizon.result == "untriggered"
    assert at_horizon.direction_hit is None and at_horizon.ret_pct == 0.0
    assert at_horizon.hit_stop_first is None and at_horizon.hit_target_first is None


def test_a_filled_call_with_nothing_touched_expires_at_the_horizon():
    thesis = make_thesis(side="long", instrument="XYZ", entry_px=100.0, stop_px=90.0,
                         targets=[110.0], horizon_days=1, created_at=T0, scenarios=[])
    closes = [100.0, 103.0, 97.0, 104.0, 96.0, 102.0, 98.0] + [101.0] * 18
    fetcher = _fetcher({"XYZ": make_candles(closes, start=T0, step=HOUR)})
    grade = G.grade_thesis(thesis, hourly_fetcher=fetcher, now=thesis.expires_at)
    assert grade.result == "expired"
    assert grade.outcome_px == 101.0 and grade.resolved_at == T0 + 24 * HOUR


def test_a_bar_touching_stop_and_target_is_a_loss():
    """Hourly bars cannot order two touches; the stop wins the tie."""
    thesis = make_thesis(side="long", instrument="XYZ", entry_px=100.0, stop_px=95.0,
                         targets=[105.0], horizon_days=1, created_at=T0, scenarios=[])
    from services.strategies.tests.conftest import make_candles_ohlc
    bars = make_candles_ohlc([{"o": 100, "h": 100, "l": 100, "c": 100},
                              {"o": 100, "h": 106, "l": 94, "c": 100}] + [{"o": 100, "h": 100, "l": 100, "c": 100}] * 23,
                             start=T0, step=HOUR)
    grade = G.grade_thesis(thesis, hourly_fetcher=_fetcher({"XYZ": bars}), now=T0 + 3 * HOUR)
    assert grade.result == "loss" and grade.hit_stop_first is True and grade.hit_target_first is False


def test_a_short_limit_fills_on_a_high_touch_and_wins_on_a_low_touch():
    thesis = make_thesis(side="short", instrument="XYZ", entry_px=102.0, stop_px=105.0,
                         targets=[96.0], horizon_days=1, created_at=T0, scenarios=[])
    from services.strategies.tests.conftest import make_candles_ohlc
    bars = make_candles_ohlc([{"o": 100, "h": 101, "l": 99, "c": 100},     # not filled: high 101 < 102
                              {"o": 100, "h": 102.5, "l": 99.5, "c": 101},  # filled
                              {"o": 101, "h": 103, "l": 97, "c": 98},
                              {"o": 98, "h": 98, "l": 95.5, "c": 96}] + [{"o": 96, "h": 96, "l": 96, "c": 96}] * 21,
                             start=T0, step=HOUR)
    grade = G.grade_thesis(thesis, hourly_fetcher=_fetcher({"XYZ": bars}), now=T0 + 5 * HOUR)
    assert grade.result == "win" and grade.triggered_at == T0 + HOUR and grade.resolved_at == T0 + 3 * HOUR
    assert grade.ret_pct == pytest.approx(0.06 / 1.02)                    # (102-96)/102
    assert grade.mae_pct == pytest.approx((103.0 / 102.0 - 1.0) * -1.0)   # the 103 high went against the short


def test_sweep_fetches_each_coin_once_per_desk_and_covers_the_oldest_call():
    """32 pending calls on one coin are one venue read, sized back to the
    oldest call's created_at (the fetcher counts back from now)."""
    created = NOW_11_13 - timedelta(days=5)
    for i in range(6):
        journal.save_thesis(make_thesis(desk_id="oil", thesis_id=f"c{i}", instrument="XYZ",
                                        created_at=created + timedelta(hours=i), horizon_days=7, scenarios=[]))
    asks = []

    def fetcher(coin, interval, count):
        asks.append((coin, interval, count))
        return make_candles([100.0] * 300, start=NOW_11_13 - timedelta(hours=299), step=HOUR)
    G.grade_all_due(hourly_fetcher=fetcher, now=NOW_11_13)
    assert asks == [("XYZ", "1h", 5 * 24 + 48)]


def test_hours_needed_counts_back_from_now_not_the_window_end():
    """Regrading a call whose horizon closed a week ago must still reach its
    window: the venue fetcher returns the last N bars up to now."""
    start = T0
    now = T0 + timedelta(days=14)
    assert G._hours_needed(start, now) == 14 * 24 + 48


def test_regrade_rewrites_old_grades_but_never_with_an_ungradeable_one():
    old = Grade(graded_at=T0, outcome_px=1.0, ret_pct=0.01, direction_hit=True, mae_pct=0.0, mfe_pct=0.0)
    thesis = make_thesis(desk_id="oil", thesis_id="old", instrument="XYZ", entry_px=100.0, stop_px=90.0,
                         targets=[110.0], horizon_days=1, created_at=T0, scenarios=[], grade=old)
    journal.save_thesis(thesis)
    now = T0 + timedelta(days=3)

    # no candles that far back: the real (old) grade stays
    assert G.grade_all_due(hourly_fetcher=_fetcher({"XYZ": []}), now=now, regrade=True) == 0
    assert journal.get_thesis("oil", "old").grade.result is None

    # candles available: the grade is rewritten under today's rules
    closes = [100.0, 98.0, 89.0] + [95.0] * 22
    fetcher = _fetcher({"XYZ": make_candles(closes, start=T0, step=HOUR)})
    assert G.grade_all_due(hourly_fetcher=fetcher, now=now, regrade=True) == 1
    assert journal.get_thesis("oil", "old").grade.result == "loss"

    # the plain sweep still skips it: idempotent
    assert G.grade_all_due(hourly_fetcher=fetcher, now=now) == 0


def test_track_record_counts_results():
    def graded(result, direction_hit):
        return make_thesis(grade=Grade(graded_at=T0, outcome_px=1.0, ret_pct=0.0, direction_hit=direction_hit,
                                       mae_pct=0.0, mfe_pct=0.0, result=result))
    tr = G.aggregate_track_record([graded("win", True), graded("loss", False), graded("loss", False),
                                   graded("untriggered", None), graded(None, True)])
    assert tr.n_graded == 5
    assert tr.results == {"win": 1, "loss": 2, "untriggered": 1}
    assert tr.direction_hit_rate == pytest.approx(2 / 4)    # untriggered has no direction to score
    assert tr.by_author["machine"]["results"] == tr.results


# ── TrackRecord aggregation ────────────────────────────────────────────────

def test_aggregate_track_record_overall_and_by_author():
    def graded(author, direction_hit, brier, ret_pct):
        return make_thesis(author=author, grade=Grade(
            graded_at=T0, outcome_px=1.0, ret_pct=ret_pct, direction_hit=direction_hit,
            brier=brier, mae_pct=0.0, mfe_pct=0.0,
        ))

    theses = [
        graded("machine", True, 0.2, 0.10),
        graded("machine", False, 0.8, -0.05),
        graded("machine", True, 0.4, 0.02),
        graded("user", True, 0.1, 0.20),
    ]
    tr = G.aggregate_track_record(theses)
    assert tr.n_graded == 4
    assert tr.direction_hit_rate == pytest.approx(3 / 4)
    assert tr.mean_brier == pytest.approx((0.2 + 0.8 + 0.4 + 0.1) / 4)
    assert tr.mean_ret_pct == pytest.approx((0.10 - 0.05 + 0.02 + 0.20) / 4)

    assert tr.by_author["machine"]["n_graded"] == 3
    assert tr.by_author["machine"]["direction_hit_rate"] == pytest.approx(2 / 3)
    assert tr.by_author["user"]["n_graded"] == 1
    assert tr.by_author["user"]["direction_hit_rate"] == pytest.approx(1.0)


def test_aggregate_track_record_ignores_ungraded():
    theses = [make_thesis(thesis_id="ungraded", author="machine")]  # grade=None by default
    tr = G.aggregate_track_record(theses)
    assert tr.n_graded == 0
    assert tr.direction_hit_rate is None
    # "machine" shows up with n_graded=0 (it HAS theses, just none graded yet);
    # "user" is absent entirely since no user thesis exists in this input at all.
    assert tr.by_author.keys() == {"machine"}
    assert tr.by_author["machine"]["n_graded"] == 0
    assert tr.by_author["machine"]["direction_hit_rate"] is None


def test_track_record_for_desk_reads_the_journal():
    journal.save_thesis(make_thesis(desk_id="oil", thesis_id="g1", grade=Grade(
        graded_at=T0, outcome_px=1.0, ret_pct=0.05, direction_hit=True, mae_pct=0.0, mfe_pct=0.05)))
    tr = G.track_record_for_desk("oil")
    assert tr.n_graded == 1
    assert tr.direction_hit_rate == 1.0
