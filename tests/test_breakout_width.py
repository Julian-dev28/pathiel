"""t_240a0d58: breakout width rule — ATR-relative min break + confirmed bar.

width_mode: off (v1 byte-identical) | shadow (v1 decides, arms accrue) |
live (2a decides: last CLOSED bar beyond the edge by >= atr_mult x ATR14(5m);
2b = 1h-ATR variant keeps accruing for the bake-off).
"""

import math

import pytest

from hermes_trader.indicators.triggers import breakout


def _bo(*a, **k):
    return dict(breakout(*a, **k))
from hermes_trader.models.types import Candle


def _flat(n=60, base=100.0, hi=101.0, lo=99.0):
    return [
        Candle(t=1_700_000_000_000 + i * 300_000, o=base, h=hi, l=lo,
               c=base, v=1000)
        for i in range(n)
    ]


def _amend(cs, idx, **over):
    d = dict(t=cs[idx].t, o=cs[idx].o, h=cs[idx].h, l=cs[idx].l,
             c=cs[idx].c, v=cs[idx].v)
    d.update(over)
    cs[idx] = Candle(**d)


# ── off = byte-identical v1 ────────────────────────────────────────────────

def test_off_mode_is_v1_and_carries_no_arms():
    cs = _flat()
    _amend(cs, -1, c=101.05, h=101.1)          # 1-tick scrape on forming bar
    hit = _bo(cs, 48)
    assert hit["fired"] and hit["side"] == "up"
    assert "width_arms" not in hit


# ── shadow: v1 decides, arms recorded ──────────────────────────────────────

def test_shadow_records_arms_without_changing_verdict():
    cs = _flat()
    _amend(cs, -1, c=101.05, h=101.1)          # scrape: v1 fires, 2a must not
    hit = _bo(cs, 48, width_mode="shadow")
    assert hit["fired"] is True                 # v1 still decides
    wa = hit["width_arms"]
    assert wa["v1"] is True and wa["2a"] is False


# ── live: the width rule decides ───────────────────────────────────────────

def test_live_suppresses_forming_bar_scrape():
    cs = _flat()
    _amend(cs, -1, c=101.05, h=101.1)          # 5bp scrape, closed bar inside
    hit = _bo(cs, 48, width_mode="live")
    assert hit["fired"] is False
    assert hit["reason"] == "width-suppressed"
    assert hit["width_arms"]["v1"] is True and hit["width_arms"]["2a"] is False


def test_live_fires_on_wide_confirmed_break():
    cs = _flat()
    _amend(cs, -2, c=102.0, h=102.0)           # CLOSED 99bp beyond the 101 high
    _amend(cs, -1, c=102.5, h=102.6)
    hit = _bo(cs, 48, width_mode="live")
    assert hit["fired"] is True and hit["side"] == "up"
    assert hit["pct_break"] == pytest.approx(0.99, abs=0.05)
    assert "confirmed beyond" in hit["reason"]


def test_live_suppresses_poke_that_closed_back_inside():
    cs = _flat()
    _amend(cs, -2, c=100.5, h=101.3)           # poked over, CLOSED inside
    _amend(cs, -1, c=101.2, h=101.3)           # forming bar beyond -> v1 fires
    hit = _bo(cs, 48, width_mode="live")
    assert hit["fired"] is False
    assert hit["width_arms"]["bp"] == 0.0       # confirmation, not width, killed it


def test_live_records_2b_disagreement():
    cs = _flat()
    _amend(cs, -2, c=102.0, h=102.0)           # 99bp: clears 2a (~51bp)...
    _amend(cs, -1, c=102.5, h=102.6)
    hit = _bo(cs, 48, width_mode="live", atr1h=20.0)  # ...not 2b (~490bp)
    assert hit["fired"] is True                 # 2a decides in live mode
    wa = hit["width_arms"]
    assert wa["2a"] is True and wa["2b"] is False
    assert wa["thr2b_bp"] > wa["thr2a_bp"]


def test_live_falls_back_when_not_evaluable():
    cs = _flat(n=50)                            # < lookback+3 for the v2 window
    _amend(cs, -1, c=101.5, h=101.6)
    hit = _bo(cs, 48, width_mode="live")
    assert hit["fired"] is True                 # v1 verdict kept (fallback)
    assert hit["width_arms"]["2a"] is None      # na, never silently False


def test_live_no_atr_falls_back():
    cs = _flat(n=14)                            # >= lookback+3 but ATR14 short
    _amend(cs, -1, c=101.5, h=101.6)
    hit = _bo(cs, 10, width_mode="live")        # short lookback, short history
    assert hit["width_arms"]["2a"] is None
