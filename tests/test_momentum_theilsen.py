"""t_62c58463 — Theil-Sen momentum estimator (t_38944f65 NXPC post-mortem).

The live close-to-close net reads a pump bar's MEMORY, not a trend: on
2026-10-15 it fired uptrendMomentum +13.3% UP through six red hours into the
NXPC long. These tests pin the robust Theil-Sen arm's behaviour on the
incident shapes and the three config modes.
"""

import math

import pytest

from hermes_trader.indicators.triggers import (
    _theilsen_move_pct,
    downtrend_momentum,
    uptrend_momentum,
)
from hermes_trader.models.types import Candle


def _closes_to_candles(closes):
    return [Candle(t=i * 300_000, o=c, h=c, l=c, c=c, v=1.0) for i, c in enumerate(closes)]


def _path(moves, start=1.0):
    """Build a close path from per-bar % moves."""
    p, out = start, [start]
    for m in moves:
        p *= 1 + m / 100
        out.append(p)
    return out


# ---- scenario paths (72-bar windows, threshold 5.0 like live config) ----
LB = 72
TH = 5.0

GRIND_UP = _path([0.12] * LB)                       # steady uptrend
PUMP_CRASH = _path([2.4] * 12 + [-0.25] * 60)       # NXPC shape: pump then six red hours
BLEED_PARABOLA = _path([-0.2] * 40 + [1.5] * 32)    # owner's regime-change case
FLAT = _path([0.0] * LB)
PUMP_FLAT = _path([2.4] * 12 + [0.0] * 60)


class TestTheilsenMovePct:
    def test_insufficient_history_returns_none(self):
        assert _theilsen_move_pct(_closes_to_candles([1.0] * 10), 72) is None

    def test_pump_crash_reads_down(self):
        """The incident: net says +13% UP; Theil-Sen must say DOWN."""
        candles = _closes_to_candles(PUMP_CRASH)
        net = (PUMP_CRASH[-1] / PUMP_CRASH[-LB - 1] - 1) * 100
        ts = _theilsen_move_pct(candles, LB)
        assert net > TH                      # the bug: net fires UP
        assert ts is not None and ts < -TH   # the fix: robust slope reads DN

    def test_bleed_parabola_reads_up(self):
        """Owner's regime-change case: median-of-bars majority-vote says DN;
        Theil-Sen must read UP."""
        ts = _theilsen_move_pct(_closes_to_candles(BLEED_PARABOLA), LB)
        assert ts is not None and ts > TH

    def test_grind_up_reads_up(self):
        ts = _theilsen_move_pct(_closes_to_candles(GRIND_UP), LB)
        assert ts is not None and ts > TH

    def test_flat_reads_flat(self):
        ts = _theilsen_move_pct(_closes_to_candles(FLAT), LB)
        assert ts is not None and abs(ts) < TH

    def test_pump_flat_not_a_trend(self):
        """Pump then dead tape: net says UP (pump memory); Theil-Sen must not."""
        ts = _theilsen_move_pct(_closes_to_candles(PUMP_FLAT), LB)
        assert ts is not None and abs(ts) < TH

    def test_nonpositive_closes_returns_none(self):
        bad = GRIND_UP[:]
        bad[10] = 0.0
        assert _theilsen_move_pct(_closes_to_candles(bad), LB) is None


class TestUptrendMomentumModes:
    def test_off_mode_is_legacy_net(self):
        hit = uptrend_momentum(_closes_to_candles(PUMP_CRASH), LB, TH)
        assert hit["fired"] is True          # legacy behaviour preserved
        assert "shadow_ts_move" not in hit
        assert "live_net_move" not in hit

    def test_shadow_mode_keeps_net_fired_and_attaches_arm(self):
        hit = uptrend_momentum(_closes_to_candles(PUMP_CRASH), LB, TH, theilsen_mode="shadow")
        assert hit["fired"] is True          # net still decides
        assert hit.get("shadow_ts_fired") is False
        assert hit.get("shadow_ts_move") < -TH   # arm recorded the truth

    def test_live_mode_theilsen_decides(self):
        hit = uptrend_momentum(_closes_to_candles(PUMP_CRASH), LB, TH, theilsen_mode="live")
        assert hit["fired"] is False         # the incident would NOT surface UP
        assert hit.get("live_net_move") > TH  # flight recorder keeps the net

    def test_live_mode_falls_back_to_net_when_ts_none(self):
        candles = _closes_to_candles(GRIND_UP)
        hit = uptrend_momentum(candles, LB, TH, theilsen_mode="live")
        assert hit["fired"] is True          # genuine grind still fires

    def test_off_mode_unchanged_signature_on_grind(self):
        off = uptrend_momentum(_closes_to_candles(GRIND_UP), LB, TH)
        shadow = uptrend_momentum(_closes_to_candles(GRIND_UP), LB, TH, theilsen_mode="shadow")
        live = uptrend_momentum(_closes_to_candles(GRIND_UP), LB, TH, theilsen_mode="live")
        assert off["fired"] == shadow["fired"] == live["fired"] is True
        assert off["score"] == shadow["score"] == pytest.approx(live["score"], rel=0.5)


class TestDowntrendMomentumMirror:
    def test_live_mode_surfaces_the_incident_as_downtrend(self):
        hit = downtrend_momentum(_closes_to_candles(PUMP_CRASH), LB, TH, theilsen_mode="live")
        assert hit["fired"] is True          # the decline gets surfaced for SHORT
        assert "downtrend" in hit["reason"]

    def test_off_mode_misses_the_incident(self):
        hit = downtrend_momentum(_closes_to_candles(PUMP_CRASH), LB, TH)
        assert hit["fired"] is False         # net +13% hides the decline entirely

    def test_shadow_mode_mirror_fields(self):
        hit = downtrend_momentum(_closes_to_candles(PUMP_CRASH), LB, TH, theilsen_mode="shadow")
        assert hit["fired"] is False
        assert hit.get("shadow_ts_fired") is True

    def test_bleed_parabola_not_surfaced_as_downtrend_in_live(self):
        hit = downtrend_momentum(_closes_to_candles(BLEED_PARABOLA), LB, TH, theilsen_mode="live")
        assert hit["fired"] is False
