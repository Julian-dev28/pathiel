"""Wiring proof for the dsl_exit.atr_stop feature.

Asserts the full path: ExitPolicy(atr_stop_*) + entry_atr_pct on the tracker
→ check() actually moves the Phase-1 stop. Two coins with different ATR get
different stops from the same multiplier; clamps and the ROE cap still bind;
disabled flag and missing ATR fall back to the fixed stop; state round-trips.
"""

from __future__ import annotations

import time

from hermes_trader.agents.dsl_exit import (
    DSLTracker,
    ExitPolicy,
    _tracker_from_dict,
    _tracker_to_dict,
)


def _policy(**kw) -> ExitPolicy:
    base = dict(max_loss_pct=3.5, max_loss_roe_pct=100.0, protect_pct=1.0,
                retrace_threshold=0.40, hard_timeout_minutes=99999.0,
                atr_stop_enabled=True, atr_stop_mult=1.5,
                atr_stop_floor_pct=1.0, atr_stop_ceiling_pct=4.0)
    base.update(kw)
    return ExitPolicy(**base)


def _stop_pct(tracker: DSLTracker, entry: float = 100.0) -> float:
    """Find the realized stop width by probing check() with falling marks."""
    for bps in range(1, 1200):  # probe 0.01% .. 12% below entry
        px = entry * (1 - bps / 10000)
        v = tracker.check(px)
        if v.exit:
            assert "max_loss" in v.reason
            return bps / 100
    raise AssertionError("no stop fired within 12%")


def test_different_atr_different_stop_same_mult():
    quiet = DSLTracker("QUIET", "long", 100.0, time.time(), _policy(),
                       leverage=1, entry_atr_pct=1.0)   # 1.5x1.0 = 1.5%
    wild = DSLTracker("WILD", "long", 100.0, time.time(), _policy(),
                      leverage=1, entry_atr_pct=2.0)    # 1.5x2.0 = 3.0%
    s_quiet, s_wild = _stop_pct(quiet), _stop_pct(wild)
    assert abs(s_quiet - 1.5) < 0.06
    assert abs(s_wild - 3.0) < 0.06
    assert s_wild > s_quiet  # same mult, wider stop on the wilder coin


def test_clamps_bind():
    tiny = DSLTracker("TINY", "long", 100.0, time.time(), _policy(),
                      leverage=1, entry_atr_pct=0.2)    # 0.3% -> floor 1.0%
    huge = DSLTracker("HUGE", "long", 100.0, time.time(), _policy(),
                      leverage=1, entry_atr_pct=10.0)   # 15% -> ceiling 4.0%
    assert abs(_stop_pct(tiny) - 1.0) < 0.06
    assert abs(_stop_pct(huge) - 4.0) < 0.06


def test_roe_cap_still_applies_on_top():
    # 3% ATR stop but ROE cap 18% at 10x = 1.8% spot — ROE cap must win.
    t = DSLTracker("LEV", "long", 100.0, time.time(),
                   _policy(max_loss_roe_pct=18.0), leverage=10,
                   entry_atr_pct=2.0)
    assert abs(_stop_pct(t) - 1.8) < 0.06


def test_disabled_or_missing_atr_falls_back_to_fixed():
    off = DSLTracker("OFF", "long", 100.0, time.time(),
                     _policy(atr_stop_enabled=False), leverage=1,
                     entry_atr_pct=2.0)
    no_atr = DSLTracker("NOATR", "long", 100.0, time.time(), _policy(),
                        leverage=1, entry_atr_pct=0.0)
    assert abs(_stop_pct(off) - 3.5) < 0.06
    assert abs(_stop_pct(no_atr) - 3.5) < 0.06


def test_entry_atr_pct_survives_state_roundtrip():
    t = DSLTracker("RT", "short", 50.0, time.time(), _policy(),
                   leverage=3, entry_atr_pct=2.34)
    t2 = _tracker_from_dict(_tracker_to_dict(t))
    assert t2.entry_atr_pct == 2.34
    assert t2.policy.atr_stop_enabled is True
    assert t2.policy.atr_stop_mult == 1.5


def test_noise_band_policy_survives_state_roundtrip():
    pol = _policy(noise_band_enabled=True, noise_band_atr_mult=1.75,
                  consecutive_breaches_required=2)
    t = DSLTracker("NB", "long", 100.0, time.time(), pol,
                   leverage=2, entry_atr_pct=1.2)
    t2 = _tracker_from_dict(_tracker_to_dict(t))
    assert t2.policy.noise_band_enabled is True
    assert t2.policy.noise_band_atr_mult == 1.75
    assert t2.policy.consecutive_breaches_required == 2


def test_parse_verdict_tags_ai_down():
    from hermes_trader.agents.research import parse_verdict
    failed = parse_verdict("", "BTC", {"mid": 100.0})
    assert failed["verdict"] == "PASS" and failed["ai_down"] is True
    ok = parse_verdict('{"verdict": "PASS", "confidence": 0.4}', "BTC", {"mid": 100.0})
    assert ok["ai_down"] is False


def test_ta_sidestep_blocked_when_ai_down(monkeypatch):
    """A TA-sidestep failure-PASS must NOT be upgraded to a blind LONG."""
    from hermes_trader.agents import executor as ex
    monkeypatch.setattr(
        ex, "read_agent_config",
        lambda: {"mode": "LIVE", "enable_crypto": True,
                 "ta_sidestep_force_execute": True,
                 "runner_entry_gate": {"min_composite": 30.0}},
    )
    res = ex.maybe_execute({
        "id": "t1", "coin": "BTC", "verdict": "PASS", "confidence": 0.0,
        "composite_score": 35.0, "ai_down": True,
    })
    assert res["executed"] is False
    assert "override_blocked_ai_down" in res["reason"]


def test_loss_cooldown_blocks_reentry(monkeypatch):
    """A coin with an active loss cooldown must be refused before any order."""
    from hermes_trader.agents import executor as ex
    import time as _t
    monkeypatch.setattr(
        ex, "read_agent_config",
        lambda: {"mode": "LIVE", "enable_crypto": True, "loss_cooldown_min": 180},
    )
    # Never flush test cooldowns into the LIVE .agent-memory.json (this test
    # once armed a real 60min TON cooldown in production state).
    monkeypatch.setattr(ex.memory, "flush", lambda: None)
    ex.memory.set_loss_cooldown("TON", int(_t.time() * 1000 + 60 * 60_000))
    try:
        res = ex.maybe_execute({
            "id": "t2", "coin": "TON", "verdict": "LONG", "side": "long",
            "confidence": 0.9,
        })
        assert res["executed"] is False
        assert "loss_cooldown" in res["reason"]
    finally:
        ex.memory._cooldowns.pop("TON", None)


def test_degraded_read_filter_protects_daily_pnl(monkeypatch):
    """A >25% equity spike within 180s must be IGNORED (partial-dex read);
    the same value re-asserted after 180s must be ACCEPTED (real move)."""
    from hermes_trader.agents.memory import AgentMemory
    m = AgentMemory()
    monkeypatch.setattr(m, "flush", lambda: None)
    m._initialized = True
    m.track_daily_pnl(100.0)          # baseline: SOD=100
    m.track_daily_pnl(99.0)           # normal tick: dailyPnl=-1
    assert round(m.get_daily_pnl(), 2) == -1.0
    m.track_daily_pnl(59.7)           # phantom: -40% in seconds -> ignored
    assert round(m.get_daily_pnl(), 2) == -1.0  # unchanged, kill-switch safe
    m.track_daily_pnl(59.7, force_accept=True)  # confirmed vanished tracker/liquidation
    assert round(m.get_daily_pnl(), 2) == -40.3
    m._last_eq_reading_ts -= 200
    m.track_daily_pnl(99.0)           # reset to plausible level for sustained check below
    m._last_eq_reading_ts -= 200      # pretend 200s passed -> now plausible
    m.track_daily_pnl(59.7)           # sustained -> accepted
    assert round(m.get_daily_pnl(), 2) == -40.3


def test_stale_flat_timeout_cuts_drifters_spares_peakers():
    """8h below protect -> cut; ever-peaked positions exempt; 0=off."""
    import time as _t
    old = _t.time() - 9 * 3600  # 9h ago
    pol = _policy(stale_flat_timeout_minutes=480.0)
    drifter = DSLTracker("DRIFT", "long", 100.0, old, pol, leverage=1,
                         entry_atr_pct=1.0)
    v = drifter.check(99.0)  # never peaked above protect
    assert v.exit and "stale_flat_timeout" in v.reason
    peaker = DSLTracker("PEAK", "long", 100.0, old, pol, leverage=1,
                        entry_atr_pct=1.0)
    peaker.check(102.0)      # armed phase-2 historically
    v2 = peaker.check(100.5)
    assert not (v2.exit and "stale_flat" in v2.reason)
    off = DSLTracker("OFF2", "long", 100.0, old, _policy(), leverage=1,
                     entry_atr_pct=1.0)
    v3 = off.check(99.0)
    assert "stale_flat" not in (v3.reason or "")
