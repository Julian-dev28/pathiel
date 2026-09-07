"""A book's hold must actually be the hold, not whatever the DSL truncates it to.

WHY THIS EXISTS
xs_reversal moved to a 72h hold on 2026-09-07. The GLOBAL dsl_exit settings are
a 30h hard timeout and an 8h stale-flat timeout — both SHORTER than the hold. If
the book did not override them per position, a 72h book would be silently exited
at 30h, and a flat one at 8h, so the live book would be trading an exit geometry
its backtest never modelled. Nothing would error; the returns would just quietly
stop matching the research.

The override exists (book_helpers.bounded_exit_override), which is exactly why
this needs a test rather than a comment: it is load-bearing and invisible.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pathia.agents.book_helpers import bounded_exit_override

ROOT = Path(__file__).resolve().parents[1]
CFG = json.loads((ROOT / ".agent-config.json").read_text())


def _hold_h() -> float:
    return float(CFG["xs_reversal"]["hold_hours"])


def test_the_override_carries_the_books_full_hold():
    ov = bounded_exit_override(15.0, 3, _hold_h() * 60.0)
    assert ov["hard_timeout_minutes"] == pytest.approx(_hold_h() * 60.0)


def test_the_override_disables_the_stale_flat_cut():
    """dsl_exit.py gates on `> 0`, so 0.0 means disabled. A flat position must
    ride to the book's own timeout, not be cut at the global 8h."""
    ov = bounded_exit_override(15.0, 3, _hold_h() * 60.0)
    assert ov["stale_flat_timeout_minutes"] == 0.0


def test_global_timeouts_would_truncate_the_hold_without_the_override():
    """Documents WHY the override matters. If this ever stops being true the
    override became optional; while it is true, the override is load-bearing."""
    g_hard = float(CFG["dsl_exit"]["hard_timeout_minutes"]) / 60.0
    g_flat = float(CFG["dsl_exit"]["stale_flat_timeout_minutes"]) / 60.0
    if g_hard >= _hold_h() and g_flat >= _hold_h():
        pytest.skip("globals already exceed the hold; override not load-bearing")
    assert min(g_hard, g_flat) < _hold_h()


def test_hold_and_selectivity_match_the_researched_cell():
    """C10 chose momentum 3d / 72h / top 2% because it is positive in all three
    thirds. The live config must BE that cell — a hold or percentile that
    drifted off it is trading something never validated."""
    b = CFG["xs_reversal"]
    assert float(b["lookback_d"]) == 3.0
    assert float(b["hold_hours"]) == 72.0
    assert float(b["top_pct"]) == 98.0, "top_pct 98 = top 2% (book takes mom_pct >= top_pct)"


def test_turnover_is_consistent_with_the_hold():
    """Slots turn over once per hold; this is the number every dollar
    projection divides by, so it must come from the hold and not a constant."""
    slots = int(CFG["max_concurrent"])
    assert 24.0 / _hold_h() * slots == pytest.approx(1.0, abs=0.01)
