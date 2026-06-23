"""Tests for day_of_week_tilt and extreme_fade (agents/).

Covers:
- tilt_scalar: Monday boost, Thursday reduction, neutral other days
- tilt_scalar: disabled → always 1.0; clamped to [0.5, 1.5]
- extreme_fade.compute_signals: fires when |ret| > threshold, correct side
- extreme_fade.compute_signals: disabled → empty list
- extreme_fade: no signal below threshold
"""
from hermes_trader.agents.day_of_week_tilt import tilt_scalar, current_tilt_info
from hermes_trader.agents.extreme_fade import compute_signals as ef_compute, FadeSignal


# ── day_of_week_tilt ──────────────────────────────────────────────────────────

def _dow_cfg(enabled=True, monday=1.15, thursday=0.75):
    return {"day_of_week_tilt": {"enabled": enabled,
                                 "monday_scalar": monday,
                                 "thursday_scalar": thursday}}


def test_tilt_monday_boost():
    """Monday (weekday=0) → scalar > 1.0."""
    cfg = _dow_cfg(enabled=True)
    s = tilt_scalar(cfg, weekday=0)
    assert s > 1.0


def test_tilt_thursday_reduction():
    """Thursday (weekday=3) → scalar < 1.0."""
    cfg = _dow_cfg(enabled=True)
    s = tilt_scalar(cfg, weekday=3)
    assert s < 1.0


def test_tilt_neutral_on_other_days():
    """Tuesday/Wednesday/Friday/Sat/Sun → scalar == 1.0."""
    cfg = _dow_cfg(enabled=True)
    for wd in [1, 2, 4, 5, 6]:
        assert tilt_scalar(cfg, weekday=wd) == 1.0, f"weekday {wd} should be neutral"


def test_tilt_disabled_always_one():
    """When enabled=False, all days return 1.0 regardless of configured scalars."""
    cfg = _dow_cfg(enabled=False)
    for wd in range(7):
        assert tilt_scalar(cfg, weekday=wd) == 1.0


def test_tilt_clamped_upper():
    """Scalar is capped at 1.5 even if monday_scalar is set very high."""
    cfg = _dow_cfg(enabled=True, monday=99.0)
    assert tilt_scalar(cfg, weekday=0) == 1.5


def test_tilt_clamped_lower():
    """Scalar is floored at 0.5 even if thursday_scalar is set very low."""
    cfg = _dow_cfg(enabled=True, thursday=-99.0)
    assert tilt_scalar(cfg, weekday=3) == 0.5


def test_tilt_no_config_neutral():
    """Empty config → tilt_scalar returns 1.0 (safe default)."""
    assert tilt_scalar({}, weekday=0) == 1.0
    assert tilt_scalar({}, weekday=3) == 1.0


def test_current_tilt_info_returns_dict():
    """current_tilt_info returns a dict with the expected keys."""
    cfg = _dow_cfg(enabled=True)
    info = current_tilt_info(cfg)
    assert "weekday_name" in info
    assert "tilt_scalar" in info
    assert "enabled" in info
    assert info["enabled"] is True


# ── extreme_fade ──────────────────────────────────────────────────────────────

def _ef_cfg(enabled=True, threshold=12.0, shadow=True):
    return {"extreme_fade": {"enabled": enabled, "threshold_pct": threshold,
                             "shadow_mode": shadow}}


def _bars_with_last_ret(n, last_ret):
    """Build bar list where the LAST daily return is last_ret."""
    closes = [100.0] * (n - 1) + [100.0 * (1 + last_ret)]
    return [{"t": i, "o": c, "h": c, "l": c, "c": c, "v": 1e6}
            for i, c in enumerate(closes)]


def test_ef_big_up_triggers_short():
    """A +15% prior day should produce a SHORT fade signal."""
    cfg = _ef_cfg(enabled=True, threshold=12.0)
    cbc = {"COIN_A": _bars_with_last_ret(5, 0.15)}
    sigs = ef_compute(cbc, cfg)
    assert len(sigs) == 1
    assert sigs[0].coin == "COIN_A"
    assert sigs[0].side == "short"


def test_ef_big_down_triggers_long():
    """A -15% prior day should produce a LONG fade signal."""
    cfg = _ef_cfg(enabled=True, threshold=12.0)
    cbc = {"COIN_B": _bars_with_last_ret(5, -0.15)}
    sigs = ef_compute(cbc, cfg)
    assert len(sigs) == 1
    assert sigs[0].side == "long"


def test_ef_no_signal_below_threshold():
    """A 5% move with 12% threshold → no signal."""
    cfg = _ef_cfg(enabled=True, threshold=12.0)
    cbc = {"COIN_A": _bars_with_last_ret(5, 0.05)}
    sigs = ef_compute(cbc, cfg)
    assert sigs == []


def test_ef_disabled_returns_empty():
    """enabled=False → always empty list."""
    cfg = _ef_cfg(enabled=False)
    cbc = {"COIN_A": _bars_with_last_ret(5, 0.50)}  # 50% move
    sigs = ef_compute(cbc, cfg)
    assert sigs == []


def test_ef_no_config_returns_empty():
    """Missing config block → disabled → empty."""
    cbc = {"COIN_A": _bars_with_last_ret(5, 0.50)}
    assert ef_compute(cbc, {}) == []


def test_ef_multiple_coins():
    """Multiple eligible coins all return separate signals."""
    cfg = _ef_cfg(enabled=True, threshold=12.0)
    cbc = {
        "BIG_UP": _bars_with_last_ret(5, 0.20),
        "BIG_DN": _bars_with_last_ret(5, -0.18),
        "SMALL":  _bars_with_last_ret(5, 0.05),   # below threshold
    }
    sigs = ef_compute(cbc, cfg)
    coins = {s.coin for s in sigs}
    assert "BIG_UP" in coins
    assert "BIG_DN" in coins
    assert "SMALL" not in coins


def test_ef_signal_has_prior_ret():
    """FadeSignal.prior_daily_ret should reflect the actual last daily return."""
    cfg = _ef_cfg(enabled=True, threshold=12.0)
    cbc = {"COIN_A": _bars_with_last_ret(5, 0.18)}
    sigs = ef_compute(cbc, cfg)
    assert len(sigs) == 1
    assert abs(sigs[0].prior_daily_ret - 0.18) < 0.001


def test_ef_signal_is_fadesginal_instance():
    cfg = _ef_cfg(enabled=True, threshold=12.0)
    cbc = {"COIN_A": _bars_with_last_ret(5, -0.20)}
    sigs = ef_compute(cbc, cfg)
    assert all(isinstance(s, FadeSignal) for s in sigs)
