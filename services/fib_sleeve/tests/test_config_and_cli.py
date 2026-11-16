"""Config validation, the override file, the cron entry point and the eval's
own pass rule. No network: every venue read is made to raise."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from services.fib_sleeve import config as C
from services.fib_sleeve.evals import live_dry_run as E
from services.fib_sleeve.store import Store

ROOT = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location("fib_sleeve_tick", ROOT / "scripts" / "fib_sleeve_tick.py")
CLI = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(CLI)


@pytest.fixture(autouse=True)
def no_network_no_env(tmp_path, monkeypatch):
    empty = tmp_path / "empty.env"
    empty.write_text("")
    monkeypatch.setenv("PATHIEL_ENV_FILE", str(empty))     # the script loads this and nothing else
    from pathiel.client import exchange, hl_client

    def refuse(*a, **k):
        raise AssertionError("a gate test reached for the network")
    monkeypatch.setattr(hl_client, "_http_post", refuse)
    monkeypatch.setattr(hl_client, "_http_post_strict", refuse)
    monkeypatch.setattr(exchange, "_http_post", refuse)
    monkeypatch.setattr(exchange, "_make_exchange", refuse)


def test_defaults_load_disabled_with_no_override_file():
    cfg = C.load()
    assert cfg == C.SleeveConfig() and cfg.enabled is False
    assert cfg.swing == "range24"                              # the live default since 2026-11-16
    assert (cfg.zigzag_k, cfg.level, cfg.direction, cfg.stop, cfg.target, cfg.ratchet) == (
        2.0, 0.382, "with", "atr1", 0.272, "be50")
    assert (cfg.risk_frac, cfg.leverage, cfg.min_r, cfg.max_consecutive_losses, cfg.max_drawdown_frac) == (
        0.06, 20, 2.0, 3, 0.20)


def test_swing_can_switch_to_zigzag_via_override(state_dir):
    cfg = C.write_override({"swing": "zigzag"})
    assert cfg.swing == "zigzag"


def test_override_file_is_validated_and_merged(state_dir):
    cfg = C.write_override({"enabled": True})
    assert cfg.enabled and C.load().enabled
    assert json.loads((state_dir / "fib_sleeve" / "config.json").read_text()) == {"enabled": True}
    assert C.write_override({"risk_frac": 0.03}).enabled is True          # merged, not replaced


@pytest.mark.parametrize("bad", [
    {"enabeld": True}, {"enabled": "yes"}, {"level": 0.4}, {"zigzag_k": 2.5}, {"risk_frac": 0.10},
    {"leverage": 25}, {"min_r": 1.5}, {"max_drawdown_frac": 0.5}, {"account": "0x123"}, {"ratchet": "fib75"},
    {"entry": "confirm"}, {"direction": "thesis"}, {"target": 1.0}, {"stop": "wide"}, {"swing": "vwap"},
])
def test_untested_or_misspelled_settings_are_refused_and_nothing_is_written(bad, state_dir):
    with pytest.raises(C.ConfigError):
        C.write_override(bad)
    assert not (state_dir / "fib_sleeve").exists()


def test_unreadable_override_stops_the_sleeve(state_dir):
    (state_dir / "fib_sleeve").mkdir()
    (state_dir / "fib_sleeve" / "config.json").write_text("{not json")
    with pytest.raises(C.ConfigError):
        C.load()
    assert CLI.main(["--live"]) == 2


def test_cli_live_while_disabled_does_nothing(capsys, state_dir):
    assert CLI.main(["--live"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["action"] == "disabled" and out["enabled"] is False
    assert not (state_dir / "fib_sleeve").exists()                        # no state, no log


def test_cli_enable_disable_rearm_place_nothing(capsys, state_dir):
    assert CLI.main(["--enable"]) == 0
    assert json.loads(capsys.readouterr().out)["enabled"] is True and C.load().enabled
    assert CLI.main(["--disable"]) == 0
    assert json.loads(capsys.readouterr().out)["enabled"] is False and not C.load().enabled
    store = Store(C.sleeve_dir())
    store.save({"epoch_ms": 1, "starting_equity_usd": 20.0, "kill": {"reason": "3 consecutive losses"}})
    assert CLI.main(["--rearm"]) == 0
    assert json.loads(capsys.readouterr().out)["cleared"]["kill"]["reason"] == "3 consecutive losses"
    assert store.load() == {}


def test_cli_modes_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        CLI.main(["--live", "--dry-run"])


# ── the eval's pass rule ──────────────────────────────────────────────────

GOOD = {"mode": "dry_run", "action": "would_place", "account_state": {"equity": 20.06},
        "plan": {"entry": 100.0, "stop": 99.0, "target": 103.0},
        "bracket": {"r_multiple": 3.0}, "size": {"risk_usd": 1.2, "equity": 20.06, "notional": 300.0}}


def test_eval_passes_a_sound_decision_and_a_no_order():
    cfg = C.SleeveConfig()
    assert E.check(GOOD, cfg, []) == []
    assert E.check({"mode": "dry_run", "action": "no_order", "account_state": {"equity": 20.06},
                    "plan": {"reason": "no_swing"}}, cfg, []) == []


def test_eval_fails_on_signing_attempts_errors_and_broken_limits():
    cfg = C.SleeveConfig()
    assert E.check(GOOD, cfg, ["set_leverage"])
    assert E.check({"mode": "dry_run", "action": "error", "why": "x"}, cfg, [])
    assert E.check({**GOOD, "mode": "live"}, cfg, [])
    assert E.check({**GOOD, "bracket": {"r_multiple": 1.5}}, cfg, [])
    assert E.check({**GOOD, "size": {"risk_usd": 1.3, "equity": 20.06, "notional": 300.0}}, cfg, [])
    assert E.check({**GOOD, "size": {"risk_usd": 1.2, "equity": 20.06, "notional": 500.0}}, cfg, [])
    assert E.check({**GOOD, "plan": {"entry": 98.0, "stop": 99.0, "target": 103.0}}, cfg, [])


def test_counting_facade_records_a_reach_for_a_signing_function():
    class Inner:
        def set_leverage(self):
            return 1

        def get_hl_price(self):
            return 2
    attempts = []
    c = E.Counting(Inner(), attempts)
    assert c.get_hl_price() == 2 and attempts == []
    c.set_leverage()
    assert attempts == ["set_leverage"]
