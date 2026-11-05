"""Gate tests for the gold thesis replay eval's pure parts
(services/strategies/evals/gold_thesis_replay_test.py,
GOLD_THESIS_REPLAY_2026-11-05.md). Synthetic candles/theses only, no
network, no live journal reads, no LLM."""
from __future__ import annotations

from pathiel.models.types import Candle
from services.strategies.backtest.data import ThesisDirection
from services.strategies.evals import gold_thesis_replay_test as G
from services.strategies.evals.live_params import live_gold_params

DAY_MS = 86_400_000
HOUR_MS = 3_600_000


def _candle(t, c, o=None):
    return Candle(t=t, o=c if o is None else o, h=c, l=c, c=c, v=1.0)


def _thesis(created_at_ms, side, entry_px=4000.0):
    return ThesisDirection(created_at_ms=created_at_ms, side=side, entry_px=entry_px, stop_px=None,
                           invalidation_px=None, targets=[], thesis_id=f"t{created_at_ms}")


def _summary(final, dd=10.0):
    return {"final_equity": final, "max_dd_pct": dd}


def _results(replay_final, long_final, short_final, sign_final, dd=10.0):
    return {"replay": {"variants": {G.LIVE: _summary(replay_final, dd)}},
            "always_long": {"variants": {G.LIVE: _summary(long_final)}},
            "always_short": {"variants": {G.LIVE: _summary(short_final)}},
            "daily_sign": {"variants": {G.LIVE: _summary(sign_final)}}}


# ── variants / base ──────────────────────────────────────────────────────

def test_variants_are_the_floor_grid_times_both_leverage_rules_with_no_break_pinned_on():
    named = G.variants()
    assert len(named) == len(G.FLOOR_PCTS) * len(G.LEV_MODES) == 6
    assert G.LIVE in named
    assert named[G.LIVE] == {"stop_buffer_floor_frac": 0.008, "entry_fit_leverage": (20.0, 15.0), "no_break_entry": True}
    assert named["floor_1.0_fix20"] == {"stop_buffer_floor_frac": 0.01, "entry_fit_leverage": None, "no_break_entry": True}
    for ov in named.values():
        assert ov["no_break_entry"] is True


def test_live_variant_is_live_gold_params_at_the_six_percent_cap():
    base = G.base_params(25.0, 12.0)
    assert base.risk_cap_frac == 0.06 == G.LIVE_RISK_CAP_FRAC
    assert base.starting_equity == G.START
    assert base.no_break_entry is True
    ref = live_gold_params(25.0, 12.0, starting_equity=G.START, risk_cap_frac=0.06)
    assert base == ref
    # applying the live variant's overrides to the base changes nothing: it IS the live rule
    import dataclasses
    assert dataclasses.replace(base, **G.variants()[G.LIVE]) == base


# ── windows ──────────────────────────────────────────────────────────────

def test_replay_window_runs_from_the_first_call_to_a_day_after_the_last_clamped_to_the_data():
    th = [_thesis(10 * DAY_MS, "short"), _thesis(12 * DAY_MS, "long"), _thesis(11 * DAY_MS, "flat")]
    assert G.replay_window(th, 0, 100 * DAY_MS) == (10 * DAY_MS, 13 * DAY_MS)
    assert G.replay_window(th, 0, 12 * DAY_MS + HOUR_MS) == (10 * DAY_MS, 12 * DAY_MS + HOUR_MS)   # data ends first
    assert G.replay_window(th, 11 * DAY_MS, 100 * DAY_MS) == (11 * DAY_MS, 13 * DAY_MS)            # data starts late
    assert G.replay_window(th, 0, 10 * DAY_MS) is None
    assert G.replay_window([], 0, 100 * DAY_MS) is None


def test_side_counts_and_buy_hold_row():
    th = [_thesis(0, "short"), _thesis(1, "short"), _thesis(2, "long"), _thesis(3, "flat")]
    assert G.side_counts(th) == {"flat": 1, "long": 1, "short": 2}
    c15 = [_candle(0, 4100.0, o=4000.0), _candle(900_000, 4200.0), _candle(1_800_000, 4400.0)]
    row = G.buy_hold_row(c15, 0, 1_800_000, start=64.0)          # 4000 open -> 4200 close; the 4400 bar is outside
    assert row == {"return_pct": 5.0, "final_equity": 67.2, "net_usd": 3.2}
    assert G.buy_hold_row(c15[:1], 0, 900_000) == {"return_pct": None, "final_equity": None, "net_usd": None}


# ── the verdict rule ─────────────────────────────────────────────────────

def test_verdict_activates_only_when_net_positive_beats_every_control_and_dd_under_the_cap():
    v = G.activation_verdict(_results(80.0, 70.0, 60.0, 50.0, dd=34.9))
    assert v["activate"] is True
    assert v["fails"] == []
    assert v["beats"] == {"always_long": True, "always_short": True, "daily_sign": True}
    assert v["net_usd"] == 16.0


def test_verdict_fails_on_a_net_loss():
    v = G.activation_verdict(_results(60.0, 50.0, 50.0, 50.0))
    assert v["activate"] is False
    assert v["fails"] == ["net not positive"]


def test_verdict_fails_when_one_control_ties_or_wins():
    tie = G.activation_verdict(_results(80.0, 80.0, 60.0, 50.0))
    assert tie["activate"] is False and tie["fails"] == ["does not beat every control"]
    assert tie["beats"]["always_long"] is False
    win = G.activation_verdict(_results(80.0, 60.0, 60.0, 90.0))
    assert win["activate"] is False and win["beats"]["daily_sign"] is False


def test_verdict_fails_at_the_drawdown_cap_and_at_zero_net():
    dd = G.activation_verdict(_results(80.0, 60.0, 60.0, 50.0, dd=35.0))
    assert dd["activate"] is False
    assert dd["fails"] == ["max drawdown 35.0% not under 35%"]
    flat = G.activation_verdict(_results(64.0, 64.0, 64.0, 64.0, dd=0.0))
    assert flat["activate"] is False
    assert flat["fails"] == ["net not positive", "does not beat every control"]


def test_verdict_never_passes_by_absence():
    assert G.activation_verdict({})["fails"] == ["no replay dataset"]
    missing = _results(80.0, 60.0, 60.0, 50.0)
    del missing["daily_sign"]
    v = G.activation_verdict(missing)
    assert v["activate"] is False
    assert v["beats"]["daily_sign"] is None
    assert "does not beat every control" in v["fails"]


# ── CSV rows ─────────────────────────────────────────────────────────────

def test_summary_csv_rows_carry_one_row_per_dataset_variant_with_every_field():
    s = {"final_equity": 70.0, "return_pct": 9.4, "per_day_pct": 0.3, "max_dd_pct": 5.0, "trades": 3,
         "win_rate": 0.667, "avg_r": 0.5, "max_stop_liq_frac": 0.4, "exits": {"stop": 1, "target": 2},
         "refused": {"chase": 4}}
    results = {"replay": {"window": ["a", "b"], "days": 30.0, "gold_move_pct": 1.2, "variants": {G.LIVE: s}}}
    rows = G.summary_csv_rows(results)
    assert len(rows) == 1
    assert set(rows[0]) == set(G.SUMMARY_FIELDS)
    assert rows[0]["net_usd"] == 6.0 and rows[0]["stop_exits"] == 1 and rows[0]["target_exits"] == 2
    assert rows[0]["refused"] == '{"chase": 4}'
