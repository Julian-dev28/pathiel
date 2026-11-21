"""Every sleeve test gets its own throwaway PATHIEL_STATE_DIR: the override
file, the kill-switch state and the tick log never land in the live one."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def state_dir(tmp_path, monkeypatch):
    d = tmp_path / "state"
    d.mkdir()
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(d))
    return d
