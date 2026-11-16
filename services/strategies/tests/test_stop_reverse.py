"""Gate tests for the stop-and-reverse eval
(services/strategies/evals/stop_reverse_test.py, 2026-11-16): the grid, the
level and sizing rules, the walker's leg sequencing (short, flip on the
stop, third leg on the target), both fill models, the one-position rules
beside the live book, the replay matcher and the verdict labels.
Synthetic bars only, no network, no state read.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import pytest

from services.strategies.evals import short_ladder_test as SL
from services.strategies.evals import stop_reverse_test as E
from services.strategies.evals.stop_reverse_test import Live, Series, Variant

F = E.FIVE_MS
T0 = int(datetime(2026, 11, 3, 8, tzinfo=timezone.utc).timestamp() * 1000)     # a Tuesday
EQ = 1000.0
P = dataclasses.replace(E.base_params(EQ, 0.06), taker_fee=0.0, maker_fee=0.0, slippage=0.0, size_slippage=False,
                        leverage=20.0)
ARM = (100.2, 100.7, 100.2, 100.6)       # closes 0.6% over the 100.00 thesis entry: arms the short
FILL = (100.6, 100.7, 100.5, 100.6)      # the short fills at 100.6 less half the 0.2 range = 100.5
STOPPED = (100.6, 101.2, 100.6, 101.1)   # trades through the +0.5% stop at 101.0025
QUIET = (101.1, 101.2, 101.0, 101.1)


def series(rows, late=0, entry=100.0, target=103.0, loc=103.0, weekend=None, tids=None) -> Series:
    n = len(rows)
    return Series(step_ms=F, late=late, t=[T0 + i * F for i in range(n)], o=[r[0] for r in rows],
                  h=[r[1] for r in rows], l=[r[2] for r in rows], c=[r[3] for r in rows], ok=[True] * n,
                  weekend=weekend or [False] * n, entry=[entry] * n, target=[target] * n,
                  tid=tids or ["A"] * n, hi24=[104.0] * n, atr=[0.5] * n, fund=[None] * n,
                  loc={k: [loc] * n for k in (4, 8, 12)})


def run(rows, v, **kw):
    live = kw.pop("live", None)
    forced = kw.pop("forced", None)
    p = kw.pop("p", P)
    return E.walk(series(rows, **kw), v, p, EQ, live=live, forced=forced)


SHORT_ONLY = Variant(0.005, "p0.50", flip=False, size="m20")
RESTING = Variant(0.005, "p0.50", True, "loc8", "p0.25", "resting", "off", "m20")
TICK = dataclasses.replace(RESTING, fill="tick")


# ── the grid ───────────────────────────────────────────────────────────────

def test_grid_size_names_and_families():
    vs = E.variants()
    assert len(vs) == 60 + 7200
    assert len({v.name for v in vs}) == len(vs)
    assert {v.family for v in vs} == set(E.FAMILIES)
    assert E.OPERATOR_VARIANT.name in {v.name for v in vs} and E.OPERATOR_VARIANT.family == "SR3_thesis"
    assert E.PRIMARY_VARIANT.family == "SR3_local"


def test_neighbours_step_one_ordered_axis_at_a_time():
    v = Variant(0.005, "p0.36", True, "loc8", "atr1.0", "tick", "off", "m20")
    got = {n.name for n in E.neighbours(v)}
    assert len(got) == 2 + 2 + 2 + 1
    assert dataclasses.replace(v, x=0.0025).name in got and dataclasses.replace(v, ltarget="loc12").name in got
    assert all(n.size == v.size and n.fill == v.fill for n in E.neighbours(v))
    assert len(E.neighbours(Variant(0.0025, "swing", flip=False))) == 1


# ── levels and sizing ──────────────────────────────────────────────────────

def test_rolling_high_is_the_window_before_the_bar():
    t = [i * F for i in range(15)]
    h = [1, 5, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 3, 9]
    got = E.rolling_high_before(t, h, 12 * F)
    assert got[:12] == [None] * 12           # no full window behind yet
    assert got[12] == 5 and got[13] == 5     # bar 1 is still inside
    assert got[14] == 3                      # bar 1 has left; the bar's own high (9) never counts


def test_stop_rules():
    assert E.short_stop("p0.25", 100.0, None) == pytest.approx(100.25)      # 5% of the margin at 20x
    assert E.short_stop("swing", 100.0, 100.6) == pytest.approx(100.6 * 1.0005)
    assert E.short_stop("swing", 100.0, 99.0) == pytest.approx(100.1)       # never closer than MIN_STOP
    assert E.short_stop("swing", 100.0, None) is None
    assert E.long_stop("p0.25", 101.0, 100.5, 0.5) == pytest.approx(101.0 * 0.9975)
    assert E.long_stop("sentry", 101.0, 100.5, 0.5) == pytest.approx(100.5 * 0.9995)
    assert E.long_stop("atr2.0", 101.0, 100.5, 0.5) == pytest.approx(100.0)


def test_sizing_by_margin_share_and_by_risk():
    assert E.leg_notional("m05", "short", 200.0, 100.0, 100.25) == pytest.approx(200.0)
    assert E.leg_notional("m20", "flip", 200.0, 100.0, 99.75) == pytest.approx(800.0)
    assert [E.leg_notional("op", leg, 214.0, 100.0, 99.0) / 20.0 / 214.0 for leg in ("short", "flip", "third")
            ] == pytest.approx([0.20, 0.60, 0.05])
    assert E.leg_notional("r1", "short", 200.0, 100.0, 100.25) == pytest.approx(800.0)   # 1% risk on a 0.25% stop
    assert E.leg_notional("r1", "short", 200.0, 100.0, 100.01) == pytest.approx(4000.0)  # capped at 20x


# ── the short leg ──────────────────────────────────────────────────────────

def test_short_arms_on_a_close_over_the_entry_and_covers_at_the_thesis_entry():
    rows = [(100.2, 100.3, 100.1, 100.2), ARM, FILL, (100.6, 100.6, 99.9, 100.0), ARM, FILL, QUIET]
    out = run(rows, SHORT_ONLY)
    assert len(out["trades"]) == 1           # one cycle per thesis: the second arm is ignored
    tr = out["trades"][0]
    assert (tr["leg"], tr["side"], tr["reason"]) == ("short", "short", "target")
    assert tr["entry_ms"] == T0 + 2 * F and tr["entry_px"] == pytest.approx(100.5)
    assert tr["stop_px"] == pytest.approx(101.0025) and tr["exit_px"] == 100.0
    assert tr["margin_pct"] == pytest.approx(20.0)
    assert tr["net_usd"] == pytest.approx(0.5 * 0.2 * EQ * 20.0 / 100.5)
    assert len(out["curve"]) == len(rows) and out["curve"][-1] == pytest.approx(tr["net_usd"])


def test_a_new_thesis_re_arms_the_short():
    rows = [ARM, FILL, (100.6, 100.6, 99.9, 100.0), ARM, FILL, FILL]
    out = run(rows, SHORT_ONLY, tids=["A", "A", "A", "B", "B", "B"])
    assert [tr["reason"] for tr in out["trades"]] == ["target", "open"]


def test_no_short_under_the_arm_level_and_a_bare_touch_is_no_target():
    assert run([(100.2, 100.4, 100.1, 100.4)] * 3, SHORT_ONLY)["trades"] == []
    out = run([ARM, FILL, (100.6, 100.6, 100.0, 100.3)], SHORT_ONLY)     # low == target: no trade-through
    assert out["trades"][0]["reason"] == "open"


def test_the_desk_fill_is_one_bar_late_on_5m_and_pays_half_the_range():
    rows = [ARM, QUIET, (100.8, 101.0, 100.6, 100.8), QUIET]
    tr = run(rows, SHORT_ONLY, late=1)["trades"][0]
    assert tr["entry_ms"] == T0 + 2 * F
    assert tr["entry_px"] == pytest.approx(100.8 - 0.2)
    moved = run([ARM, QUIET, (100.4, 100.5, 100.3, 100.4), QUIET], SHORT_ONLY, late=1)
    assert moved["trades"] == [] and moved["refused"] == {"short_moved": 1}


def test_stop_and_target_on_one_bar_is_a_loss():
    out = run([ARM, FILL, (100.6, 101.2, 99.8, 100.0)], SHORT_ONLY)
    assert out["trades"][0]["reason"] == "stop" and out["trades"][0]["net_usd"] < 0


def test_fees_and_stop_slippage_are_charged():
    p = dataclasses.replace(P, taker_fee=0.0001, maker_fee=0.00003, slippage=0.0003)
    tr = run([ARM, FILL, STOPPED], SHORT_ONLY, p=p)["trades"][0]
    assert tr["exit_px"] == pytest.approx(tr["stop_px"] * 1.0003)
    assert tr["fees_usd"] == pytest.approx(0.0001 * (tr["notional"] + tr["exit_px"] * tr["size"]))
    win = run([ARM, FILL, (100.6, 100.6, 99.9, 100.0)], SHORT_ONLY, p=p)["trades"][0]
    assert win["fees_usd"] == pytest.approx(0.0001 * win["notional"] + 0.00003 * 100.0 * win["size"])


# ── the flip ───────────────────────────────────────────────────────────────

def test_resting_flip_goes_long_at_the_stop_and_exits_at_the_local_high():
    out = run([ARM, FILL, STOPPED, (101.1, 103.2, 101.0, 103.0), QUIET], RESTING)
    short, flip = out["trades"]
    assert (short["leg"], short["reason"], flip["leg"], flip["side"], flip["reason"]) == (
        "short", "stop", "flip", "long", "target")
    assert flip["entry_ms"] == short["exit_ms"] == T0 + 2 * F
    assert flip["entry_px"] == pytest.approx(short["exit_px"]) == pytest.approx(101.0025)
    assert flip["stop_px"] == pytest.approx(101.0025 * 0.9975) and flip["target_px"] == 103.0
    assert flip["cycle"] == short["cycle"]
    assert short["net_usd"] == pytest.approx(-0.5025 * short["size"])
    assert flip["net_usd"] == pytest.approx((103.0 - 101.0025) * flip["size"])
    assert flip["notional"] == pytest.approx(0.2 * (EQ + short["net_usd"]) * 20.0)       # sized on the equity left


def test_resting_flip_is_stopped_on_its_own_bar_only_by_the_close():
    low_first = run([ARM, FILL, (100.6, 101.2, 100.4, 101.1), QUIET], RESTING)["trades"]
    assert [tr["reason"] for tr in low_first] == ["stop", "open"]          # the low came before the fill
    harsh = E.walk(series([ARM, FILL, (100.6, 101.2, 100.4, 101.1), QUIET]), RESTING, P, EQ, midbar_worst=True)
    assert [tr["reason"] for tr in harsh["trades"]] == ["stop", "stop"]    # the harsh reading counts the low
    closed_under = run([ARM, FILL, (100.6, 101.2, 100.6, 100.7), QUIET], RESTING)["trades"]
    assert [tr["reason"] for tr in closed_under] == ["stop", "stop"]
    assert closed_under[1]["exit_ms"] == closed_under[1]["entry_ms"]


def test_tick_flip_fills_at_the_market_one_bar_late():
    rows = [ARM, QUIET, FILL, STOPPED, QUIET, (101.2, 101.4, 101.2, 101.3), (101.3, 103.2, 101.2, 103.0)]
    short, flip = run(rows, TICK, late=1)["trades"]
    assert short["exit_ms"] == T0 + 3 * F and flip["entry_ms"] == T0 + 5 * F
    assert flip["entry_px"] == pytest.approx(101.2 + 0.1)                  # the open plus half the range
    assert flip["reason"] == "target"


def test_no_flip_without_room_to_the_target():
    out = run([ARM, FILL, STOPPED, QUIET], RESTING, loc=101.05)
    assert [tr["leg"] for tr in out["trades"]] == ["short"]
    assert out["refused"] == {"flip_no_room": 1}


def test_long_targets_by_rule():
    s = series([ARM, FILL])
    assert E.long_target("loc4", s, 1) == 103.0
    assert E.long_target("hi24", s, 1) == pytest.approx(104.0 * 0.995)
    assert E.long_target("thesis", s, 1) == 103.0


def test_short_alone_never_flips():
    assert [tr["leg"] for tr in run([ARM, FILL, STOPPED, QUIET], SHORT_ONLY)["trades"]] == ["short"]


# ── the third leg ──────────────────────────────────────────────────────────

def test_third_leg_shorts_the_longs_target_back_to_the_longs_entry():
    v = dataclasses.replace(RESTING, third="flip", size="op")
    rows = [ARM, FILL, STOPPED, (101.1, 103.2, 101.0, 103.0), (103.0, 103.1, 102.8, 102.9)]
    short, flip, third = run(rows, v)["trades"]
    assert (third["leg"], third["side"], third["reason"]) == ("third", "short", "open")
    assert third["entry_px"] == 103.0 and third["target_px"] == pytest.approx(flip["entry_px"])
    assert third["stop_px"] == pytest.approx(103.0 * 1.005)
    assert [round(tr["margin_pct"]) for tr in (short, flip, third)] == [20, 60, 5]
    done = run(rows + [(102.9, 103.0, 100.9, 101.0), ARM, FILL], v)["trades"]
    assert [tr["reason"] for tr in done] == ["stop", "target", "target"]   # and no fourth leg: the thesis is used


def test_third_leg_to_the_thesis_entry_and_off():
    rows = [ARM, FILL, STOPPED, (101.1, 103.2, 101.0, 103.0), QUIET]
    third = run(rows, dataclasses.replace(RESTING, third="thesis"))["trades"][2]
    assert third["target_px"] == 100.0
    assert len(run(rows, RESTING)["trades"]) == 2


# ── beside the live book, the weekend, the pinned replay ───────────────────

def test_beside_live_nothing_opens_while_the_long_is_open_and_the_entry_closes_the_sleeve():
    rows = [ARM, FILL, FILL, (100.6, 100.8, 100.6, 100.7)]
    n = len(rows)
    busy = Live([EQ] * n, [True] * n, [False] * n)
    assert run(rows, SHORT_ONLY, live=busy)["trades"] == []
    enters = Live([EQ] * n, [False, False, False, True], [False, False, False, True])
    tr = run(rows, SHORT_ONLY, live=enters)["trades"][0]
    assert tr["reason"] == "live_long" and tr["exit_ms"] == T0 + 3 * F
    assert tr["exit_px"] == pytest.approx(100.6 + 0.1)                     # a market close: half the range


def test_weekend_flat():
    rows = [ARM, FILL, FILL, FILL]
    assert run(rows, SHORT_ONLY, weekend=[False, True, True, True])["trades"] == []
    tr = run(rows, SHORT_ONLY, weekend=[False, False, False, True])["trades"][0]
    assert tr["reason"] == "weekend"


def test_forced_short_runs_the_legs_and_skips_the_arming():
    rows = [QUIET, (100.6, 100.7, 100.5, 100.6), STOPPED, (101.1, 103.2, 101.0, 103.0), ARM, FILL]
    out = run(rows, RESTING, forced={T0 + F: (100.55, 101.0, 100.0)})
    assert [(tr["leg"], tr["reason"]) for tr in out["trades"]] == [("short", "stop"), ("flip", "target")]
    assert out["trades"][0]["entry_px"] == 100.55 and out["trades"][0]["exit_px"] == 101.0


def test_match_tonight():
    good = [{"leg": "short", "reason": "stop", "exit_px": 92.93, "entry_px": 92.56},
            {"leg": "flip", "reason": "target", "exit_px": 93.70, "entry_px": 92.93},
            {"leg": "third", "reason": "open", "exit_px": 93.85, "entry_px": 93.70}]
    assert E.match_tonight(good) == {"short": True, "flip": True, "third": True, "all": True}
    bad = [good[0], {**good[1], "reason": "stop"}]
    assert E.match_tonight(bad) == {"short": True, "flip": False, "third": False, "all": False}
    assert E.match_tonight([])["all"] is False


# ── series, datasets, the bar ──────────────────────────────────────────────

def _ticks(rows):
    return [SL.Tick(t=T0 + i * F, o=r[0], h=r[1], l=r[2], c=r[3], ok=True, hi24=104.0, atr=0.5, side="long",
                    ceiling=100.0, thesis_id="A", target=103.0) for i, r in enumerate(rows)]


def test_build_series_and_live_on():
    s = E.build_series(_ticks([ARM, FILL, QUIET]), F, 1)
    assert s.entry == [100.0] * 3 and s.target == [103.0] * 3 and s.late == 1 and set(s.loc) == {4, 8, 12}
    flat = dataclasses.replace(_ticks([ARM])[0], side="flat")
    assert E.build_series([flat], F, 1).entry == [None]
    prep = {"ticks": _ticks([ARM, FILL, QUIET]), "long_curve": [1000.0, 1010.0, 1020.0],
            "intervals": [(T0 + F, None)], "end_ms": T0 + 3 * F}
    live = E.live_on([T0, T0 + F, T0 + 2 * F], prep, 1000.0)
    assert live.eq == [1000.0, 1010.0, 1020.0]
    assert live.long_open == [False, True, True] and live.long_entry == [False, True, False]


def _dataset(rows):
    s = series(rows)
    n = len(rows)
    return {"series": s, "live": Live([EQ] * n, [False] * n, [False] * n), "lo": 0, "hi": n - 1, "params": P,
            "intervals": [], "start_ms": T0, "end_ms": T0 + n * F, "mid_ms": T0 + 3 * F,
            "live_sum": {"trades": 0, "wins": 0, "net_usd": 0.0, "max_dd_pct": 0.0}}


def test_rows_legs_halves_and_under_ten_trades_is_never_a_pass():
    rows = [ARM, FILL, STOPPED, (101.1, 103.2, 101.0, 103.0), QUIET]
    vs = [RESTING] + E.neighbours(RESTING)
    got = E.evaluate(vs, {E.PRIMARY: _dataset(rows)})
    r = got[RESTING.name]
    d = r["datasets"][E.PRIMARY]
    assert d["alone"]["n"] == 2 and d["legs_alone"]["short"]["n"] == 1 and d["legs_alone"]["flip"]["wins"] == 1
    assert d["alone"]["net_usd"] > 0 and d["alone"]["profit_factor"] > 1 and d["cycles_alone"] == 1
    assert r["halves"][0] == pytest.approx(d["combined"]["net_usd"]) and r["halves"][1] == 0.0
    assert r["judge"]["verdict"] in ("LEANING", "NOISE") and r["judge"]["n_primary"] == 2
    fams = E.summarize_families(got, [E.PRIMARY])
    assert fams["SR_local"]["best"] in got and fams["SR_local"]["best_desk_fill"] is None
    assert fams["SR_local"]["verdict"] == "NOISE"            # no desk-fill row here: nothing to judge
    assert fams["SR_local"]["verdict_resting"] in ("LEANING", "NOISE")


def test_desk_fill_is_the_short_alone_or_a_tick_flip():
    def row(v):
        return {"variant": dataclasses.asdict(v)}
    assert E.desk_fill(row(SHORT_ONLY)) and E.desk_fill(row(TICK)) and not E.desk_fill(row(RESTING))


def test_audit_reads_the_twin_and_drops_the_night():
    rows = [ARM, FILL, STOPPED, (101.1, 103.2, 101.0, 103.0), QUIET, QUIET]
    ds = {E.PRIMARY: _dataset(rows)}
    vs = [RESTING, TICK]
    got = E.evaluate(vs, ds)
    a = E.audit([RESTING.name], got, {v.name: v for v in vs}, ds, (T0 + 2 * F, T0 + 3 * F))[RESTING.name]
    x = a["datasets"][E.PRIMARY]
    assert a["twin"] == TICK.name and not a["desk_fill"]
    assert x["twin_combined_usd"] == got[TICK.name]["datasets"][E.PRIMARY]["combined"]["net_usd"]
    short_net = got[RESTING.name]["datasets"][E.PRIMARY]["legs_combined"]["short"]["net_usd"]
    assert x["combined_without_night_usd"] == pytest.approx(short_net)       # the flip entered inside the night
    assert x["combined_midbar_worst_usd"] < 0 < x["combined_usd"]           # the stop bar's low sits under the long's stop
    assert E.without_night([{"entry_ms": 5, "net_usd": 2.0}, {"entry_ms": 50, "net_usd": 7.0}], 0, 10) == 7.0


def test_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as e:
        E.main(["--help"])
    assert e.value.code == 0
    assert "Research only" in " ".join(capsys.readouterr().out.split())
