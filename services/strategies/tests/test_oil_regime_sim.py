"""Gate tests for the 2026-11-02 oil regime sim options (desk_tick_sim:
`regime_filter`, `regime_cfg`, `regime_refresh`, `range_buffer_k`,
`range_buffer_floor`, `target_offset_range_frac`; see
docs/strategy/OIL_REGIME_TEST_2026-11-02.md).

Synthetic bars, no network. Covers: off (or neutral) changes nothing; a
chop label blocks new entries and leaves an open position alone; "day"
reads the label at 00:00Z and "hour" at the last close; the filtered run is
causal (bars after a tick cannot change what happened up to it); the
range-scaled stop and target land where the rule says.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import pytest

from pathiel.models.types import Candle
from services.strategies.backtest.desk_tick_sim import Direction, _setup, live_params, simulate, simulate_steps
from services.strategies.backtest.regime import CHOP, DAY_MS, HOUR_MS, TREND_UP, label_series

Q = 900_000
D0 = int(datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp() * 1000)


def _base(**over):
    return dataclasses.replace(live_params(), starting_equity=225.0, **over)


def hours(closes, start, wick=0.05):
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        o = prev
        out.append(Candle(t=start + i * HOUR_MS, o=o, h=max(o, c) + wick, l=min(o, c) - wick, c=c, v=1.0))
        prev = c
    return out


def quarters(c1h):
    """Each 1h bar split into four 15m bars along a straight line."""
    out = []
    for b in c1h:
        for k in range(4):
            o = b.o + (b.c - b.o) * k / 4
            c = b.o + (b.c - b.o) * (k + 1) / 4
            out.append(Candle(t=b.t + k * Q, o=o, h=max(o, c) + 0.01, l=min(o, c) - 0.01, c=c, v=1.0))
    return out


def chop_history():
    """21 days of a slow rise (a 20-day trend), then 3 days of bars that
    swing 97 <-> 103 every hour: a 24h range of about one ATR, chop."""
    rise = [92.0 + 8.0 * i / (21 * 24) for i in range(21 * 24)]
    swing = [103.0 if i % 2 else 97.0 for i in range(3 * 24)]
    return hours(rise + swing, D0)


def chop_ticks(c1h):
    """15m ticks on the 3 chop days, opening at 97.2 with a tiny range:
    a long there pays well over 2R, passes the 20x line and never reaches
    its stop or target."""
    t0 = D0 + 21 * DAY_MS
    return [Candle(t=t0 + i * Q, o=97.2, h=97.3, l=97.1, c=97.2, v=1.0) for i in range(3 * 96)]


LONG = lambda t: Direction(side="long")   # noqa: E731


def _key(run):
    return ([dataclasses.asdict(t) for t in run.trades], run.equity_curve, run.final_equity, dict(run.refused),
            run.open_position_at_end)


# ── off changes nothing ────────────────────────────────────────────────────

def test_options_off_or_neutral_change_nothing():
    c1h = chop_history()
    c15 = chop_ticks(c1h)
    ref = _key(simulate(c15, c1h, LONG, _base()))
    assert ref[4] is not None                     # the live rule buys the chop
    for over in ({"regime_filter": None}, {"range_buffer_k": 0.0}, {"target_offset_range_frac": None},
                 {"regime_refresh": "hour"}, {"regime_cfg": None}):
        assert _key(simulate(c15, c1h, LONG, _base(**over))) == ref, over
    # a filter with nothing to read (only 2 days of history: warm-up) never blocks
    short_h = c1h[-3 * 24:]
    ref2 = _key(simulate(c15, short_h, LONG, _base()))
    assert _key(simulate(c15, short_h, LONG, _base(regime_filter="trend_only"))) == ref2


def test_bad_values_raise():
    c1h = chop_history()
    with pytest.raises(ValueError):
        simulate([], c1h, LONG, _base(regime_filter="nope"))
    with pytest.raises(ValueError):
        simulate([], c1h, LONG, _base(regime_filter="no_chop", regime_refresh="week"))


# ── the filter ─────────────────────────────────────────────────────────────

def test_chop_label_blocks_new_entries():
    c1h = chop_history()
    s = label_series(c1h)
    for d in range(1, 3):
        assert s.label_at(D0 + (21 + d) * DAY_MS) == CHOP
    assert s.label_at(D0 + 21 * DAY_MS) == TREND_UP       # the first chop day opened on the rise
    c15 = chop_ticks(c1h)
    live = simulate(c15, c1h, LONG, _base())
    nc = simulate(c15, c1h, LONG, _base(regime_filter="no_chop"))
    # day 1 opened trend_up: the long opens at 00:00 just as live, and is held
    assert nc.open_position_at_end == live.open_position_at_end
    assert nc.refused.get("regime_chop", 0) == 0
    # with_trend on a trend_up day lets the long through too
    assert simulate(c15, c1h, LONG, _base(regime_filter="with_trend")).open_position_at_end is not None


def test_chop_blocks_from_the_first_chop_day_and_never_touches_the_open_position():
    c1h = chop_history()
    c15 = [c for c in chop_ticks(c1h) if c.t >= D0 + 22 * DAY_MS]     # start on the first chop day
    live = simulate(c15, c1h, LONG, _base())
    nc = simulate(c15, c1h, LONG, _base(regime_filter="no_chop"))
    assert live.open_position_at_end is not None
    assert nc.open_position_at_end is None and not nc.trades
    assert nc.refused["regime_chop"] == len(c15)                   # every flat tick refused


def test_day_reads_the_open_and_hour_reads_the_last_close():
    """Flat 20 days (no trend), then from 00:00 on day 21 a straight rise of
    0.5 an hour. At 00:00 the label is chop; after a few hours the 48h
    efficiency crosses fast_er and the label turns trend_up. A short into
    that rise: with_trend by the day's open lets it through, by the hour
    refuses it."""
    flat = [100.0 + (0.05 if i % 2 else -0.05) for i in range(21 * 24)]
    rise = [100.0 + 0.5 * (i + 1) for i in range(12)]
    c1h = hours(flat + rise, D0, wick=0.02)
    s = label_series(c1h)
    day = D0 + 21 * DAY_MS
    assert s.label_at(day) == CHOP
    turn = next(h for h in range(12) if s.label_at(day + h * HOUR_MS) == TREND_UP)
    assert 2 <= turn <= 8
    c15 = [c for c in quarters(c1h) if c.t >= day]
    short = lambda t: Direction(side="short")   # noqa: E731
    by_day = simulate(c15, c1h, short, _base(regime_filter="with_trend", regime_refresh="day"))
    by_hour = simulate(c15, c1h, short, _base(regime_filter="with_trend", regime_refresh="hour"))
    assert by_day.refused.get("regime_against", 0) == 0
    assert by_day.trades or by_day.open_position_at_end
    assert by_hour.refused["regime_against"] > 0
    assert all(t.entry_time < day + turn * HOUR_MS for t in by_hour.trades)   # none after the turn


def test_filtered_run_is_causal():
    """Bars after a tick (15m and 1h) cannot change anything up to it."""
    c1h = chop_history()
    c15 = chop_ticks(c1h)
    cut = D0 + 22 * DAY_MS + 5 * HOUR_MS
    wild = [Candle(t=c.t, o=c.o * 2, h=c.h * 3, l=c.l * 0.3, c=c.c * 1.7, v=1.0) for c in c1h if c.t + HOUR_MS > cut]
    c1h_alt = [c for c in c1h if c.t + HOUR_MS <= cut] + wild
    c15_alt = [c if c.t < cut else Candle(t=c.t, o=150.0, h=160.0, l=40.0, c=55.0, v=1.0) for c in c15]

    def upto(c15_, c1h_, **over):
        out = []
        for t, mark, run in simulate_steps(c15_, c1h_, LONG, _base(**over)):
            if t >= cut:
                break
            out.append((t, mark, len(run.trades), dict(run.refused)))
        return out

    for over in ({"regime_filter": "no_chop", "regime_refresh": "hour"}, {"regime_filter": "trend_only"},
                 {"regime_filter": "veto_20d", "regime_refresh": "hour"}):
        assert upto(c15, c1h, **over) == upto(c15_alt, c1h_alt, **over), over


# ── range-scaled stop and target ───────────────────────────────────────────

LV = {"atr14": 5.0, "entry_low": 1000.0, "entry_high": 1040.0, "target_low": 1000.0, "target_high": 1040.0,
      "range_8h_low": 1000.0, "range_8h_high": 1040.0, "range_24h_low": 1000.0, "range_24h_high": 1040.0}


def test_range_buffer_floor_widens_and_only_mode_scales():
    d = Direction(side="long")
    live = _setup(d, 1000.2, LV, _base())
    assert live["stop_px"] == pytest.approx(1000.0 - 0.75)
    wide = _setup(d, 1000.2, LV, _base(range_buffer_k=0.03))         # max(0.75, 0.03 x 40) = 1.2
    assert wide["stop_px"] == pytest.approx(1000.0 - 1.2)
    narrow = _setup(d, 1000.2, LV, _base(range_buffer_k=0.01))       # max(0.75, 0.4) = 0.75
    assert narrow["stop_px"] == pytest.approx(live["stop_px"])
    only = _setup(d, 1000.2, LV, _base(range_buffer_k=0.01, range_buffer_floor=False))
    assert only["stop_px"] == pytest.approx(1000.0 - 0.4)
    s = _setup(Direction(side="short"), 1039.8, LV, _base(range_buffer_k=0.03))
    assert s["stop_px"] == pytest.approx(1040.0 + 1.2)


def test_range_target_offset():
    d = Direction(side="long")
    live = _setup(d, 1000.2, LV, _base())
    assert live["target_px"] == pytest.approx(1040.0 * 0.995)
    rt = _setup(d, 1000.2, LV, _base(target_offset_range_frac=0.1))
    assert rt["target_px"] == pytest.approx(1040.0 - 4.0) and rt["orig_target_px"] == 1040.0
    st = _setup(Direction(side="short"), 1039.8, LV, _base(target_offset_range_frac=0.25))
    assert st["target_px"] == pytest.approx(1000.0 + 10.0)


def test_wider_range_stop_still_meets_the_gates():
    """A buffer so wide a gate fails is refused like any other setup."""
    d = Direction(side="long")
    lv2 = {**LV, "entry_high": 1010.0, "target_high": 1010.0, "range_8h_high": 1010.0, "range_24h_high": 1010.0}
    assert "stop_px" in _setup(d, 1001.0, lv2, _base())                                  # 2.3R live
    assert _setup(d, 1001.0, lv2, _base(range_buffer_k=0.15)) == {"refused": "min_r"}    # buffer 1.5: 1.6R
    assert _setup(d, 1000.2, LV, _base(range_buffer_k=0.5)) == {"refused": "leverage"}   # stop 980: 2%
