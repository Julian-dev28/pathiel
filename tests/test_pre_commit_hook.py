"""The pre-commit gate CLAUDE.md always claimed existed.

CLAUDE.md has said "Gate tests — run on every commit via pre-commit hook" since
the project started. No hook existed until 2026-08-29, so nothing was gating
anything and the claim was simply false. These tests keep it true.

The hook lives in scripts/hooks/ and is wired through core.hooksPath rather than
being copied into .git/hooks, so it is version-controlled, reviewable in a diff,
and survives a fresh clone.
"""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "scripts" / "hooks" / "pre-commit"


def test_the_hook_exists_and_is_executable():
    assert HOOK.exists(), "CLAUDE.md promises a pre-commit gate"
    assert os.stat(HOOK).st_mode & stat.S_IXUSR, "the hook is not executable"


def test_the_hook_is_valid_shell():
    subprocess.run(["bash", "-n", str(HOOK)], check=True,
                   capture_output=True)


def test_the_hook_runs_the_gate_suite():
    body = HOOK.read_text()
    assert "pytest" in body, "a pre-commit hook that does not run tests is decoration"
    assert "-x" in body, "fail fast — a slow hook is a hook people bypass"


def test_the_hook_refuses_to_commit_an_env_file():
    """The one failure a later commit cannot undo: a pushed key is a rotated
    key."""
    assert ".env" in HOOK.read_text()


def test_the_hook_checks_for_machine_bound_paths():
    assert "check_no_absolute_paths.py" in HOOK.read_text()


def test_the_hook_isolates_state_from_the_live_tree():
    """The root conftest loads the real .env.local, and a test that writes state
    must never touch the ledger the running system reads."""
    assert "HERMES_STATE_DIR" in HOOK.read_text()


def test_the_hook_does_not_offer_a_bypass():
    """--no-verify is banned by CLAUDE.md. The hook must not document a way
    around itself."""
    body = HOOK.read_text().lower()
    assert "skip:" not in body or "not with --no-verify" in body


def test_the_installer_uses_hookspath_not_a_copy():
    """A hook copied into .git/hooks is untracked, unreviewable in a diff, and
    lost on a fresh clone."""
    installer = (ROOT / "scripts" / "install_hooks.sh").read_text()
    assert "core.hooksPath" in installer
    assert "cp " not in installer


def test_claude_md_still_documents_the_gate():
    """If someone removes the hook, this points at the doc that would then be
    lying again."""
    md = (ROOT / "CLAUDE.md").read_text()
    assert "pre-commit hook" in md
