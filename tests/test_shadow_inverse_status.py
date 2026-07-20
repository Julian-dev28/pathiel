"""Gate tests for the read-only shadow-ledger inverse audit."""
from __future__ import annotations

import importlib.util
from pathlib import Path


_PATH = Path(__file__).resolve().parents[1] / "scripts" / "shadow_inverse_status.py"
_SPEC = importlib.util.spec_from_file_location("shadow_inverse_status", _PATH)
assert _SPEC and _SPEC.loader
MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(MODULE)


def test_inverse_side_is_exact_and_rejects_unknown_values():
    assert MODULE.inverse_side("long") == "short"
    assert MODULE.inverse_side("short") == "long"
    try:
        MODULE.inverse_side("flat")
    except ValueError as exc:
        assert "unknown side" in str(exc)
    else:
        raise AssertionError("unknown ledger side must not silently become a trade")


def test_inverse_records_flips_only_side_and_does_not_mutate_source():
    original = [{
        "coin": "SOL", "side": "short", "entry_ref_px": 100.0,
        "meta": {"nested": ["unchanged"]},
    }]
    inverted = MODULE.inverse_records(original)

    assert inverted == [{
        "coin": "SOL", "side": "long", "entry_ref_px": 100.0,
        "meta": {"nested": ["unchanged"]},
    }]
    inverted[0]["meta"]["nested"].append("copy")
    assert original[0]["side"] == "short"
    assert original[0]["meta"]["nested"] == ["unchanged"]


def test_script_uses_the_same_project_root_as_the_shadow_ledger():
    assert MODULE._REPO == Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------- matched null
class _Bar:
    def __init__(self, t, c):
        self.t = t
        self.o = self.h = self.l = self.c = c


def _trend_bars(n=60, start=100.0, growth=1.01):
    """Constant +1%/bar geometric drift: every entry time earns the same
    percent return, so a book matching that return has zero excess."""
    return [_Bar(1_000 + i, start * growth ** i) for i in range(n)]


def test_matched_null_uptrend_long_is_beta_not_edge():
    import random
    bars = _trend_bars()
    cache = {("UP", "1d"): bars}
    # observed = exactly the tape's own 3-bar drift (1.01^3 - 1 = +3.03%)
    detail = [{"coin": "UP", "side": "long", "interval": "1d",
               "price_pct": 3.03, "stop_pct": 20.0, "n_bars": 3}]
    out = MODULE.matched_null(detail, cache, draws=500, rng=random.Random(1))
    assert out["n_episodes"] == 1 and out["skipped"] == 0
    # random long entries earn the same +3% — a drift-matching read has no
    # excess and must NOT look significant
    assert out["null_mean_price12_pct"] > 1.0
    assert abs(out["excess_pct"]) < 0.2
    assert out["mc_p"] > 0.05


def test_matched_null_short_in_uptrend_null_is_negative():
    import random
    bars = _trend_bars()
    cache = {("UP", "1d"): bars}
    detail = [{"coin": "UP", "side": "short", "interval": "1d",
               "price_pct": 5.0, "stop_pct": 20.0, "n_bars": 3}]
    out = MODULE.matched_null(detail, cache, draws=500, rng=random.Random(1))
    # random shorts bleed in an uptrend; a +5% short read towers over that null
    assert out["null_mean_price12_pct"] < 0.0
    assert out["excess_pct"] > 4.0
    assert out["mc_p"] < 0.05


def test_matched_null_skips_episodes_without_enough_bars():
    import random
    cache = {("TINY", "1d"): _trend_bars(n=2)}
    detail = [{"coin": "TINY", "side": "long", "interval": "1d",
               "price_pct": 1.0, "stop_pct": 20.0, "n_bars": 5}]
    assert MODULE.matched_null(detail, cache, draws=50, rng=random.Random(1)) is None


def test_grade_inverse_applies_meta_filter_before_inverting(monkeypatch):
    ledger = [
        {"coin": "A", "side": "short", "signal_bar_t": 1, "entry_ref_px": 1.0,
         "horizon_days": 1, "stop_pct": 20.0, "meta": {"breaking": True}},
        {"coin": "B", "side": "short", "signal_bar_t": 1, "entry_ref_px": 1.0,
         "horizon_days": 1, "stop_pct": 20.0, "meta": {"breaking": False}},
    ]
    monkeypatch.setattr(MODULE.SL, "load", lambda book: ledger)
    seen = {}

    def fake_grade(records, fetch_fwd, now_ms=None, fetch_funding=None, dedup=True):
        seen["records"] = records
        return {"n": 0}

    monkeypatch.setattr(MODULE.SL, "grade_records", fake_grade)
    MODULE.grade_inverse("newsish", now_ms=10_000_000,
                         meta_filters={"breaking": True})
    assert [r["coin"] for r in seen["records"]] == ["A"]
    assert seen["records"][0]["side"] == "long"  # inverted after the filter
