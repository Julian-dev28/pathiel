"""Gate tests for the loss fix eval's pure parts (services/strategies/evals/loss_fix_test.py):
the grids and their plateau families, the seeded bootstrap, the verdict
(grid vs single switch), the real-trade lenses (resize, entry filters at
the fill minute, the lock replay with the trade's own fee rate) and the
2026-11-02 Brent long through `nov2_long`.

No network: the 11-02 bars and theses come from test_loss_fix.py (real
candles, typed in); the real trades are the audit's rows for that long and
the 10-23 weekend round trip, cut to the fields the lenses read.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import pytest

from pathiel.models.types import Candle
from services.strategies.backtest.data import ThesisDirection
from services.strategies.backtest.desk_tick_sim import Run, Trade, live_params, live_params_before_no_break
from services.strategies.evals import loss_fix_test as F
from services.strategies.tests.test_loss_fix import NOV2_1H, nov2_bars, nov2_direction

HOUR_MS = 3_600_000
FIVE = 300_000


def ms(*parts: int) -> int:
    return int(datetime(*parts, tzinfo=timezone.utc).timestamp() * 1000)


# ── grids ──────────────────────────────────────────────────────────────────

def test_grids_and_families():
    s = F.sizing_variants()
    assert s["live"] == {} and s["lev_5"] == {"leverage": 5.0} and s["risk_10"] == {"risk_cap_frac": 0.10}
    w = F.filter_variants()
    assert w["delay_60"] == {"entry_delay_min": 60.0} and w["age_8h"] == {"side_age_h": 8.0}
    assert w["no_break"] == {"no_break_entry": True} and w["edge_limit"] == {"entry_mode": "edge"}
    r = F.lock_variants()
    assert r["wk_befees_t1"] == {"lock_weekend_only": True, "lock_trigger_r": 1.0, "lock_r": 0.0,
                                 "lock_plus_fees": True}
    assert r["lock_t2_l0.5"] == {"lock_trigger_r": 2.0, "lock_r": 0.5}
    for named, fams in ((s, F.SIZING_FAMILIES), (w, F.FILTER_FAMILIES), (r, F.LOCK_FAMILIES),
                        ({"live": {}, **F.STACKS}, F.STACK_FAMILIES)):
        for grid in fams.values():
            assert all(v in named for v in grid), grid
    for name, ov in F.STACKS.items():          # every stack is built from tested parts
        dataclasses.replace(live_params(), **ov)


# ── bootstrap ──────────────────────────────────────────────────────────────

def test_bootstrap_is_seeded_and_needs_three_trades():
    rs = [0.3, -0.15, 0.2, -0.15, 0.1]
    assert F.bootstrap(rs) == F.bootstrap(rs)
    assert F.bootstrap(rs[:2]) == {"p_ruin": None, "p_loss": None, "median_multiple": None}
    assert F.bootstrap([0.1, 0.2, 0.05])["p_ruin"] == 0.0
    out = F.bootstrap([-0.6, -0.6, -0.6])
    assert out["p_ruin"] == 1.0 and out["p_loss"] == 1.0
    assert out["median_multiple"] == pytest.approx(0.4 ** 3)


def test_bootstrap_bigger_bets_ruin_more():
    small = [r * 0.5 for r in (0.4, -0.16, 0.3, -0.16, -0.16, 0.5, -0.16)]
    big = [0.4, -0.16, 0.3, -0.16, -0.16, 0.5, -0.16]
    assert F.bootstrap(small)["p_ruin"] < F.bootstrap(big)["p_ruin"]


def test_trade_returns_use_equity_before_each_trade():
    run = Run(params=live_params(), equity_curve=[(0, 64.0), (10, 80.0), (20, 72.0)])
    run.trades = [Trade(side="long", entry_time=5, entry_px=1, size=1, stop_px=0.9, target_px=1.2, net_pnl_usd=16.0),
                  Trade(side="long", entry_time=15, entry_px=1, size=1, stop_px=0.9, target_px=1.2, net_pnl_usd=-8.0)]
    assert F.trade_returns(run) == [pytest.approx(0.25), pytest.approx(-0.1)]


# ── verdict ────────────────────────────────────────────────────────────────

def test_verdict_grid_vs_switch():
    scores = {"a": {"beats": 5, "loses": 3, "of": 8, "geo_mean_ratio": 1.1},
              "b": {"beats": 6, "loses": 2, "of": 8, "geo_mean_ratio": 1.2},
              "c": {"beats": 2, "loses": 6, "of": 8, "geo_mean_ratio": 0.8},
              "sw": {"beats": 6, "loses": 2, "of": 8, "geo_mean_ratio": 1.1}}
    fams = {"g": ["a", "b", "c"]}
    liq = {k: 0.0 for k in scores}
    assert F.verdict(scores, liq, "a", fams)["adopt"] is True           # grid edge, its one neighbour passes
    b = F.verdict(scores, liq, "b", fams)
    assert b["adopt"] is False and b["fails"] == ["plateau"]            # neighbour c fails: a spike
    sw = F.verdict(scores, liq, "sw", fams)
    assert sw["adopt"] is True and sw["plateau"] == "n/a"


def test_growth_beats():
    rows = {"d1": {"live": {"median_multiple": 2.0}, "x": {"median_multiple": 2.1}},
            "d2": {"live": {"median_multiple": 2.0}, "x": {"median_multiple": 1.0}},
            "d3": {"live": {"median_multiple": None}, "x": {"median_multiple": 1.0}}}
    assert F.growth_beats(rows, "x") == {"beats": 1, "of": 2}


# ── the real trades ────────────────────────────────────────────────────────

NOV2_TRADE = {"coin": "xyz:BRENTOIL", "desk": "oil", "side": "long", "entry_time": 1793591428254,
              "exit_time": 1793608515729, "entry_px": 110.01, "avg_entry_px": 110.0099, "exit_px": 109.23,
              "max_size": 42.36, "notional_usd": 4660.02, "stop_px": 109.25, "target_px": 111.83,
              "net_usd": -33.87, "fees": 0.8351, "equity_at_size": 261.01}


def test_resize_the_nov2_long():
    """Desk equity 0.9 x 261.01 = 234.91. 20x of it is $4,698, above the
    actual $4,660, so live is the actual; 10x halves it; a 10% cap sizes
    the 0.69% stop (plus 0.12% costs) to about $2,900."""
    assert F.desk_share(NOV2_TRADE) == 0.9
    assert F.resize(NOV2_TRADE, {})["net_usd"] == pytest.approx(-33.87)
    lev10 = F.resize(NOV2_TRADE, {"leverage": 10.0})
    assert lev10["net_usd"] == pytest.approx(-33.87 * 10 * 234.909 / 4660.02, abs=0.02)
    r10 = F.resize(NOV2_TRADE, {"risk_cap_frac": 0.10})
    per = 0.76 / 110.01 + 2 * F.TAKER + F.SLIP
    assert r10["scale"] == pytest.approx(0.1 * 234.909 / per / 4660.02, abs=0.002)
    assert r10["pct_desk"] == pytest.approx(-9.0, abs=0.2)          # actual fees beat the cap's cost guess
    assert F.resize(NOV2_TRADE, {"risk_cap_frac": 0.20})["scale"] == 1.0


def _thesis(parts, side, entry=None):
    return ThesisDirection(created_at_ms=ms(*parts), side=side, entry_px=entry, stop_px=None,
                           invalidation_px=None, targets=[], thesis_id="t")


OIL = [_thesis((2026, 10, 28, 16, 27), "flat"), _thesis((2026, 10, 28, 20, 8), "long", 110.18),
       _thesis((2026, 11, 1, 18, 19), "long", 111.7), _thesis((2026, 11, 2, 3, 48), "long", 110.0),
       _thesis((2026, 11, 2, 8, 40), "short", 110.2)]


def test_side_since_skips_flat_and_same_side_rewrites():
    assert F.side_since(ms(2026, 11, 2, 3, 50), OIL) == ms(2026, 10, 28, 20, 8)
    assert F.side_since(ms(2026, 11, 2, 9, 0), OIL) == ms(2026, 11, 2, 8, 40)
    assert F.side_since(ms(2026, 10, 28, 17, 0), OIL) is None


def test_filter_hits_on_the_nov2_long():
    """At 03:50:28Z: the thesis was 2 minutes old (delay refuses), the side
    had held since 10-28 (side age passes), the 02:00Z hour closed red
    (confirm refuses), 110.01 sat under the 110.14 24h low (no_break refuses)."""
    _, c1h = nov2_bars()
    hits = F.filter_hits(NOV2_TRADE, c1h, OIL, F.filter_variants())
    assert hits["delay_15"] and hits["delay_120"]
    assert not any(hits[f"age_{h}h"] for h in F.SIDE_AGES)
    assert hits["confirm_1h"] and hits["no_break"]
    assert "thesis_levels" not in hits and "edge_limit" not in hits and hits["live"] is False


def test_lock_real_round_trip_and_fee_rate():
    """A long from 100 (stop 99, target 104) that reaches +1.6R and falls to
    its stop: a 1R lock at 0.5R exits at 100.5, at the trade's own fee rate."""
    t0 = ms(2026, 10, 23, 16, 30)
    rows = [(100, 100.2, 99.9, 100.1), (100.1, 101.6, 100, 101.5), (101.5, 101.5, 100.3, 100.4),
            (100.4, 100.4, 98.9, 99)]
    bars = [Candle(t=t0 + i * FIVE, o=o, h=h, l=lo, c=c, v=1.0) for i, (o, h, lo, c) in enumerate(rows)]
    tr = {"coin": "xyz:BRENTOIL", "side": "long", "entry_time": t0 + 1000, "exit_time": t0 + 4 * FIVE,
          "entry_px": 100.0, "avg_entry_px": 100.0, "exit_px": 99.0, "max_size": 10.0, "notional_usd": 1000.0,
          "stop_px": 99.0, "target_px": 104.0, "net_usd": -10.2, "fees": 0.199}
    rate = F.fee_rate(tr)
    assert rate == pytest.approx(0.199 / 1990.0)
    out = F.lock_real(tr, bars, {"lock_trigger_r": 1.0, "lock_r": 0.5}, live_params())
    assert out["reason"] == "lock" and out["exit_px"] == pytest.approx(100.5)
    assert out["net_usd"] == pytest.approx(5.0 - rate * (1000.0 + 1005.0), abs=0.01)
    assert F.lock_real(tr, bars, {}, live_params()) == {"reason": "actual", "net_usd": -10.2}
    wk = F.lock_real(tr, bars, {"lock_weekend_only": True, "lock_trigger_r": 1.0, "lock_r": 0.5}, live_params())
    assert wk["reason"] == "actual"                                  # a Friday afternoon: not the weekend


def test_nov2_long_runs_every_variant():
    c15, c1h = nov2_bars()
    # The rule live on 11-02 (breaks allowed; no_break_entry since 11-04).
    base = dataclasses.replace(live_params_before_no_break(), starting_equity=64.0, size_slippage=True)
    named = {"live": {}, "risk_10": {"risk_cap_frac": 0.10}, "no_break": {"no_break_entry": True}}
    out = F.nov2_long(c15, c1h, nov2_direction, base, named, ms(2026, 11, 2, 3, 45), ms(2026, 11, 2, 9, 0))
    assert out["live"]["reason"] == "stop" and out["live"]["pct_equity"] == pytest.approx(-16.5, abs=0.3)
    assert out["risk_10"]["pct_equity"] == pytest.approx(-10.0, abs=0.6)
    assert out["no_break"]["entry"] == "2026-11-02 04:00Z" and out["no_break"]["reason"] == "direction_flip"
    assert len(NOV2_1H) == 33


def test_stack_real_on_the_nov2_long():
    """Size scales the actual loss; a filter that refuses at the fill minute
    counts the trade as 0 (an upper bound); a weekend lock cannot act on a
    Monday trade."""
    _, c1h = nov2_bars()
    base = live_params()
    r15 = F.stack_real(NOV2_TRADE, {"risk_cap_frac": 0.15}, [], c1h, OIL, base)
    assert r15 == pytest.approx(-33.87 * F.resize(NOV2_TRADE, {"risk_cap_frac": 0.15})["scale"], abs=0.01)
    wk = {"risk_cap_frac": 0.15, "lock_weekend_only": True, "lock_trigger_r": 1.0, "lock_r": 0.0,
          "lock_plus_fees": True}
    assert F.stack_real(NOV2_TRADE, wk, [], c1h, OIL, base) == r15
    assert F.stack_real(NOV2_TRADE, {"risk_cap_frac": 0.10, "no_break_entry": True}, [], c1h, OIL, base) == 0.0
    assert F.filter_hits(NOV2_TRADE, c1h, OIL, {"two": {"side_age_h": 1.0, "confirm_1h": True}})["two"] is True
