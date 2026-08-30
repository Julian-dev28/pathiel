"""Gate tests for scripts/preflight_secrets.py.

Deterministic, offline, tmp_path-backed. Every value used here is synthetic
and obviously fake — never a real Hyperliquid address/key, and never anything
read out of the repo's real .env.local. Tests must not assert on, print, or
otherwise surface the real secrets that the root conftest.py loads into
os.environ before collection; every test that touches process env explicitly
overrides the vars it cares about via monkeypatch rather than trusting
whatever the real environment happens to hold.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict

import pytest

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "preflight_secrets.py"
_SPEC = importlib.util.spec_from_file_location("preflight_secrets", _PATH)
assert _SPEC and _SPEC.loader
pf = importlib.util.module_from_spec(_SPEC)
# dataclasses (used throughout preflight_secrets.py) look their defining
# module up in sys.modules at class-creation time — register before exec.
sys.modules[_SPEC.name] = pf
_SPEC.loader.exec_module(pf)


# ── synthetic fixtures ─────────────────────────────────────────────────────────

FAKE_AGENT_WALLET = "0xAAAA000000000000000000000000000000AAAA"
FAKE_MASTER_ADDR = "0xBBBB000000000000000000000000000000BBBB"
FAKE_PRIVATE_KEY = "0xFAKEPRIVATEKEYNOTREAL0000000000000000000000000000000000000001"
FAKE_OPERATOR_TOKEN = "faketoken1234567890abcdef"
FAKE_OPENROUTER_KEY = "sk-or-fake0000000000000000000000000000"


def _complete_env(**overrides: str) -> Dict[str, str]:
    base = {
        "HYPERLIQUID_WALLET_ADDRESS": FAKE_AGENT_WALLET,
        "HYPERLIQUID_MASTER_ADDRESS": FAKE_MASTER_ADDR,
        "HYPERLIQUID_PRIVATE_KEY": FAKE_PRIVATE_KEY,
        "HERMES_OPERATOR_TOKEN": FAKE_OPERATOR_TOKEN,
        # openrouter + a fake key, NOT claude_cli. Selecting a CLI provider here
        # used to "sidestep the OPENROUTER_API_KEY requirement", but the brain
        # readiness check verifies the `claude` binary is on PATH — so the test
        # passed on a developer machine and failed on CI, which has no such
        # binary. openrouter's readiness is a pure env-var check with no host
        # dependency, which is what a fixture should assert against.
        "AI_BRAIN_PROVIDER": "openrouter",
        "OPENROUTER_API_KEY": FAKE_OPENROUTER_KEY,
    }
    base.update(overrides)
    return base


def _git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    return repo


def _statuses(findings, check=None):
    return {f.status for f in findings if check is None or f.check == check}


def _names_with_status(findings, status, check=None):
    return {f.name for f in findings if f.status == status and (check is None or f.check == check)}


# ── required-present ───────────────────────────────────────────────────────────

def test_required_present_fails_when_missing():
    findings = pf.check_required_present({})
    failed = _names_with_status(findings, "FAIL")
    assert "HYPERLIQUID_WALLET_ADDRESS" in failed
    assert "HYPERLIQUID_PRIVATE_KEY" in failed
    assert "HYPERLIQUID_MASTER_ADDRESS" in failed
    assert "HERMES_OPERATOR_TOKEN" in failed


def test_required_present_passes_with_complete_synthetic_env():
    findings = pf.check_required_present(_complete_env())
    assert _names_with_status(findings, "FAIL") == set()
    always = {s.name for s in pf.REGISTRY if s.required == "always"}
    assert always <= _names_with_status(findings, "PASS")


def test_openrouter_key_required_only_when_provider_is_openrouter():
    # provider unset -> defaults to openrouter -> key required. The key must be
    # cleared explicitly: _complete_env now supplies one by default so the
    # fixture does not depend on a CLI binary being installed on the host.
    findings_default = pf.check_required_present(
        _complete_env(AI_BRAIN_PROVIDER="", OPENROUTER_API_KEY=""))
    assert "OPENROUTER_API_KEY" in _names_with_status(findings_default, "FAIL")

    # provider explicitly claude_cli -> key not required, no finding for it at all
    findings_cli = pf.check_required_present(_complete_env(AI_BRAIN_PROVIDER="claude_cli"))
    assert "OPENROUTER_API_KEY" not in {f.name for f in findings_cli}

    # provider openrouter + key present -> passes
    findings_ok = pf.check_required_present(
        _complete_env(AI_BRAIN_PROVIDER="openrouter", OPENROUTER_API_KEY=FAKE_OPENROUTER_KEY)
    )
    assert "OPENROUTER_API_KEY" in _names_with_status(findings_ok, "PASS")


# ── agent vs master (the highest-value check) ──────────────────────────────────

def test_agent_not_master_fails_when_trading_key_is_master():
    env = _complete_env(HYPERLIQUID_WALLET_ADDRESS=FAKE_MASTER_ADDR,
                         HYPERLIQUID_MASTER_ADDRESS=FAKE_MASTER_ADDR)
    findings = pf.check_master_not_agent(env)
    assert _statuses(findings) == {"FAIL"}


def test_agent_not_master_fails_when_addresses_match_case_insensitively():
    env = _complete_env(HYPERLIQUID_WALLET_ADDRESS=FAKE_MASTER_ADDR.lower(),
                         HYPERLIQUID_MASTER_ADDRESS=FAKE_MASTER_ADDR.upper())
    findings = pf.check_master_not_agent(env)
    assert _statuses(findings) == {"FAIL"}


def test_agent_not_master_fails_when_master_address_unset():
    env = _complete_env(HYPERLIQUID_MASTER_ADDRESS="")
    findings = pf.check_master_not_agent(env)
    assert _statuses(findings) == {"FAIL"}


def test_agent_not_master_passes_for_distinct_agent_wallet():
    findings = pf.check_master_not_agent(_complete_env())
    assert _statuses(findings) == {"PASS"}


def test_agent_not_master_skips_when_wallet_unset():
    findings = pf.check_master_not_agent({})
    assert _statuses(findings) == {"SKIP"}


# ── master private key must never be in a deployed environment ────────────────

def test_master_private_key_fails_in_deploy_mode():
    env = {"HYPERLIQUID_MASTER_PRIVATE_KEY": FAKE_PRIVATE_KEY}
    findings = pf.check_master_key_not_deployed(env, deploy_mode=True)
    assert _statuses(findings) == {"FAIL"}


def test_master_private_key_only_warns_locally():
    env = {"HYPERLIQUID_MASTER_PRIVATE_KEY": FAKE_PRIVATE_KEY}
    findings = pf.check_master_key_not_deployed(env, deploy_mode=False)
    assert _statuses(findings) == {"WARN"}


def test_master_private_key_absent_passes_either_mode():
    for deploy_mode in (True, False):
        findings = pf.check_master_key_not_deployed({}, deploy_mode=deploy_mode)
        assert _statuses(findings) == {"PASS"}


def test_is_deploy_context_detects_platform_signals():
    assert pf.is_deploy_context({"FLY_APP_NAME": "hermes-trader"}) is True
    assert pf.is_deploy_context({"KUBERNETES_SERVICE_HOST": "10.0.0.1"}) is True
    assert pf.is_deploy_context({}) is False
    assert pf.is_deploy_context({}, forced=True) is True


# ── env file permissions ───────────────────────────────────────────────────────

def test_world_readable_env_file_fails(tmp_path):
    env_file = tmp_path / ".env.local"
    env_file.write_text("HYPERLIQUID_PRIVATE_KEY=" + FAKE_PRIVATE_KEY + "\n")
    os.chmod(env_file, 0o644)
    findings = pf.check_env_file_permissions(tmp_path)
    assert _statuses(findings, "env_file_permissions") == {"FAIL"}


def test_0600_env_file_passes(tmp_path):
    env_file = tmp_path / ".env.local"
    env_file.write_text("HYPERLIQUID_PRIVATE_KEY=" + FAKE_PRIVATE_KEY + "\n")
    os.chmod(env_file, 0o600)
    findings = pf.check_env_file_permissions(tmp_path)
    assert _statuses(findings, "env_file_permissions") == {"PASS"}


def test_group_readable_env_file_fails(tmp_path):
    env_file = tmp_path / ".env.local"
    env_file.write_text("X=1\n")
    os.chmod(env_file, 0o640)  # owner rw, group r — still a leak to any group member
    findings = pf.check_env_file_permissions(tmp_path)
    assert _statuses(findings, "env_file_permissions") == {"FAIL"}


def test_no_env_files_present_passes_permissions_check(tmp_path):
    findings = pf.check_env_file_permissions(tmp_path)
    assert _statuses(findings, "env_file_permissions") == {"PASS"}


# ── gitignore / tracked checks ─────────────────────────────────────────────────

def test_env_file_gitignored_and_untracked_passes(tmp_path):
    repo = _git_repo(tmp_path)
    (repo / ".gitignore").write_text(".env.local\n")
    (repo / ".env.local").write_text("X=1\n")
    findings = pf.check_env_files_gitignored(repo) + pf.check_env_files_not_tracked(repo)
    assert _statuses(findings) == {"PASS"}


def test_env_file_not_gitignored_fails(tmp_path):
    repo = _git_repo(tmp_path)
    (repo / ".env.local").write_text("X=1\n")
    findings = pf.check_env_files_gitignored(repo)
    assert _statuses(findings, "env_gitignored") == {"FAIL"}


def test_env_file_tracked_in_git_fails(tmp_path):
    repo = _git_repo(tmp_path)
    # No .gitignore entry, so `git add` succeeds — exactly the accidental-add case.
    (repo / ".env.local").write_text("X=1\n")
    subprocess.run(["git", "add", ".env.local"], cwd=repo, check=True)
    findings = pf.check_env_files_not_tracked(repo)
    assert _statuses(findings, "env_not_tracked") == {"FAIL"}


# ── git index secret scan ──────────────────────────────────────────────────────

def test_git_index_scan_detects_staged_secret(tmp_path):
    repo = _git_repo(tmp_path)
    leaked = repo / "config_dump.py"
    leaked.write_text(f'API_KEY = "{FAKE_PRIVATE_KEY}"\n')
    subprocess.run(["git", "add", "config_dump.py"], cwd=repo, check=True)

    findings = pf.check_git_index_for_secrets(repo, {"HYPERLIQUID_PRIVATE_KEY": FAKE_PRIVATE_KEY})
    fail = [f for f in findings if f.status == "FAIL"]
    assert len(fail) == 1
    assert fail[0].name == "HYPERLIQUID_PRIVATE_KEY"
    assert "config_dump.py" in fail[0].message


def test_git_index_scan_still_catches_secret_after_working_tree_revert(tmp_path):
    """The whole point of scanning --cached: a value staged then reverted in the
    working tree is still sitting in the index, exactly the accidental
    `git add .env.local` scenario the task description calls out."""
    repo = _git_repo(tmp_path)
    leaked = repo / "config_dump.py"
    leaked.write_text(f'API_KEY = "{FAKE_PRIVATE_KEY}"\n')
    subprocess.run(["git", "add", "config_dump.py"], cwd=repo, check=True)
    leaked.write_text("API_KEY = None\n")  # working tree no longer shows it

    findings = pf.check_git_index_for_secrets(repo, {"HYPERLIQUID_PRIVATE_KEY": FAKE_PRIVATE_KEY})
    assert _statuses(findings) == {"FAIL"}


def test_git_index_scan_clean_repo_passes(tmp_path):
    repo = _git_repo(tmp_path)
    (repo / "readme.txt").write_text("nothing secret here\n")
    subprocess.run(["git", "add", "readme.txt"], cwd=repo, check=True)

    findings = pf.check_git_index_for_secrets(repo, {"HYPERLIQUID_PRIVATE_KEY": FAKE_PRIVATE_KEY})
    assert _statuses(findings) == {"PASS"}


def test_git_index_scan_skips_short_placeholder_values(tmp_path):
    repo = _git_repo(tmp_path)
    leaked = repo / "config_dump.py"
    leaked.write_text('TOKEN = "1234"\n')
    subprocess.run(["git", "add", "config_dump.py"], cwd=repo, check=True)

    # "1234" is below MIN_SECRET_SCAN_LEN — must not be scanned (would be noise).
    findings = pf.check_git_index_for_secrets(repo, {"SOME_TOKEN": "1234"})
    assert _statuses(findings) == {"PASS"}


def test_git_index_scan_non_git_dir_skips(tmp_path):
    findings = pf.check_git_index_for_secrets(tmp_path, {"X": "y" * 10})
    assert _statuses(findings) == {"SKIP"}


# ── redaction ───────────────────────────────────────────────────────────────────

def test_redact_strips_known_secret_value():
    msg = f"oops leaked {FAKE_PRIVATE_KEY} in a log line"
    redacted = pf._redact(msg, [FAKE_PRIVATE_KEY])
    assert FAKE_PRIVATE_KEY not in redacted
    assert "[REDACTED]" in redacted


def test_redact_ignores_short_values_but_keeps_them_out_of_scan():
    # Short values are exempt from redaction (nothing under 4 chars is
    # touched) — deliberate, since redacting 2-3 char substrings would mangle
    # unrelated words. The secret registry's min-length gate is what keeps
    # short secrets out of harm's way in the first place.
    msg = "the count is 12"
    assert pf._redact(msg, ["12"]) == msg


# ── end-to-end: the checker must never emit a value ────────────────────────────

def test_main_never_prints_a_secret_value_even_on_a_detected_leak(tmp_path, monkeypatch, capsys):
    """Builds a repo where a fake secret IS leaked into a tracked file (so the
    FAIL path that names the leak fires), then asserts the raw secret value
    never appears in anything main() printed — only the variable NAME and the
    leaking file path do."""
    for spec in pf.REGISTRY:
        monkeypatch.delenv(spec.name, raising=False)
    for k, v in _complete_env().items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("HYPERLIQUID_MASTER_PRIVATE_KEY", raising=False)

    repo = _git_repo(tmp_path)
    (repo / ".gitignore").write_text(".env.local\n")
    leaked = repo / "oops_committed_config.py"
    leaked.write_text(f'HYPERLIQUID_PRIVATE_KEY = "{FAKE_PRIVATE_KEY}"\n')
    subprocess.run(["git", "add", "."], cwd=repo, check=True)

    env_file = tmp_path / "env_not_used.local"
    env_file.write_text("")  # everything comes from the monkeypatched process env

    rc = pf.main(["--repo-root", str(repo), "--env-file", str(env_file)])
    captured = capsys.readouterr()

    assert FAKE_PRIVATE_KEY not in captured.out
    assert FAKE_PRIVATE_KEY not in captured.err
    assert "HYPERLIQUID_PRIVATE_KEY" in captured.out
    assert "oops_committed_config.py" in captured.out
    assert rc == 1
    assert "PREFLIGHT: FAIL" in captured.out


def test_main_passes_end_to_end_with_a_clean_synthetic_repo(tmp_path, monkeypatch, capsys):
    for spec in pf.REGISTRY:
        monkeypatch.delenv(spec.name, raising=False)
    for k, v in _complete_env().items():
        monkeypatch.setenv(k, v)

    repo = _git_repo(tmp_path)
    (repo / ".gitignore").write_text(".env.local\n.env\n.env.old\n.env.local.old\n")

    env_file = tmp_path / ".env.local"
    env_file.write_text("")
    os.chmod(env_file, 0o600)

    rc = pf.main(["--repo-root", str(repo), "--env-file", str(env_file)])
    captured = capsys.readouterr()

    assert rc == 0
    assert "PREFLIGHT: PASS" in captured.out
    for v in (FAKE_PRIVATE_KEY, FAKE_OPERATOR_TOKEN):
        assert v not in captured.out


# ── env file parsing / precedence ──────────────────────────────────────────────

def test_parse_env_file_ignores_comments_and_blank_lines(tmp_path):
    env_file = tmp_path / ".env.local"
    env_file.write_text("# comment\n\nFOO=bar\nBAZ=\"quoted\"\n")
    parsed = pf.parse_env_file(env_file)
    assert parsed == {"FOO": "bar", "BAZ": "quoted"}


def test_parse_env_file_missing_file_returns_empty(tmp_path):
    assert pf.parse_env_file(tmp_path / "does_not_exist.local") == {}


def test_effective_env_process_env_wins_over_file(tmp_path):
    env_file = tmp_path / ".env.local"
    env_file.write_text("FOO=from_file\n")
    merged = pf.effective_env(env_file, process_env={"FOO": "from_process"})
    assert merged["FOO"] == "from_process"


def test_effective_env_file_fills_gaps_not_set_in_process(tmp_path):
    env_file = tmp_path / ".env.local"
    env_file.write_text("FOO=from_file\n")
    merged = pf.effective_env(env_file, process_env={})
    assert merged["FOO"] == "from_file"


# ── provider normalisation ──────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("", "openrouter"),
    ("openrouter", "openrouter"),
    ("open-router", "openrouter"),
    ("claude", "claude_cli"),
    ("claude_cli", "claude_cli"),
    ("codex", "codex_cli"),
    ("codex_cli", "codex_cli"),
    ("something_unknown", "openrouter"),
])
def test_effective_ai_brain_provider_normalisation(raw, expected):
    assert pf._effective_ai_brain_provider({"AI_BRAIN_PROVIDER": raw}) == expected
