"""Gate tests for the winner-traits eval (services/strategies/evals/
winner_traits_test.py, 2026-11-13) and the zone gate it added to the
simulator (`desk_tick_sim.Params.entry_zone_frac`, off by default).

Synthetic candles only (test_stop_cap's flat warmup: a 24h range of 100 to
104), no network. The eval's own verdict logic is checked on hand-built
result dicts.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.strategies.backtest.desk_tick_sim import Direction, Params, live_params, zone_blocks, zone_position
from services.strategies.evals import strategy_tournament as T
from services.strategies.evals import winner_traits_test as E
from services.strategies.tests.test_stop_cap import ENTER_LONG, QUIET, _run

HOUR_MS = 3_600_000


# ── the zone gate: pure ────────────────────────────────────────────────────

def test_zone_position_is_the_ledgers_range_pos():
    assert zone_position(101.0, 100.0, 104.0, "long") == pytest.approx(0.25)
    assert zone_position(101.0, 100.0, 104.0, "short") == pytest.approx(0.75)
    assert zone_position(99.0, 100.0, 104.0, "long") == pytest.approx(-0.25)     # through the low
    assert zone_position(101.0, 104.0, 104.0, "long") is None                    # degenerate range


def test_zone_blocks_beyond_the_fraction_only():
    assert zone_blocks(101.0, 100.0, 104.0, "long", 0.33) is False      # 0.25 of the range up
    assert zone_blocks(102.0, 100.0, 104.0, "long", 0.33) is True       # mid-range
    assert zone_blocks(103.0, 100.0, 104.0, "short", 0.33) is False     # 0.25 down from the high
    assert zone_blocks(102.0, 100.0, 104.0, "short", 0.33) is True
    assert zone_blocks(99.0, 100.0, 104.0, "long", 0.33) is False       # past the edge: no_break's business
    assert zone_blocks(101.32, 100.0, 104.0, "long", 0.33) is False     # at the line, not over it
    assert zone_blocks(102.0, 104.0, 104.0, "long", 0.33) is False      # degenerate range blocks nothing


# ── the zone gate: in the sim ──────────────────────────────────────────────

def test_zone_is_off_by_default_and_the_live_rule_does_not_set_it():
    assert Params().entry_zone_frac is None and live_params().entry_zone_frac is None


def test_zone_off_is_byte_identical():
    rows = [ENTER_LONG, QUIET, QUIET]
    a, b = _run(rows), _run(rows, entry_zone_frac=None)
    assert [(t.entry_time, t.entry_px) for t in a.trades] == [(t.entry_time, t.entry_px) for t in b.trades]
    assert a.open_position_at_end == b.open_position_at_end and "zone" not in a.refused


def test_zone_refuses_a_mid_range_long_the_live_rule_would_take():
    # mid 101.0 in the 100..104 range: pos 0.25; stop 99.8, target 104 -> 2.5R, so the live rule buys
    at_101 = (101.0, 101.1, 100.9, 101.0)
    base = _run([at_101, at_101])
    assert base.open_position_at_end and base.open_position_at_end["entry_px"] == pytest.approx(101.0)
    gated = _run([at_101, at_101], entry_zone_frac=0.10)
    assert not gated.trades and gated.open_position_at_end is None
    assert gated.refused["zone"] == 2
    allowed = _run([at_101, at_101], entry_zone_frac=0.33)
    assert allowed.open_position_at_end["entry_px"] == pytest.approx(101.0) and "zone" not in allowed.refused
    # a later tick back at the edge (100.3, 0.075 of the range) trades under the tight gate
    late = _run([at_101, QUIET], entry_zone_frac=0.10)
    assert late.refused["zone"] == 1 and late.open_position_at_end["entry_px"] == pytest.approx(100.3)


def test_zone_gate_mirrors_for_shorts():
    def _short(t):
        return Direction(side="short")
    at_103 = (103.0, 103.1, 102.9, 103.0)       # 0.25 down from the 104 high
    gated = _run([at_103, at_103], direction=_short, entry_zone_frac=0.10)
    assert not gated.trades and gated.open_position_at_end is None and gated.refused["zone"] == 2
    allowed = _run([at_103, at_103], direction=_short, entry_zone_frac=0.33)
    assert allowed.open_position_at_end["side"] == "short" and allowed.open_position_at_end["entry_px"] == pytest.approx(103.0)


# ── the eval: grid and verdict (pure) ──────────────────────────────────────

def test_variants_hold_the_baseline_and_every_candidate_with_its_neighbours():
    v = E.variants()
    assert v["baseline"] == {}
    assert v["zone"] == {"entry_zone_frac": 0.33} and v["zone_25"] == {"entry_zone_frac": 0.25}
    assert v["delay"] == {"entry_delay_min": 120.0} and set(E.CANDIDATES["delay"]["neighbours"]) == {"delay_90", "delay_180"}
    assert v["edge"] == {"entry_mode": "edge", "atr_rest_frac": 0.25}
    assert len(v) == 1 + 3 * 3
    for name, ov in v.items():
        Params(**ov)                       # every override is a real Params field


def _ds(nets, dds, last_thirds, trades=None):
    return {"variants": {k: {"net_usd": nets[k], "max_dd_pct": dds[k], "trades": 10, "wins": 5} for k in nets},
            "thirds": [{}, {}, {k: {"net_usd": last_thirds[k]} for k in last_thirds}],
            "trades": trades or {k: [] for k in nets}, "days": 25.0}


def test_passes_needs_all_five_legs():
    nets = {"baseline": 100.0, "delay": 120.0, "delay_90": 110.0, "delay_180": 130.0}
    dds = {k: 20.0 for k in nets}
    last = {"baseline": 10.0, "delay": 20.0, "delay_90": 0.0, "delay_180": 0.0}
    wti = _ds({k: 5.0 for k in nets}, dds, last)
    p = E.passes(_ds(nets, dds, last), "delay", wti)
    assert p["pass"] is True and p["delta_usd"] == 20.0 and p["neighbours"] == {"delay_90": True, "delay_180": True}
    # each leg alone fails it
    assert E.passes(_ds({**nets, "delay": 90.0}, dds, last), "delay", wti)["pass"] is False          # net
    assert E.passes(_ds(nets, {**dds, "delay": 25.0}, last), "delay", wti)["pass"] is False          # drawdown
    assert E.passes(_ds({**nets, "delay_90": 99.0}, dds, last), "delay", wti)["pass"] is False       # plateau
    assert E.passes(_ds(nets, dds, {**last, "delay": 5.0}), "delay", wti)["pass"] is False           # last third below
    assert E.passes(_ds(nets, dds, {**last, "delay": -1.0, "baseline": -5.0}), "delay", wti)["pass"] is False  # negative
    assert E.passes(_ds(nets, dds, last), "delay", None)["pass"] is False                            # no WTI
    wti_neg = _ds({**{k: 5.0 for k in nets}, "delay": -0.5}, dds, last)
    assert E.passes(_ds(nets, dds, last), "delay", wti_neg)["pass"] is False                         # WTI negative


def test_verdict_picks_the_best_passer_and_flags_one_day_edges():
    nets = {"baseline": 100.0, "delay": 120.0, "delay_90": 110.0, "delay_180": 130.0}
    dds = {k: 20.0 for k in nets}
    last = {"baseline": 10.0, "delay": 20.0, "delay_90": 0.0, "delay_180": 0.0}
    trades = {"baseline": [{"exit": "2026-11-04 15:00Z", "entry": "2026-11-04 14:00Z", "r": -1.1, "net_usd": -17.0},
                           {"exit": "2026-11-09 11:00Z", "entry": "2026-11-09 07:00Z", "r": -1.0, "net_usd": -18.0}],
              "delay": [{"exit": "2026-11-09 11:00Z", "entry": "2026-11-09 09:00Z", "r": -1.0, "net_usd": -19.0}]}
    cands = {"delay": E.CANDIDATES["delay"]}
    res = {"brent_journal": _ds(nets, dds, last, trades), "wti_journal": _ds({k: 5.0 for k in nets}, dds, last)}
    v = E.verdict(res, cands)
    assert v["judged"] and v["pass"] and v["best"] == "delay" and v["passing"] == ["delay"]
    od = v["candidates"]["delay"]["one_day"]
    assert od["biggest_day"] == "2026-11-04" and od["biggest_r"] == 1.1 and od["fluke"] is True
    assert v["candidates"]["delay"]["delta_by_day"] == {"2026-11-04": {"r": 1.1, "net_usd": 17.0},
                                                        "2026-11-09": {"r": 0.0, "net_usd": -1.0}}
    assert E.verdict({}, cands)["judged"] is False


def test_one_day_flips_threshold():
    assert E.one_day_flips({"a": {"r": 2.0, "net_usd": 0}, "b": {"r": 0.1, "net_usd": 0}})["fluke"] is True
    assert E.one_day_flips({"a": {"r": 2.0, "net_usd": 0}, "b": {"r": 0.3, "net_usd": 0}})["fluke"] is False
    assert E.one_day_flips({})["fluke"] is True


def test_monthly_scales_net_to_30_days():
    ds = _ds({"baseline": 10.0, "delay": 22.0}, {"baseline": 1.0, "delay": 1.0}, {"baseline": 0, "delay": 0})
    m = E.monthly(ds, "delay")
    assert m["per_30d_usd"] == pytest.approx(26.4) and m["net_usd"] == 22.0 and m["days"] == 25.0


# ── the eval: the no-thesis zone strategy ──────────────────────────────────

def test_make_zone_long_gates_the_tournament_dip_buy():
    f = SimpleNamespace(lo24=[100.0], hi24=[106.0])
    s33 = E.make_zone_long(0.33)
    assert s33.name == "Z_zone33" and s33.family == "Z"
    inside = s33.setup(0, 100.5, f)
    assert isinstance(inside, T.Setup) and inside.side == 1 and inside.stop_px == pytest.approx(99.25)
    assert s33.setup(0, 103.0, f) is None                         # mid-range: gated
    assert T.make_baseline(1).setup(0, 101.0, f) is not None      # the ungated rule would still try there
    assert s33.setup(0, 100.5, SimpleNamespace(lo24=[None], hi24=[None])) is None
