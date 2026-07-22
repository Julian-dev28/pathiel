"""Gate test for W-MC1 Markov backtest: same seed + same cache -> same verdict.

Locks the REFUTED result so a future edit that accidentally introduces lookahead (and a
spurious 'edge') fails loudly. Skips cleanly if the dataset cache is absent.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "research" / "alpha_swarm" / "hypotheses" / "W-MC1_markov.py"
DATASET = ROOT / "research" / "alpha_swarm" / "dataset.json"

pytestmark = pytest.mark.skipif(not DATASET.exists(), reason="dataset.json cache absent")

sys.path.insert(0, str(ROOT / "research" / "alpha_swarm" / "lib"))
_spec = importlib.util.spec_from_file_location("wmc1", SCRIPT)
wmc1 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wmc1)


def _d():
    import alpha_lib as A
    return A.load_dataset()


def test_primary_cell_refuted_and_gross_near_zero():
    r = wmc1.run(_d(), iv="1d", n_states=3)
    assert r["verdict"] == "REFUTED"
    assert r["cells"]["ev25"] < 0            # dies to fees
    assert abs(r["cells"]["ev0"]) < 0.1      # gross ~flat (%), no OOS edge


def test_deterministic_permutation_p():
    r1 = wmc1.run(_d(), iv="1d", n_states=3)
    r2 = wmc1.run(_d(), iv="1d", n_states=3)
    assert r1["perm_p"] == r2["perm_p"]      # seed-locked


def test_transition_matrix_is_near_uniform():
    """The core finding: memoryless. Every row within 0.06 of 1/n_states."""
    r = wmc1.run(_d(), iv="1d", n_states=3)
    for row in r["transition_matrix"].values():
        for p in row:
            assert abs(p - 1 / 3) < 0.06, r["transition_matrix"]
