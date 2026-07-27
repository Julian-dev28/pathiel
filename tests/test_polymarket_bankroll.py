"""Bankroll simulation: the Kelly math, the DOWN-fill repair, and the sequential
compounding. Injected resolver — no network."""
from __future__ import annotations

import pytest

from services.polymarket_scout import bankroll


def _row(mid, side, llm_yes, mkt_yes, ts, fill_px=None):
    return {"market_id": mid, "side": side, "llm_yes": llm_yes, "mkt_yes": mkt_yes,
            "fill_px": fill_px if fill_px is not None else mkt_yes, "ts": ts,
            "lane": "updown_5m", "question": f"q{mid}"}


# ── Kelly ────────────────────────────────────────────────────────────────────
def test_kelly_fraction_positive_only_on_a_real_edge():
    # buy YES at 0.50, we think 0.60 -> edge -> positive Kelly
    assert bankroll.kelly_fraction_for(0.60, 0.50) > 0
    # buy at 0.50, we think 0.50 -> no edge -> zero
    assert bankroll.kelly_fraction_for(0.50, 0.50) == pytest.approx(0.0, abs=1e-9)
    # we think LESS than the price -> negative edge -> clamped to 0 (never short)
    assert bankroll.kelly_fraction_for(0.40, 0.50) == 0.0


def test_kelly_never_exceeds_full_bankroll():
    assert bankroll.kelly_fraction_for(0.99, 0.02) <= 1.0


# ── the DOWN-fill repair ─────────────────────────────────────────────────────
def test_corrected_fill_repairs_the_down_side():
    # a DOWN/NO row recorded fill_px=mkt_yes (the bug); corrected = 1-mkt_yes
    row = _row("1", "NO", 0.45, 0.55, 1, fill_px=0.55)
    assert bankroll.corrected_fill(row) == pytest.approx(0.45)
    # YES side unchanged
    assert bankroll.corrected_fill(_row("2", "YES", 0.6, 0.55, 1)) == pytest.approx(0.55)


# ── the sim ──────────────────────────────────────────────────────────────────
def test_simulate_compounds_wins_and_sizes_by_kelly():
    rows = [_row("1", "YES", 0.7, 0.50, 1), _row("2", "YES", 0.7, 0.50, 2)]
    # both resolve YES-won -> both our YES bets win
    sim = bankroll.simulate(rows, resolver=lambda mid: True, start=50.0, kelly_fraction=0.5)
    assert sim["n_resolved"] == 2
    assert sim["final_bankroll"] > 50.0          # two wins compounded up
    assert sim["win_rate"] == 1.0
    assert sim["trades"][1]["bankroll"] == pytest.approx(sim["final_bankroll"], abs=0.01)


def test_simulate_processes_trades_in_time_order():
    rows = [_row("late", "YES", 0.7, 0.5, 200), _row("early", "YES", 0.7, 0.5, 100)]
    sim = bankroll.simulate(rows, resolver=lambda mid: True, start=50.0)
    assert [t["q"] for t in sim["trades"]] == ["qearly", "qlate"]


def test_simulate_skips_unresolved():
    rows = [_row("1", "YES", 0.7, 0.5, 1), _row("2", "YES", 0.7, 0.5, 2)]
    sim = bankroll.simulate(rows, resolver=lambda mid: True if mid == "1" else None, start=50.0)
    assert sim["n_resolved"] == 1


def test_simulate_a_loss_shrinks_the_bankroll():
    sim = bankroll.simulate([_row("1", "YES", 0.7, 0.5, 1)],
                            resolver=lambda mid: False, start=50.0, kelly_fraction=0.5)
    assert sim["final_bankroll"] < 50.0 and sim["trades"][0]["won"] is False


def test_simulate_restricts_to_a_lane():
    rows = [_row("1", "YES", 0.7, 0.5, 1)]
    rows.append({**_row("2", "YES", 0.7, 0.5, 2), "lane": "trending"})
    sim = bankroll.simulate(rows, resolver=lambda mid: True, start=50.0, lane="updown_5m")
    assert sim["n_resolved"] == 1 and sim["lane"] == "updown_5m"


def test_simulate_empty_is_renderable():
    sim = bankroll.simulate([], resolver=lambda mid: True, start=50.0)
    assert sim["n_resolved"] == 0 and sim["final_bankroll"] == 50.0
    assert "no resolved trades" in bankroll._fmt(sim)
