"""Gate tests for services/strategies/evals/pullback_correlation_test.py
(docs/strategy/PULLBACK_AND_CORRELATION_TEST_2026-11-02.md).

No network. The pure parts (variants, families, the correlation numbers,
which pair is the opposite bet, driver tags, the guard on the real fills)
and the 2026-11-02 regression for both fixes on the real bars
(fixtures/nov2_brent_silver_bars.json: public Brent and silver candles to
14:00Z and the journal's calls).
"""
from __future__ import annotations

import dataclasses
import json
import os

import pytest

from pathiel.models.types import Candle
from services.strategies.backtest import data as D
from services.strategies.backtest.desk_tick_sim import Params, simulate
from services.strategies.backtest.portfolio import Desk, simulate_portfolio
from services.strategies.backtest.regime import HOUR_MS
from services.strategies.evals import pullback_correlation_test as P
from services.strategies.evals.desk_tick_backtest import journaled_direction

FX_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "nov2_brent_silver_bars.json")


# ── variants and families ──────────────────────────────────────────────────

def test_a_variants_are_valid_params_and_families_point_at_variants():
    named = P.a_variants()
    assert named[P.BASELINE] == {}
    for v, ov in named.items():
        dataclasses.replace(Params(), **ov)          # every override is a real field
    fam = P.a_families()
    for v, nb in fam.items():
        assert v in named and all(n in named for n in nb) and v not in nb
    assert fam["a1_8h_far"] == ["a1_6h_far", "a1_12h_far"]
    assert set(fam["a3_k1_c4_thesis"]) == {"a3_k0.5_c4_thesis", "a3_k1.5_c4_thesis", "a3_k1_c2_thesis",
                                           "a3_k1_c8_thesis"}
    assert fam["a4_2h_fit"] == ["a4_1h_fit", "a4_4h_fit"]
    assert "a1_8h_far_nocap" not in fam                     # a contrast, never a candidate


def test_b_variants_and_families():
    named = P.b_variants()
    assert named[P.BASELINE] is None
    g = named["b_same_silver_m0"]
    assert g.pair == "same" and g.mult == 0.0 and g.desks == ("silver",)
    assert named["b_opposite_both_m0.5"].desks == ()
    assert P.b_families()["b_same_silver_m0.25"] == ["b_same_silver_m0", "b_same_silver_m0.5"]


# ── correlation (pure) ─────────────────────────────────────────────────────

def _hours(closes, t0=0):
    return [Candle(t=t0 + i * HOUR_MS, o=c, h=c, l=c, c=c, v=1.0) for i, c in enumerate(closes)]


def test_hourly_returns_need_the_previous_hour():
    bars = _hours([100.0, 101.0, 99.99])
    r = P.hourly_returns(bars)
    assert r == {HOUR_MS: pytest.approx(0.01), 2 * HOUR_MS: pytest.approx(99.99 / 101.0 - 1.0)}
    gap = bars[:2] + [Candle(t=5 * HOUR_MS, o=1, h=1, l=1, c=1, v=1)]
    assert 5 * HOUR_MS not in P.hourly_returns(gap)


def test_corr_stats_mirror_and_big_moves():
    a = {i: (0.02 if i % 2 else -0.015) * (1 + i / 100) for i in range(40)}
    b = {i: -0.5 * v for i, v in a.items()}
    s = P.corr_stats(a, b)
    assert s["corr"] == pytest.approx(-1.0) and s["corr_big"] == pytest.approx(-1.0)
    assert s["n"] == 40 and s["n_big"] == 40 and s["opposite_share_big"] == 1.0
    assert s["silver_mean_on_brent_down_pct"] > 0 > s["silver_mean_on_brent_up_pct"]
    small = P.corr_stats({1: 0.001, 2: -0.002, 3: 0.003}, {1: 0.0, 2: 0.1, 3: 0.2})
    assert small["n_big"] == 0 and small["corr_big"] is None and small["opposite_share_big"] is None


def test_opposite_pair_follows_the_sign():
    assert P.opposite_pair(-0.67) == "same"        # oil short + silver short offset each other
    assert P.opposite_pair(0.4) == "opposite"
    assert P.opposite_pair(None) is None and P.opposite_pair(0.0) is None


def test_driver_tags_read_the_argument_not_the_calendar():
    raw = {"call": {"one_liner": "Short Brent on Iran talks"}, "bull": [{"text": "Hormuz shut"}],
           "bear": [{"text": "no cuts priced, real yield 2.6%"}], "catalysts": [{"title": "FOMC"}]}
    assert P.driver_tags(raw) == frozenset({"iran", "rates"})
    assert P.driver_tags({"call": {"one_liner": "Short silver into the bounce"}, "catalysts": [{"title": "Fed"}]}) \
        == frozenset()
    at = P.tags_at([(10, frozenset({"iran"})), (20, frozenset())])
    assert at(5) == frozenset() and at(15) == frozenset({"iran"}) and at(25) == frozenset()


# ── 11-02, the guard on the real fills ─────────────────────────────────────

def test_real_fills_guard_rows():
    rows = P.real_guard_rows()
    assert rows[P.BASELINE] == {"net_usd": -41.1, "acted": []}
    # oil never held a short on 11-02: the same-side guard (the brief's B1) acts on nothing
    for v in ("b_same_silver_m0", "b_same_both_m0", "b_same_both_m0.5"):
        assert rows[v]["net_usd"] == -41.1 and not rows[v]["acted"]
    # the 05:34Z silver short opened while oil was long since 03:50Z
    assert rows["b_opposite_silver_m0"]["net_usd"] == -38.25
    assert rows["b_opposite_silver_m0.5"]["net_usd"] == pytest.approx(-39.675, abs=0.01)
    # both guarded: the 03:50Z oil long met the silver short held since 01:18Z; blocked,
    # oil holds nothing at 05:34Z, so that silver short goes through
    assert rows["b_opposite_both_m0"]["net_usd"] == -7.23
    assert rows["b_opposite_both_m0"]["acted"] == ["oil long 2026-11-02 03:50Z x0 (other: short)"]


# ── 11-02 on the real bars ─────────────────────────────────────────────────

@pytest.fixture(scope="module")
def nov2():
    with open(FX_PATH) as fh:
        fx = json.load(fh)

    def candles(rows):
        return [Candle(t=r[0], o=r[1], h=r[2], l=r[3], c=r[4], v=1.0) for r in rows]

    def direction(key):
        return journaled_direction([D.ThesisDirection(**t) for t in fx["theses"][key]])

    b1h, s1h = candles(fx["bars"]["brent"]["1h"]), candles(fx["bars"]["silver"]["1h"])
    return {"b1h": b1h, "b15": candles(fx["bars"]["brent"]["15m"]), "s1h": s1h,
            "s15": candles(fx["bars"]["silver"]["15m"]), "oil": direction("brent"), "silver": direction("silver"),
            "bp": P.brent_params(), "sp": P.silver_params(s1h)}


def _a_row(nov2, coin, variant):
    named = P.a_variants()
    p = dataclasses.replace(nov2["bp" if coin == "brent" else "sp"], **named[variant])
    t0 = P._ms(P.NOV2_A_FROM)
    c15 = [c for c in nov2["b15" if coin == "brent" else "s15"] if c.t >= t0]
    run = simulate(c15, nov2["b1h" if coin == "brent" else "s1h"], nov2["oil" if coin == "brent" else "silver"], p)
    return P.nov2_row(run, p)


def test_nov2_brent_live_never_shorts_the_fall(nov2):
    row = _a_row(nov2, "brent", P.BASELINE)
    assert row["entry"] is None and row["refused"] == {"chase": 21}     # every tick 08:45-13:45Z a chase


def test_nov2_brent_pullback_rows(nov2):
    a2 = _a_row(nov2, "brent", "a2_12h_thesis")
    assert (a2["entry"], a2["side"], a2["kind"]) == ("2026-11-02 13:00Z", "short", "a2")
    assert a2["entry_px"] == pytest.approx(108.657, abs=0.002) and a2["stop_px"] == pytest.approx(109.82)
    assert a2["pnl_at_end_usd"] == pytest.approx(53.8, abs=0.05) and a2["at_stop_usd"] == pytest.approx(-33.77, abs=0.05)
    a1 = _a_row(nov2, "brent", "a1_4h_thesis")
    assert (a1["entry"], a1["kind"]) == ("2026-11-02 13:30Z", "a1") and a1["pnl_at_end_usd"] == pytest.approx(56.37, abs=0.05)
    # the 8h high is too far for 20x, then wider than the 09:25Z thesis stop 110.90
    a1_8 = _a_row(nov2, "brent", "a1_8h_thesis")
    assert a1_8["entry"] is None and a1_8["refused"]["pb_wide"] == 18 and a1_8["refused"]["pb_leverage"] == 3
    # at the leverage the stop allows, the 08:00Z retest of the broken low sells at 09:00Z
    fit = _a_row(nov2, "brent", "a2_12h_thesis_fit")
    assert (fit["entry"], fit["kind"]) == ("2026-11-02 09:00Z", "a2") and fit["pnl_at_end_usd"] == pytest.approx(45.13, abs=0.05)


def test_nov2_brent_thesis_levels_fail_20x(nov2):
    """a4: the thesis's own stop (110.90 over 109.30, written for 5x) is past
    half the way to liquidation at 20x; at the leverage it allows the limit
    rests at 109.30 and lapses unfilled, like the hand-placed one."""
    a4 = _a_row(nov2, "brent", "a4_2h")
    assert a4["entry"] is None and a4["refused"]["pb_ai_leverage"] == 12
    fit = _a_row(nov2, "brent", "a4_2h_fit")
    assert fit["entry"] is None and fit["rested"] == 2 and fit["lapsed"] == 1


def test_nov2_silver_rows(nov2):
    assert _a_row(nov2, "silver", P.BASELINE)["entry"] is None
    a3 = _a_row(nov2, "silver", "a3_k1_c8_far")
    assert (a3["entry"], a3["exit"]) == ("2026-11-02 13:30Z", "stop") and a3["net_usd"] == pytest.approx(-32.86, abs=0.05)
    a1 = _a_row(nov2, "silver", "a1_8h_thesis")
    assert (a1["entry"], a1["exit"]) == ("2026-11-02 13:45Z", "open") and a1["pnl_at_end_usd"] == pytest.approx(27.35, abs=0.05)


def _portfolio(nov2, variant):
    desks = [Desk("oil", 0.9, nov2["b15"], nov2["b1h"], nov2["oil"], nov2["bp"]),
             Desk("silver", 0.1, nov2["s15"], nov2["s1h"], nov2["silver"], nov2["sp"])]
    return simulate_portfolio(desks, P.START, P.b_variants()[variant])


def test_nov2_portfolio_guard(nov2):
    live = _portfolio(nov2, P.BASELINE)
    sides = [(n, t.side, P._iso(t.entry_time)) for n, r in live.runs.items() for t in r.trades]
    assert sides == [("oil", "long", "2026-11-02 02:45Z"), ("oil", "long", "2026-11-02 03:15Z"),
                     ("silver", "short", "2026-11-02 05:30Z")]
    assert live.final_equity == pytest.approx(181.27, abs=0.01)
    same = _portfolio(nov2, "b_same_silver_m0")
    assert repr(same.runs) == repr(live.runs) and not same.guard_log
    opp = _portfolio(nov2, "b_opposite_silver_m0")
    assert not opp.runs["silver"].trades and opp.runs["silver"].refused["guard"] >= 1
    assert opp.guard_log[0]["held"] == {"oil": "long"} and P._iso(opp.guard_log[0]["time"]) == "2026-11-02 05:30Z"
    assert opp.final_equity == pytest.approx(184.48, abs=0.01)
