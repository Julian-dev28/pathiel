"""Unit tests for grader.py: target-first / stop-first / neither / flat-call
grading, Brier math, idempotent grade_all_due, and TrackRecord aggregation."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from contracts.strategies.models import DeskSpec, Grade, Instrument, PairSpec, Scenario
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


def test_grade_all_due_skips_not_yet_expired():
    created = T0
    thesis = make_thesis(desk_id="oil", thesis_id="not-due", horizon_days=30, created_at=created)
    journal.save_thesis(thesis)
    n = G.grade_all_due(hourly_fetcher=_fetcher({}), now=created + timedelta(days=1))
    assert n == 0
    assert journal.get_thesis("oil", "not-due").grade is None


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
