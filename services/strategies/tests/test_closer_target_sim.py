"""Gate tests for the 2026-11-03 closer-target option (desk_tick_sim:
`Params.target_cap_frac`, `cap_target`; see
docs/strategy/CLOSER_TARGET_TEST_2026-11-03.md).

Operator, 2026-11-03, on the open 103.61 Brent long (far-edge target
111.77, about 13R): "whats the realistic high? ... test it" -- a 208-day
empirical check found the target fills first only 15% of the time within
72h. This backtests closer targets: higher fixed-R (`Params.target_r`),
larger `target_offset_frac`, a hard cap on how far ANY target rule may sit
from the entry (`target_cap_frac`, new), and the 24h range midpoint / 75%
point (`Params.target_offset_range_frac` at 0.5 / 0.25 -- no new field
needed for those, see the module docstring's "Target cap" section).

Synthetic bars and a bare `LV` levels dict only, no network. Covers: the
field defaults to off and is byte-identical; the pure `cap_target` helper;
the cap applied inside `_setup` on top of the far edge, `target_r` and
`target_offset_range_frac`; it only ever pulls the target closer, never
pushes it out; it can turn a passing min-R gate into a refusal; both sides.
"""
from __future__ import annotations

import dataclasses

import pytest

from pathiel.models.types import Candle
from services.strategies.backtest.desk_tick_sim import (
    Direction, Params, _setup, cap_target, live_params, simulate)

HOUR_MS = 3_600_000
Q = 900_000

# Mirrors test_oil_regime_sim.py's LV: a flat 24h range of 1000 to 1040,
# ATR14 5.0, so a live long at 1000.2 stops at 999.25 (0.75 under 1000) and
# targets 1034.8 (0.5% inside 1040): a 36.6R structural target, 3.46% away.
LV = {"atr14": 5.0, "entry_low": 1000.0, "entry_high": 1040.0, "target_low": 1000.0, "target_high": 1040.0,
      "range_8h_low": 1000.0, "range_8h_high": 1040.0, "range_24h_low": 1000.0, "range_24h_high": 1040.0}


def _base(**over):
    return dataclasses.replace(live_params(), starting_equity=225.0, **over)


LONG = Direction(side="long")
SHORT = Direction(side="short")


# ── defaults / off ──────────────────────────────────────────────────────────

def test_cap_is_off_by_default():
    assert Params().target_cap_frac is None
    assert live_params().target_cap_frac is None


def test_off_is_byte_identical_to_explicit_none():
    live = _setup(LONG, 1000.2, LV, _base())
    explicit = _setup(LONG, 1000.2, LV, _base(target_cap_frac=None))
    assert live == explicit


def test_off_is_byte_identical_over_a_full_run():
    c1h = [Candle(t=i * HOUR_MS, o=1020.0, h=1040.0, l=1000.0, c=1020.0, v=1.0) for i in range(24)]
    c15 = [Candle(t=24 * HOUR_MS + i * Q, o=1000.2, h=1000.4, l=1000.1, c=1000.2, v=1.0) for i in range(8)]
    base = simulate(c15, c1h, lambda t: LONG, _base())
    explicit = simulate(c15, c1h, lambda t: LONG, _base(target_cap_frac=None))
    assert [dataclasses.asdict(t) for t in base.trades] == [dataclasses.asdict(t) for t in explicit.trades]
    assert base.equity_curve == explicit.equity_curve


# ── the pure helper ──────────────────────────────────────────────────────────

def test_cap_target_pure_helper():
    # off: None or non-positive never changes the target
    assert cap_target(1034.8, 1000.2, "long", None) == 1034.8
    assert cap_target(1034.8, 1000.2, "long", 0.0) == 1034.8
    assert cap_target(1034.8, 1000.2, "long", -0.01) == 1034.8
    # inside the cap: unchanged (the cap only ever pulls IN, never widens)
    assert cap_target(1010.0, 1000.2, "long", 0.05) == 1010.0
    # outside the cap: the nearer point, entry + cap_frac x entry
    assert cap_target(1034.8, 1000.2, "long", 0.02) == pytest.approx(1000.2 * 1.02)
    # a short mirrors it on the other side
    assert cap_target(1000.0, 1039.8, "short", 0.02) == pytest.approx(1039.8 * 0.98)
    assert cap_target(1015.0, 1039.8, "short", 0.02) == pytest.approx(1039.8 * 0.98)   # 24.8 away > 20.8 cap
    assert cap_target(1035.0, 1039.8, "short", 0.1) == 1035.0   # inside: unchanged
    assert cap_target(100.0, 0.0, "long", 0.02) == 100.0        # entry <= 0: no-op guard


# ── inside _setup, every target rule ─────────────────────────────────────────

def test_cap_pulls_in_the_far_edge_target():
    live = _setup(LONG, 1000.2, LV, _base())
    assert live["target_px"] == pytest.approx(1040.0 * 0.995)        # uncapped: 1034.8, 3.46% away
    capped = _setup(LONG, 1000.2, LV, _base(target_cap_frac=0.02))
    assert capped["target_px"] == pytest.approx(1000.2 * 1.02)
    assert capped["orig_target_px"] == 1040.0                        # the structural target is unchanged


def test_cap_wider_than_the_rule_never_widens_it():
    live = _setup(LONG, 1000.2, LV, _base())
    wide_cap = _setup(LONG, 1000.2, LV, _base(target_cap_frac=0.05))  # 5% > the 3.46% far-edge distance
    assert wide_cap["target_px"] == pytest.approx(live["target_px"])


def test_cap_on_target_r():
    # risk = 1000.2 - 999.25 = 0.95; target_r=25 -> entry + 23.75 = 1023.95, 2.37% away
    uncapped = _setup(LONG, 1000.2, LV, _base(target_r=25.0))
    assert uncapped["target_px"] == pytest.approx(1023.95)
    capped = _setup(LONG, 1000.2, LV, _base(target_r=25.0, target_cap_frac=0.02))
    assert capped["target_px"] == pytest.approx(1000.2 * 1.02)
    # a target_r already inside the cap is untouched
    tight = _setup(LONG, 1000.2, LV, _base(target_r=10.0, target_cap_frac=0.02))
    assert tight["target_px"] == pytest.approx(1000.2 + 10.0 * 0.95)


def test_cap_on_range_fraction_target():
    # target_offset_range_frac=0.1 -> 1040 - 4 = 1036, same ballpark as the far edge
    uncapped = _setup(LONG, 1000.2, LV, _base(target_offset_range_frac=0.1))
    assert uncapped["target_px"] == pytest.approx(1036.0)
    capped = _setup(LONG, 1000.2, LV, _base(target_offset_range_frac=0.1, target_cap_frac=0.02))
    assert capped["target_px"] == pytest.approx(1000.2 * 1.02)


def test_cap_can_turn_a_passing_gate_into_a_refusal():
    """min_r=2.0 (live) needs the target at least 2 x 0.95 = 1.9 away
    (>= 1002.1). A cap tight enough to pull it under that refuses the setup
    the uncapped rule would have taken."""
    live = _setup(LONG, 1000.2, LV, _base())
    assert "stop_px" in live
    tiny_cap = _setup(LONG, 1000.2, LV, _base(target_cap_frac=0.001))   # cap_dist = 1.0002 -> target 1001.2003
    assert tiny_cap == {"refused": "min_r"}


def test_cap_on_the_short_side():
    s_live = _setup(SHORT, 1039.8, LV, _base())
    assert s_live["target_px"] == pytest.approx(1000.0 * 1.005)
    s_capped = _setup(SHORT, 1039.8, LV, _base(target_cap_frac=0.02))
    assert s_capped["target_px"] == pytest.approx(1039.8 * 0.98)
    assert s_capped["target_px"] > s_live["target_px"]    # pulled UP toward the entry, not down past it
