"""Shared fixtures for services/research_mcp's test suite.

Every test gets an isolated PATHIEL_RESEARCH_STATE_DIR (a fresh tmp_path) --
never this machine's real ~/.pathiel-research, same discipline services/
intel/tests/conftest.py uses for PATHIEL_STATE_DIR. Autouse so a test can't
accidentally skip it and touch real state.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolated_research_state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("PATHIEL_RESEARCH_STATE_DIR", str(tmp_path / "research-state"))
    yield tmp_path
