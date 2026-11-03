"""Gate tests for the closer-target eval's pure parts
(services/strategies/evals/closer_target_test.py): the variant grid and its
plateau families, the current (open) trade regression, `per_day_pct` and the
summary CSV rows. No network: the current-trade bars reproduce the real
xyz:BRENTOIL structural numbers (struct_low 103.74, struct_high 112.33,
stop 102.99 = struct_low - 0.75) the operator gave on 2026-11-03, typed in."""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

from pathiel.models.types import Candle
from services.strategies.backtest.desk_tick_sim import HOUR_MS, live_params
from services.strategies.evals import closer_target_test as T
from services.strategies.evals.brent_stop_buffer_test import BASELINE

Q = 900_000


def ms(*parts: int) -> int:
    return int(datetime(*parts, tzinfo=timezone.utc).timestamp() * 1000)


def _base(**over):
    return dataclasses.replace(live_params(), starting_equity=64.0, **over)


# ── the grid ─────────────────────────────────────────────────────────────

def test_variant_grid_names_and_overrides():
    v = T.variants()
    assert v[BASELINE] == {}
    assert v["tr7"] == {"target_r": 7.0} and v["tr4"] == {"target_r": 4.0} and v["tr10"] == {"target_r": 10.0}
    assert v["off_2"] == {"target_offset_frac": 0.02} and v["off_4"] == {"target_offset_frac": 0.04}
    assert v["cap_2.5"] == {"target_cap_frac": 0.025} and v["cap_5"] == {"target_cap_frac": 0.05}
    assert v["rtarget_p75"] == {"target_offset_range_frac": 0.25}
    assert v["rtarget_mid"] == {"target_offset_range_frac": 0.5}
    # every existing (0.75%/1%/1.5%) offset and every fixed-R up to 3 stays untested here (already rejected)
    assert "off_0.75" not in v and "tr3" not in v and "tr1.5" not in v


def test_families_cover_every_grid_member_and_live_only_anchors_offset():
    v = T.variants()
    for fam in T.FAMILIES.values():
        assert all(n == BASELINE or n in v for n in fam)
    assert T.FAMILIES["offset"][0] == BASELINE
    assert BASELINE not in T.FAMILIES["target_r"] and BASELINE not in T.FAMILIES["cap"]
    assert BASELINE not in T.FAMILIES["range"]
    assert len(T.FAMILIES["range"]) == 2        # p75 and midpoint, each other's only neighbour


# ── per_day_pct ────────────────────────────────────────────────────────────

def test_per_day_pct_compounds():
    # +100% over 7 days: daily rate r solves (1+r)^7 = 2
    out = T.per_day_pct({"days": 7.0, "return_pct": 100.0})
    assert out == round((2.0 ** (1 / 7) - 1) * 100, 3)
    assert T.per_day_pct({"days": 0, "return_pct": 10.0}) is None
    assert T.per_day_pct({"days": 5.0, "return_pct": None}) is None
    assert T.per_day_pct({"days": None, "return_pct": 10.0}) is None


# ── summary CSV rows ─────────────────────────────────────────────────────

def test_summary_csv_rows_breaks_out_exits_and_open_at_end():
    results = {"ds1": {"variants": {
        "live": {"final_equity": 100.0, "return_pct": 56.3, "days": 10.0, "max_dd_pct": 12.0, "trades": 7,
                 "win_rate": 0.57, "avg_r": 1.2, "worst_loss_usd": -10.0, "worst_loss_pct": -10.0,
                 "max_stop_liq_frac": 0.3, "exits": {"target": 3, "stop": 2, "invalidation": 1,
                                                      "direction_flip": 1}, "open_at_end": None},
        "tr7": {"final_equity": 90.0, "return_pct": 40.6, "days": 10.0, "max_dd_pct": 20.0, "trades": 5,
               "win_rate": 0.4, "avg_r": 0.8, "worst_loss_usd": -8.0, "worst_loss_pct": -8.0,
               "max_stop_liq_frac": 0.3, "exits": {"target": 4, "stop": 1}, "open_at_end": {"side": "long"}},
    }}}
    rows = T.summary_csv_rows(results)
    assert {r["variant"]: r for r in rows}["live"]["target_exits"] == 3
    assert {r["variant"]: r for r in rows}["live"]["stop_exits"] == 2
    assert {r["variant"]: r for r in rows}["live"]["thesis_close_exits"] == 2       # invalidation + direction_flip
    assert {r["variant"]: r for r in rows}["live"]["other_exits"] == 0             # 3+2+2 == 7 trades
    assert {r["variant"]: r for r in rows}["live"]["open_at_end"] is False
    tr7 = {r["variant"]: r for r in rows}["tr7"]
    assert tr7["other_exits"] == 0 and tr7["open_at_end"] is True
    assert set(rows[0]) == set(T.SUMMARY_FIELDS)


# ── the current (open) trade regression ───────────────────────────────────

def _struct_bars():
    """24 closed 1h bars ending at 18:00Z 2026-11-02 whose 24h range is
    103.74 to 112.33, matching the live trade's real stop (103.74 - 0.75 =
    102.99) exactly."""
    first = ms(2026, 11, 1, 18) - 0 * HOUR_MS
    c1h = [Candle(t=first + i * HOUR_MS, o=108.0, h=109.0, l=107.0, c=108.0, v=1.0) for i in range(24)]
    c1h[2] = Candle(t=c1h[2].t, o=108.0, h=112.33, l=107.0, c=108.0, v=1.0)   # the high, 11-01 21:00Z bar
    c1h[-1] = Candle(t=c1h[-1].t, o=108.0, h=109.0, l=103.74, c=108.0, v=1.0)  # the low, 11-02 17:00Z bar (closes 18:00)
    # one more bar AFTER the entry tick, wild, to prove no lookahead
    c1h.append(Candle(t=ms(2026, 11, 2, 18), o=500.0, h=600.0, l=1.0, c=500.0, v=1.0))
    return c1h


def test_current_trade_struct_matches_the_real_stop():
    c1h = _struct_bars()
    c15 = [Candle(t=ms(2026, 11, 2, 18) + i * Q, o=104.0, h=104.5, l=103.5, c=104.0, v=1.0) for i in range(4)]
    out = T.current_trade_targets(c15, c1h, {T.BASELINE: {}}, _base())
    assert out["struct_low"] == 103.74 and out["struct_high"] == 112.33
    # the real stop (102.99) == struct_low - the live 0.75 buffer, confirming the lookback window
    assert out["struct_low"] - 0.75 == T.CURRENT_TRADE["stop_px"]
    live = out["variants"][T.BASELINE]
    assert live["target_px"] > 111.0           # close to the real 111.77 tp (0.5% inside 112.33)
    assert live["r_multiple"] > 13.0            # about 13R, as the operator said


def test_current_trade_target_variants_and_already_filled():
    c1h = _struct_bars()
    entry_t = ms(2026, 11, 2, 18)
    rows = [(104.0, 104.5, 103.5, 104.0), (104.0, 105.6, 104.0, 105.5), (105.5, 105.9, 105.3, 105.8)]
    c15 = [Candle(t=entry_t + i * Q, o=o, h=h, l=lo, c=c, v=1.0) for i, (o, h, lo, c) in enumerate(rows)]
    named = {T.BASELINE: {}, "tr7": {"target_r": 7.0}, "cap_2": {"target_cap_frac": 0.02},
            "rtarget_mid": {"target_offset_range_frac": 0.5}}
    out = T.current_trade_targets(c15, c1h, named, _base())
    assert out["high_so_far"] == 105.9 and out["data_end"] is not None
    # entry 103.61, risk 0.62: tr7 targets 103.61 + 7*0.62 = 107.95, well above the 105.9 high: not filled
    assert out["variants"]["tr7"]["target_px"] == 107.95 and not out["variants"]["tr7"]["already_filled"]
    # cap_2: entry*1.02 = 105.6822 (rounded to 105.682), the 105.9 high clears it: filled
    assert out["variants"]["cap_2"]["target_px"] == 105.682 and out["variants"]["cap_2"]["already_filled"]
    # the live far-edge target (>111) is nowhere close: not filled
    assert not out["variants"][T.BASELINE]["already_filled"]
    assert out["variants"]["rtarget_mid"]["target_px"] == (112.33 + 103.74) / 2.0


def test_current_trade_is_causal():
    """The 1h bar strictly after the entry tick (the wild one in
    `_struct_bars`) does not change the levels; only closed 1h candles enter
    `_levels`."""
    c1h = _struct_bars()
    c15 = [Candle(t=ms(2026, 11, 2, 18) + i * Q, o=104.0, h=104.2, l=103.9, c=104.0, v=1.0) for i in range(2)]
    out = T.current_trade_targets(c15, c1h, {T.BASELINE: {}}, _base())
    assert out["struct_high"] == 112.33 and out["struct_low"] == 103.74   # not 600 / 1.0 from the wild bar


def test_current_trade_short_side_mirrors_long():
    c1h = _struct_bars()
    trade = {**T.CURRENT_TRADE, "side": "short", "entry_px": 112.0, "stop_px": 112.75}
    c15 = [Candle(t=ms(2026, 11, 2, 18) + i * Q, o=112.0, h=112.1, l=103.8, c=104.0, v=1.0) for i in range(2)]
    out = T.current_trade_targets(c15, c1h, {T.BASELINE: {}, "tr5": {"target_r": 5.0}}, _base(), trade=trade)
    assert out["low_so_far"] == 103.8
    # a short's tr5 target: entry - 5 x risk = 112.0 - 5*0.75 = 108.25
    assert out["variants"]["tr5"]["target_px"] == 108.25
    assert out["variants"]["tr5"]["already_filled"]            # the low (103.8) is well past it
