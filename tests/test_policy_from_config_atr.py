"""Regression: _policy_from_config() must read the nested dsl_exit.atr_stop block so a
post-blackout synthesized tracker gets the SAME ATR stop a fresh entry does (not the
fixed-stop fallback). Bug found by the 2026-06-29 config audit (Lane 3)."""
from hermes_trader.agents import dsl_exit
from hermes_trader.agents import config_store


_CFG = {
    "dsl_exit": {
        "max_loss_pct": 2.5,
        "atr_stop": {"enabled": True, "atr_mult": 1.5, "floor_pct": 1.0, "ceiling_pct": 2.5},
        "phase2_tiers": [{"pct_above_entry": 8.0, "retrace_threshold": 0.35}],
    }
}


def test_policy_from_config_reads_atr_stop(monkeypatch):
    monkeypatch.setattr(config_store, "read_agent_config", lambda: _CFG)
    pol = dsl_exit._policy_from_config()
    assert pol.atr_stop_enabled is True
    assert pol.atr_stop_mult == 1.5
    assert pol.atr_stop_floor_pct == 1.0
    assert pol.atr_stop_ceiling_pct == 2.5
    # and the rest still wired
    assert pol.max_loss_pct == 2.5
    assert pol.phase2_tiers and pol.phase2_tiers[0].pct_above_entry == 8.0


def test_policy_from_config_atr_disabled_when_block_absent(monkeypatch):
    monkeypatch.setattr(config_store, "read_agent_config", lambda: {"dsl_exit": {"max_loss_pct": 2.0}})
    pol = dsl_exit._policy_from_config()
    assert pol.atr_stop_enabled is dsl_exit.ExitPolicy.atr_stop_enabled  # class default (off)
