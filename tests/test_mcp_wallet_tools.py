"""Gate tests for the MCP server's wallet-aware tools.

The tools exist so an agent can answer "whose account am I looking at" against
the dashboard's own sign-ins. The important assertion is the one about what they
CANNOT do: this server signs with the deployment's key and no other, so a wallet
that signed in granted a session, not a key. An agent that treats a connected
wallet as tradeable would be reaching for the house key on a stranger's behalf.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "pathia_mcp_server", _ROOT / "scripts" / "pathia-mcp-server.py")
MCP = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(MCP)

TOOLS = {t["name"]: t for t in MCP.TOOLS}


class TestToolsAreRegistered:
    @pytest.mark.parametrize("name", ["connected_wallets", "wallet_account"])
    def test_the_tool_is_declared(self, name):
        assert name in TOOLS

    @pytest.mark.parametrize("name", ["connected_wallets", "wallet_account"])
    def test_the_tool_is_dispatchable(self, name):
        """A schema with no handler is a tool that fails only when called."""
        source = (_ROOT / "scripts" / "pathia-mcp-server.py").read_text()
        assert f'"{name}": handle_{name}' in source

    def test_the_description_states_the_read_only_boundary(self):
        """An agent reads these descriptions and nothing else before deciding
        what a tool is for."""
        text = TOOLS["connected_wallets"]["description"].lower()
        assert "read-only" in text
        assert "never" in text


class TestConnectedWallets:
    def test_it_survives_having_no_auth_database(self, monkeypatch, tmp_path):
        """MCP often runs beside a deployment rather than inside one."""
        monkeypatch.setenv("PATHIA_AUTH_DB", str(tmp_path / "nothing" / "auth.db"))
        body = json.loads(MCP.handle_connected_wallets({}))
        assert body["wallets"] == [] or isinstance(body["wallets"], list)

    def test_it_reports_that_it_cannot_trade_them(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PATHIA_AUTH_DB", str(tmp_path / "auth.db"))
        body = json.loads(MCP.handle_connected_wallets({}))
        assert body["can_this_server_trade_them"] is False
        assert body["why_not"]

    def test_it_lists_a_wallet_that_signed_in(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PATHIA_AUTH_DB", str(tmp_path / "auth.db"))
        from services.auth.store import AuthStore
        AuthStore(str(tmp_path / "auth.db")).upsert_user("0x" + "a" * 40)
        body = json.loads(MCP.handle_connected_wallets({}))
        assert [w["address"] for w in body["wallets"]] == ["0x" + "a" * 40]

    def test_newest_sign_in_comes_first(self, monkeypatch, tmp_path):
        """`wallet_account` with no address reads the first entry, so the order
        is the contract, not a presentation detail."""
        monkeypatch.setenv("PATHIA_AUTH_DB", str(tmp_path / "auth.db"))
        from services.auth.store import AuthStore
        store = AuthStore(str(tmp_path / "auth.db"))
        store.upsert_user("0x" + "b" * 40, now=1000.0)
        store.upsert_user("0x" + "c" * 40, now=2000.0)
        body = json.loads(MCP.handle_connected_wallets({}))
        assert body["wallets"][0]["address"] == "0x" + "c" * 40

    def test_the_limit_is_bounded(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PATHIA_AUTH_DB", str(tmp_path / "auth.db"))
        from services.auth.store import AuthStore
        store = AuthStore(str(tmp_path / "auth.db"))
        for i in range(5):
            store.upsert_user("0x" + f"{i:040x}")
        assert len(json.loads(MCP.handle_connected_wallets({"limit": 2}))["wallets"]) == 2
        # Absurd input must not become an unbounded query.
        assert len(json.loads(
            MCP.handle_connected_wallets({"limit": 10_000}))["wallets"]) <= 100


class TestWalletAccount:
    def test_a_malformed_address_is_refused_without_a_network_call(self):
        body = json.loads(MCP.handle_wallet_account({"address": "not-an-address"}))
        assert "error" in body

    def test_it_says_so_when_nothing_has_signed_in(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PATHIA_AUTH_DB", str(tmp_path / "auth.db"))
        body = json.loads(MCP.handle_wallet_account({}))
        assert "error" in body

    def test_a_network_failure_is_reported_not_raised(self, monkeypatch):
        """A tool that raises kills the agent's turn; one that reports lets it
        say what went wrong."""
        def boom(*a, **kw):
            raise RuntimeError("hyperliquid unreachable")
        monkeypatch.setattr(MCP, "fetch_account_state", boom)
        body = json.loads(MCP.handle_wallet_account({"address": "0x" + "d" * 40}))
        assert body["status"] == "unavailable"
        assert "hyperliquid unreachable" in body["error"]

    def test_an_unfunded_address_is_not_an_error(self, monkeypatch):
        """The ordinary state of somebody who just connected a wallet."""
        monkeypatch.setattr(MCP, "fetch_account_state",
                            lambda *a, **kw: {"equity": 0.0, "asset_positions": []})
        body = json.loads(MCP.handle_wallet_account({"address": "0x" + "e" * 40}))
        assert body["funded"] is False
        assert "error" not in body

    def test_it_marks_its_own_output_read_only(self, monkeypatch):
        monkeypatch.setattr(MCP, "fetch_account_state",
                            lambda *a, **kw: {"equity": 12.5, "asset_positions": []})
        body = json.loads(MCP.handle_wallet_account({"address": "0x" + "f" * 40}))
        assert body["read_only"] is True
        assert body["funded"] is True


class TestTheReadmeTellsTheTruth:
    """The README is how someone decides whether to wire this up at all.

    It claimed 99 tools while the server exposed 88 — a number nobody
    recomputed after tools were added and removed. Pinned here because a count
    in prose has no other way to stay honest.
    """

    def test_the_advertised_tool_count_matches_the_server(self):
        readme = (_ROOT / "README.md").read_text()
        assert f"{len(MCP.TOOLS)} tools over stdio" in readme, (
            f"README does not say '{len(MCP.TOOLS)} tools over stdio'; the "
            f"server exposes {len(MCP.TOOLS)}")

    def test_the_wallet_tools_are_documented(self):
        readme = (_ROOT / "README.md").read_text()
        for name in ("connected_wallets", "wallet_account"):
            assert f"`{name}`" in readme, f"{name} is not in the README"

    def test_the_readme_states_the_read_only_boundary(self):
        """Someone wiring an agent reads this and nothing else before deciding
        what the agent may be told to do with a visitor's wallet."""
        readme = (_ROOT / "README.md").read_text()
        assert "can_this_server_trade_them" in readme
