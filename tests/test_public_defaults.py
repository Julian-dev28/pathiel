"""Safe public defaults: which config a public install reads, which risk
profile it gets, and the one-time seed from config/public/agent-config.json.

A public install (PATHIEL_INSTALL=public) must never read the repo's tracked
.agent-config.json (the operator's 20x whole-equity config) and must size
with services.risk.presets.PUBLIC_DEFAULT_PROFILE when it has no profile of
its own. The operator path is pinned unchanged beside each public case.

Offline. Subprocesses use public_env (tests/conftest.py).

    .venv/bin/python -m pytest -q tests/test_public_defaults.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from contracts.risk.models import RiskProfile
from pathiel.agents import config_store as cs
from services.risk.presets import PUBLIC_DEFAULT_PROFILE

ROOT = Path(__file__).resolve().parents[1]
REPO_CONFIG = ROOT / ".agent-config.json"
TEMPLATE = ROOT / "config" / "public" / "agent-config.json"
CONFIG_PRESET = ROOT / "scripts" / "config_preset.py"

# The shape of the operator's live profile (the tracked .agent-config.json).
OPERATOR_PROFILE = {"preset": "auto", "risk_per_trade_pct": 100, "daily_loss_limit_pct": 100,
                    "max_leverage": 20, "max_open_positions": 1, "allow_min_order_bump": True,
                    "max_bumped_risk_pct": 100}


@pytest.fixture
def public(monkeypatch):
    monkeypatch.setenv("PATHIEL_INSTALL", "public")


@pytest.fixture
def operator(monkeypatch):
    monkeypatch.delenv("PATHIEL_INSTALL", raising=False)


def _repo_config_state():
    return (REPO_CONFIG.read_bytes(), REPO_CONFIG.stat().st_mtime_ns) if REPO_CONFIG.exists() else None


# ── read_risk_profile ─────────────────────────────────────────────────────────

def test_public_install_without_a_config_gets_the_public_profile(public, monkeypatch, tmp_path):
    monkeypatch.setattr(cs, "CONFIG_PATH", str(tmp_path / "missing" / ".agent-config.json"))
    assert cs.read_risk_profile() == PUBLIC_DEFAULT_PROFILE
    assert cs.read_risk_profile({}) == PUBLIC_DEFAULT_PROFILE
    assert cs.read_risk_profile() is not PUBLIC_DEFAULT_PROFILE, "a copy, never the shared object"


@pytest.mark.parametrize("bad", [
    {"max_leverage": 999},
    {"preset": "yolo"},
    {"risk_per_trade_pct": -1},
    {"unknown_field": 1},
])
def test_public_install_with_an_invalid_profile_gets_the_public_profile(public, bad):
    assert cs.read_risk_profile({"risk_profile": bad}) == PUBLIC_DEFAULT_PROFILE


def test_public_install_keeps_a_valid_profile_the_user_saved(public):
    mine = {"preset": "custom", "risk_per_trade_pct": 0.5, "daily_loss_limit_pct": 2.0,
            "max_leverage": 2.0, "max_open_positions": 1, "allow_min_order_bump": False,
            "max_bumped_risk_pct": 1.0}
    assert cs.read_risk_profile({"risk_profile": mine}) == RiskProfile(**mine)


def test_operator_without_a_profile_keeps_auto(operator, monkeypatch, tmp_path):
    monkeypatch.setattr(cs, "CONFIG_PATH", str(tmp_path / "missing" / ".agent-config.json"))
    assert cs.read_risk_profile() == RiskProfile()
    assert cs.read_risk_profile({}) == RiskProfile()


def test_operator_with_an_invalid_profile_still_migrates(operator):
    rp = cs.read_risk_profile({"risk_profile": {"max_leverage": 999}, "leverage": 5,
                               "equity_fraction_per_trade": 0.1})
    assert rp != PUBLIC_DEFAULT_PROFILE and rp.max_leverage == 5.0


def test_operator_shaped_profile_is_untouched(operator):
    rp = cs.read_risk_profile({"risk_profile": OPERATOR_PROFILE})
    assert rp.max_leverage == 20 and rp.risk_per_trade_pct == 100
    # The tracked file itself, read (never written) on the operator path.
    tracked = json.loads(REPO_CONFIG.read_text())
    assert cs.read_risk_profile(tracked).max_leverage == tracked["risk_profile"]["max_leverage"]


# ── CONFIG_PATH ───────────────────────────────────────────────────────────────

_PRINT_CONFIG_PATH = ("from pathiel.agents import config_store as cs; "
                      "import scripts.config_preset as cp; "
                      "print(cs.CONFIG_PATH); print(cp.CONFIG_FILE)")


def _config_paths(env):
    proc = subprocess.run([sys.executable, "-c", _PRINT_CONFIG_PATH], env=env, cwd=str(ROOT),
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.split()


def test_public_config_path_is_never_the_repo_file(public_env, tmp_path):
    home = tmp_path / "home"
    # PATHIEL_HOME set (the launcher's case) and unset (a bare public process).
    for env in (public_env(PATHIEL_AGENT_CONFIG_FILE=None),
                public_env(PATHIEL_AGENT_CONFIG_FILE=None, PATHIEL_HOME=None)):
        for path in _config_paths(env):
            assert path == str(home / ".pathiel" / ".agent-config.json")
            assert os.path.realpath(path) != os.path.realpath(REPO_CONFIG)


def test_operator_config_path_is_still_the_repo_file(public_env):
    env = public_env(PATHIEL_AGENT_CONFIG_FILE=None, PATHIEL_INSTALL=None)
    assert _config_paths(env) == [str(REPO_CONFIG), str(REPO_CONFIG)]


def test_the_variable_wins_on_both_installs(public_env, tmp_path):
    target = str(tmp_path / "x" / "cfg.json")
    for install in ("public", None):
        assert _config_paths(public_env(PATHIEL_AGENT_CONFIG_FILE=target,
                                        PATHIEL_INSTALL=install)) == [target, target]


# ── seed-public ───────────────────────────────────────────────────────────────

def _seed(env, *extra):
    return subprocess.run([sys.executable, str(CONFIG_PRESET), "seed-public", "--json", *extra],
                          env=env, capture_output=True, text=True, timeout=60)


def test_seed_public_writes_once_and_keeps_an_existing_file(public_env, tmp_path):
    target = tmp_path / "fresh" / "dir" / ".agent-config.json"
    env = public_env(PATHIEL_AGENT_CONFIG_FILE=str(target))

    first = _seed(env)
    assert first.returncode == 0, first.stdout + first.stderr
    assert json.loads(first.stdout)["status"] == "written"
    assert json.loads(target.read_text()) == json.loads(TEMPLATE.read_text())
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    written = target.read_bytes()

    second = _seed(env)
    assert second.returncode == 0
    assert json.loads(second.stdout)["status"] == "kept"
    assert target.read_bytes() == written

    # A file the user changed is kept byte for byte.
    target.write_text('{"risk_profile": {"preset": "pro"}}\n')
    before = target.read_bytes()
    third = _seed(env)
    assert third.returncode == 0 and json.loads(third.stdout)["status"] == "kept"
    assert target.read_bytes() == before


def test_seed_public_refuses_the_repo_config_and_an_unset_target(public_env, tmp_path):
    before = _repo_config_state()

    unset = _seed(public_env(PATHIEL_AGENT_CONFIG_FILE=None))
    assert unset.returncode == 2 and json.loads(unset.stdout)["status"] == "refused"
    empty = _seed(public_env(PATHIEL_AGENT_CONFIG_FILE=""))
    assert empty.returncode == 2

    repo = _seed(public_env(PATHIEL_AGENT_CONFIG_FILE=str(REPO_CONFIG)))
    assert repo.returncode == 2 and json.loads(repo.stdout)["status"] == "refused"

    # The same file through a symlink and through a non-normalized path.
    link = tmp_path / "link.json"
    link.symlink_to(REPO_CONFIG)
    assert _seed(public_env(PATHIEL_AGENT_CONFIG_FILE=str(link))).returncode == 2
    dotted = ROOT / "scripts" / ".." / ".agent-config.json"
    assert _seed(public_env(PATHIEL_AGENT_CONFIG_FILE=str(dotted))).returncode == 2

    assert _repo_config_state() == before, "the repo's .agent-config.json must never change"
    assert not any(tmp_path.glob("**/.agent-config.json"))


def _load_config_preset():
    spec = importlib.util.spec_from_file_location("pathiel_config_preset_under_test", CONFIG_PRESET)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("drift", [
    {"max_leverage": 20.0},
    {"risk_per_trade_pct": 2.0},
    {"max_open_positions": None},
    {"extra": 1},
])
def test_seed_public_refuses_a_template_that_drifted(monkeypatch, tmp_path, capsys, drift):
    template = json.loads(TEMPLATE.read_text())
    template["risk_profile"].update(drift)
    if drift.get("max_open_positions", 1) is None:
        template["risk_profile"].pop("max_open_positions")
    drifted = tmp_path / "template.json"
    drifted.write_text(json.dumps(template))
    target = tmp_path / "out" / ".agent-config.json"

    mod = _load_config_preset()
    monkeypatch.setattr(mod, "PUBLIC_TEMPLATE", drifted)
    monkeypatch.setenv("PATHIEL_AGENT_CONFIG_FILE", str(target))
    assert mod.cmd_seed_public(as_json=True) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "refused" and "PUBLIC_DEFAULT_PROFILE" in out["message"]
    assert not target.exists()


def test_seed_public_reports_a_failed_write(monkeypatch, tmp_path, capsys):
    """A read-only home: exit 1, nothing half-written. The launcher turns this
    into a warning and config_store's public fallback still applies."""
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        mod = _load_config_preset()
        monkeypatch.setenv("PATHIEL_AGENT_CONFIG_FILE", str(locked / ".agent-config.json"))
        assert mod.cmd_seed_public(as_json=True) == 1
        assert json.loads(capsys.readouterr().out)["status"] == "failed"
        assert not (locked / ".agent-config.json").exists()
    finally:
        locked.chmod(0o700)


def test_config_preset_derive_still_works(public_env, tmp_path):
    # Public: seeded config, so derive reads the public profile.
    target = tmp_path / "home" / ".pathiel" / ".agent-config.json"
    env = public_env(PATHIEL_AGENT_CONFIG_FILE=str(target))
    assert _seed(env).returncode == 0
    proc = subprocess.run([sys.executable, str(CONFIG_PRESET), "derive", "--equity", "100", "--json"],
                          env=env, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    limits = json.loads(proc.stdout)
    assert limits["profile"]["max_leverage"] == 3.0 and limits["max_open_positions"] == 1
    assert limits["risk_per_trade_usd"] == pytest.approx(1.0)

    # Operator: an operator-shaped config file resolves 20x, as before.
    op_cfg = tmp_path / "operator.json"
    op_cfg.write_text(json.dumps({"risk_profile": OPERATOR_PROFILE}))
    op_env = public_env(PATHIEL_INSTALL=None, PATHIEL_AGENT_CONFIG_FILE=str(op_cfg))
    proc = subprocess.run([sys.executable, str(CONFIG_PRESET), "derive", "--equity", "100", "--json"],
                          env=op_env, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["profile"]["max_leverage"] == 20.0
