"""Eval (paid, marked live): do the public tool definitions score well on the
directory rubric?

Outcome: Glama's Tool Definition Quality Score for pathiel was 3.6/5 over 65
tools (v0.6.0, 2026-11-13). scripts/tdqs_eval.py runs the same six-part
rubric (purpose, usage, behavior, parameters, completeness, conciseness)
through services/llm (local Claude Code). Its judge is stricter than Glama's:
it scored that same 65-tool surface 3.04, about 0.56 under Glama.

Thresholds on the judge's scale (2026-11-13 run of the 30-tool toolset:
mean 4.29, lowest tool 4.00):

  mean over the 30 public tools   >= 4.2
  every tool                      >= 3.8
  purpose (mean)                  >= 4.5

    PATHIEL_E2E=1 .venv/bin/python -m pytest -m live -s tests/test_eval_tdqs.py
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.live

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "tdqs_eval.py"


@pytest.mark.skipif(os.environ.get("PATHIEL_E2E") != "1", reason="paid: set PATHIEL_E2E=1")
def test_public_tool_definitions_meet_the_rubric(tmp_path, monkeypatch):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))   # the LLM usage log stays out of the tree
    spec = importlib.util.spec_from_file_location("tdqs_eval_live", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    result = mod.grade(mod.surface_tools("public"))
    print(mod.report("public", result))
    assert result["tools_graded"] == result["tools_listed"] == 30
    assert result["mean"] >= 4.2, result["mean"]
    low = [r for r in result["rows"] if r["score"] < 3.8]
    assert not low, low
    assert result["by_dimension"]["purpose"] >= 4.5
