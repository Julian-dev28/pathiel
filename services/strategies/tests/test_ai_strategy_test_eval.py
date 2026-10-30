"""Offline gate test for services/strategies/evals/ai_strategy_test.py's
pure parts: the variant table, params_for, and run_variants/
evaluate_theses_dir on synthetic candles/theses. No network -- the eval's
own __main__ fetch path is a periodic/paid run, not a commit gate."""
from __future__ import annotations

import pytest

from pathiel.models.types import Candle
from services.strategies.backtest import data as D
from services.strategies.backtest.desk_tick_sim import live_params
from services.strategies.evals import ai_strategy_test as E

HOUR_MS = D.HOUR_MS
FIFTEEN_MIN_MS = 900_000
DAY_MS = D.DAY_MS


# ── variant_overrides / params_for ──────────────────────────────────────────

def test_variant_overrides_covers_live_a_b_and_a_plus_b():
    ov = E.variant_overrides()
    assert ov["live"] == {}
    assert ov["ai_levels"] == {"ai_levels": True}
    for t in E.CONVICTION_THRESHOLDS:
        key = f"conviction@{t:g}"
        assert ov[key]["conviction_leverage"] == (t, E.CONVICTION_HIGH_LEV, E.CONVICTION_LOW_LEV)
    combo = ov["ai_levels+conviction"]
    assert combo["ai_levels"] is True
    assert combo["conviction_leverage"][0] == E.DEFAULT_CONVICTION_THRESHOLD


def test_params_for_starts_from_live_params_and_applies_overrides():
    live = live_params()
    p = E.params_for({"ai_levels": True}, start_equity=64.0)
    assert p.ai_levels is True and p.starting_equity == 64.0
    assert (p.stop_buffer, p.target_offset_frac, p.target_mode, p.leverage, p.entry_mode, p.min_r) == \
        (live.stop_buffer, live.target_offset_frac, live.target_mode, live.leverage, live.entry_mode, live.min_r)


def test_params_for_raises_on_an_unknown_field():
    with pytest.raises(TypeError):
        E.params_for({"not_a_real_field": 1})


# ── run_variants / evaluate_theses_dir on synthetic data ───────────────────

def _1h(n, l=99.0, h=101.0):
    return [Candle(t=i * HOUR_MS, o=100.0, h=h, l=l, c=100.0, v=1.0) for i in range(n)]


def _15m(start_bar, n, o=99.3, h=99.4, l=99.2, c=99.3):
    return [Candle(t=(start_bar + i) * FIFTEEN_MIN_MS, o=o, h=h, l=l, c=c, v=1.0) for i in range(n)]


def _theses(confidence=0.80):
    """One long thesis at t=24h, entry 99.5 / stop 98.3 / target 102.0
    (after the 0.5% target offset: risk 1.0, reward 2.19, R ~= 2.19 --
    passes live_params()'s own 2.0R gate and 20x leverage gate), with a
    confidence reading for the conviction variants."""
    return [D.ThesisDirection(created_at_ms=24 * HOUR_MS, side="long", entry_px=99.5, stop_px=98.3,
                             invalidation_px=None, targets=[102.0], thesis_id="t1", confidence=confidence)]


def test_run_variants_returns_every_variant_with_a_per_day_row():
    c1h = _1h(40)
    c15 = (_15m(96, 1, 99.3, 99.4, 99.2, 99.3)        # bar at t=24h: rests/opens
          + _15m(97, 1, 99.3, 102.1, 99.2, 102.0))   # next bar: reaches the thesis's own target
    theses = _theses()
    start_ms, end_ms = E.thesis_window(c15, theses)
    rows = E.run_variants(c15, c1h, None, theses, start_ms, end_ms)
    assert set(rows) == set(E.variant_overrides())
    for name, row in rows.items():
        assert "daily_pct" in row and "final_equity" in row and row["overrides"] == E.variant_overrides()[name]


def test_ai_levels_variant_fills_at_the_thesis_target_not_the_structural_one():
    c1h = _1h(40)
    c15 = _15m(96, 1, 99.3, 99.4, 99.2, 99.3) + _15m(97, 1, 99.3, 102.1, 99.2, 102.0)
    theses = _theses()
    start_ms, end_ms = E.thesis_window(c15, theses)
    rows = E.run_variants(c15, c1h, None, theses, start_ms, end_ms)
    assert rows["live"]["trades"] == 0           # live's 24h-range target (101) is never reached by 102's bar alone...
    assert rows["ai_levels"]["trades"] >= 1       # ...but the thesis's own 102.0 target fills
    assert rows["ai_levels"]["final_equity"] > rows["live"]["final_equity"]


def test_conviction_variant_sizes_by_confidence_not_params_leverage():
    # A wide 24h range (98-102) so a market entry at the low edge pays
    # live_params()'s own 2.0R gate: stop 0.75 under 98 = risk 0.75,
    # target 102 * 0.995 - 98 = reward 3.49, R ~= 4.65.
    c1h = _1h(40, l=98.0, h=102.0)
    c15 = _15m(96, 1, 98.0, 98.1, 97.9, 98.0) + _15m(97, 1, 98.0, 101.6, 97.9, 101.5)
    high_conf = E.run_variants(c15, c1h, None, _theses(confidence=0.90), *E.thesis_window(c15, _theses()))
    low_conf = E.run_variants(c15, c1h, None, _theses(confidence=0.40), *E.thesis_window(c15, _theses()))
    t = f"conviction@{E.DEFAULT_CONVICTION_THRESHOLD:g}"
    # both use live_params()'s own stop/entry/target rule (ai_levels off here) -- only the
    # leverage differs (20x vs 15x, the floored low_lev), which must still show up as a
    # different ending equity once the (identically priced) trade closes.
    assert high_conf[t]["trades"] >= 1 and low_conf[t]["trades"] >= 1
    assert high_conf[t]["final_equity"] != low_conf[t]["final_equity"]


def test_evaluate_theses_dir_reports_a_note_for_a_missing_directory():
    c1h = _1h(40)
    c15 = _15m(96, 1)
    out = E.evaluate_theses_dir(c15, c1h, None, "/does/not/exist/ever")
    assert out["n_theses"] == 0 and "note" in out


def test_evaluate_theses_dir_runs_every_variant_for_a_real_directory(tmp_path):
    import json
    theses_dir = tmp_path / "theses"
    theses_dir.mkdir()
    (theses_dir / "t1.json").write_text(json.dumps({
        "id": "t1", "desk_id": "oil", "created_at": "1970-01-02T00:00:00Z",
        "expires_at": "1970-01-09T00:00:00Z", "horizon_days": 7,
        "call": {"side": "long", "instrument": "xyz:BRENTOIL", "one_liner": "x", "entry_px": 99.5,
                 "stop_px": 98.0, "targets": [102.0], "confidence": 0.8, "invalidation": "below 97"},
    }))
    c1h = _1h(40)
    c15 = _15m(96, 1, 99.3, 99.4, 99.2, 99.3) + _15m(97, 1, 99.3, 102.1, 99.2, 102.0)
    out = E.evaluate_theses_dir(c15, c1h, None, str(theses_dir))
    assert out["n_theses"] == 1
    assert set(out["variants"]) == set(E.variant_overrides())
