"""Config v2 storage: pathiel.agents.config_store.{read_risk_profile,
write_risk_profile, migration_report}. See config_store.read_risk_profile's
docstring for the exact precedence (explicit "risk_profile" key > migration
> RiskProfile() defaults) and services/risk/migrate.py for why migration
never raises.
"""
from __future__ import annotations

import json
from pathlib import Path

from contracts.risk.models import RiskProfile
from pathiel.agents import config_store as cs

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_default_config_has_no_risk_profile_key():
    """DELIBERATE: see config_store.py's comment at the DEFAULT_CONFIG
    definition. If "risk_profile" ever gets added back to DEFAULT_CONFIG,
    merge_agent_config would inject a syntactically-valid v2 profile into
    EVERY config read through read_agent_config() -- permanently defeating
    migration for every pre-v2 config, since read_risk_profile only
    migrates when the key is ABSENT. This is the regression test for
    exactly that bug (caught manually via scripts/config_preset.py derive
    against a copy of the real $15.19 account's config, which silently
    used plain "auto" starter numbers instead of migrating its actual
    0.35 equity-fraction / 3x leverage settings)."""
    assert "risk_profile" not in cs.DEFAULT_CONFIG


def test_a_pristine_config_through_read_agent_config_still_migrates():
    """The actual end-to-end regression check: a config with no legacy
    risk keys and no "risk_profile" key, read through the REAL
    read_agent_config() (DEFAULT_CONFIG merge included, not a raw dict),
    must still go through migrate_legacy rather than silently finding a
    phantom v2 profile."""
    merged = cs.merge_agent_config(cs.DEFAULT_CONFIG, {})
    assert "risk_profile" not in merged
    rp = cs.read_risk_profile(merged)
    assert rp.preset == "auto"


def test_read_risk_profile_from_an_explicit_valid_key():
    cfg = {"risk_profile": {"preset": "pro", "risk_per_trade_pct": 0.5,
                            "daily_loss_limit_pct": None, "max_leverage": None,
                            "max_open_positions": None, "allow_min_order_bump": None,
                            "max_bumped_risk_pct": None}}
    rp = cs.read_risk_profile(cfg)
    assert rp.preset == "pro"
    assert rp.risk_per_trade_pct == 0.5


def test_read_risk_profile_falls_back_to_migration_when_key_absent():
    rp = cs.read_risk_profile({"leverage": 5, "equity_fraction_per_trade": 0.1})
    assert rp.preset == "auto"
    assert rp.max_leverage == 5.0


def test_read_risk_profile_falls_back_to_migration_on_malformed_value(caplog):
    cfg = {"risk_profile": {"preset": "not_a_real_preset"}}   # fails the Literal
    rp = cs.read_risk_profile(cfg)
    assert rp.preset == "auto"   # migrate_legacy's own default, not a crash


def test_read_risk_profile_malformed_non_dict_also_falls_back():
    rp = cs.read_risk_profile({"risk_profile": "not even an object"})
    assert rp.preset == "auto"


def test_read_risk_profile_defaults_to_read_agent_config_when_omitted(monkeypatch, tmp_path):
    cfg_path = tmp_path / "agent-config.json"
    cfg_path.write_text(json.dumps({
        "risk_profile": {"preset": "small", "risk_per_trade_pct": None,
                         "daily_loss_limit_pct": None, "max_leverage": None,
                         "max_open_positions": None, "allow_min_order_bump": None,
                         "max_bumped_risk_pct": None},
    }))
    monkeypatch.setattr(cs, "CONFIG_PATH", str(cfg_path))
    rp = cs.read_risk_profile()
    assert rp.preset == "small"


def test_write_risk_profile_is_atomic_and_round_trips(monkeypatch, tmp_path):
    cfg_path = tmp_path / "agent-config.json"
    cfg_path.write_text(json.dumps({"mode": "OFF", "some_other_key": 42}))
    monkeypatch.setattr(cs, "CONFIG_PATH", str(cfg_path))

    profile = RiskProfile(preset="standard", risk_per_trade_pct=1.5)
    cs.write_risk_profile(profile)

    on_disk = json.loads(cfg_path.read_text())
    assert on_disk["risk_profile"]["preset"] == "standard"
    assert on_disk["risk_profile"]["risk_per_trade_pct"] == 1.5
    # Nothing else in the file was disturbed.
    assert on_disk["mode"] == "OFF"
    assert on_disk["some_other_key"] == 42
    # No leftover .tmp file from the atomic write.
    assert not (tmp_path / "agent-config.json.tmp").exists()

    round_tripped = cs.read_risk_profile()
    assert round_tripped.model_dump() == profile.model_dump()


def test_migration_report_available_even_when_a_v2_profile_already_exists():
    """migration_report always explains what migrate_legacy WOULD do, even
    past the point read_risk_profile would just use the saved profile — a
    "what would changing this do" preview, not the read path itself."""
    cfg = {
        "risk_profile": {"preset": "pro", "risk_per_trade_pct": None,
                         "daily_loss_limit_pct": None, "max_leverage": None,
                         "max_open_positions": None, "allow_min_order_bump": None,
                         "max_bumped_risk_pct": None},
        "max_concurrent": 5,
    }
    report = cs.migration_report(cfg)
    assert any("max_concurrent(5)" in r for r in report)
    # And read_risk_profile on the SAME config still returns the saved "pro"
    # profile, proving migration_report did not become the read path.
    assert cs.read_risk_profile(cfg).preset == "pro"


def test_migration_report_defaults_to_read_agent_config_when_omitted(monkeypatch, tmp_path):
    cfg_path = tmp_path / "agent-config.json"
    cfg_path.write_text(json.dumps({"max_concurrent": 7}))
    monkeypatch.setattr(cs, "CONFIG_PATH", str(cfg_path))
    report = cs.migration_report()
    assert any("max_concurrent(7)" in r for r in report)


def test_regression_the_real_config_migrates_through_the_real_read_agent_config(monkeypatch, tmp_path):
    """THE regression test for the bug this fix caught: a COPY of Julien's
    real .agent-config.json (0.35 equity-fraction, 3x leverage, no
    "risk_profile" key), read through the ACTUAL
    pathiel.agents.config_store.read_agent_config() -- DEFAULT_CONFIG merge
    included, not a hand-built dict skipping it -- must still migrate to
    the account's OWN aggressive numbers (~15.75% risk per trade from its
    real settings), not silently fall back to the plain "starter" preset's
    generic 3%. Caught manually via `scripts/config_preset.py derive`
    before this test existed; this is what would have caught it in the gate
    suite instead."""
    cfg_path = tmp_path / "agent-config.json"
    cfg_path.write_text((REPO_ROOT / ".agent-config.json").read_text())
    monkeypatch.setattr(cs, "CONFIG_PATH", str(cfg_path))

    merged = cs.read_agent_config()
    assert "risk_profile" not in merged, (
        "DEFAULT_CONFIG must not inject a phantom v2 profile into the merge")
    rp = cs.read_risk_profile(merged)
    assert rp.preset == "auto"
    assert rp.risk_per_trade_pct is not None and rp.risk_per_trade_pct > 5.0, (
        f"expected the account's own aggressive migrated risk% (~15.75%), got "
        f"{rp.risk_per_trade_pct} -- migration silently did not fire")
    assert rp.max_open_positions == 3   # the real file's max_concurrent, carried over
    assert rp.daily_loss_limit_pct == 12.1   # the real file's max_daily_loss_pct=0.121
