"""Gate tests for the MCP server -> client sampling transport.

`_server_request` sends a server -> client JSON-RPC request and blocks for
its correlated reply; `_deliver_response` routes that reply back to the
waiting call by id while other traffic keeps flowing. No tool currently uses
it -- the `research` tool that originally drove verdict completion through
`sampling/createMessage` was retired with the bot's brain path on 2026-10-23
(see tests/test_mcp_overhaul.py) -- but the transport and the `initialize`
capability negotiation (`_CLIENT_SUPPORTS_SAMPLING`) stay: a future tool is
free to ask the connected harness's own model a question this way instead of
a hosted API. These are deterministic, no network, no LLM.
"""
import importlib.util
import io
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_server():
    script = ROOT / "scripts" / "pathiel-mcp-server.py"
    spec = importlib.util.spec_from_file_location("pathiel_mcp_server_sampling_test", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


MOD = _load_server()


# ── transport: server -> client request correlation ───────────────────────────
def test_server_request_correlates_response_by_id(monkeypatch):
    # a stray notification arrives first, then the matching response.
    replies = io.StringIO(
        json.dumps({"jsonrpc": "2.0", "method": "notifications/progress"}) + "\n"
        + json.dumps({"jsonrpc": "2.0", "id": "srv-1", "result": {"ok": True}}) + "\n"
    )
    out = io.StringIO()
    monkeypatch.setattr(MOD.sys, "stdin", replies)
    monkeypatch.setattr(MOD.sys, "stdout", out)
    MOD._server_request_seq = 0
    assert MOD._server_request("sampling/createMessage", {"x": 1}) == {"ok": True}
    sent = json.loads(out.getvalue().splitlines()[0])
    assert sent["method"] == "sampling/createMessage"
    assert sent["id"] == "srv-1"
    assert sent["params"] == {"x": 1}


def test_server_request_raises_on_client_error(monkeypatch):
    replies = io.StringIO(
        json.dumps({"jsonrpc": "2.0", "id": "srv-1", "error": {"code": -1, "message": "no"}}) + "\n"
    )
    monkeypatch.setattr(MOD.sys, "stdin", replies)
    monkeypatch.setattr(MOD.sys, "stdout", io.StringIO())
    MOD._server_request_seq = 0
    with pytest.raises(RuntimeError):
        MOD._server_request("sampling/createMessage", {})


# The "research seam: injected brain wins, empty result falls back" tests
# that used to live here covered pathiel.agents.research._call_ai, deleted
# 2026-10-23 (wave 2) with the rest of the autonomous scan/research/execute
# engine. get_brain() itself and its last caller (services/trend_engine/ai.py)
# were deleted 2026-10-25; desk_refresh goes through services/llm, which has
# its own seam and tests, so there is no equivalent seam left to test here.
