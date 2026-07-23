"""Gate tests for W-SOC1 coverage-surge backtest — the parts that do NOT need network.

Locks the lookahead-safe entry/return math and the taint/surge filters. The full run's
verdict is not asserted here (needs the fetched cache); a separate determinism check runs
only when the cache exists.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "research" / "alpha_swarm" / "hypotheses" / "W-SOC1_social_long.py"
LEDGER = ROOT / ".state" / "shadow_ledger" / "news_surge_short.jsonl"

sys.path.insert(0, str(ROOT / "research" / "alpha_swarm" / "lib"))
_spec = importlib.util.spec_from_file_location("wsoc1", SCRIPT)
wsoc1 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wsoc1)


def test_entry_idx_is_lookahead_safe():
    bars = [[100, 1, 1, 1, 1, 1], [200, 2, 2, 2, 2, 2], [300, 3, 3, 3, 3, 3]]
    # ts strictly inside bar 0 -> entry must be the FIRST bar at/after ts, never before
    assert wsoc1.entry_idx(bars, 150) == 1
    assert wsoc1.entry_idx(bars, 200) == 1
    assert wsoc1.entry_idx(bars, 999) is None


def test_fwd_return_open_to_close():
    bars = [[100, 10.0, 0, 0, 11.0, 0], [200, 11.0, 0, 0, 13.2, 0]]
    # h=1: entry open 10.0 -> exit close (bar e+0) 11.0 => +10%
    assert abs(wsoc1.fwd_return(bars, 0, 1) - 0.10) < 1e-9
    # h=2: entry open 10.0 -> close of bar index 1 = 13.2 => +32%
    assert abs(wsoc1.fwd_return(bars, 0, 2) - 0.32) < 1e-9
    assert wsoc1.fwd_return(bars, 0, 5) is None  # runs off the end


def test_long_short_are_mirror_less_fees():
    rets = [0.05, -0.02, 0.10]
    lng = wsoc1.cell_stats(rets, +1)["ev0"]
    sht = wsoc1.cell_stats(rets, -1)["ev0"]
    assert abs(lng + sht) < 1e-9  # gross long == -gross short


@pytest.mark.skipif(not LEDGER.exists(), reason="shadow ledger absent")
def test_load_events_filters_taint_and_surge():
    evs = wsoc1.load_events()
    assert evs, "expected untainted surge events"
    for e in evs:
        assert e["surge_x"] >= wsoc1.SURGE_MIN
    # events are time-sorted
    assert all(evs[i]["ts"] <= evs[i + 1]["ts"] for i in range(len(evs) - 1))
