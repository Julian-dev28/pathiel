"""Gate tests for the PAID forecaster eval: it must refuse without an explicit
opt-in, and its scoring must fail the things it exists to catch. Free and
deterministic — no LLM is constructed."""
from __future__ import annotations

from io import StringIO

from scripts import eval_polymarket_forecaster as paid_eval


def test_eval_refuses_without_explicit_opt_in():
    called = False

    def factory():
        nonlocal called
        called = True
        raise AssertionError("paid provider must not be constructed")

    err = StringIO()
    code = paid_eval.main(env={}, forecaster_factory=factory, stderr=err)
    assert code == 2
    assert called is False
    assert paid_eval.OPT_IN_ENV in err.getvalue()


class _Fake:
    provider = "fake"

    def __init__(self, probs):
        self._probs = list(probs)

    def forecast(self, question, context):
        p = self._probs.pop(0)
        return None if p is None else (p, "why")


def _anchors(expected):
    return tuple((f"q{i}", "ctx", e) for i, e in enumerate(expected))


def test_eval_passes_a_confident_correct_forecaster():
    anchors = _anchors([1.0, 0.0, 1.0])
    out = StringIO()
    code = paid_eval.main(env={paid_eval.OPT_IN_ENV: "1"},
                          forecaster_factory=lambda: _Fake([0.97, 0.02, 0.95]),
                          anchors=anchors, stdout=out)
    assert code == 0 and "PASS" in out.getvalue()


def test_eval_fails_a_hedging_forecaster_that_is_directionally_right():
    # 0.55 on a certainty is "right side, useless number" — the Brier bound is
    # what catches it; directional accuracy alone would pass this
    out = StringIO()
    code = paid_eval.main(env={paid_eval.OPT_IN_ENV: "1"},
                          forecaster_factory=lambda: _Fake([0.55, 0.45, 0.55]),
                          anchors=_anchors([1.0, 0.0, 1.0]), stdout=out)
    assert code == 1 and "FAIL" in out.getvalue()


def test_parse_failures_count_against_accuracy_not_just_parse_rate():
    s = paid_eval.score([{"expected": 1.0, "prob": 0.98},
                         {"expected": 0.0, "prob": None}])
    assert s["parse_rate"] == 0.5
    assert s["accuracy"] == 0.5          # the skipped market is not a free pass
    assert s["passed"] is False


def test_score_of_nothing_does_not_pass():
    assert paid_eval.score([])["passed"] is False


def test_thresholds_are_explicit_and_reachable():
    assert paid_eval.THRESHOLDS["parse_rate"] == 1.0
    assert 0 < paid_eval.THRESHOLDS["accuracy"] <= 1.0
    assert 0 < paid_eval.THRESHOLDS["brier"] < 0.25
    perfect = paid_eval.score([{"expected": 1.0, "prob": 0.99},
                               {"expected": 0.0, "prob": 0.01}])
    assert perfect["passed"] is True


def test_anchors_are_two_sided():
    sides = {e > 0.5 for _, _, e in paid_eval.ANCHORS}
    assert sides == {True, False}, "a one-sided anchor set rewards a constant answer"
