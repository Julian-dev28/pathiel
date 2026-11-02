"""Every review test gets its own throwaway PATHIEL_STATE_DIR: the ledger and
last_run.json never land in the live state dir."""
from __future__ import annotations

import os

import pytest

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
BUNDLE = os.path.join(FIXTURES, "bundle_2026-11-02")
AUDIT_CSV = os.path.join(FIXTURES, "audit_trades_2026-11-02.csv")


@pytest.fixture(autouse=True)
def state_dir(tmp_path, monkeypatch):
    d = tmp_path / "state"
    d.mkdir()
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(d))
    return d
