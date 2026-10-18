"""Periodic eval: scripts/mcp_smoke.py over the real stdio transport against
live Hyperliquid. Every read-only tool answers inside its latency budget,
nothing leaks a configured secret, a fast call is not stuck behind a slow one,
and every advertised tool is either smoked or skipped with a reason.

    .venv/bin/python -m pytest -q -m online tests/test_mcp_smoke_online.py

Deselected by default (pyproject addopts). Never calls a live-money tool, a
journal write or the LLM: see mcp_smoke.SKIPPED.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.online

ROOT = Path(__file__).resolve().parents[1]


def _smoke():
    spec = importlib.util.spec_from_file_location("mcp_smoke", ROOT / "scripts" / "mcp_smoke.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod          # @dataclass resolves its module through sys.modules
    spec.loader.exec_module(mod)
    return mod


def test_every_read_only_tool_answers_live(tmp_path):
    assert _smoke().run(None, tmp_path / "report.json") == 0, (tmp_path / "report.json").read_text()


def test_the_smoke_never_calls_a_live_money_tool():
    smoke = _smoke()
    called = {c.tool for c in smoke.cases("0x" + "0" * 40)}
    for tool in ("execute", "close_position", "set_leverage", "cancel_order",
                 "desk_refresh", "desk_submit_call", "research", "submit_verdict"):
        assert tool in smoke.SKIPPED and tool not in called
