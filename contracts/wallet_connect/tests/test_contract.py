"""contracts.wallet_connect: the boundary between the MCP server, the CLI
and services/accounts/connect_wallet.py."""
from __future__ import annotations

import ast
import json
from pathlib import Path

import contracts.wallet_connect as c

SRC = Path(c.__file__)


def test_stdlib_and_runtime_only_at_import():
    """The MCP server imports this at start on every install, the operator's
    included: it must pull in nothing from pathiel or services."""
    tree = ast.parse(SRC.read_text())
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in ("pathiel", "services"), alias.name
        if isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] not in ("pathiel", "services"), node.module


def test_tool_shape():
    tool = c.MCP_TOOL
    assert tool["name"] == c.TOOL_NAME == "wallet_connect"
    assert set(tool) == {"name", "description", "inputSchema"}
    props = tool["inputSchema"]["properties"]
    assert set(props) == {"status", "replace", "cancel", "open_browser"}
    assert all(p["type"] == "boolean" for p in props.values())
    assert len(tool["description"]) < 400
    assert "never withdraw" in tool["description"]
    assert c.MCP_TOOL_CLASS == "account_setup"
    assert c.MCP_HANDLER == ("services.accounts.connect_wallet", "handle_wallet_connect")


def test_codes_states_and_keys():
    assert len(set(c.ERROR_CODES)) == len(c.ERROR_CODES)
    assert set(c.FINAL_STATES) <= set(c.STATES)
    assert c.ENV_KEYS == ("HYPERLIQUID_PRIVATE_KEY", "HYPERLIQUID_WALLET_ADDRESS",
                          "HYPERLIQUID_MASTER_ADDRESS")
    assert c.PAGE_TIMEOUT_S == 600
    assert c.AGENT_NAME == "pathiel"


def test_operator_refusal_happens_before_any_import(monkeypatch):
    monkeypatch.delenv("PATHIEL_INSTALL", raising=False)
    monkeypatch.setattr(c.importlib, "import_module",
                        lambda name: (_ for _ in ()).throw(AssertionError(f"imported {name}")))
    out = json.loads(c.mcp_handler()({}))
    assert out == {"ok": False, "state": "none", "code": "operator_install",
                   "error": c.OPERATOR_INSTALL_MESSAGE}
    assert c.result_problems(out) == []
    assert c.operator_refusal()["code"] == "operator_install"
    monkeypatch.setenv("PATHIEL_INSTALL", "public")
    assert c.operator_refusal() is None


def test_public_install_dispatches_to_the_named_handler(monkeypatch):
    monkeypatch.setenv("PATHIEL_INSTALL", "public")
    seen = {}

    class Fake:
        @staticmethod
        def handle_wallet_connect(params):
            seen["params"] = params
            return '{"ok": true, "state": "none"}'

    monkeypatch.setattr(c.importlib, "import_module", lambda name: seen.update(module=name) or Fake)
    assert json.loads(c.mcp_handler()(None)) == {"ok": True, "state": "none"}
    assert seen == {"module": "services.accounts.connect_wallet", "params": {}}


def test_result_problems():
    assert c.result_problems({"ok": True, "state": "waiting", "url": "http://127.0.0.1:1/x/"}) == []
    assert c.result_problems([]) == ["not a dict: list"]
    assert c.result_problems({"ok": "yes", "state": "done"})
    assert c.result_problems({"ok": True, "state": "nope"})
    key = "0x" + "c" * 64
    assert c.result_problems({"ok": True, "state": "done", "next": key})
    assert c.result_problems({"ok": True, "state": "done", "next": key.upper()[2:]})
    # a 130-hex signature or a 40-hex address is not a key
    assert c.result_problems({"ok": True, "state": "done", "agent_address": "0x" + "a" * 40}) == []
