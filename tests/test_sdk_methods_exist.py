"""Calls to Hyperliquid SDK methods that do not exist.

Moved out of tests/test_close_and_sdk_paths.py 2026-10-23 (wave 2): that
file's other tests covered `pathiel.agents.executor.route_verdict`, deleted
with the rest of the autonomous executor (its one caller, `maybe_execute`,
has no caller left either). These two checks are a general hygiene guard,
unrelated to route_verdict, and the bug class they catch is real: see below.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_the_mcp_server_calls_only_sdk_methods_that_exist():
    """get_user_state called info.frontend_user_state(), which exists in NO
    hyperliquid-python-sdk version back to 0.5.0. Every call raised and returned
    an {'error': ...} payload that read like an API failure."""
    from hyperliquid.info import Info

    src = (ROOT / "scripts" / "pathiel-mcp-server.py").read_text()
    called = set(re.findall(r"\binfo\.([a-z_][a-z0-9_]*)\(", src))
    missing = sorted(m for m in called if not hasattr(Info, m))
    assert not missing, f"the MCP server calls SDK methods that do not exist: {missing}"


def test_the_repo_calls_only_sdk_methods_that_exist():
    """Same check, whole repo. This is the class of bug, not one instance."""
    from hyperliquid.info import Info

    offenders = []
    for sub in ("pathiel", "scripts", "services"):
        for f in (ROOT / sub).rglob("*.py"):
            for m in set(re.findall(r"\binfo\.([a-z_][a-z0-9_]*)\(", f.read_text())):
                if not hasattr(Info, m):
                    offenders.append(f"{f.relative_to(ROOT)}:{m}")
    assert not offenders, f"calls to non-existent SDK methods: {sorted(offenders)}"
