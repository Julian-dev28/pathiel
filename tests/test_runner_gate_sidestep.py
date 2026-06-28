"""The TA-sidestep confidence exemption in the runner entry gate.

Audit (2026-06-28): the sidestep upgrades an AI-PASSed but TA-confirmed breakout to
LONG @ min_ai_confidence (0.62), then the runner gate re-blocked it on min_confidence
(0.65/0.67) — 54 rescues all blocked while the coins ran +21-75%. The exemption lets a
sidestep override clear the CONF check; structure/composite checks still apply.
"""
from hermes_trader.agents.executor import _runner_entry_block_reason


def _cfg(**gate):
    g = {"enabled": True, "min_confidence": 0.65, "min_composite": 30.0,
         "min_crypto_composite": 20.0, "sidestep_exempt_conf": True}
    g.update(gate)
    return {"runner_entry_gate": g}


def _breakout_long(conf, sidestep):
    # fresh impulse (volume + breakout) + composite >= floor → passes everything but conf
    return {"coin": "PURR", "side": "long", "confidence": conf, "composite_score": 35.0,
            "volume_spike_fired": True, "breakout_fired": True, "sidestep_override": sidestep}


def test_sidestep_breakout_exempt_from_conf_floor():
    # conf 0.62 < gate 0.65 BUT sidestep override → not blocked on confidence
    r = _runner_entry_block_reason(_breakout_long(0.62, True), _cfg())
    assert "confidence" not in r   # passes (or blocks on something else, not conf)
    assert r == ""


def test_non_sidestep_still_blocked_on_conf():
    r = _runner_entry_block_reason(_breakout_long(0.62, False), _cfg())
    assert r.startswith("runner_gate_blocked (confidence")


def test_exemption_reversible_via_flag():
    # turn the exemption off → sidestep is blocked again
    r = _runner_entry_block_reason(_breakout_long(0.62, True), _cfg(sidestep_exempt_conf=False))
    assert r.startswith("runner_gate_blocked (confidence")


def test_sidestep_still_subject_to_structure_checks():
    # a sidestep with NO fresh impulse + weak crypto composite is still gated by composite,
    # i.e. the exemption is conf-only, not a free pass
    a = {"coin": "PURR", "side": "long", "confidence": 0.62, "composite_score": 10.0,
         "volume_spike_fired": True, "breakout_fired": True, "sidestep_override": True}
    r = _runner_entry_block_reason(a, _cfg(min_crypto_composite=20.0))
    assert r != "" and "confidence" not in r   # still blocked (on structure), not waved through
