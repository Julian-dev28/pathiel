from __future__ import annotations

import os

from services.research_mcp import state


def test_env_override_wins(monkeypatch, tmp_path):
    monkeypatch.setenv(state.ENV_STATE_DIR, str(tmp_path / "custom"))
    assert state.state_dir() == str(tmp_path / "custom")


def test_default_is_a_sibling_of_pathiel_home_not_inside_it(monkeypatch):
    monkeypatch.delenv(state.ENV_STATE_DIR, raising=False)
    d = state.state_dir()
    home = os.path.expanduser("~")
    assert d.startswith(os.path.join(home, ".pathiel-research"))
    assert os.path.join(home, ".pathiel", "") not in (d + os.sep)
    assert d != os.path.join(home, ".pathiel")


def test_state_dir_never_touches_the_filesystem(monkeypatch, tmp_path):
    """state_dir() only computes a path string; it must not create the
    directory or any file as a side effect."""
    target = tmp_path / "never-created"
    monkeypatch.setenv(state.ENV_STATE_DIR, str(target))
    state.state_dir()
    assert not target.exists()
