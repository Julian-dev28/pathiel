"""services.accounts.env — state dir resolution."""
from __future__ import annotations

import os

from services.accounts import env


def test_state_dir_honors_pathiel_state_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    assert env.state_dir() == str(tmp_path)


def test_state_dir_falls_back_to_repo_root_without_env_var(monkeypatch):
    monkeypatch.delenv("PATHIEL_STATE_DIR", raising=False)
    result = env.state_dir()
    assert os.path.isdir(result)


def test_accounts_dir_and_paths_nest_under_state_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    assert env.accounts_dir() == str(tmp_path / "accounts")
    assert env.registry_path() == str(tmp_path / "accounts" / "accounts.json")
    assert env.vault_dir() == str(tmp_path / "accounts" / "keys")
    assert env.vault_secret_path() == str(tmp_path / "accounts" / ".vault_secret")


def test_state_dir_is_re_read_live_not_cached(monkeypatch, tmp_path):
    """Changing PATHIEL_STATE_DIR mid-process must take effect immediately —
    this is what lets a test (and services.accounts.credentials' cache
    fingerprint) rely on monkeypatch.setenv rather than a process restart."""
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path / "a"))
    first = env.state_dir()
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path / "b"))
    second = env.state_dir()
    assert first != second
