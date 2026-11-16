"""Gate tests for the thesis-range eval
(services/strategies/evals/thesis_range_test.py, 2026-11-16): the range read
off a thesis, the desk-tick cadence of resting orders, the resting
stop-entry flip, the operator's ladder (short, flip, ratchet, bounce), the
holdout split fixed before the search, the search-only selection and the
random-level baseline. Synthetic bars only, no network, no state read.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from pathiel.models.types import Candle
from services.strategies.evals import thesis_range_test as E
from services.strategies.evals.thesis_range_test import Ladder, Series, Th, Variant

F = E.FIVE_MS
T0 = int(datetime(2026, 11, 3, 8, tzinfo=timezone.utc).timestamp() * 1000)     # a Tuesday, on the hour
EQ = 1000.0
P = dataclasses.replace(E.base_params(EQ, 0.06), taker_fee=0.0, maker_fee=0.0, slippage=0.0, size_slippage=False,
                        leverage=20.0)


def series(rows, entry=100.0, target=102.0, stop=99.0, tids=None, gi=None, atr=0.5, weekend=None, flt=None,
           swing=(None, None)) -> Series:
    n = len(rows)
    gi = gi or list(range(n))
    c = [r[3] for r in rows]
    return Series(step_ms=F, t=[T0 + i * F for i in range(n)], o=[r[0] for r in rows], h=[r[1] for r in rows],
                  l=[r[2] for r in rows], c=c, weekend=weekend or [False] * n, fund=[None] * n, gi=gi,
                  entry=[entry] * n, target=[target] * n, stop=[stop] * n, tid=tids or ["A"] * n, atr=[atr] * n,
                  pc=[c[g - 1] if g > 0 else rows[0][0] for g in gi],
                  flt={f: list((flt or {}).get(f, [True] * n)) for f in E.FILTERS},
                  swing_hi=[swing[0]] * n, swing_lo=[swing[1]] * n)


def run(rows, v, **kw):
    walk_kw = {k: kw.pop(k) for k in ("rand_seed", "midbar_worst", "live") if k in kw}
    return E.walk_any(series(rows, **kw), v, P, EQ, **walk_kw)


SELL_TOP = Variant(mode="S", a=1.0, stf=1.0, sstop="p0.36")
BUY_ENTRY = Variant(mode="L", b=0.0, ltf=1.0, lstop="p0.36")
QUIET = (101.0, 101.2, 100.9, 101.0)


# ── the range from a thesis ────────────────────────────────────────────────

def test_range_is_the_long_thesis_entry_to_its_first_target():
    th = Th(created_ms=1, side="long", entry=92.1, stop=91.3, target=93.701)
    assert E.thesis_range(th) == (92.1, 93.701, 91.3)
    assert E.level_at(92.1, 93.701, 0.0) == 92.1 and E.level_at(92.1, 93.701, 1.0) == pytest.approx(93.701)
    assert E.range_pos(92.9005, 92.1, 93.701) == pytest.approx(0.5)


@pytest.mark.parametrize("th", [None, Th(1, "short", 110.2, 112.8, 105.0), Th(1, "flat", None, None, 113.75),
                                Th(1, "long", 100.0, 99.0, None), Th(1, "long", 100.0, 99.0, 100.1)])
def test_no_range_without_a_long_thesis_a_target_and_some_width(th):
    assert E.thesis_range(th) is None


def test_range_theses_scale_and_flag_a_thesis_written_after_a_close():
    raw = [SimpleNamespace(created_at_ms=T0, side="long", entry_px=100.0, stop_px=99.0, targets=[102.0, 104.0],
                           confidence=0.4),
           SimpleNamespace(created_at_ms=T0 + 5 * E.HOUR_MS, side="long", entry_px=101.0, stop_px=None, targets=[],
                           confidence=None)]
    hours = [Candle(t=T0 - E.HOUR_MS, o=90, h=91, l=89, c=90.5, v=1)]
    got = E.range_theses(raw, hours, {T0: 0.9}, close_times=[T0 - 30 * 60_000])
    assert got[0].entry == pytest.approx(90.0) and got[0].target == pytest.approx(91.8)
    assert got[0].post_close and got[0].px_at_write == 90.5 and got[0].tid == str(T0)
    assert got[1].entry is None and not got[1].post_close          # no factor: no levels on that instrument


# ── the holdout is fixed before the search ─────────────────────────────────

def test_holdout_split_is_the_newest_quarter_of_theses_and_reads_only_timestamps():
    theses = [SimpleNamespace(created_at_ms=1000 + 7 * i) for i in range(78)]
    assert E.holdout_split(theses) == 1000 + 7 * 58                 # the newest 20 of 78
    assert E.holdout_split(list(reversed(theses))) == 1000 + 7 * 58
    assert E.holdout_split(theses[:8]) == 1000 + 7 * 6              # 2 of 8
    assert E.holdout_split([]) is None


def test_select_reads_the_search_segment_only():
    def row(n, net, halves, hold):
        return {E.SEARCH: {"n": n, "net_usd": net, "halves": halves},
                **{s: {"n": 5, "net_usd": hold} for s in E.HOLDOUT_SEGS}}
    rows = {"a": row(12, 30.0, [10.0, 20.0], -99.0), "b": row(12, 40.0, [45.0, -5.0], 99.0),
            "c": row(9, 80.0, [40.0, 40.0], 99.0), "d": row(15, 20.0, [5.0, 15.0], 500.0)}
    assert E.select(rows) == "a"                                    # b fails a half, c is under MIN_N
    for r in rows.values():
        for s in E.HOLDOUT_SEGS:
            r[s]["net_usd"] *= -7.0                                 # any holdout number: the pick does not move
    assert E.select(rows) == "a"
    assert E.select({k: {E.SEARCH: r[E.SEARCH]} for k, r in rows.items()}) == "a"
    assert E.select({"b": rows["b"], "c": rows["c"]}) == "b"        # no selectable row: best net with MIN_N trades


def test_evaluate_on_the_search_segments_never_touches_a_holdout_segment():
    seg = {"series": series([QUIET] * 4), "params": P, "lo": 0, "hi": 3, "mid_ms": T0 + 2 * F}
    rows = E.evaluate([SELL_TOP, E.OPERATOR], {E.SEARCH: seg, "wti_5m": None}, (E.SEARCH,))
    assert set(rows) == {SELL_TOP.name, E.OPERATOR.name}
    assert all(set(r) == {E.SEARCH} for r in rows.values())
    assert set(E.SEARCH_SEGS).isdisjoint(E.HOLDOUT_SEGS) and E.SEARCH in E.SEARCH_SEGS
    assert all(s.startswith("wti") or s.endswith("holdout") for s in E.HOLDOUT_SEGS)


# ── the grid ───────────────────────────────────────────────────────────────

def test_grid_sizes_and_names():
    base, ladder = E.base_variants(), E.ladder_variants()
    assert len(base) == 36 + 90 + 36 * 90
    assert len(ladder) == 4 * 3 * 5 * 3 * 5 * 2 * 2 * 9 == 32400
    names = [v.name for v in base + ladder]
    assert len(set(names)) == len(names)
    assert E.OPERATOR.name in names and E.BASELINE.name in names
    assert E.OPERATOR == Ladder("mkt0.75", 0.20, "p0.25", 0.20, "be50", "tick", True, ())
    assert E.OPERATOR.family == "O_tick_bounce"
    assert dataclasses.replace(E.OPERATOR, filt=("sess_ny",)).family == "O_tick_bounce_filtered"
    assert {n.entry for n in E.ladder_neighbours(Ladder(entry="lim0.5"))} == {"lim0.33", "lim0.5", "lim0.75"}
    assert all(n.mode == "S" for n in E.neighbours(SELL_TOP)) and len(E.neighbours(SELL_TOP)) == 2


# ── the desk-tick cadence ──────────────────────────────────────────────────

def test_tick_bar_and_sessions():
    assert [E.tick_bar(T0 + k * F, F) for k in range(4)] == [False, True, False, False]
    assert E.tick_bar(T0, E.FIFTEEN_MS) and E.tick_bar(T0 + E.FIFTEEN_MS, E.FIFTEEN_MS)
    assert [E.session_of(T0 + h * E.HOUR_MS) for h in (-3, -1, 4, 5, 13, 14)] == [
        "asia", "london", "london", "ny", "ny", "asia"]


def test_build_series_reads_the_thesis_the_cron_had_seen():
    hours = [Candle(t=T0 - (30 - k) * E.HOUR_MS, o=100, h=100.5, l=99.5, c=100, v=1) for k in range(30)]
    bars = [Candle(t=T0 + k * F, o=100, h=100.2, l=99.8, c=100 + k / 10, v=1) for k in range(5)]
    a = Th(created_ms=T0 - E.HOUR_MS, side="long", entry=99.0, stop=98.0, target=101.0, conf=0.6, px_at_write=99.2)
    b = Th(created_ms=T0 + 4 * 60_000, side="long", entry=99.5, stop=98.5, target=102.0, conf=0.4, px_at_write=103.0)
    s = E.build_series(bars, hours, [a, b], F)
    assert s.gi == [-1, 1, 1, 1, 4]
    assert s.entry == [None, 99.0, 99.0, 99.0, 99.5]                # b was written a minute after the :03 cron
    assert s.tid[3] == a.tid and s.tid[4] == b.tid
    assert s.pc[1] == 100.0 and s.pc[4] == pytest.approx(100.3)
    assert s.atr[1] == pytest.approx(1.0)
    assert s.flt["conf_ge50"][1] and not s.flt["conf_ge50"][4] and s.flt["conf_lt50"][4]
    assert s.flt["near_write"][1] and s.flt["far_write"][4]
    assert s.flt["age_lt2h"][1] and s.flt["wid_lt4atr"][1] and s.flt["sess_london"][1]
    s15 = E.build_series([Candle(t=T0 + k * E.FIFTEEN_MS, o=100, h=100.2, l=99.8, c=100, v=1) for k in range(3)],
                         hours, [a, b], E.FIFTEEN_MS)
    assert s15.gi == [-1, 1, 2] and s15.entry == [None, 99.0, 99.5]


def test_filter_flags_fail_closed_without_a_thesis():
    off = E.filter_flags(None, T0, 0.5, None, None, False)
    assert off["eia_clear"] and off["sess_london"]
    assert not any(v for k, v in off.items() if k not in ("eia_clear", "sess_london"))
    th = Th(created_ms=T0 - 9 * E.HOUR_MS, side="long", entry=100.0, stop=99.0, target=103.0, conf=0.5,
            post_close=True, px_at_write=100.2)
    on = E.filter_flags(th, T0, 0.5, 1, -1, True)
    assert on["age_ge8h"] and on["wid_ge4atr"] and on["conf_ge50"] and on["post_close"] and on["near_write"]
    assert on["t1h_up"] and on["t4h_down"] and not on["eia_clear"] and not on["age_2to8h"]


# ── the range rule: resting limits at the thesis levels ────────────────────

def test_sell_limit_at_the_thesis_target_fills_on_a_trade_through_and_exits_at_the_thesis_entry():
    got = run([QUIET, (101.0, 102.05, 101.0, 101.9), (101.9, 101.9, 99.9, 100.0), QUIET], SELL_TOP)
    (tr,) = got["trades"]
    assert (tr["leg"], tr["entry_px"], tr["exit_px"], tr["reason"], tr["i"], tr["x"]) == ("short", 102.0, 100.0,
                                                                                         "target", 1, 2)
    assert tr["stop_px"] == pytest.approx(102.3672) and tr["pos"] == 1.0
    assert tr["net_usd"] == pytest.approx(2.0 * 0.10 * EQ * 20 / 102.0)
    assert not run([QUIET, (101.0, 102.0, 101.0, 101.9), QUIET], SELL_TOP)["trades"]     # a touch is not a fill


def test_a_limit_fill_bar_counts_the_stop_but_never_the_target():
    got = run([QUIET, (101.0, 102.5, 99.0, 102.4)], SELL_TOP)
    (tr,) = got["trades"]
    assert tr["reason"] == "stop" and tr["exit_px"] == pytest.approx(102.3672) and tr["x"] == 1


def test_a_limit_is_only_placed_on_the_far_side_of_the_last_close():
    above = (102.5, 102.6, 102.4, 102.5)
    assert not run([above, (102.5, 102.6, 102.2, 102.3), above], SELL_TOP)["trades"]
    below = (99.5, 99.6, 99.4, 99.5)
    assert not run([below, (99.5, 99.8, 99.3, 99.6), below], BUY_ENTRY)["trades"]


def test_one_entry_per_leg_per_thesis_and_only_the_next_tick_places_again():
    rows = [(100.5, 100.6, 99.9, 100.2), (100.2, 100.5, 99.5, 100.4), (100.4, 100.6, 99.95, 100.5),
            (100.5, 100.6, 99.95, 100.3), QUIET]
    same = run(rows, BUY_ENTRY)
    assert [(t["i"], t["reason"]) for t in same["trades"]] == [(0, "stop")]            # thesis A is spent
    every = run(rows, BUY_ENTRY, tids=["A", "A", "B", "B", "B"])
    assert [t["i"] for t in every["trades"]] == [0, 2]
    slow = run(rows, BUY_ENTRY, tids=["A", "A", "B", "B", "B"], gi=[0, 0, 0, 3, 3])
    assert [t["i"] for t in slow["trades"]] == [0, 3]               # bar 2 has no tick since the close


def test_a_filter_that_does_not_hold_at_the_tick_pulls_the_order():
    rows = [QUIET, (101.0, 102.05, 101.0, 101.9), QUIET]
    v = dataclasses.replace(SELL_TOP, filt=("sess_ny",))
    assert len(run(rows, v)["trades"]) == 1
    assert not run(rows, v, flt={"sess_ny": [True, False, True]})["trades"]


def test_weekend_pulls_orders_and_closes_at_the_open():
    rows = [QUIET, (101.0, 102.05, 101.0, 101.9), (101.8, 101.9, 101.7, 101.8), QUIET]
    assert not run(rows, SELL_TOP, weekend=[False, True, True, True])["trades"]
    (tr,) = run(rows, SELL_TOP, weekend=[False, False, True, True])["trades"]
    assert tr["reason"] == "weekend" and tr["exit_px"] == pytest.approx(101.8 * (1 + 0.1 / 101.8))


# ── the resting stop-entry flip ────────────────────────────────────────────

FLIP_ROWS = [(100.5, 100.6, 100.4, 100.5), (100.5, 101.1, 100.5, 101.0), (101.0, 101.5, 100.7, 101.4),
             (101.4, 102.1, 101.3, 102.0)]
FLIP_UP = Variant(mode="S", a=0.5, stf=1.0, sstop="p0.36", flip_up="atr1.0")


def test_flip_up_is_a_resting_buy_stop_filled_on_the_stop_bar_at_the_stop_price():
    got = run(FLIP_ROWS, FLIP_UP)
    short, flip = got["trades"]
    assert (short["leg"], short["reason"], short["entry_px"]) == ("short", "stop", 101.0)
    assert short["exit_px"] == pytest.approx(101.3636)
    assert (flip["leg"], flip["i"], flip["reason"]) == ("flip_up", 2, "target")
    assert flip["entry_px"] == pytest.approx(101.3636) and flip["exit_px"] == 102.0     # the thesis target
    assert flip["stop_px"] == pytest.approx(101.3636 - 0.5)
    assert dataclasses.replace(FLIP_UP, flip_up="off").name != FLIP_UP.name
    assert len(run(FLIP_ROWS, dataclasses.replace(FLIP_UP, flip_up="off"))["trades"]) == 1


def test_on_the_flip_bar_only_the_close_stops_the_new_leg_unless_the_worst_case_is_asked():
    flip = run(FLIP_ROWS, FLIP_UP, midbar_worst=True)["trades"][1]
    assert flip["reason"] == "stop" and flip["x"] == 2               # the bar's low 100.7 is under 100.8636


def test_flip_down_shorts_the_broken_thesis_stop_for_one_measured_move():
    v = Variant(mode="L", b=0.0, ltf=1.0, lstop="thesis", flip_dn=True)
    rows = [(100.5, 100.6, 99.9, 100.2), (100.2, 100.3, 98.9, 98.95), (98.95, 99.0, 97.9, 98.0)]
    long_, flip = run(rows, v)["trades"]
    assert (long_["reason"], long_["exit_px"]) == ("stop", 99.0)
    assert (flip["leg"], flip["entry_px"], flip["target_px"], flip["reason"]) == ("flip_dn", 99.0, 98.0, "target")
    assert flip["stop_px"] == pytest.approx(100.0 * (1 + E.ENTRY_PAD))


def test_a_flip_with_no_room_to_the_thesis_target_is_refused():
    v = dataclasses.replace(SELL_TOP, flip_up="atr1.0")
    got = run([QUIET, (101.0, 102.5, 101.0, 102.4), QUIET], v)
    assert [t["leg"] for t in got["trades"]] == ["short"] and got["refused"] == {"flip_up_no_room": 1}


# ── the operator's ladder ──────────────────────────────────────────────────

LADDER_ROWS = [(100.9, 101.0, 100.8, 100.9), (100.9, 101.5, 100.9, 101.4), (101.4, 102.1, 101.3, 102.0),
               (102.0, 102.1, 99.9, 100.1), (100.1, 100.4, 100.0, 100.3)]
RESTING = Ladder(entry="mkt0.75", ssize=0.20, stop="p0.50", fsize=0.20, ratchet="none", fill="resting", bounce=True)


def test_ladder_short_flip_and_bounce_with_resting_orders():
    got = run(LADDER_ROWS, RESTING)
    assert [(t["leg"], t["reason"], t["i"]) for t in got["trades"]] == [
        ("short", "stop", 0), ("flip", "target", 1), ("bshort", "target", 2), ("blong", "open", 3)]
    short, flip, bshort, blong = got["trades"]
    assert short["entry_px"] == pytest.approx(100.8)                 # the open less half the bar's range
    assert short["target_px"] == 100.0 and short["stop_px"] == pytest.approx(100.8 * 1.005)
    assert flip["entry_px"] == pytest.approx(101.304) and flip["target_px"] == 102.0
    assert flip["stop_px"] == pytest.approx(101.304 * 0.995)         # the mirror of the short's stop
    assert (bshort["entry_px"], bshort["exit_px"]) == (102.0, 100.0)
    assert (blong["entry_px"], blong["target_px"]) == (100.0, 102.0)
    assert short["margin_pct"] == pytest.approx(20.0) and flip["margin_pct"] == pytest.approx(20.0)


def test_ladder_sizes_each_side_by_its_own_share():
    got = run(LADDER_ROWS, dataclasses.replace(RESTING, ssize=0.05, fsize=0.30))
    assert [round(t["margin_pct"]) for t in got["trades"]] == [5, 30, 5, 30]


def test_ladder_tick_flip_is_a_market_order_on_the_next_desk_tick():
    got = run(LADDER_ROWS, dataclasses.replace(RESTING, fill="tick"))
    flip = got["trades"][1]
    assert (flip["leg"], flip["i"]) == ("flip", 2)
    assert flip["entry_px"] == pytest.approx(101.4 * (1 + 0.4 / 101.4))   # the open plus half the bar's range
    slow = run(LADDER_ROWS, dataclasses.replace(RESTING, fill="tick"), gi=[0, 0, 0, 3, 3])
    assert [t["leg"] for t in slow["trades"]] == ["short"]           # no tick at bar 2; by bar 3 the room is gone
    assert slow["refused"] == {"flip_no_room": 1}


def test_bounce_ends_when_the_thesis_is_rewritten_and_is_off_without_the_flag():
    moved = run(LADDER_ROWS, RESTING, tids=["A", "A", "B", "B", "B"])
    assert [t["leg"] for t in moved["trades"]] == ["short", "flip"]
    assert moved["refused"].get("bshort_thesis_moved") == 1
    once = run(LADDER_ROWS, dataclasses.replace(RESTING, bounce=False))
    assert [t["leg"] for t in once["trades"]] == ["short", "flip"]


def test_a_stopped_bounce_leg_ends_the_cycle():
    rows = LADDER_ROWS[:3] + [(102.0, 102.6, 101.9, 102.5), QUIET]
    got = run(rows, RESTING)
    assert [(t["leg"], t["reason"]) for t in got["trades"]] == [("short", "stop"), ("flip", "target"),
                                                                ("bshort", "stop")]


def test_ladder_limit_entry_rests_at_its_range_position():
    got = run([(100.2, 100.3, 100.1, 100.2), (100.2, 100.7, 100.2, 100.6), (100.6, 100.6, 99.9, 100.0)],
              dataclasses.replace(RESTING, entry="lim0.33"))
    (tr,) = got["trades"]
    assert tr["entry_px"] == pytest.approx(100.66) and tr["reason"] == "target" and tr["pos"] == pytest.approx(0.33)


def test_ratchet_and_stop_rules():
    assert E.ratchet_stop("be50", 100.0, 102.0, 99.5, 101.0, 0.5) == 100.0
    assert E.ratchet_stop("be50", 100.0, 102.0, 99.5, 100.9, 0.5) == 99.5
    assert E.ratchet_stop("be75", 100.0, 102.0, 99.5, 101.4, 0.5) == 99.5
    assert E.ratchet_stop("trail1.0", 100.0, 102.0, 99.5, 101.5, 0.5) == 101.0
    assert E.ratchet_stop("trail2.0", 100.0, 102.0, 101.2, 101.5, 0.5) == 101.2      # never lower
    assert E.ratchet_stop("none", 100.0, 102.0, 99.5, 101.9, 0.5) == 99.5
    assert E.leg_stop("p0.25", -1, 100.0, None) == pytest.approx(100.25)             # 5% of the margin at 20x
    assert E.leg_stop("p0.25", 1, 100.0, None) == pytest.approx(99.75)
    assert E.leg_stop("swing", -1, 100.0, 100.4) == pytest.approx(100.4 * 1.0005)
    assert E.leg_stop("swing", 1, 100.0, 99.99) == pytest.approx(99.9)               # never closer than MIN_STOP
    assert E.leg_stop("swing", -1, 100.0, None) is None


def test_the_ratchet_moves_the_long_stop_at_the_next_tick():
    rows = LADDER_ROWS[:2] + [(101.4, 101.8, 101.35, 101.7), (101.7, 101.75, 101.2, 101.3), QUIET]
    plain = run(rows, RESTING)["trades"][1]
    assert plain["reason"] == "open"
    be = run(rows, dataclasses.replace(RESTING, ratchet="be50"))["trades"][1]
    assert (be["reason"], be["x"]) == ("stop", 3) and be["exit_px"] == pytest.approx(101.304)
    assert be["net_usd"] == pytest.approx(0.0)


def test_beside_the_live_book_nothing_opens_and_an_open_leg_is_closed():
    from services.strategies.evals.stop_reverse_test import Live
    n = len(LADDER_ROWS)
    blocked = Live([EQ] * n, [True] * n, [False] * n)
    assert not run(LADDER_ROWS, RESTING, live=blocked)["trades"]
    enters = Live([EQ] * n, [False, False, True, True, True], [False, False, True, False, False])
    got = run(LADDER_ROWS, RESTING, live=enters)["trades"]
    assert [(t["leg"], t["reason"]) for t in got] == [("short", "stop"), ("flip", "live_long")]


# ── the random-level baseline ──────────────────────────────────────────────

def test_random_pos_is_seeded_uniform_and_per_thesis():
    draws = [E.random_pos(7, "short", str(k)) for k in range(200)]
    assert draws == [E.random_pos(7, "short", str(k)) for k in range(200)]
    assert all(0.0 <= d < 1.0 for d in draws) and len(set(draws)) == 200
    assert 0.4 < sum(draws) / 200 < 0.6
    assert E.random_pos(7, "short", "A") != E.random_pos(8, "short", "A") != E.random_pos(8, "long", "A")


def test_random_level_baseline_draws_the_entry_inside_the_same_range():
    rows = [(100.0, 100.05, 99.95, 100.0)] + [(100.0 + k / 4, 100.3 + k / 4, 100.0 + k / 4, 100.25 + k / 4)
                                              for k in range(10)]
    v = dataclasses.replace(SELL_TOP, sstop="top")
    for seed in range(5):
        got = run(rows, v, rand_seed=seed)
        assert got["trades"] == run(rows, v, rand_seed=seed)["trades"]
        (tr,) = got["trades"]
        assert 100.0 < tr["entry_px"] < 102.0
        assert tr["entry_px"] == pytest.approx(E.level_at(100.0, 102.0, E.random_pos(seed, "short", "A")))
        assert tr["target_px"] == 100.0                               # everything but the level is held
    assert len({run(rows, v, rand_seed=k)["trades"][0]["entry_px"] for k in range(5)}) == 5


def test_ladder_baseline_displaces_the_whole_range_and_keeps_its_width():
    s = series([QUIET] * 2)
    assert E.range_at(s, 0) == (100.0, 102.0, 99.0, "A")
    e, tg, st, tid = E.range_at(s, 0, rand_seed=3)
    assert tg - e == pytest.approx(2.0) and e - st == pytest.approx(1.0) and tid == "A"
    assert abs(e - 100.0) <= 2.0 and e != 100.0
    assert E.range_at(s, 0, rand_seed=3) == E.range_at(s, 1, rand_seed=3)


def test_baseline_compares_the_rule_with_its_own_random_draws():
    rows = [(100.0, 100.05, 99.95, 100.0)] + [(100.0 + k / 4, 100.3 + k / 4, 100.0 + k / 4, 100.25 + k / 4)
                                              for k in range(8)] + [(102.0, 102.04, 99.9, 100.0)]
    seg = {"series": series(rows), "params": P, "lo": 0, "hi": len(rows) - 1}
    names = ("brent_5m_holdout", "wti_5m")
    b = E.baseline(dataclasses.replace(SELL_TOP, sstop="top"), {n: seg for n in names}, names, draws=20)
    cell = b["segments"]["wti_5m"]
    assert cell["rule_n"] == 1 and cell["rule_usd"] == pytest.approx(2.0 * 2000 / 102.0, abs=0.01)
    assert cell["rule_percentile"] == 100.0 and cell["beats_p95"]    # no level in the range sells higher
    assert b["pool"]["segments"] == list(names) and b["pool"]["rule_usd"] == pytest.approx(2 * cell["rule_usd"], abs=0.01)
    assert b == E.baseline(dataclasses.replace(SELL_TOP, sstop="top"), {n: seg for n in names}, names, draws=20)


# ── verdicts, the deflated view, the filters ───────────────────────────────

def _row(search_net, halves, hold, n=12):
    return {E.SEARCH: {"n": n, "wins": 6, "net_usd": search_net, "halves": halves},
            **{s: {"n": 4, "wins": 2, "net_usd": hold} for s in E.HOLDOUT_SEGS + ("brent_15m_search",)}}


def test_holds_needs_every_leg():
    good = {"pool": {"beats_p95": True}}
    assert E.holds(_row(20.0, [5.0, 15.0], 3.0), good)["holds"]
    assert not E.holds(_row(20.0, [5.0, 15.0], 3.0), {"pool": {"beats_p95": False}})["holds"]
    assert not E.holds(_row(20.0, [25.0, -5.0], 3.0), good)["holds"]
    assert not E.holds(_row(20.0, [5.0, 15.0], 3.0, n=9), good)["holds"]
    bad = _row(20.0, [5.0, 15.0], 3.0)
    bad["wti_15m"]["net_usd"] = -0.5
    got = E.holds(bad, good)
    assert not got["holds"] and not got["legs"]["wti_15m_above_zero"] and got["holdout_trades"] == 12


def test_deflated_view_follows_the_search_winners_onto_the_holdout():
    rows = {f"v{k}": _row(float(k), [1.0, 1.0], float(-k)) for k in range(1, 21)}
    d = E.deflated(rows, "v20", top_k=5)
    assert d["variants"] == 20 and d["search"] == {"chosen_usd": 20.0, "median_usd": 10.5, "above_zero": 20}
    cell = d["holdout"]["wti_5m"]
    assert cell["spearman_search_vs_holdout"] == -1.0 and cell["chosen_percentile"] == 0.0
    assert cell["top_k_median_usd"] == -18.0 and cell["top_k_above_zero_pct"] == 0.0
    assert E.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == 1.0 and E.spearman([1, 2], [1, 2]) is None
    assert E.percentile_of(5.0, [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]) == 40.0
    assert E.quantile([0, 10], 95.0) == pytest.approx(9.5)


def test_filters_rank_by_expectancy_not_by_trading_less():
    rows = {}
    for c in ("x", "y"):
        rows[c] = _row(-10.0, [0, 0], 0.0)
        rows[f"{c}+sess_ny"] = {E.SEARCH: {"n": 30, "wins": 20, "net_usd": 15.0}}       # +0.5 a trade
        rows[f"{c}+age_lt2h"] = {E.SEARCH: {"n": 2, "wins": 2, "net_usd": 8.0}}         # +4 a trade on 2 trades
        rows[f"{c}+t1h_up"] = {E.SEARCH: {"n": 40, "wins": 10, "net_usd": -4.0}}
    assert E.rank_filters(rows, ["x", "y"], ("t1h_up", "age_lt2h", "sess_ny")) == ["sess_ny", "t1h_up", "age_lt2h"]
    assert E.pooled(rows, ["x+sess_ny", "y+sess_ny"], E.SEARCH)["exp_usd"] == 0.5
    assert E.combos(["t4h_up", "sess_ny", "age_ge8h"]) == [("t4h_up", "sess_ny"), ("t4h_up", "age_ge8h"),
                                                          ("sess_ny", "age_ge8h"), ("t4h_up", "sess_ny", "age_ge8h")]
    assert E.combos(["age_lt2h", "age_ge8h", "sess_ny"]) == [("age_lt2h", "sess_ny"), ("age_ge8h", "sess_ny")]
    assert E.combos(["post_close", "not_post_close"]) == []


# ── the account's own trades ───────────────────────────────────────────────

def test_hand_trades_are_labelled_by_where_they_entered_the_range():
    assert [E.label_entry(p) for p in (None, -0.5, 0.0, 0.1, 0.5, 0.95, 1.5)] == [
        "no_range", "below_entry", "at_entry", "at_entry", "mid_range", "at_target", "above_target"]
    ths = [Th(created_ms=T0, side="long", entry=92.1, stop=91.3, target=93.701)]

    def trip(i, side, px, exit_px, net, **kw):
        return {"id": f"t{i}", "coin": E.BRENT, "entry_ms": T0 + i * 1000, "side": side, "entry_px": px,
                "exit_px": exit_px, "net_usd": net, "notional_usd": 1000.0, "exit_kind": "target", **kw}
    rows = [trip(1, "long", 92.962, 93.722, 19.94, target_px=93.709), trip(2, "short", 93.65, 92.2, 14.0),
            trip(3, "long", 92.1, 91.3, -8.0), trip(3, "long", 92.1, 91.3, -8.5),      # the same id: last wins
            {"id": "g", "coin": "xyz:GOLD", "entry_ms": T0, "side": "long", "entry_px": 1, "net_usd": 1}]
    got = E.score_hand_trades(rows, ths)
    assert [t["label"] for t in got["trades"]] == ["mid_range", "at_target", "at_entry"]
    first = got["trades"][0]
    assert first["pos"] == pytest.approx(0.538, abs=1e-3) and first["target_is_thesis"]
    assert first["reach"] == pytest.approx(1.03, abs=0.01) and first["net_bps"] == pytest.approx(199.4)
    sl = got["slices"]
    assert sl["all|all"]["n"] == 3 and sl["long|all"]["net_usd"] == pytest.approx(11.44)
    assert sl["short|at_target"]["label"] == "LEANING +" and sl["long|at_entry"]["label"] == "LEANING -"
    assert sl["long|all"]["label"] == "NOISE"                         # one win of two, net up: not a lean


def test_small_slices_never_read_as_an_edge():
    assert E.slice_label(0, 0.0, 0) == "NONE"
    assert E.slice_label(9, 50.0, 9) == "LEANING +" and E.slice_label(3, 5.0, 1) == "NOISE"
    assert E.slice_label(12, 50.0, 9) == "EDGE?" and E.slice_label(12, -50.0, 3) == "LEAK?"
    assert E.slice_label(12, 50.0, 5) == "NOISE"


def test_cli_help_exits_clean(capsys):
    with pytest.raises(SystemExit) as exc:
        E.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "Research only" in out and "--cache-dir" in out
