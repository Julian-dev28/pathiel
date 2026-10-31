"""Gate tests for services/thesis/scorecard.py: no-lookahead eligibility,
the per-row scenario-bucket comparison, the n-cap, the aggregate stats, the
calibration note, and the env-var feature flag. Pure/offline -- no LLM, no
network, no journal I/O."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from contracts.strategies.models import Call, Grade, Scenario, Thesis
from services.thesis import scorecard as SC

T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)
DAY = timedelta(days=1)


def _scenarios():
    return [
        Scenario(name="up", probability=0.6, move_lo_pct=0.0, move_hi_pct=1.0, description="up"),
        Scenario(name="down", probability=0.4, move_lo_pct=-1.0, move_hi_pct=0.0, description="down"),
    ]


def _thesis(i: int, *, side="long", entry_px=100.0, outcome_px=105.0, direction_hit=True,
           ret_pct=0.05, brier=0.16, scenarios=None, created_at=None, horizon_days=7,
           graded=True) -> Thesis:
    created_at = created_at or (T0 + DAY * i)
    scenarios = _scenarios() if scenarios is None else scenarios
    grade = None
    if graded:
        grade = Grade(graded_at=created_at + DAY * horizon_days, outcome_px=outcome_px,
                      ret_pct=ret_pct, direction_hit=direction_hit, brier=brier,
                      mae_pct=0.0, mfe_pct=0.0)
    return Thesis(
        id=f"test-{i}", desk_id="oil", created_at=created_at,
        expires_at=created_at + DAY * horizon_days, horizon_days=horizon_days,
        call=Call(side=side, instrument="xyz:BRENTOIL", one_liner="x", entry_px=entry_px,
                 stop_px=90.0, targets=[110.0], confidence=0.6, invalidation="below 90"),
        scenarios=scenarios, grade=grade,
    )


# ── eligible_theses: no lookahead ───────────────────────────────────────────

def test_eligible_theses_excludes_an_ungraded_thesis():
    t = _thesis(0, graded=False)
    assert SC.eligible_theses([t], as_of=t.expires_at + DAY) == []


def test_eligible_theses_excludes_a_thesis_whose_window_has_not_closed_yet():
    """A Grade can exist early (grader.grade_thesis is a point-in-time read,
    not necessarily final) -- expires_at <= as_of is still required."""
    t = _thesis(0, graded=True)
    as_of = t.expires_at - timedelta(hours=1)
    assert SC.eligible_theses([t], as_of=as_of) == []


def test_eligible_theses_includes_a_thesis_exactly_at_its_expiry():
    t = _thesis(0, graded=True)
    assert SC.eligible_theses([t], as_of=t.expires_at) == [t]


def test_eligible_theses_sorted_oldest_first():
    newer = _thesis(1)
    older = _thesis(0, created_at=T0)
    newer = newer.model_copy(update={"created_at": T0 + DAY, "expires_at": T0 + DAY + DAY * 7})
    as_of = T0 + DAY * 20
    assert SC.eligible_theses([newer, older], as_of=as_of) == [older, newer]


# ── _row / build_scorecard: scenario-bucket comparison ─────────────────────

def test_row_marks_top_pick_when_the_top_scenario_bucket_actually_happened():
    """outcome 105 off entry 100 -> raw return +5%, inside the "up" bucket
    [0, 1.0) which also holds the highest probability (0.6) -- the top pick
    is what happened."""
    t = _thesis(0, entry_px=100.0, outcome_px=105.0)
    card = SC.build_scorecard([t], as_of=t.expires_at)
    row = card.rows[0]
    assert row.actual_bucket_name == "up" and row.top_scenario_name == "up"
    assert row.was_top_pick is True
    assert row.actual_bucket_probability == 0.6


def test_row_marks_a_miss_when_a_lower_probability_bucket_happened_instead():
    """outcome 95 off entry 100 -> raw return -5%, inside "down" [-1, 0),
    which only got 40% -- NOT the top pick ("up" at 60%)."""
    t = _thesis(0, entry_px=100.0, outcome_px=95.0, direction_hit=True, ret_pct=0.05)
    card = SC.build_scorecard([t], as_of=t.expires_at)
    row = card.rows[0]
    assert row.actual_bucket_name == "down" and row.actual_bucket_probability == 0.4
    assert row.top_scenario_name == "up" and row.top_scenario_probability == 0.6
    assert row.was_top_pick is False


def test_row_is_short_side_bucket_matching_uses_the_raw_not_sign_adjusted_return():
    """A short call's scenario buckets are still written as raw price-move
    fractions (Scenario's own doc) -- the price going UP still lands in the
    "up" bucket even though that is a losing outcome for a short."""
    t = _thesis(0, side="short", entry_px=100.0, outcome_px=110.0,
               direction_hit=False, ret_pct=-0.10)
    card = SC.build_scorecard([t], as_of=t.expires_at)
    row = card.rows[0]
    assert row.direction_hit is False
    assert row.actual_bucket_name == "up"   # raw +10%, not direction-flipped


def test_row_has_no_scenario_fields_when_the_thesis_wrote_no_scenarios():
    t = _thesis(0, scenarios=[])
    card = SC.build_scorecard([t], as_of=t.expires_at)
    row = card.rows[0]
    assert row.actual_bucket_name is None and row.top_scenario_name is None and row.was_top_pick is None


def test_row_has_no_scenario_fields_when_entry_px_is_missing():
    t = _thesis(0, entry_px=None)
    card = SC.build_scorecard([t], as_of=t.expires_at)
    assert card.rows[0].actual_bucket_name is None


def test_flat_call_has_no_direction_hit():
    t = _thesis(0, side="flat", direction_hit=None, ret_pct=0.0)
    card = SC.build_scorecard([t], as_of=t.expires_at)
    assert card.rows[0].direction_hit is None


# ── build_scorecard: n-cap and aggregate stats ──────────────────────────────

def test_build_scorecard_caps_at_n_keeping_the_most_recent():
    theses = [_thesis(i, created_at=T0 + DAY * i, horizon_days=1) for i in range(5)]
    as_of = T0 + DAY * 20
    card = SC.build_scorecard(theses, as_of=as_of, n=2)
    assert card.n == 2
    assert [r.created_at for r in card.rows] == [T0 + DAY * 3, T0 + DAY * 4]


def test_build_scorecard_direction_hit_rate_and_mean_brier():
    hit = _thesis(0, created_at=T0, horizon_days=1, direction_hit=True, brier=0.2)
    miss = _thesis(1, created_at=T0 + DAY, horizon_days=1, direction_hit=False, brier=0.8)
    as_of = T0 + DAY * 10
    card = SC.build_scorecard([hit, miss], as_of=as_of)
    assert card.n == 2
    assert card.direction_hit_rate == pytest.approx(0.5)
    assert card.mean_brier == pytest.approx(0.5)


def test_build_scorecard_calibration_numbers():
    top_pick_wins = _thesis(0, created_at=T0, horizon_days=1, entry_px=100.0, outcome_px=105.0)
    top_pick_loses = _thesis(1, created_at=T0 + DAY, horizon_days=1, entry_px=100.0, outcome_px=95.0,
                             ret_pct=0.05)
    as_of = T0 + DAY * 10
    card = SC.build_scorecard([top_pick_wins, top_pick_loses], as_of=as_of)
    assert card.top_scenario_mean_probability == pytest.approx(0.6)   # both theses' top pick is 60%
    assert card.top_scenario_hit_rate == pytest.approx(0.5)           # hit once out of two


def test_build_scorecard_empty_when_nothing_is_eligible():
    card = SC.build_scorecard([], as_of=T0)
    assert card.n == 0 and card.rows == []
    assert card.direction_hit_rate is None and card.mean_brier is None


# ── render_scorecard ─────────────────────────────────────────────────────

def test_render_scorecard_zero_graded_says_so_in_one_line():
    text = SC.render_scorecard(SC.build_scorecard([], as_of=T0))
    assert len(text.splitlines()) == 1
    assert "n=0" in text
    assert "SCORECARD" in text


def test_render_scorecard_states_n_plainly():
    t = _thesis(0)
    text = SC.render_scorecard(SC.build_scorecard([t], as_of=t.expires_at))
    assert "n=1" in text


def test_render_scorecard_small_sample_caution_under_the_threshold():
    t = _thesis(0)
    text = SC.render_scorecard(SC.build_scorecard([t], as_of=t.expires_at))
    assert "small" in text.lower()
    assert "do not flip side" in text.lower()


def test_render_scorecard_no_small_sample_line_at_or_above_the_threshold():
    theses = [_thesis(i, created_at=T0 + DAY * i, horizon_days=1) for i in range(SC.SMALL_SAMPLE_N)]
    as_of = T0 + DAY * 100
    text = SC.render_scorecard(SC.build_scorecard(theses, as_of=as_of, n=SC.SMALL_SAMPLE_N))
    assert "weak evidence" not in text.lower()


def test_render_scorecard_row_shows_the_date_side_and_return():
    t = _thesis(0)
    text = SC.render_scorecard(SC.build_scorecard([t], as_of=t.expires_at))
    assert "2026-10-01" in text and "long" in text and "+5.0%" in text


def test_render_scorecard_has_no_em_dash():
    t = _thesis(0)
    text = SC.render_scorecard(SC.build_scorecard([t], as_of=t.expires_at))
    assert "—" not in text


# ── scorecard_enabled ────────────────────────────────────────────────────

def test_scorecard_enabled_defaults_off(monkeypatch):
    monkeypatch.delenv(SC.SCORECARD_ENV_VAR, raising=False)
    assert SC.scorecard_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "True", "YES", "on"])
def test_scorecard_enabled_truthy_values(monkeypatch, value):
    monkeypatch.setenv(SC.SCORECARD_ENV_VAR, value)
    assert SC.scorecard_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "", "no"])
def test_scorecard_enabled_falsy_values(monkeypatch, value):
    monkeypatch.setenv(SC.SCORECARD_ENV_VAR, value)
    assert SC.scorecard_enabled() is False
