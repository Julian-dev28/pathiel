"""Isolation for services/llm's gate tests.

Every test gets its own throwaway PATHIEL_STATE_DIR (so usage.jsonl / the
model-probe cache never touch the real state dir) and starts with
PATHIEL_LLM_MODEL/PATHIEL_LLM_CONCURRENCY unset, regardless of the ambient
shell. The in-process probe memo and completion semaphore are module-level
singletons by design (see model_probe.py, api.py docstrings) - this fixture
resets both so one test's probe/concurrency state cannot leak into the next.
"""
from __future__ import annotations

import pytest

from services.llm import api, model_probe


@pytest.fixture(autouse=True)
def _llm_test_isolation(tmp_path, monkeypatch):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("PATHIEL_LLM_MODEL", raising=False)
    monkeypatch.delenv("PATHIEL_LLM_CONCURRENCY", raising=False)
    monkeypatch.delenv("CLAUDE_CLI_COMMAND", raising=False)
    model_probe._reset_memo_for_tests()
    api._reset_semaphore_for_tests()
    yield
    model_probe._reset_memo_for_tests()
    api._reset_semaphore_for_tests()
