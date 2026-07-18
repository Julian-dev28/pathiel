"""v2 gate tests — DSL floor golden cases.

Pins the exit math v2 rides on: (a) the book stop-or-horizon policy
(executor.book_exit_policy) behaves exactly like the validated structure —
hard stop, horizon close, NO trail; (b) the engine's own two-phase floor math
(default policy) so the verbatim-survivor re-export can never drift silently.

Style follows tests/test_dsl_add_refresh.py: direct tracker manipulation with
the persistence layer stubbed out.
"""
from __future__ import annotations

import time

import pytest

import hermes_trader.agents.dsl_exit as dsl
import hermes_trader.v2.dsl_exit as v2dsl
from hermes_trader.v2.executor import book_exit_policy


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(dsl, "load_state", lambda force=False: None)
    monkeypatch.setattr(dsl, "_save_state", lambda: None)
    dsl._active_positions.clear()
    yield
    dsl._active_positions.clear()


def _tracker(side="long", entry=100.0, stop=20.0, hold=3.0, lev=1, age_min=1.0):
    return dsl.DSLTracker("AAA", side, entry, time.time() - age_min * 60,
                          book_exit_policy(stop, hold, lev), leverage=lev)


class TestVerbatimReexport:
    def test_v2_module_is_the_same_engine(self):
        """The re-export must alias, not copy — shared registry, shared classes."""
        assert v2dsl.DSLTracker is dsl.DSLTracker
        assert v2dsl.ExitPolicy is dsl.ExitPolicy
        assert v2dsl.check_all_positions is dsl.check_all_positions
        assert v2dsl.register_position is dsl.register_position


class TestBookPolicyGolden:
    """The validated book structure: stop or horizon, never a trail."""

    def test_long_20pct_stop_floor_at_80(self):
        t = _tracker(stop=20.0)
        v = t.check(81.0)
        assert v.exit is False and v.floor_price == pytest.approx(80.0)
        v = t.check(80.0)                          # loss 20% >= 20% cap
        assert v.exit is True and "max_loss" in v.reason
        assert v.floor_price == pytest.approx(80.0)

    def test_long_never_trails_a_winner(self):
        """protect_pct=9999 means a +50% ride that gives back to +21% still holds —
        exit is stop-or-horizon, exactly what the fade was validated with."""
        t = _tracker(stop=20.0)
        assert t.check(150.0).exit is False
        v = t.check(121.0)                          # -19.3% off peak, +21% on entry
        assert v.exit is False
        assert v.phase == "phase1"                  # phase 2 never armed
        assert v.floor_price == pytest.approx(80.0) # floor never ratcheted up

    def test_short_15pct_stop_at_115(self):
        t = _tracker(side="short", stop=15.0)
        assert t.check(114.9).exit is False
        v = t.check(115.0)
        assert v.exit is True and "max_loss" in v.reason
        assert v.floor_price == pytest.approx(115.0)

    def test_leverage_does_not_tighten_the_book_stop(self):
        """max_loss_roe_pct = stop×lev, so effective = min(stop, stop×lev/lev) = stop."""
        t = _tracker(stop=20.0, lev=12)
        assert t.check(80.5).exit is False          # -19.5% spot: inside the 20% stop
        assert t.check(80.0).exit is True

    def test_horizon_close_fires_at_hold_days(self):
        t = _tracker(stop=20.0, hold=3.0, age_min=3 * 1440 + 1)
        v = t.check(100.0)
        assert v.exit is True and "hard_timeout" in v.reason

    def test_one_breach_is_enough(self):
        assert book_exit_policy(20.0, 3.0).consecutive_breaches_required == 1


class TestEngineTwoPhaseGolden:
    """Default-policy floor math golden — pins the engine itself."""

    def _default(self, entry=100.0):
        pol = dsl.ExitPolicy()                      # protect 1.5, tiers 5/10/20/50
        return dsl.DSLTracker("BBB", "long", entry, time.time(), pol, leverage=1)

    def test_phase2_tier_floor_at_106(self):
        """Peak 110 → 10%-tier retrace 0.40 → floor = 100 + 10×0.60 = 106."""
        t = self._default()
        assert t.check(110.0).exit is False         # sets peak, phase 2 armed
        v = t.check(106.1)
        assert v.exit is False and v.floor_price == pytest.approx(106.0)
        v = t.check(105.9)
        assert v.exit is True and "floor_breach" in v.reason
        assert v.floor_price == pytest.approx(106.0)

    def test_floor_never_decreases_for_longs(self):
        t = self._default()
        t.check(110.0)
        f1 = t.check(107.0).floor_price
        t.peak_px = 110.0                           # simulate: price falls back
        f2 = t.check(106.5).floor_price
        assert f2 >= f1                             # ratchet only moves up

    def test_phase1_max_loss_default(self):
        t = self._default()
        v = t.check(97.4)                           # -2.6% < default 2.5% cap
        assert v.exit is True and "max_loss" in v.reason
