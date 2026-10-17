"""Shared fixtures for services/thesis's gate tests.

``oil`` loads contracts/strategies/fixtures/_build.py's module-level
spec/analytics/intel/thesis objects directly (no JSON round trip, no file
writes - ``main()`` is never called). Those objects are SHARED module
state: tests must never mutate them in place (use ``.model_copy(update=...)``
for every adversarial variant) or one test's mutation leaks into another's
"golden path" assertion.
"""
from __future__ import annotations

import pytest

from contracts.strategies.fixtures import _build as oil_fixture
from services.llm import api as llm_api
from services.llm import model_probe


@pytest.fixture
def oil():
    return oil_fixture


@pytest.fixture(autouse=True)
def _thesis_test_isolation(tmp_path, monkeypatch):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("PATHIEL_LLM_MODEL", raising=False)
    model_probe._reset_memo_for_tests()
    llm_api._reset_semaphore_for_tests()
    yield
