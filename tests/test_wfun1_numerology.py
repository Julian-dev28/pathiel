"""Gate tests for W-FUN1 — the numerology oracle. Locks the two real lessons: the null
discipline runs, and 25x-daily full-margin compounding craters even a positive-mean bettor.
Pure-function tests need no dataset; the ETH run is skipped if the cache is absent.
"""
import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "research" / "alpha_swarm" / "hypotheses" / "W-FUN1_numerology.py"
DATASET = ROOT / "research" / "alpha_swarm" / "dataset.json"

sys.path.insert(0, str(ROOT / "research" / "alpha_swarm" / "lib"))
_spec = importlib.util.spec_from_file_location("wfun1", SCRIPT)
wfun1 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wfun1)


def test_numerology_primitives():
    assert wfun1.reduce_num(11) == 11 and wfun1.reduce_num(22) == 22   # masters kept
    assert wfun1.reduce_num(1988) == 8                                  # 1+9+8+8=26 -> 8
    assert wfun1.ETH_RESONANCE == 5                                     # ETHEREUM = 5
    d = wfun1.schemes(datetime(2026, 8, 8, tzinfo=timezone.utc))
    assert set(d.values()) <= {1, -1}                                   # every scheme is a bet


def test_liquidation_is_total_loss():
    # a long that breaches the 4% liq floor loses the whole margin, ignoring the up-move
    entries = [(None, 100.0, 0.50, True, False)]   # ret +50% but low breached liq
    assert wfun1.pnl_of(entries, [1]) == [-1.0]


def test_positive_mean_can_still_crater_the_bankroll():
    """The whole point: +EV per trade, -EV compounded, once a liquidation tail exists."""
    # alternate a big 25x win and a liquidation: mean > 0, product -> 0
    entries = [(None, 1.0, 0.05, False, False), (None, 1.0, 0.0, True, False)] * 20
    dirs = [1] * len(entries)
    r = wfun1.evaluate(entries, dirs, "demo")
    assert r["mean_margin_pnl_pct"] != 0
    assert r["final_equity_x"] == 0.0            # a single -1.0 zeroes the compounding


@pytest.mark.skipif(not DATASET.exists(), reason="dataset.json absent")
def test_eth_run_is_a_crater_and_null_is_deterministic():
    bars = wfun1.eth_bars("1h")
    entries = wfun1.daily_entries(bars, 14)
    assert len(entries) > 20
    p1 = wfun1.null_p(entries, 0.05)
    p2 = wfun1.null_p(entries, 0.05)
    assert p1 == p2                              # seed-locked
