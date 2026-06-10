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


def test_parse_verdict_tags_ai_down():
    from hermes_trader.agents.research import parse_verdict
    failed = parse_verdict("", "BTC", {"mid": 100.0})
    assert failed["verdict"] == "PASS" and failed["ai_down"] is True
    ok = parse_verdict('{"verdict": "PASS", "confidence": 0.4}', "BTC", {"mid": 100.0})
    assert ok["ai_down"] is False


def test_override_blocked_when_ai_down(monkeypatch):
    """A whale-hinted failure-PASS must NOT be upgraded to a blind LONG."""
    from hermes_trader.agents import executor as ex
    monkeypatch.setattr(
        ex, "read_agent_config",
        lambda: {"mode": "LIVE", "enable_crypto": True, "whale_force_execute": True,
                 "override_requires_ai": True},
    )
    res = ex.maybe_execute({
        "id": "t1", "coin": "BTC", "verdict": "PASS", "confidence": 0.0,
        "whale_signal": True, "ai_down": True,
    })
    assert res["executed"] is False
    assert "override_blocked_ai_down" in res["reason"]
