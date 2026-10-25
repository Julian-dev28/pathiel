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
import uuid
from pathlib import Path
from typing import Dict


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


def _complete_env(**overrides: str) -> Dict[str, str]:
    base = {
        "HYPERLIQUID_WALLET_ADDRESS": FAKE_AGENT_WALLET,
        "HYPERLIQUID_MASTER_ADDRESS": FAKE_MASTER_ADDR,
        "HYPERLIQUID_PRIVATE_KEY": FAKE_PRIVATE_KEY,
        "PATHIEL_OPERATOR_TOKEN": FAKE_OPERATOR_TOKEN,
    }
    base.update(overrides)
    return base


def _git_repo(tmp_path: Path) -> Path:
    """An isolated throwaway git repo, deliberately NOT a descendant of this
    repo's own git state.

    Every call here passes `env=pf._git_subprocess_env()`: a bare `git init`
    inherits GIT_DIR / GIT_INDEX_FILE / GIT_WORK_TREE from the parent process,
    and when the parent process IS a git hook (scripts/hooks/pre-commit runs
    this exact suite), those point at the real repo-under-commit, not `repo`.
    Without this, `git init` here still (re)writes against the outer repo's
    git state, and every check_env_files_* call below inherits the same
    pollution — this is what made
    test_env_file_gitignored_and_untracked_passes flake: green run directly,
    red under `git commit` on 2026-10-17.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    env = pf._git_subprocess_env()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, env=env)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True, env=env)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, env=env)
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
    assert "PATHIEL_OPERATOR_TOKEN" in failed


def test_required_present_passes_with_complete_synthetic_env():
    findings = pf.check_required_present(_complete_env())
    assert _names_with_status(findings, "FAIL") == set()
    always = {s.name for s in pf.REGISTRY if s.required == "always"}
    assert always <= _names_with_status(findings, "PASS")


def test_no_llm_key_is_required_any_more():
    """2026-10-25: OPENROUTER_API_KEY left the registry with the AI brain
    module. A complete env with no LLM key of any kind passes, and the
    registry names no provider-conditional secret."""
    findings = pf.check_required_present(_complete_env())
    assert _statuses(findings) == {"PASS"}
    assert not any(s.name.startswith(("OPENROUTER", "BRAVE", "HYDROMANCER")) for s in pf.REGISTRY)
    assert {s.required for s in pf.REGISTRY} <= {"always", "optional", "local_only"}


# ── accounts vault awareness (services/accounts) ────────────────────────────────
# A fully-migrated-to-the-vault setup has NONE of the three HYPERLIQUID_*
# env vars below in .env.local at all -- preflight must recognise the
# connected vault account as satisfying them, never FAIL a correctly
# configured box. Every fixture here writes ONLY public fields (ids, labels,
# addresses) to a synthetic accounts.json, exactly what
# services.accounts.registry itself writes -- never a key, never the vault's
# encrypted keystore files, matching _vault_active_trade_account's own
# contract.

FAKE_VAULT_MAIN = "0xCCCC000000000000000000000000000000CCCC"
FAKE_VAULT_AGENT = "0xDDDD000000000000000000000000000000DDDD"


def _write_vault_registry(tmp_path: Path, *, active_id="acc_1", accounts=None) -> Path:
    import json
    state_dir = tmp_path / "state"
    accounts_dir = state_dir / "accounts"
    accounts_dir.mkdir(parents=True, exist_ok=True)
    if accounts is None:
        accounts = [{
            "id": "acc_1", "label": "Main", "main_address": FAKE_VAULT_MAIN,
            "agent_address": FAKE_VAULT_AGENT, "mode": "trade", "source": "wallet_sign",
            "created_at": "2026-10-17T12:00:00+00:00", "verified_at": "2026-10-17T12:00:00+00:00",
            "agent_name": "pathiel",
        }]
    (accounts_dir / "accounts.json").write_text(
        json.dumps({"version": 1, "rev": 1, "active_id": active_id, "accounts": accounts}))
    return state_dir


def test_required_present_satisfied_by_a_connected_vault_account_with_no_env_vars(tmp_path):
    state_dir = _write_vault_registry(tmp_path)
    env = {"PATHIEL_STATE_DIR": str(state_dir), "PATHIEL_OPERATOR_TOKEN": FAKE_OPERATOR_TOKEN}
    findings = pf.check_required_present(env)
    for name in ("HYPERLIQUID_WALLET_ADDRESS", "HYPERLIQUID_PRIVATE_KEY", "HYPERLIQUID_MASTER_ADDRESS"):
        matches = [f for f in findings if f.name == name]
        assert len(matches) == 1
        assert matches[0].status == "PASS"
        assert "vault" in matches[0].message.lower()


def test_required_present_still_fails_when_vault_account_is_watch_mode(tmp_path):
    state_dir = _write_vault_registry(tmp_path, accounts=[{
        "id": "acc_1", "label": "Whale", "main_address": FAKE_VAULT_MAIN, "agent_address": None,
        "mode": "watch", "source": "watch", "created_at": "2026-10-17T12:00:00+00:00",
        "verified_at": None, "agent_name": None,
    }])
    env = {"PATHIEL_STATE_DIR": str(state_dir), "PATHIEL_OPERATOR_TOKEN": FAKE_OPERATOR_TOKEN}
    findings = pf.check_required_present(env)
    assert "HYPERLIQUID_WALLET_ADDRESS" in _names_with_status(findings, "FAIL")


def test_required_present_ignores_a_missing_or_malformed_vault_file(tmp_path):
    env = {"PATHIEL_STATE_DIR": str(tmp_path / "nope"), "PATHIEL_OPERATOR_TOKEN": FAKE_OPERATOR_TOKEN}
    findings = pf.check_required_present(env)
    assert "HYPERLIQUID_WALLET_ADDRESS" in _names_with_status(findings, "FAIL")


def test_required_present_real_env_vars_still_win_over_the_vault(tmp_path):
    """An env var actually set always passes on its own merits -- the vault
    branch only ever fires for the gap, never overrides a real value."""
    state_dir = _write_vault_registry(tmp_path)
    env = _complete_env(PATHIEL_STATE_DIR=str(state_dir))
    findings = pf.check_required_present(env)
    match = [f for f in findings if f.name == "HYPERLIQUID_WALLET_ADDRESS"][0]
    assert match.status == "PASS"
    assert "vault" not in match.message.lower()


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


def test_agent_not_master_passes_for_a_vault_account_with_distinct_agent(tmp_path):
    state_dir = _write_vault_registry(tmp_path)
    findings = pf.check_master_not_agent({"PATHIEL_STATE_DIR": str(state_dir)})
    assert _statuses(findings) == {"PASS"}
    assert "vault" in findings[0].message.lower()


def test_agent_not_master_fails_for_a_corrupt_vault_account(tmp_path):
    state_dir = _write_vault_registry(tmp_path, accounts=[{
        "id": "acc_1", "label": "Corrupt", "main_address": FAKE_VAULT_MAIN,
        "agent_address": FAKE_VAULT_MAIN, "mode": "trade", "source": "wallet_sign",
        "created_at": "2026-10-17T12:00:00+00:00", "verified_at": "2026-10-17T12:00:00+00:00",
        "agent_name": "pathiel",
    }])
    findings = pf.check_master_not_agent({"PATHIEL_STATE_DIR": str(state_dir)})
    assert _statuses(findings) == {"FAIL"}


def test_agent_not_master_warns_for_a_vault_account_missing_agent_address(tmp_path):
    state_dir = _write_vault_registry(tmp_path, accounts=[{
        "id": "acc_1", "label": "Half-built", "main_address": FAKE_VAULT_MAIN, "agent_address": None,
        "mode": "trade", "source": "wallet_sign", "created_at": "2026-10-17T12:00:00+00:00",
        "verified_at": None, "agent_name": None,
    }])
    findings = pf.check_master_not_agent({"PATHIEL_STATE_DIR": str(state_dir)})
    assert _statuses(findings) == {"WARN"}


def test_agent_not_master_vault_check_takes_priority_over_env_vars(tmp_path):
    """A vault account active AND stale/mismatched env vars present: the
    vault account is what actually trades (services.accounts.credentials'
    own priority), so it is what gets checked."""
    state_dir = _write_vault_registry(tmp_path)
    env = _complete_env(PATHIEL_STATE_DIR=str(state_dir),
                        HYPERLIQUID_WALLET_ADDRESS=FAKE_MASTER_ADDR,
                        HYPERLIQUID_MASTER_ADDRESS=FAKE_MASTER_ADDR)  # would FAIL on its own
    findings = pf.check_master_not_agent(env)
    assert _statuses(findings) == {"PASS"}


# ── master private key must never be in a deployed environment ────────────────

def test_master_private_key_fails_in_deploy_mode():
    env = {"HYPERLIQUID_MASTER_PRIVATE_KEY": FAKE_PRIVATE_KEY}
    findings = pf.check_master_key_not_deployed(env, deploy_mode=True)
    assert _statuses(findings) == {"FAIL"}


def test_master_private_key_only_warns_locally():
    env = {"HYPERLIQUID_MASTER_PRIVATE_KEY": FAKE_PRIVATE_KEY}
    findings = pf.check_master_key_not_deployed(env, deploy_mode=False)
    assert _statuses(findings) == {"WARN"}


def test_master_private_key_warning_names_the_specific_risk_and_mitigation():
    """The task's exact wording: the main key can withdraw, an agent key
    makes it unnecessary for trading, and only treasury.py uses it."""
    env = {"HYPERLIQUID_MASTER_PRIVATE_KEY": FAKE_PRIVATE_KEY}
    findings = pf.check_master_key_not_deployed(env, deploy_mode=False)
    message = findings[0].message.lower()
    assert "withdraw" in message
    assert "agent key" in message
    assert "treasury.py" in message


def test_master_private_key_absent_passes_either_mode():
    for deploy_mode in (True, False):
        findings = pf.check_master_key_not_deployed({}, deploy_mode=deploy_mode)
        assert _statuses(findings) == {"PASS"}


def test_is_deploy_context_detects_platform_signals():
    # 2026-10-25: FLY_APP_NAME dropped with the Fly deploy target; k8s is the
    # one container platform left.
    assert pf.is_deploy_context({"FLY_APP_NAME": "pathiel"}) is False
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


def test_env_file_checks_are_immune_to_an_inherited_git_dir(tmp_path, monkeypatch):
    """Regression for the 2026-10-17 flake: green when run directly, red when
    run from inside scripts/hooks/pre-commit.

    A git hook's own subprocess inherits GIT_DIR (and friends) from the
    `git commit` that invoked it, pointing at the repo-under-commit. Every
    `git` call this module makes must still resolve against its OWN
    `cwd=repo_root` regardless of what GIT_DIR says — otherwise, run from
    inside a hook, these checks silently answer for the wrong repository.

    This repo (THIS_REPO) is the realistic stand-in for "whatever repo the
    hook's GIT_DIR happens to point at": it tracks `.env.local` nowhere and
    gitignores it everywhere (see .gitignore), the opposite of the isolated
    `repo` built below — so if GIT_DIR pollution ever leaks through again,
    this test fails with the exact wrong-repo answer instead of passing by
    coincidence.
    """
    this_repo = Path(__file__).resolve().parents[1]
    real_git_dir = subprocess.run(
        ["git", "rev-parse", "--absolute-git-dir"], cwd=this_repo,
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    monkeypatch.setenv("GIT_DIR", real_git_dir)
    monkeypatch.setenv("GIT_WORK_TREE", str(this_repo))

    # Isolated repo: .env.local IS tracked and NOT gitignored — both checks
    # must report FAIL. Answering for `this_repo` instead (tracks nothing,
    # gitignores everything) would flip both to PASS.
    repo = _git_repo(tmp_path)
    (repo / ".env.local").write_text("X=1\n")
    subprocess.run(["git", "add", ".env.local"], cwd=repo, check=True, env=pf._git_subprocess_env())

    gitignored = pf.check_env_files_gitignored(repo)
    not_tracked = pf.check_env_files_not_tracked(repo)
    assert _statuses(gitignored, "env_gitignored") == {"FAIL"}
    assert _statuses(not_tracked, "env_not_tracked") == {"FAIL"}


def test_git_index_scan_is_immune_to_an_inherited_git_dir(tmp_path, monkeypatch):
    """Same inherited-GIT_DIR hazard, for the one check whose entire job is
    catching a leaked secret in the git index. A false PASS here, caused by
    silently scanning the wrong repo, is the worst possible failure mode for
    a secrets scanner.

    Uses a freshly-generated uuid, NOT the shared FAKE_PRIVATE_KEY constant:
    that constant is itself a string literal inside this file, which is
    tracked in THIS repo — scanning the wrong repo would still "find" it
    there and the test would pass for the wrong reason. A uuid cannot
    coincidentally already be sitting in this repo's history.
    """
    this_repo = Path(__file__).resolve().parents[1]
    real_git_dir = subprocess.run(
        ["git", "rev-parse", "--absolute-git-dir"], cwd=this_repo,
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    monkeypatch.setenv("GIT_DIR", real_git_dir)
    monkeypatch.setenv("GIT_WORK_TREE", str(this_repo))

    unique_secret = f"unique-secret-{uuid.uuid4().hex}"
    repo = _git_repo(tmp_path)
    leaked = repo / "config_dump.py"
    leaked.write_text(f'API_KEY = "{unique_secret}"\n')
    subprocess.run(["git", "add", "config_dump.py"], cwd=repo, check=True, env=pf._git_subprocess_env())

    findings = pf.check_git_index_for_secrets(repo, {"HYPERLIQUID_PRIVATE_KEY": unique_secret})
    assert _statuses(findings) == {"FAIL"}


def test_env_file_not_gitignored_fails(tmp_path):
    repo = _git_repo(tmp_path)
    (repo / ".env.local").write_text("X=1\n")
    findings = pf.check_env_files_gitignored(repo)
    assert _statuses(findings, "env_gitignored") == {"FAIL"}


def test_env_file_tracked_in_git_fails(tmp_path):
    repo = _git_repo(tmp_path)
    # No .gitignore entry, so `git add` succeeds — exactly the accidental-add case.
    (repo / ".env.local").write_text("X=1\n")
    subprocess.run(["git", "add", ".env.local"], cwd=repo, check=True, env=pf._git_subprocess_env())
    findings = pf.check_env_files_not_tracked(repo)
    assert _statuses(findings, "env_not_tracked") == {"FAIL"}


# ── git index secret scan ──────────────────────────────────────────────────────

def test_git_index_scan_detects_staged_secret(tmp_path):
    repo = _git_repo(tmp_path)
    leaked = repo / "config_dump.py"
    leaked.write_text(f'API_KEY = "{FAKE_PRIVATE_KEY}"\n')
    subprocess.run(["git", "add", "config_dump.py"], cwd=repo, check=True, env=pf._git_subprocess_env())

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
    subprocess.run(["git", "add", "config_dump.py"], cwd=repo, check=True, env=pf._git_subprocess_env())
    leaked.write_text("API_KEY = None\n")  # working tree no longer shows it

    findings = pf.check_git_index_for_secrets(repo, {"HYPERLIQUID_PRIVATE_KEY": FAKE_PRIVATE_KEY})
    assert _statuses(findings) == {"FAIL"}


def test_git_index_scan_clean_repo_passes(tmp_path):
    repo = _git_repo(tmp_path)
    (repo / "readme.txt").write_text("nothing secret here\n")
    subprocess.run(["git", "add", "readme.txt"], cwd=repo, check=True, env=pf._git_subprocess_env())

    findings = pf.check_git_index_for_secrets(repo, {"HYPERLIQUID_PRIVATE_KEY": FAKE_PRIVATE_KEY})
    assert _statuses(findings) == {"PASS"}


def test_git_index_scan_skips_short_placeholder_values(tmp_path):
    repo = _git_repo(tmp_path)
    leaked = repo / "config_dump.py"
    leaked.write_text('TOKEN = "1234"\n')
    subprocess.run(["git", "add", "config_dump.py"], cwd=repo, check=True, env=pf._git_subprocess_env())

    # "1234" is below MIN_SECRET_SCAN_LEN — must not be scanned (would be noise).
    findings = pf.check_git_index_for_secrets(repo, {"SOME_TOKEN": "1234"})
    assert _statuses(findings) == {"PASS"}


def test_git_index_scan_non_git_dir_skips(tmp_path):
    findings = pf.check_git_index_for_secrets(tmp_path, {"X": "y" * 10})
    assert _statuses(findings) == {"SKIP"}


def test_index_scan_values_exclude_public_addresses():
    """A wallet address is public (on chain, and written into the desk-tick
    skill and README on purpose); only keys and tokens are leak material.
    2026-10-25: the master address in a tracked SKILL.md failed every run."""
    env = _complete_env()
    scanned = pf._index_scan_values(env)
    assert "HYPERLIQUID_PRIVATE_KEY" in scanned and "PATHIEL_OPERATOR_TOKEN" in scanned
    assert not any(name.endswith("_ADDRESS") for name in scanned)
    assert FAKE_MASTER_ADDR not in scanned.values() and FAKE_AGENT_WALLET not in scanned.values()


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
    subprocess.run(["git", "add", "."], cwd=repo, check=True, env=pf._git_subprocess_env())

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


# ── redaction (moved from tests/test_ai_brain_readiness.py, 2026-10-25) ───────

def test_redaction_covers_every_declared_secret_regardless_of_length():
    env = {spec.name: "x" * 6 for spec in pf.REGISTRY}
    vals = pf._redaction_values(env)
    assert all(v in vals for v in env.values()), (
        "a declared secret escaped redaction because it was short")


def test_redaction_leaves_short_non_secret_config_readable():
    """Redacting everything made findings useless: '[REDACTED]' in place of a
    port or a mode name says nothing. No credential is 4 chars long."""
    vals = pf._redaction_values({"PATHIEL_PORT": "8000"})
    assert "8000" not in vals


def test_redaction_still_catches_an_undeclared_long_value():
    """Something added to .env.local that nobody remembered to declare here must
    still never print."""
    secret = "s" * pf._UNDECLARED_SECRET_MIN_LEN
    assert secret in pf._redaction_values({"SOME_NEW_THING": secret})


def test_gate_sessions_never_load_wallet_private_keys():
    """The root conftest must not put .env.local private keys into a gate run;
    only PATHIEL_E2E=1 (the real-money suite) loads them."""
    import os
    if os.environ.get("PATHIEL_E2E") == "1":
        return
    for k, v in os.environ.items():
        if "PRIVATE_KEY" in k.upper() and v:
            # a test may set a FAKE key itself; the real ones are 66-char hex
            # loaded at session start, before any test runs
            assert k not in ("HYPERLIQUID_PRIVATE_KEY", "HYPERLIQUID_MASTER_PRIVATE_KEY"), k
