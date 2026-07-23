"""Gate test for W-X7 xs_momentum sweep — the two facts that drive the recommendation.

Locks: (1) meme-exclusion beats memes-in on the live recipe, (2) the live cell is present
and on the frontier (no strict dominator). Skips if the shared crypto cache is absent.
Uses the fast (null-free) sweep to stay well under the gate budget.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "research" / "alpha_swarm" / "hypotheses" / "W-X7_xs_sweep.py"
CACHE = ROOT / "research" / "alpha_swarm" / "hypotheses" / "W-X2_cache_daily.json"

pytestmark = pytest.mark.skipif(not CACHE.exists(), reason="W-X2_cache_daily.json absent")

sys.path.insert(0, str(ROOT / "research" / "alpha_swarm" / "lib"))
_spec = importlib.util.spec_from_file_location("wx7", SCRIPT)
wx7 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wx7)


def _rows():
    _, rows = wx7.sweep()
    return rows


def test_grid_size_and_live_present():
    rows = _rows()
    assert len(rows) >= 200, len(rows)  # 288 minus any too-few-rebalance drops
    assert any(wx7.is_live(r) for r in rows), "live recipe cell missing"


def test_meme_exclusion_beats_memes_in():
    rows = _rows()
    live = next(r for r in rows if wx7.is_live(r))
    user_now = next(r for r in rows if wx7.is_user_current(r))
    # dropping memes must improve the live recipe's net25 (W-X4 reproduced)
    assert live["net25"] > user_now["net25"], (live["net25"], user_now["net25"])


def test_live_recipe_has_top_tier_sharpe():
    """No cell should beat live on BOTH net25 AND Sharpe AND both halves (frontier)."""
    rows = _rows()
    live = next(r for r in rows if wx7.is_live(r))
    for r in rows:
        if wx7.is_live(r):
            continue
        strictly_better = (
            r["net25"] > live["net25"]
            and r["sharpe_like_net25"] > live["sharpe_like_net25"]
            and r["oos_net25"][0] > live["oos_net25"][0]
            and r["oos_net25"][1] > live["oos_net25"][1]
        )
        assert not strictly_better, f"unexpected dominator {r['label']}"
