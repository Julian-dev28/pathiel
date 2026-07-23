"""Gate test for W-FUN2 golden ratio — locks the real finding: not phi, just mean-reversion."""
import importlib.util
import statistics as st
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "research" / "alpha_swarm" / "hypotheses" / "W-FUN2_golden_ratio.py"
DATASET = ROOT / "research" / "alpha_swarm" / "dataset.json"

pytestmark = pytest.mark.skipif(not DATASET.exists(), reason="dataset.json absent")

sys.path.insert(0, str(ROOT / "research" / "alpha_swarm" / "lib"))
_spec = importlib.util.spec_from_file_location("wfun2", SCRIPT)
g = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(g)


def _bucket_ev(rows, lo, hi):
    xs = [r["fwd"] - g.FEE for r in rows if lo <= r["pos"] < hi]
    return st.fmean(xs) if len(xs) >= 8 else None


def test_upper_range_worse_than_lower_range():
    """The real signal: buying near the range HIGH (0.786) is worse than a pullback (0.382)
    on ETH — mean-reversion, not a golden-ratio property."""
    import alpha_lib as A
    rows = g.series(g.daily("ETH", A.load_dataset()))
    low = _bucket_ev(rows, *g.ZONES["0.382"])
    high = _bucket_ev(rows, *g.ZONES["0.786"])
    assert low is not None and high is not None
    assert low > high, (low, high)


def test_null_is_deterministic():
    import alpha_lib as A
    rows = g.series(g.daily("BTC", A.load_dataset()))
    assert g.null_p(rows, 0.01, 20) == g.null_p(rows, 0.01, 20)
