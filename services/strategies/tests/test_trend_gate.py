"""Gate tests for the 5-day trend veto: `next_trade.trend_gate` (pure),
`desk_tick_sim.Params.trend_gate_span` / `trend_gate_blocks`, and the pure
parts of its eval (services/strategies/evals/trend_gate_test.py), 2026-11-13.

The rule: no new long while the 120h close-to-close return (the last
CLOSED 1h close against the close 120 closed bars earlier) is negative;
no new short while it is positive. Tested on the journaled theses
(TREND_GATE_TEST_2026-11-13.md) it FAILED its bar, so it stays off:
`Params()` and `live_params()` keep `trend_gate_span = None`, and
next_trade carries no live constant for it.

Synthetic candles for the sim (test_stop_cap's flat warmup, with sloped
closes where the gate needs a trend) and one real fixture: Brent's closed
1h closes 2026-10-20 to 11-12 (`fixtures/brent_1h_closes_2026-10-20_to_
11-12.json`, cut from the eval's candle cache), so the November longs the
veto would have blocked are checked on the real tape. No network.
"""
from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from pathiel.models.types import Candle
from services.strategies.backtest.desk_tick_sim import (
    Direction, Params, live_params, simulate, trend_gate_blocks)
from services.strategies.evals import trend_gate_test as E
from services.strategies.next_trade import trend_gate
from services.strategies.tests.test_stop_cap import ENTER_LONG, QUIET, _bars, _params, _run

HOUR_MS = 3_600_000
Q = 900_000
T0 = 24 * HOUR_MS
FIXTURE = Path(__file__).parent / "fixtures" / "brent_1h_closes_2026-10-20_to_11-12.json"


def _ms(s: str) -> int:
    return int(datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc).timestamp() * 1000)


def _sloped(n: int = 24, first: float = 103.0, last: float = 101.0, lo: float = 100.0, hi: float = 104.0):
    """`n` closed 1h bars, the 24h range 100..104 (so a market long at 100.3
    stops at 99.8 with the target 104, as in test_stop_cap), closes sliding
    from `first` to `last`: a down-slope when last < first."""
    step = (last - first) / (n - 1)
    return [Candle(t=i * HOUR_MS, o=first + i * step, h=hi, l=lo, c=first + i * step, v=1.0) for i in range(n)]


def _long(t):
    return Direction(side="long")


def _short(t):
    return Direction(side="short")


# ── the pure function ──────────────────────────────────────────────────────

def test_a_negative_return_blocks_a_long_and_frees_a_short():
    closes = [100.0] * 5 + [95.0]           # span 5: 100 -> 95
    g = trend_gate("long", closes, 5)
    assert g["blocked"] is True and g["ret"] == pytest.approx(-0.05) and g["span_h"] == 5
    assert g["last_close"] == 95.0 and g["base_close"] == 100.0 and "no new long" in g["reason"]
    s = trend_gate("short", closes, 5)
    assert s["blocked"] is False and s["ret"] == pytest.approx(-0.05) and "allowed" in s["reason"]


def test_a_positive_return_blocks_a_short_and_frees_a_long():
    closes = [100.0] * 5 + [103.0]
    assert trend_gate("short", closes, 5)["blocked"] is True
    assert trend_gate("long", closes, 5)["blocked"] is False


def test_the_return_reads_the_last_close_against_the_close_span_bars_earlier():
    # only closes[-1] and closes[-1 - span] matter; everything between is ignored
    closes = [50.0, 100.0, 1.0, 999.0, 1.0, 110.0]
    g = trend_gate("long", closes, 4)
    assert g["base_close"] == 100.0 and g["last_close"] == 110.0 and g["ret"] == pytest.approx(0.10)
    # extra history in front changes nothing
    assert trend_gate("long", [7.0, 8.0] + closes, 4)["ret"] == pytest.approx(0.10)


def test_a_zero_return_blocks_nothing():
    closes = [100.0] * 7
    assert trend_gate("long", closes, 6)["blocked"] is False
    assert trend_gate("short", closes, 6)["blocked"] is False
    assert trend_gate("long", closes, 6)["ret"] == 0.0


def test_too_little_history_never_blocks():
    closes = [100.0, 90.0, 80.0]              # a clear slide, but under span + 1 closes
    g = trend_gate("long", closes, 3)
    assert g["blocked"] is False and g["ret"] is None and "veto not applied" in g["reason"]
    assert trend_gate("long", [], 120)["blocked"] is False
    # exactly span + 1 closes is enough
    assert trend_gate("long", [100.0, 90.0, 80.0, 70.0], 3)["blocked"] is True


def test_a_flat_or_unknown_side_and_bad_closes_never_block():
    closes = [100.0] * 5 + [95.0]
    assert trend_gate("flat", closes, 5) == {"blocked": False, "ret": None, "span_h": 5, "last_close": None,
                                             "base_close": None, "reason": "the thesis is flat: nothing to veto"}
    assert trend_gate("long", [0.0] * 5 + [95.0], 5)["blocked"] is False
    assert trend_gate("long", [100.0] * 5 + [0.0], 5)["blocked"] is False


def test_the_default_span_is_120_and_a_bad_span_is_refused():
    closes = [100.0] * 120 + [99.0]
    assert trend_gate("long", closes)["span_h"] == 120 and trend_gate("long", closes)["blocked"] is True
    assert trend_gate("long", closes[1:])["blocked"] is False           # 120 closes: one short
    with pytest.raises(ValueError):
        trend_gate("long", closes, 0)


def test_it_is_pure():
    closes = [100.0] * 5 + [95.0]
    before = list(closes)
    trend_gate("long", closes, 5)
    assert closes == before


# ── the November longs, on the real tape ───────────────────────────────────

def _fixture():
    rows = json.loads(FIXTURE.read_text())["rows"]
    return [(int(t), float(c)) for t, c in rows]


def _closes_before(rows, t_ms: int):
    """The closes of the bars CLOSED at tick t (open + 1h <= t), oldest first."""
    return [c for t, c in rows if t + HOUR_MS <= t_ms]


# The eval's blocked entries on the live journal, 2026-11-02..11-10 (the
# desk's slide): the tick, the 120h return the gate read there
# (TREND_GATE_TEST_2026-11-13.md).
NOVEMBER_LONGS = [
    ("2026-11-02 03:00", -0.613), ("2026-11-04 14:00", -11.341), ("2026-11-04 16:00", -12.665),
    ("2026-11-04 17:30", -17.208), ("2026-11-09 07:45", -7.869), ("2026-11-09 13:00", -8.641),
    ("2026-11-10 06:45", -3.996), ("2026-11-10 13:45", -8.067),
]


@pytest.mark.parametrize("tick,ret_pct", NOVEMBER_LONGS)
def test_every_november_long_on_the_journal_is_blocked_at_120h(tick, ret_pct):
    rows = _fixture()
    g = trend_gate("long", _closes_before(rows, _ms(tick)), 120)
    assert g["blocked"] is True
    assert g["ret"] * 100 == pytest.approx(ret_pct, abs=0.001)
    # the mirror: a short was free at every one of those ticks
    assert trend_gate("short", _closes_before(rows, _ms(tick)), 120)["blocked"] is False


def test_the_october_longs_the_veto_kept_were_allowed():
    rows = _fixture()
    for tick in ("2026-10-27 05:00", "2026-10-27 14:30"):        # the journal's 109.59 / 109.63 longs, both targets
        g = trend_gate("long", _closes_before(rows, _ms(tick)), 120)
        assert g["blocked"] is False and g["ret"] > 0


def test_the_11_10_bottom_was_blocked_at_every_span_in_the_grid():
    """The baseline's 2026-11-10 13:45Z long at 88.31 ran +5.3R into the
    11-12 bounce; the veto blocked it at 72, 96, 120, 144 and 168h. The
    dip-buy's best entries are the bottoms, which is why the veto failed."""
    rows = _fixture()
    closes = _closes_before(rows, _ms("2026-11-10 13:45"))
    assert all(trend_gate("long", closes, s)["blocked"] for s in E.SPANS)


def test_the_fixture_is_closed_hourly_bars_oldest_first():
    rows = _fixture()
    assert rows[0][0] == _ms("2026-10-20 00:00") and rows[-1][0] == _ms("2026-11-12 17:00")
    assert all(b - a == HOUR_MS for (a, _), (b, _) in zip(rows, rows[1:]))
    assert len(rows) == 570


# ── inside the sim ─────────────────────────────────────────────────────────

def test_off_by_default_and_off_live():
    assert Params().trend_gate_span is None
    assert live_params().trend_gate_span is None
    from services.strategies import next_trade
    assert not hasattr(next_trade, "TREND_GATE_SPAN_H")           # never wired live (the eval failed)


def test_off_is_byte_identical():
    rows = [ENTER_LONG, QUIET, QUIET]
    off, explicit = _run(rows), _run(rows, trend_gate_span=None)
    assert [dataclasses.asdict(t) for t in off.trades] == [dataclasses.asdict(t) for t in explicit.trades]
    assert off.equity_curve == explicit.equity_curve
    assert off.trend_blocked == [] and "trend" not in off.refused


def test_bad_values_are_refused():
    with pytest.raises(ValueError):
        simulate([], _sloped(), _long, _params(trend_gate_span=0))


def test_sim_blocks_blocks_a_long_in_a_down_slope_and_frees_the_short():
    p = Params(trend_gate_span=12)
    g = trend_gate_blocks(_sloped(), "long", p)
    assert g is not None and g["blocked"] and g["ret"] < 0 and g["span_h"] == 12
    assert trend_gate_blocks(_sloped(), "short", p) is None
    assert trend_gate_blocks(_sloped(), "long", Params()) is None                      # off
    assert trend_gate_blocks(_sloped(), "long", Params(trend_gate_span=30)) is None    # 24 bars: too few


def test_sim_blocks_reads_only_the_last_span_plus_one_closes():
    # 23 closes rising hard, then the last span+1 flat: the gate sees a zero return
    bars = _sloped(23, 50.0, 100.0) + [Candle(t=(23 + i) * HOUR_MS, o=100, h=104, l=100, c=100.0, v=1.0)
                                        for i in range(7)]
    assert trend_gate_blocks(bars, "short", Params(trend_gate_span=6)) is None
    assert trend_gate_blocks(bars, "short", Params(trend_gate_span=10)) is not None


def test_the_entry_is_skipped_and_kept_for_the_eval():
    rows = [ENTER_LONG, QUIET, QUIET]
    off = simulate(_bars(rows, T0), _sloped(), _long, _params())
    on = simulate(_bars(rows, T0), _sloped(), _long, _params(trend_gate_span=12))
    assert off.open_position_at_end is not None and off.trades == []       # the long at tick 0, still open
    assert on.open_position_at_end is None and on.trades == []
    assert on.refused["trend"] == 3 and [b["time"] for b in on.trend_blocked] == [T0, T0 + Q, T0 + 2 * Q]
    row = on.trend_blocked[0]
    assert row["kind"] == "entry" and row["side"] == "long" and row["is_market"] is True
    assert row["entry_px"] == pytest.approx(100.3) and row["stop_px"] == pytest.approx(99.8)
    assert row["ret"] < 0 and row["span_h"] == 12
    assert on.final_equity == pytest.approx(64.0)


def test_the_short_is_free_in_the_down_slope_and_blocked_in_the_up_slope():
    short_rows = [(103.7, 103.8, 103.6, 103.7)] * 2            # a market short at 103.7, stop 104.2, target 100
    down = simulate(_bars(short_rows, T0), _sloped(), _short, _params(trend_gate_span=12))
    assert down.open_position_at_end is not None and down.refused.get("trend", 0) == 0
    up = simulate(_bars(short_rows, T0), _sloped(first=101.0, last=103.0), _short, _params(trend_gate_span=12))
    assert up.open_position_at_end is None and up.refused["trend"] == 2
    assert up.trend_blocked[0]["side"] == "short" and up.trend_blocked[0]["ret"] > 0


def test_too_little_history_in_the_sim_never_blocks():
    on = simulate(_bars([ENTER_LONG], T0), _sloped(), _long, _params(trend_gate_span=48))
    assert on.open_position_at_end is not None and on.refused.get("trend", 0) == 0


def test_an_open_position_is_never_touched():
    # the long opens on a flat tape, then the closes slide: the veto gates NEW entries only
    flat_then_down = [Candle(t=i * HOUR_MS, o=102, h=104, l=100, c=102.0, v=1.0) for i in range(24)]
    rows = [ENTER_LONG, (100.3, 102.0, 100.25, 101.8), (101.8, 102.5, 101.5, 102.0)]
    c1h = flat_then_down + [Candle(t=T0, o=100.3, h=102.0, l=100.2, c=100.1, v=1.0)]    # closes at T0 + 1h
    off = simulate(_bars(rows + [QUIET] * 4, T0), c1h, _long, _params())
    on = simulate(_bars(rows + [QUIET] * 4, T0), c1h, _long, _params(trend_gate_span=1))
    assert off.trades == on.trades == [] and off.open_position_at_end == on.open_position_at_end
    assert on.open_position_at_end is not None and on.refused.get("trend", 0) == 0


def test_the_sim_and_the_pure_function_agree_on_the_real_tape():
    rows = _fixture()
    bars = [Candle(t=t, o=c, h=c, l=c, c=c, v=1.0) for t, c in rows]
    for tick, _ in NOVEMBER_LONGS:
        closed = [b for b in bars if b.t + HOUR_MS <= _ms(tick)]
        g = trend_gate_blocks(closed, "long", Params(trend_gate_span=120))
        assert g is not None and g["ret"] == pytest.approx(trend_gate("long", [b.c for b in closed], 120)["ret"])


# ── the eval's pure parts ──────────────────────────────────────────────────

def test_grid_and_params():
    v = E.variants()
    assert list(v) == ["baseline", "trend_72", "trend_96", "trend_120", "trend_144", "trend_168"]
    assert v["baseline"] == {} and v["trend_120"] == {"trend_gate_span": 120}
    p = E.base_params()
    assert p.trend_gate_span is None and p.reclaim_after_stops is None
    assert p.risk_cap_frac == 0.06 and p.starting_equity == 182.09
    assert p.weekend_off == "flatten" and p.no_break_entry is True and p.size_slippage is True


def test_side_only_strips_the_brent_priced_levels():
    d = Direction(side="long", invalidation_px=95.0, entry_ceiling=100.0, regime_id="x", stop_px=94.0)
    out = E.side_only(lambda t: d)(0)
    assert out == Direction(side="long", regime_id="x")
    assert E.side_only(lambda t: None)(0) is None


def _ds(base, veto, thirds, dd=(20.0, 20.0), n96=None, n144=None):
    v = {"baseline": {"net_usd": base, "max_dd_pct": dd[0]}, "trend_120": {"net_usd": veto, "max_dd_pct": dd[1]},
         "trend_96": {"net_usd": base + 1 if n96 is None else n96, "max_dd_pct": 0.0},
         "trend_144": {"net_usd": base + 1 if n144 is None else n144, "max_dd_pct": 0.0}}
    return {"variants": v, "thirds": [{"baseline": {"net_usd": b}, "trend_120": {"net_usd": r},
                                       "trend_96": {"net_usd": 0}, "trend_144": {"net_usd": 0}} for b, r in thirds]}


def test_the_bar_needs_net_dd_plateau_and_the_last_third():
    ok = E.passes(_ds(10, 12, [(1, 2), (3, 3), (5, 6)]))
    assert ok["pass"] and ok["delta_usd"] == 2 and ok["neighbours"] == {"trend_96": True, "trend_144": True}
    assert not E.passes(_ds(10, 10, [(1, 2), (3, 3), (5, 6)]))["pass"]                   # a tie is not a beat
    assert not E.passes(_ds(10, 12, [(1, 2), (3, 3), (5, 6)], dd=(20.0, 20.1)))["pass"]  # worse drawdown
    assert not E.passes(_ds(10, 12, [(1, 2), (3, 3), (5, 6)], n96=9))["pass"]            # 96h loses: a spike
    assert not E.passes(_ds(10, 12, [(1, 2), (3, 3), (5, 6)], n144=10))["pass"]          # 144h ties: a spike
    assert not E.passes(_ds(10, 12, [(1, 2), (3, 3), (5, -1)]))["pass"]                  # loses the last third
    assert not E.passes(_ds(10, 12, [(1, 2), (3, 3), (5, 4)]))["pass"]                   # last third under the baseline
    assert E.passes(_ds(10, 12, [(1, 2), (3, 3), (-5, 0)]))["pass"]                      # flat beats a losing baseline


def test_verdict_judges_the_live_journal_only():
    good, bad = _ds(10, 12, [(1, 2), (3, 3), (5, 6)]), _ds(10, 9, [(1, 2), (3, 3), (5, 6)])
    assert E.verdict({"brent_journal": good, "brent_replay30": bad})["pass"]
    v = E.verdict({"brent_journal": bad, "brent_replay30": good})
    assert not v["pass"] and v["judged"] and v["dataset"] == "brent_journal"
    assert not E.verdict({"brent_replay30": good})["judged"]


def test_blocked_summary_and_november():
    rows = [{"r_net": -1.0, "usd_est": -5.0, "outcome": "loss"}, {"r_net": 2.0, "usd_est": 9.0, "outcome": "win"},
            {"r_net": 5.0, "usd_est": 70.0, "outcome": "open"}, {"reason": "rest_not_walked"}]
    assert E.blocked_summary(rows) == {"entries": 4, "wins": 1, "losses": 1, "open": 1, "r_net": 6.0, "usd_est": 74.0}
    ds = {"trades": {"baseline": [{"side": "long", "entry": "2026-11-04 14:00Z", "net_usd": -17.46},
                                  {"side": "long", "entry": "2026-10-27 05:00Z", "net_usd": 23.06},
                                  {"side": "long", "entry": "2026-11-10 06:45Z", "net_usd": -16.05},
                                  {"side": "long", "entry": "2026-11-11 06:45Z", "net_usd": 5.0}],
                     "trend_120": [{"side": "long", "entry": "2026-10-27 05:00Z", "net_usd": 23.06}]},
          "blocked_rows": {"trend_120": [{"time": "2026-11-04 14:00Z"}, {"time": "2026-10-19 02:15Z"}]}}
    nov = E.november(ds)
    assert nov["baseline_longs"] == 2 and nov["baseline_net_usd"] == -33.51
    assert nov["veto_longs"] == 0 and nov["veto_net_usd"] == 0.0 and nov["blocked"] == [{"time": "2026-11-04 14:00Z"}]


def test_summarize_counts_the_blocked_ticks_and_months():
    on = simulate(_bars([ENTER_LONG, QUIET, QUIET], T0), _sloped(), _long, _params(trend_gate_span=12))
    s = E.summarize(on, 64.0)
    assert s["trades"] == 0 and s["blocked_ticks"] == 3 and s["refused_trend"] == 3 and s["months"] == {}
    assert s["net_usd"] == pytest.approx(round(on.final_equity - 64.0, 2))
    off = simulate(_bars([ENTER_LONG, (100.3, 100.35, 99.7, 99.75)], T0), _sloped(), _long, _params())
    m = E.summarize(off, 64.0)["months"]
    assert list(m) == ["1970-01"] and m["1970-01"]["trades"] == 1 and m["1970-01"]["wins"] == 0
