"""The promotion loop and the dashboard must decide REFUTED at the same fee tier.

Until 2026-08-30 they did not. scripts/autonomous_cycle.py demoted when EV at
slip6 went non-positive; shadow_ledger.classify (what /trends renders) refutes at
slip12. Since slip25 <= slip12 <= slip6, PROMOTION never disagreed — requiring
survival at 25bps already implies a positive 12bps read. Demotion did, and in
the unsafe direction: a book at slip6 +0.2% / slip12 -0.1% showed REFUTED on the
dashboard while the loop left its capital in place.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from hermes_trader.agents import shadow_ledger as SL

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "autonomous_cycle", ROOT / "scripts" / "autonomous_cycle.py")
AC = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(AC)


def test_both_authorities_share_one_tier_definition():
    """Not merely equal — the same object, so they cannot drift."""
    assert AC.VERDICT_FEE_TIER is SL.VERDICT_FEE_TIER


def test_the_verdict_tier_is_stricter_than_the_measured_cost_tier():
    """Demotion is the safety direction, so it uses the more conservative of
    the two. slip12 is 2x the measured 6.1bps round-trip."""
    assert AC.VERDICT_FEE_TIER == "slip12"
    assert AC.REAL_FEE_TIER == "slip6"


def _grade(**kw):
    g = {"book": "b", "n": 20, "ev_real": 1.0, "ev_verdict": 1.0,
         "halves": {"first": 1.0, "second": 1.0}, "ev_strict": 0.5, "mc_p": 0.01}
    g.update(kw)
    return g


def test_the_case_that_used_to_disagree_now_demotes():
    """slip6 positive, slip12 negative: the dashboard called it REFUTED and the
    loop kept it live."""
    d = AC.decide(_grade(ev_real=0.2, ev_verdict=-0.1, ev_strict=-0.5), live=True)
    assert d["verdict"] == "REFUTED" and d["action"] == "demote"
    assert "slip12" in d["why"]


def test_a_book_positive_at_the_verdict_tier_is_not_demoted():
    assert AC.decide(_grade(), live=True)["action"] == "none"


def test_a_grade_row_without_the_new_key_falls_back():
    """A row built before ev_verdict existed must not crash the daily cycle."""
    g = _grade(ev_real=-0.5)
    g.pop("ev_verdict")
    assert AC.decide(g, live=True)["action"] == "demote"


def test_promotion_still_implies_the_dashboard_would_validate():
    """The invariant that made promotion safe all along: a promoted book has
    survived 25bps, which implies a positive 12bps read."""
    d = AC.decide(_grade(ev_verdict=0.8, ev_strict=0.3), live=False)
    assert d["action"] == "promote"
    verdict = SL.classify({
        "n": 20,
        SL.VERDICT_FEE_TIER: {"mean_pct": 0.8, "win": 0.6},
        "slip25": {"mean_pct": 0.3},
        "oos_12bps": {"first": 1.0, "second": 1.0},
    })
    assert verdict["label"] == "VALIDATED"


def test_classify_reads_the_shared_tier():
    import inspect
    src = inspect.getsource(SL.classify)
    assert 'grade.get("slip12"' not in src, "classify hardcodes the tier again"
    assert "VERDICT_FEE_TIER" in src
