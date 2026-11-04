"""No entry once the mid is past the 24h range's own edge (2026-11-04).

The trade review's first LEAK: range=past own edge, 10 real trades, 1 win,
-$117.26 (docs/strategy/learning/REVIEW_2026-11-04.md). The operator said
"go" and "DO WHAT U THINK IS MORE PROFITABLE": next_trade.NO_BREAK_ENTRY,
the silver breakout off, desk_tick_sim.live_params() in step.
"""
from __future__ import annotations

import json

import pytest

from services.strategies import next_trade as NT
from services.strategies.backtest.desk_tick_sim import live_params, live_params_before_no_break
from services.strategies.evals import no_break_real_fills as E
from services.strategies.next_trade import next_trade

KW = dict(leverage=20, coin_max_leverage=20, stop_buffer=0.75)


def test_a_long_under_the_24h_low_is_refused():
    out = next_trade("long", 99.50, 100.00, 106.00, thesis_entry=101.0, **KW)
    assert out["ok"] is False
    assert any("under the 24h low 100: no entry after a break" in b for b in out["blockers"])
    assert out["verdict"].startswith("waits: price broke the 24h low 100")


def test_the_same_long_traded_before_the_rule():
    out = next_trade("long", 99.50, 100.00, 106.00, thesis_entry=101.0, no_break=False, **KW)
    assert out["ok"] is True and out["stop_px"] == pytest.approx(98.75)   # 0.75 under the mid


def test_at_the_low_and_inside_the_range_still_trade():
    at = next_trade("long", 100.00, 100.00, 106.00, thesis_entry=101.0, **KW)
    inside = next_trade("long", 100.40, 100.00, 106.00, thesis_entry=101.0, **KW)
    assert at["ok"] and inside["ok"]
    assert at["stop_px"] == inside["stop_px"] == pytest.approx(99.25)    # 0.75 under the low


def test_a_short_over_the_24h_high_is_refused_and_its_mirror_trades():
    over = next_trade("short", 52.40, 48.60, 52.00, thesis_entry=51.80, leverage=20,
                      coin_max_leverage=25, stop_buffer=0.42)
    assert over["ok"] is False and any("over the 24h high 52: no entry after a break" in b
                                       for b in over["blockers"])
    at = next_trade("short", 52.00, 48.60, 52.00, thesis_entry=51.80, leverage=20,
                    coin_max_leverage=25, stop_buffer=0.42)
    assert at["ok"] is True


def test_the_real_11_02_long_is_refused():
    """03:50Z: mid 110.01 under the 110.14 24h low (test_loss_fix_eval's
    note); it lost $33.87, the biggest single loss in the ledger."""
    out = next_trade("long", 110.01, 110.14, 112.39, thesis_entry=110.5, **KW)
    assert out["ok"] is False and "no entry after a break" in " ".join(out["blockers"])


def test_live_rule_and_the_sim_agree():
    assert NT.NO_BREAK_ENTRY is True
    assert live_params().no_break_entry is NT.NO_BREAK_ENTRY
    assert live_params_before_no_break().no_break_entry is False
    assert NT.BREAKOUT_COINS == frozenset()


def test_the_agent_text_names_the_rule_and_drops_the_breakout():
    from pathlib import Path
    root = Path(__file__).resolve().parents[3]
    for rel in (".claude/agents/desk-tick.md", "agents/desk-tick.md"):
        text = (root / rel).read_text()
        assert "No entry after a break" in text, rel
        assert "sells at\n         the mid once the last CLOSED 1h candle" not in text, rel


# ── the eval on the real fills ──────────────────────────────────────────────

def _row(i, pos, net, desk="oil", side="long"):
    return {"id": f"t{i}", "entry_ms": i, "entry": f"2026-11-0{i}", "desk": desk, "side": side,
            "range_pos": pos, "net_usd": net}


def test_eval_scores_saved_and_missed():
    rows = [_row(1, -0.2, -30.0), _row(2, -0.1, 8.0), _row(3, 0.0, 49.0), _row(4, None, -5.0)]
    out = E.score(rows)
    assert (out["refused"], out["refused_wins"], out["saved_usd"], out["missed_usd"]) == (2, 1, 30.0, 8.0)
    assert out["net_change_usd"] == 22.0 and out["with_rule_net_usd"] == 44.0 and out["pass"]


def test_eval_fails_when_the_rule_would_cost_money():
    assert E.score([_row(1, -0.2, 20.0), _row(2, 0.5, -3.0)])["pass"] is False


def test_eval_keeps_the_last_revision_of_a_trade():
    lines = [json.dumps(_row(1, -0.2, -3.0)), json.dumps(_row(1, -0.2, -4.0)), ""]
    assert [r["net_usd"] for r in E.latest_rows(lines)] == [-4.0]
