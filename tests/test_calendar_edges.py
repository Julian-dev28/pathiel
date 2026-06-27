"""Tests for the live extreme_fade contract."""

from hermes_trader.agents.extreme_fade import FadeSignal, compute_signals as ef_compute


def _ef_cfg(enabled=True, crash_pct=-0.12):
    return {
        "extreme_fade": {
            "enabled": enabled,
            "crash_pct": crash_pct,
        }
    }


def _bars_with_last_ret(n, last_ret):
    closes = [100.0] * (n - 1) + [100.0 * (1 + last_ret)]
    return [
        {"t": i, "o": c, "h": c, "l": c, "c": c, "v": 1e6}
        for i, c in enumerate(closes)
    ]


def test_ef_big_down_triggers_long():
    cfg = _ef_cfg(crash_pct=-0.12)
    cbc = {"COIN_B": _bars_with_last_ret(5, -0.15)}
    sigs = ef_compute(cbc, cfg)
    assert len(sigs) == 1
    assert sigs[0].coin == "COIN_B"
    assert sigs[0].side == "long"


def test_ef_big_up_does_not_trigger():
    cfg = _ef_cfg(crash_pct=-0.12)
    cbc = {"COIN_A": _bars_with_last_ret(5, 0.15)}
    assert ef_compute(cbc, cfg) == []


def test_ef_no_signal_above_crash_threshold():
    cfg = _ef_cfg(crash_pct=-0.12)
    cbc = {"COIN_A": _bars_with_last_ret(5, -0.05)}
    assert ef_compute(cbc, cfg) == []


def test_ef_disabled_returns_empty():
    cfg = _ef_cfg(enabled=False)
    cbc = {"COIN_A": _bars_with_last_ret(5, -0.50)}
    assert ef_compute(cbc, cfg) == []


def test_ef_no_config_returns_empty():
    cbc = {"COIN_A": _bars_with_last_ret(5, -0.50)}
    assert ef_compute(cbc, {}) == []


def test_ef_multiple_crash_coins():
    cfg = _ef_cfg(crash_pct=-0.12)
    cbc = {
        "BIG_DN_A": _bars_with_last_ret(5, -0.20),
        "BIG_DN_B": _bars_with_last_ret(5, -0.18),
        "SMALL": _bars_with_last_ret(5, -0.05),
        "RALLY": _bars_with_last_ret(5, 0.20),
    }
    sigs = ef_compute(cbc, cfg)
    coins = {s.coin for s in sigs}
    assert coins == {"BIG_DN_A", "BIG_DN_B"}
    assert {s.side for s in sigs} == {"long"}


def test_ef_signal_has_prior_ret_and_threshold():
    cfg = _ef_cfg(crash_pct=-0.12)
    cbc = {"COIN_A": _bars_with_last_ret(5, -0.18)}
    sigs = ef_compute(cbc, cfg)
    assert len(sigs) == 1
    assert abs(sigs[0].prior_daily_ret + 0.18) < 0.001
    assert sigs[0].threshold_pct == 12.0


def test_ef_signal_is_fadesignal_instance():
    cfg = _ef_cfg(crash_pct=-0.12)
    cbc = {"COIN_A": _bars_with_last_ret(5, -0.20)}
    sigs = ef_compute(cbc, cfg)
    assert all(isinstance(s, FadeSignal) for s in sigs)
