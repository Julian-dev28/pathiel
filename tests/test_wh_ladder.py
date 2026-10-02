"""Gate tests for W-WH1 (wallet trip reconstruction) and W-WH2 (ladder backtest).

Both are research code, but a verdict is only as good as the machine that
produced it. Each test here pins a way the finding could have been wrong:

- same-millisecond fills replayed out of order (the bug that first turned one
  60,000 XRP close into a string of phantom flips)
- a bar that adds also taking profit (optimistic fill ordering)
- a liquidated account quietly trading on (the redeposit the wallet made)
- open ladders dropped at the end (the survivorship move a no-stop rule needs)
- the pre-registered parameters drifting when the wallet keeps trading
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

HYP = Path(__file__).resolve().parents[1] / "research" / "alpha_swarm" / "hypotheses"


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, HYP / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


lib = _load("wh1lib", "W-WH1_lib.py")
bt = _load("wh2bt", "W-WH2_ladder_backtest.py")

DAY = 86_400_000


def fill(t, side, px, sz, start, tid, coin="XRP", pnl="0", fee="0", crossed=True, oid=1):
    signed = sz if side == "B" else -sz
    opening = abs(start + signed) > abs(start)
    dir_ = ("Open " if opening else "Close ") + ("Long" if (start + signed > 0 or start > 0) else "Short")
    return {"coin": coin, "px": str(px), "sz": str(sz), "side": side, "time": t,
            "startPosition": str(start), "dir": dir_, "closedPnl": pnl, "fee": fee,
            "crossed": crossed, "tid": tid, "oid": oid}


# ── W-WH1: reconstruction ────────────────────────────────────────────────────

class TestChainOrder:
    def test_same_millisecond_fills_follow_start_position_not_tid(self):
        t = 1_000
        fills = [
            fill(t, "B", 3.0, 5, 25, tid=1),
            fill(t, "B", 3.0, 10, 0, tid=3),
            fill(t, "B", 3.0, 15, 10, tid=2),
        ]
        assert [float(f["startPosition"]) for f in lib.chain_order(fills)] == [0, 10, 25]

    def test_scrambled_sweep_is_one_trip_not_phantom_flips(self):
        opens = [fill(1_000, "A", 3.0, 10, -10 * i, tid=100 - i) for i in range(6)]
        closes = [fill(2_000, "B", 2.9, 20, -60 + 20 * i, tid=i, pnl="2") for i in range(3)]
        trips = lib.build_trips(list(reversed(opens)) + closes[::-1])
        assert len(trips) == 1
        trip = trips[0]
        assert trip["side"] == "short" and trip["closed"]
        assert trip["max_pos"] == pytest.approx(60)
        assert trip["avg_entry"] == pytest.approx(3.0)
        assert trip["avg_exit"] == pytest.approx(2.9)


class TestBuildTrips:
    def test_flip_closes_one_trip_and_opens_the_other_side(self):
        fills = [
            {**fill(1_000, "B", 10.0, 10, 0, tid=1), "dir": "Open Long"},
            {**fill(2_000, "A", 11.0, 15, 10, tid=2), "dir": "Long > Short"},
        ]
        trips = lib.build_trips(fills)
        assert [t["side"] for t in trips] == ["long", "short"]
        assert trips[0]["closed"] and trips[0]["avg_exit"] == pytest.approx(11.0)
        assert trips[1]["closed"] is False and trips[1]["open_pos"] == pytest.approx(-5)

    def test_history_starting_mid_position_is_skipped_not_invented(self):
        fills = [fill(1_000, "A", 10.0, 5, 5, tid=1),         # closes a position we never saw open
                 fill(2_000, "B", 10.0, 2, 0, tid=2)]
        trips = lib.build_trips(fills)
        assert len(trips) == 1 and trips[0]["t_open"] == 2_000

    def test_outcome_and_spot_fills_are_not_perp_trips(self):
        fills = [fill(1_000, "B", 0.1, 100, 0, tid=1, coin="#2020"),
                 fill(1_000, "B", 1.0, 100, 0, tid=2, coin="@107")]
        assert lib.build_trips(fills) == []

    def test_averaging_down_is_measured_against_the_running_average(self):
        fills = [fill(1_000, "B", 100.0, 1, 0, tid=1),
                 fill(2_000, "B", 90.0, 1, 1, tid=2),        # below avg 100
                 fill(3_000, "B", 99.0, 1, 2, tid=3),        # above avg 95
                 fill(4_000, "A", 110.0, 3, 3, tid=4)]
        trip = lib.build_trips(fills)[0]
        assert trip["adds_below_avg_notional"] == pytest.approx(90.0)
        assert trip["adds_above_avg_notional"] == pytest.approx(99.0)


class TestFunding:
    def test_daily_aggregate_row_stamped_before_the_open_attaches(self):
        open_t = 10 * DAY + 14 * 3_600_000
        trips = lib.build_trips([fill(open_t, "B", 1.0, 10, 0, tid=1, coin="ETH"),
                                 fill(open_t + DAY, "A", 1.0, 10, 10, tid=2, coin="ETH")])
        rows = [
            {"time": 10 * DAY, "delta": {"coin": "ETH", "usdc": "-3.0", "nSamples": 10}},
            {"time": 8 * DAY, "delta": {"coin": "ETH", "usdc": "-99.0", "nSamples": 24}},
            {"time": open_t + 3_600_000, "delta": {"coin": "ETH", "usdc": "-1.5"}},
        ]
        lib.attach_funding(trips, rows)
        assert trips[0]["funding"] == pytest.approx(-4.5)


class TestRoundness:
    @pytest.mark.parametrize("px", ["109.99", "2449.9", "76999.0", "69.999", "10.999", "99.999"])
    def test_one_tick_under_a_round_number(self, px):
        assert lib.roundness(px) == "sub"

    @pytest.mark.parametrize("px", ["4.0000", "2450.0", "110.0"])
    def test_round_number_itself(self, px):
        assert lib.roundness(px) == "exact"

    @pytest.mark.parametrize("px", ["78591.0", "2391.7", "115.27", "2.0068"])
    def test_ordinary_prices(self, px):
        assert lib.roundness(px) == "other"


def test_path_stats_marks_the_worst_bar_against_the_average_entry():
    trip = lib.build_trips([fill(0, "B", 100.0, 1, 0, tid=1, coin="BTC"),
                            fill(3 * lib.MS_4H, "B", 80.0, 1, 1, tid=2, coin="BTC"),
                            fill(6 * lib.MS_4H, "A", 95.0, 2, 2, tid=3, coin="BTC")])[0]
    candles = [[i * lib.MS_4H, 100, 100, low, 100] for i, low in
               enumerate([100, 95, 90, 80, 70, 85, 95])]
    lib.path_stats(trip, candles, now_ms=10 * lib.MS_4H)
    # bar 4: two units held at avg 90, low 70 -> -40
    assert trip["mae_usd"] == pytest.approx(-40.0)
    assert trip["mae_pct_vs_avg"] == pytest.approx(70 / 90 - 1)
    assert trip["worst_px_vs_first"] == pytest.approx(-0.30)


# ── W-WH2: the ladder machine ────────────────────────────────────────────────

def series_for(path, max_lev=10):
    """30 flat bars at 100, then `path` as (o, h, l, c) tuples."""
    bars = [[i * DAY, 100.0, 100.0, 100.0, 100.0] for i in range(30)]
    bars += [[(30 + j) * DAY, *ohlc] for j, ohlc in enumerate(path)]
    days, series = bt.align({"X": bars}, ["X"])
    return days, series, {"X": max_lev}


P = bt.frozen_params()


def test_frozen_parameters_match_the_pre_registration():
    assert P == {"trigger_dd": -0.211, "k": 5, "gap": -0.0717, "tp": 0.0514}


def test_v_shape_trigger_two_rungs_take_profit_exact_cash():
    days, series, ml = series_for([
        (100, 100, 78, 78),     # bar 30: close 78 <= 0.789 x 100 -> trigger
        (78, 78, 78, 78),       # bar 31: rung 1 at the open
        (78, 90, 72, 73),       # bar 32: rung 2 at 72.4074; high 90 would clear the OLD target
        (73, 90, 73, 85),       # bar 33: take profit at the new target
    ])
    r = bt.simulate(days, series, ml, P, lev=1)
    assert [x["result"] for x in r["ladders"]] == ["tp"]
    lad = r["ladders"][0]
    assert lad["rungs"] == 2 and lad["open"] == days[31] and lad["close"] == days[33]

    rung = 0.2
    r2_px = 78 * (1 + P["gap"])
    units = rung / 78 + rung / r2_px
    cost = 2 * rung
    target = cost / units * (1 + P["tp"])
    fees = 2 * rung * bt.FEE + units * target * bt.FEE
    funding = (rung / 78) * 78 * bt.FUNDING_PER_DAY + units * 73 * bt.FUNDING_PER_DAY
    assert r["terminal"] == pytest.approx(1 + units * target - cost - fees - funding)


def test_signal_at_the_close_fills_at_the_next_open():
    days, series, ml = series_for([(100, 100, 78, 78), (80, 80, 80, 80)])
    r = bt.simulate(days, series, ml, P, lev=1)
    lad = r["ladders"][0]
    assert lad["open"] == days[31]
    # one rung of 0.2 bought at 80, not at the 78 close that fired it
    units = 0.2 / 80
    assert lad["pnl"] == pytest.approx(0.0)
    assert r["terminal"] == pytest.approx(1 - 0.2 * bt.FEE - units * 80 * bt.FUNDING_PER_DAY)


def test_trigger_high_is_the_prior_30_bars_not_todays_bar():
    # today's high of 130 must not raise the reference: close 100 is not a -21% drawdown
    days, series, ml = series_for([(100, 130, 100, 100), (100, 100, 100, 100)])
    assert series["X"][30][4] == 100.0
    assert bt.simulate(days, series, ml, P, lev=1)["ladders"] == []


def test_a_bar_that_adds_does_not_also_take_profit():
    days, series, ml = series_for([(100, 100, 78, 78), (78, 78, 78, 78), (78, 90, 72, 73), (73, 74, 73, 74)])
    r = bt.simulate(days, series, ml, P, lev=1)
    assert [x["result"] for x in r["ladders"]] == ["open"]


def test_no_stop_at_3x_liquidates_and_the_account_stays_dead():
    path = [(100, 100, 78, 78), (78, 78, 78, 78), (78, 78, 30, 35)] + [(90, 100, 90, 100)] * 40
    days, series, ml = series_for(path, max_lev=3)
    r = bt.simulate(days, series, ml, P, lev=3)
    assert r["terminal"] == 0.0 and r["liquidated"] == days[32]
    assert [x["result"] for x in r["ladders"]] == ["liquidated"]


def test_open_ladder_is_marked_at_the_last_close_not_discarded():
    days, series, ml = series_for([(100, 100, 78, 78), (78, 78, 78, 78), (78, 78, 70, 70)])
    r = bt.simulate(days, series, ml, P, lev=1)
    assert [x["result"] for x in r["ladders"]] == ["open"]
    assert r["ladders"][0]["pnl"] < 0 and r["terminal"] < 1


def test_null_with_zero_probability_never_trades():
    import random
    days, series, ml = series_for([(100, 100, 78, 78), (78, 78, 78, 78), (78, 90, 72, 85)])
    r = bt.simulate(days, series, ml, P, lev=1, entry_prob={"X": 0.0}, rng=random.Random(0))
    assert r["ladders"] == [] and r["terminal"] == 1.0


def test_backup_stop_fills_after_the_adds_at_the_stop_price():
    days, series, ml = series_for([(100, 100, 78, 78), (78, 78, 78, 78), (78, 78, 50, 52)])
    r = bt.simulate(days, series, ml, P, lev=1, stop_frac=0.25)
    lad = r["ladders"][0]
    assert lad["result"] == "stop" and lad["rungs"] == 5
    assert lad["pnl"] < 0


# ── W-STB1: stablecoin peg reversion ─────────────────────────────────────────

stb = _load("stb1", "W-STB1_backtest.py")
FREE = {"in": 0.0, "out": 0.0, "forced": 0.0}


def hbars(rows):
    """[(h, l, c, v)] -> 1h bars [t, o, h, l, c, v]."""
    return [[i * 3_600_000, c, h, l, c, v] for i, (h, l, c, v) in enumerate(rows)]


class TestStablePegReversion:
    def test_a_touch_is_not_a_fill_a_trade_through_is(self):
        touch = hbars([(1.0, 0.999, 0.9995, 10), (1.0002, 0.9995, 1.0, 10)])
        assert stb.simulate(touch, 0.001, FREE)["trades"] == []
        through = hbars([(1.0, 0.99899, 0.9995, 10), (1.0002, 0.9995, 1.0, 10)])
        (t,) = stb.simulate(through, 0.001, FREE)["trades"]
        assert t["px_in"] == pytest.approx(0.999) and t["px_out"] == 1.0 and t["how"] == "target"

    def test_the_exit_needs_a_print_above_the_peg_and_never_the_entry_bar(self):
        same_bar = hbars([(1.0005, 0.998, 0.999, 10)] + [(1.0, 0.999, 0.9995, 10)] * 3)
        (t,) = stb.simulate(same_bar, 0.001, FREE)["trades"]
        assert t["how"] == "open_at_end"

    def test_zero_volume_bars_fill_nothing(self):
        dead = hbars([(1.0, 0.99, 0.995, 0), (1.01, 0.995, 1.0, 0)])
        assert stb.simulate(dead, 0.001, FREE)["trades"] == []

    def test_timeout_sells_at_the_close_with_the_forced_cost(self):
        rows = [(1.0, 0.998, 0.999, 10)] + [(0.9999, 0.9985, 0.9990, 10)] * (stb.HOLD_BARS + 2)
        cost = {"in": 0.0001, "out": 0.0001, "forced": 0.0003}
        t, reentry = stb.simulate(hbars(rows), 0.001, cost)["trades"]
        assert t["how"] == "timeout" and t["i_out"] - t["i_in"] == stb.HOLD_BARS
        assert reentry["i_in"] > t["i_out"] and reentry["how"] == "open_at_end"
        assert t["ret"] == pytest.approx(0.9990 / 0.999 - 1 - 0.0004)

    def test_one_position_at_a_time_and_the_next_bid_starts_after_the_exit(self):
        rows = [(1.0, 0.998, 0.999, 10), (1.0002, 0.998, 1.0, 10), (1.0002, 0.998, 1.0, 10)]
        trades = stb.simulate(hbars(rows), 0.001, FREE)["trades"]
        assert [(t["i_in"], t["i_out"]) for t in trades] == [(0, 1), (2, 2)]

    def test_the_null_anchor_never_sees_the_current_or_a_future_bar(self):
        """Always pick the LATEST bar the null is allowed to. The first order is
        placed on the jump bar; if its range included that bar, it would anchor
        on its own close of 2.0 and fill a bid at 1.999 off its own low of 1.5."""
        import random
        rows = [(0.9, 0.9, 0.9, 10), (2.0, 1.5, 2.0, 10), (2.0, 1.5, 2.0, 10)]

        class Latest(random.Random):
            def randrange(self, lo, hi):
                return hi - 1
        trades = stb.simulate(hbars(rows), 0.001, FREE, rng=Latest())["trades"]
        assert not [t for t in trades if t["i_in"] == 1], trades

    def test_fee_tiers_follow_the_stable_pair_rule(self):
        assert stb.measured("USDH") is stb.COSTS["measured_stable"]
        assert stb.measured("FEUSD") is stb.COSTS["measured_full"]
