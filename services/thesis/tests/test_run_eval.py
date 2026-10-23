"""Offline gate tests for services/thesis/evals/run_eval.py's PURE scoring
logic (call_r_multiple, stop_distance_in_atr, score_thesis). The eval's
main()/run_case() call the real local Claude CLI through write_thesis() and
are a periodic/nightly, paid run - not a commit gate (see the module's own
docstring and services/thesis/README.md). This file never calls write_thesis
or services.llm.complete; every Thesis here is hand-built so the scoring
rules themselves have fast, deterministic, free regression coverage.
"""
from __future__ import annotations

from services.thesis.evals import run_eval as E


def test_call_r_multiple_matches_check_levels(oil):
    from pathiel.agents.bracket import check_levels

    call = oil.thesis.call
    want = check_levels(call.entry_px, call.stop_px, call.targets[0])["r_multiple"]
    assert E.call_r_multiple(oil.thesis) == want


def test_call_r_multiple_none_for_a_flat_call(oil):
    flat = oil.thesis.model_copy(update={"call": oil.thesis.call.model_copy(
        update={"side": "flat", "leverage": None})})
    assert E.call_r_multiple(flat) is None


def test_call_r_multiple_none_without_targets(oil):
    no_targets = oil.thesis.model_copy(update={"call": oil.thesis.call.model_copy(update={"targets": []})})
    assert E.call_r_multiple(no_targets) is None


def test_stop_distance_in_atr_hand_computed(oil):
    call = oil.thesis.call
    snap = next(s for s in oil.analytics.instruments if s.coin == call.instrument)
    want = abs(call.entry_px - call.stop_px) / call.entry_px / snap.atr14_pct
    assert E.stop_distance_in_atr(oil.thesis, oil.analytics) == want


def test_stop_distance_in_atr_none_for_unknown_instrument(oil):
    unknown = oil.thesis.model_copy(update={"call": oil.thesis.call.model_copy(update={"instrument": "xyz:NOPE"})})
    assert E.stop_distance_in_atr(unknown, oil.analytics) is None


def test_score_thesis_golden_oil_fixture_passes_every_check(oil):
    result = E.score_thesis(oil.thesis, oil.spec, oil.analytics, oil.intel)
    assert result["score"] == 1.0
    assert all(result["checks"].values()), result["checks"]
    assert result["r_multiple"] is not None and result["r_multiple"] >= 1.5
    assert result["stop_atr_multiple"] is not None and result["stop_atr_multiple"] <= E.MAX_STOP_ATR_MULTIPLE


def test_score_thesis_flags_a_call_that_pays_under_the_r_minimum(oil):
    """Root-cause regression for the 2026-10-23 live Brent bug: a call whose
    stop is the old pre-fix WTI stop (91.4, well inside the 5x ladder's own
    limit of 88.64) pays under 1.5R to its first target - r_gate_pass must
    catch it even though every OTHER check (grounding, scenario count, ...)
    still passes, since the stop and targets are still internally
    consistent, just too wide relative to how close the target is."""
    bad_call = oil.thesis.call.model_copy(update={"stop_px": 91.4, "targets": [101.19, 106.62]})
    bad_thesis = oil.thesis.model_copy(update={"call": bad_call})
    result = E.score_thesis(bad_thesis, oil.spec, oil.analytics, oil.intel)
    assert result["checks"]["r_gate_pass"] is False
    assert result["score"] < 1.0
    assert result["r_multiple"] is not None and result["r_multiple"] < 1.5


def test_score_thesis_flags_a_stop_wider_than_the_ladder_limit_even_at_1_5r(oil):
    """Isolates the ladder-stop check from the R check: stop_px=70.0 is
    29.5% from entry (the 5x ladder rung's own limit is 10.77%), but the
    target is set far enough out that R still clears 1.5 - r_gate_pass must
    fail on the ladder-stop violation alone."""
    entry, stop = oil.thesis.call.entry_px, 70.0
    target = entry + 1.5 * (entry - stop) + 1.0   # comfortably over 1.5R
    bad_call = oil.thesis.call.model_copy(update={"stop_px": stop, "targets": [target, target + 5]})
    bad_thesis = oil.thesis.model_copy(update={"call": bad_call})
    result = E.score_thesis(bad_thesis, oil.spec, oil.analytics, oil.intel)
    assert result["r_multiple"] >= 1.5
    assert result["checks"]["r_gate_pass"] is False


def test_score_thesis_flags_a_stop_beyond_two_atr_even_inside_the_ladder_limit(oil):
    """Independent of the ladder gate: 8% from entry is more than 2x the
    fixture's 2.78% daily ATR14, but still comfortably inside the 5x
    ladder rung's own 10.77% limit, and the target is set for a clean 1.5R+
    - stop_within_2_atr must fail on its own, with r_gate_pass passing."""
    entry = oil.thesis.call.entry_px
    stop = entry * 0.92   # 8% away: > 2 ATR (5.56%), < the ladder's 10.77% limit
    target = entry + 1.6 * (entry - stop)
    call = oil.thesis.call.model_copy(update={"stop_px": stop, "targets": [target, target + 5]})
    thesis = oil.thesis.model_copy(update={"call": call})
    result = E.score_thesis(thesis, oil.spec, oil.analytics, oil.intel)
    assert result["checks"]["r_gate_pass"] is True
    assert result["checks"]["stop_within_2_atr"] is False
    assert result["stop_atr_multiple"] > E.MAX_STOP_ATR_MULTIPLE


def test_check_names_and_checks_dict_stay_in_sync(oil):
    result = E.score_thesis(oil.thesis, oil.spec, oil.analytics, oil.intel)
    assert set(result["checks"]) == set(E._CHECK_NAMES)
