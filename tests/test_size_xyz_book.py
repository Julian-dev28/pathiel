"""Gate tests for scripts/size_xyz_book.py — the xs_xyz_equities sizing derivation.

Deterministic, free, <2s. These lock the two things that would silently mis-size the
live book: the margin invariant (U = n_legs * f) and the float-floor grid trap that
made f collapse 0.070 -> 0.065.
"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "scripts" / "size_xyz_book.py"

_spec = importlib.util.spec_from_file_location("size_xyz_book", SPEC)
size_xyz_book = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(size_xyz_book)


def test_default_recommendation_is_0_07():
    """The live number. If the edge stats or policy move, this SHOULD change and the
    test forces a conscious update — that is the point."""
    r = size_xyz_book.derive()
    assert r["recommended_equity_frac"] == 0.07, r["recommended_equity_frac"]
    assert r["binding_constraint"] == "margin"


def test_grid_rounding_does_not_underflow():
    """Regression: int(0.070/0.005) floored to 0.065 without the +1e-9 guard."""
    r = size_xyz_book.derive()
    # exactly-on-grid margin cap must snap to itself, not one tick below
    assert r["margin"]["f_margin"] == 0.07
    assert r["recommended_equity_frac"] == 0.07


def test_margin_invariant_leaves_required_free():
    """U(f) = n_legs * f, and the recommended f must leave >= min_free + buffer idle."""
    r = size_xyz_book.derive()
    f = r["recommended_equity_frac"]
    u = r["at_recommended"]["margin_utilisation"]
    assert abs(u - size_xyz_book.N_LEGS * f) < 1e-9
    assert r["at_recommended"]["free_margin_left"] >= (
        size_xyz_book.MARGIN_MIN_FREE + size_xyz_book.MARGIN_PRICE_BUFFER - 1e-9
    )


def test_never_exceeds_full_margin():
    """No parameterisation may recommend >100% margin utilisation."""
    for kf in (0.1, 0.25, 0.35, 0.5, 1.0, 5.0):
        r = size_xyz_book.derive(kelly_fraction=kf)
        assert r["at_recommended"]["margin_utilisation"] <= 1.0, (kf, r)


def test_recommended_f_matches_live_config():
    """The derived f and the wired live config must agree — drift = silent mis-size."""
    cfg = json.loads((ROOT / ".agent-config.json").read_text())
    live_f = cfg["xs_xyz_equities"]["equity_frac"]
    derived_f = size_xyz_book.derive()["recommended_equity_frac"]
    assert live_f == derived_f, f"config {live_f} != derived {derived_f}"


def test_leverage_cancels_out_of_margin():
    """Margin utilisation is leverage-invariant (the reason f can be solved without
    the runtime dex equity). Changing leverage must not move f_margin or U."""
    a = size_xyz_book.derive(leverage=3)
    b = size_xyz_book.derive(leverage=6)
    assert a["margin"]["f_margin"] == b["margin"]["f_margin"]


def test_cli_json_runs():
    out = subprocess.run([sys.executable, str(SPEC), "--json"],
                         capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    r = json.loads(out.stdout)
    assert r["recommended_equity_frac"] == 0.07
