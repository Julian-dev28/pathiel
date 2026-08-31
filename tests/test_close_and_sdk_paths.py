"""Two always-broken paths, and the guards that keep them fixed."""
from __future__ import annotations

from pathlib import Path

import pytest

from pathia.agents.executor import route_verdict

ROOT = Path(__file__).resolve().parents[1]


# ── a CLOSE verdict with no coin ─────────────────────────────────────────────

def test_a_close_without_a_coin_never_calls_close_fn():
    """close_position_market takes `coin: str`. Passing None would either close
    the wrong thing or raise deep in the exchange client. Refuse at the
    boundary."""
    called = []
    r = route_verdict({"verdict": "CLOSE", "id": "x"},
                      close_fn=lambda c: called.append(c))
    assert called == [], "close_fn was called with a missing coin"
    assert r["action"] == "none"
    assert r["result"]["reason"] == "close_verdict_without_coin"


def test_a_close_with_a_coin_still_closes():
    r = route_verdict({"verdict": "CLOSE", "coin": "BTC", "id": "y"},
                      close_fn=lambda c: {"ok": True, "coin": c})
    assert r["action"] == "close" and r["result"]["coin"] == "BTC"


@pytest.mark.parametrize("coin", ["", None])
def test_every_falsy_coin_is_refused(coin):
    called = []
    route_verdict({"verdict": "CLOSE", "coin": coin, "id": "z"},
                  close_fn=lambda c: called.append(c))
    assert called == []


# ── SDK methods that do not exist ────────────────────────────────────────────

def test_the_mcp_server_calls_only_sdk_methods_that_exist():
    """get_user_state called info.frontend_user_state(), which exists in NO
    hyperliquid-python-sdk version back to 0.5.0. Every call raised and returned
    an {'error': ...} payload that read like an API failure."""
    import re

    from hyperliquid.info import Info

    src = (ROOT / "scripts" / "pathia-mcp-server.py").read_text()
    called = set(re.findall(r"\binfo\.([a-z_][a-z0-9_]*)\(", src))
    missing = sorted(m for m in called if not hasattr(Info, m))
    assert not missing, f"the MCP server calls SDK methods that do not exist: {missing}"


def test_the_repo_calls_only_sdk_methods_that_exist():
    """Same check, whole repo. This is the class of bug, not one instance."""
    import re

    from hyperliquid.info import Info

    offenders = []
    for sub in ("pathia", "scripts", "services"):
        for f in (ROOT / sub).rglob("*.py"):
            for m in set(re.findall(r"\binfo\.([a-z_][a-z0-9_]*)\(", f.read_text())):
                if not hasattr(Info, m):
                    offenders.append(f"{f.relative_to(ROOT)}:{m}")
    assert not offenders, f"calls to non-existent SDK methods: {sorted(offenders)}"
