"""t_a99fdac2 (B.44): side-aware breakout label on the seat state.

The NXPC 2026-10-15 loss rode a 12bp DOWNWARD range scrape presented to the
primary seat as a bullish-sounding "breakout" (side-less name, direction-
blind mechanic). Fix: the hit carries side + pct_break, and build_state
renders breakoutUp:0.42% / breakoutDown:0.12% behind the per-profile
`breakout_side_label` knob. Default off = byte-identical state; the internal
trigger name stays "breakout" for composite weights, gate lookups
(research.breakout_fired) and shadow logs.
"""

from hermes_trader.agents import decision_voice as dv
from hermes_trader.indicators.triggers import breakout
from hermes_trader.models.types import Candle
import pytest


def _tf(bull: bool = True):
    return {
        "ema8": 101.0 if bull else 99.0,
        "ema21": 100.0,
        "rsi14": 61.0 if bull else 39.0,
        "atr14": 1.2, "adx14": 25.0,
        "last_close": 100.8, "last_time": 1_700_000_000_000,
    }


def _perception(triggers):
    return {
        "id": "pid-bo-1", "coin": "BTC", "mid": 50_000.0,
        "composite_score": 62.0, "daily_move_pct": 3.1,
        "triggers": triggers,
    }


def _build(p, **knobs):
    return dv.build_state("BTC", p, _tf(), _tf(), _tf(), "0.0100%/hr", "n",
                          [], win_rate=0.5, n_closes=10, **knobs)


# ── trigger hit carries side + magnitude ───────────────────────────────────

def _flat_candles(n=60, base=100.0):
    return [
        Candle(t=1_700_000_000_000 + i * 300_000, o=base, h=base + 1.0,
               l=base - 1.0, c=base, v=1000)
        for i in range(n)
    ]


def test_breakout_hit_carries_side_and_pct():
    up = _flat_candles()
    up[-1] = Candle(t=up[-1].t, o=100, h=101.6, l=99.9, c=101.5, v=2000)
    hit = breakout(up)
    assert hit["fired"] and hit["name"] == "breakout"
    assert hit.get("side") == "up"
    assert hit.get("pct_break") == pytest.approx(0.5, abs=0.02)

    down = _flat_candles()
    down[-1] = Candle(t=down[-1].t, o=100, h=100.1, l=98.4, c=98.5, v=2000)
    hit = breakout(down)
    assert hit["fired"] and hit["name"] == "breakout"
    assert hit.get("side") == "down"
    assert hit.get("pct_break") == pytest.approx(0.5, abs=0.02)

    inside = breakout(_flat_candles())
    assert not inside["fired"] and "side" not in inside


# ── seat state label rendering ─────────────────────────────────────────────

def _bo(side="down", pct=0.12, **extra):
    t = {"name": "breakout", "fired": True, "reason": "breakout below 48-bar low",
         "side": side, "pct_break": pct}
    t.update(extra)
    return t


def test_knob_off_state_is_byte_identical():
    p = _perception([{"name": "momentumBurst", "fired": True, "reason": "x"},
                     _bo()])
    assert _build(p)["triggers_fired"] == ["momentumBurst", "breakout"]
    assert _build(p, breakout_side_label=False)["triggers_fired"] == [
        "momentumBurst", "breakout"]


def test_knob_on_renders_side_and_magnitude():
    p = _perception([{"name": "momentumBurst", "fired": True, "reason": "x"},
                     _bo()])
    st = _build(p, breakout_side_label=True)
    assert st["triggers_fired"] == ["momentumBurst", "breakoutDown:0.12%"]

    st = _build(_perception([_bo("up", 0.4)]), breakout_side_label=True)
    assert st["triggers_fired"] == ["breakoutUp:0.40%"]


def test_knob_on_both_shapes_and_legacy_hits():
    bo = _bo()
    # fields shape (nano): the fall-through append is labelled too
    st = dv.build_state("BTC", _perception([bo]), _tf(), _tf(), _tf(),
                        "0.0100%/hr", "n", [], win_rate=0.5, n_closes=10,
                        fade_shape="fields", breakout_side_label=True)
    assert st["triggers_fired"] == ["breakoutDown:0.12%"]
    # legacy hit without side/pct (pre-change shape) falls back to plain name
    legacy = {"name": "breakout", "fired": True, "reason": "r"}
    st = _build(_perception([legacy]), breakout_side_label=True)
    assert st["triggers_fired"] == ["breakout"]
    # other triggers never relabel
    st = _build(_perception([{"name": "volumeSpike", "fired": True,
                              "reason": "x", "side": "up"}]),
                breakout_side_label=True)
    assert st["triggers_fired"] == ["volumeSpike"]
