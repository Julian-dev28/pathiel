"""The Stage 2 eval (services/intel/evals/eval_event_score.py) runs offline
in the gate and passes its thresholds on the labelled real headlines."""
from __future__ import annotations

from services.intel.evals import eval_event_score as EV


def test_the_labels_are_well_formed():
    rows = EV.load_labels()
    assert len(rows) >= 25
    for r in rows:
        assert r["direction"] in ("bullish", "bearish", "neutral") and r["title"]
        assert r["event_type"] in ("kinetic_strike", "chokepoint_disruption", "diplomatic_deescalation",
                                   "opec_policy", "unconfirmed_rumor", "noise")


def test_the_eval_passes_on_the_real_headlines():
    out = EV.run(EV.load_labels())
    assert out["pass"], out
    assert out["direction_accuracy"] >= EV.DIRECTION_PASS and out["guard_on_noise"] == []


def test_the_eval_catches_a_broken_scorer(monkeypatch):
    monkeypatch.setattr(EV.event_score, "rule_score", lambda *a, **k: {
        "event_type": "noise", "expected_oil_direction": "neutral", "confidence_score": 0.9, "is_confirmed": False})
    out = EV.run(EV.load_labels())
    assert not out["pass"] and out["direction_accuracy"] == 0.0 and out["guard_on_noise"]


def test_main_prints_and_exits_zero(capsys):
    assert EV.main([]) == 0
    assert "PASS" in capsys.readouterr().out
