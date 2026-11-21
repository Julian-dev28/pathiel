"""services/research_mcp/server.py: the JSON-RPC dispatch (initialize,
tools/list, tools/call, ping, unknown method/tool, bad params) and the
concurrent _route() path used by run()'s stdio loop."""
from __future__ import annotations

import json
import time

from services.research_mcp import server, usage
from services.research_mcp.schemas import TOOL_NAMES


def test_initialize_returns_server_info_and_instructions():
    resp = server.dispatch_message({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                    "params": {"protocolVersion": "2025-06-18"}})
    result = resp["result"]
    assert result["protocolVersion"] == "2025-06-18"
    assert result["serverInfo"]["name"] == "pathiel-research"
    assert "research-gated" not in result["instructions"]  # sanity: not garbled
    assert "Read-only" in result["instructions"] and "keyless" in result["instructions"]


def test_initialize_falls_back_to_the_newest_version_for_an_unknown_ask():
    resp = server.dispatch_message({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                    "params": {"protocolVersion": "1999-01-01"}})
    assert resp["result"]["protocolVersion"] == server._SUPPORTED_PROTOCOL_VERSIONS[0]


def test_ping():
    resp = server.dispatch_message({"jsonrpc": "2.0", "id": 2, "method": "ping"})
    assert resp == {"jsonrpc": "2.0", "id": 2, "result": {}}


def test_tools_list_matches_the_schema_module():
    resp = server.dispatch_message({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
    names = {t["name"] for t in resp["result"]["tools"]}
    assert names == set(TOOL_NAMES)


def test_notification_gets_no_response():
    assert server.dispatch_message({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_unknown_method_is_method_not_found():
    resp = server.dispatch_message({"jsonrpc": "2.0", "id": 4, "method": "bogus/method"})
    assert resp["error"]["code"] == server._METHOD_NOT_FOUND


def test_malformed_message_is_invalid_request():
    assert server.dispatch_message("not a dict")["error"]["code"] == server._INVALID_REQUEST
    assert server.dispatch_message({"id": 1, "method": 7})["error"]["code"] == server._INVALID_REQUEST
    assert server.dispatch_message({"id": 1, "method": "x", "params": "nope"})["error"]["code"] == server._INVALID_PARAMS


def test_tools_call_unknown_tool_is_invalid_params():
    resp = server.dispatch_message({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                                    "params": {"name": "place_bracket", "arguments": {}}})
    assert resp["error"]["code"] == server._INVALID_PARAMS


def test_tools_call_happy_path(monkeypatch):
    from services.research_mcp import tools
    monkeypatch.setattr(tools, "levels_tool", lambda coin: {"coin": coin, "mid": 42.0})
    resp = server.dispatch_message({"jsonrpc": "2.0", "id": 6, "method": "tools/call",
                                    "params": {"name": "levels", "arguments": {"coin": "BTC"}}})
    result = resp["result"]
    assert result["isError"] is False
    payload = json.loads(result["content"][0]["text"])
    assert payload == {"coin": "BTC", "mid": 42.0}


def test_tools_call_missing_required_argument_is_a_clean_tool_error(monkeypatch):
    resp = server.dispatch_message({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                                    "params": {"name": "levels", "arguments": {}}})
    result = resp["result"]
    assert result["isError"] is True
    payload = json.loads(result["content"][0]["text"])
    assert "coin" in payload["error"]


def test_tools_call_a_raising_handler_becomes_a_clean_error_not_a_crash(monkeypatch):
    from services.research_mcp import tools

    def boom(coin):
        raise RuntimeError("venue is down")
    monkeypatch.setattr(tools, "levels_tool", boom)
    resp = server.dispatch_message({"jsonrpc": "2.0", "id": 8, "method": "tools/call",
                                    "params": {"name": "levels", "arguments": {"coin": "BTC"}}})
    result = resp["result"]
    assert result["isError"] is True
    payload = json.loads(result["content"][0]["text"])
    assert "venue is down" in payload["error"]


def test_tools_call_an_unsupported_coin_is_a_clean_tool_error_not_a_500():
    resp = server.dispatch_message({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                                    "params": {"name": "levels", "arguments": {"coin": "DOGE"}}})
    result = resp["result"]
    assert result["isError"] is True
    payload = json.loads(result["content"][0]["text"])
    assert "DOGE" in payload["error"]


def test_call_tool_records_one_usage_call_per_invocation(tmp_path, monkeypatch):
    monkeypatch.setenv("PATHIEL_RESEARCH_STATE_DIR", str(tmp_path))
    from services.research_mcp import tools
    monkeypatch.setattr(tools, "levels_tool", lambda coin: {"coin": coin})
    server.call_tool("levels", {"coin": "BTC"})
    server.call_tool("levels", {"coin": "ETH"})
    data = usage.read_usage()
    assert data["levels"]["calls"] == 2


def test_call_tool_records_usage_even_on_error(tmp_path, monkeypatch):
    monkeypatch.setenv("PATHIEL_RESEARCH_STATE_DIR", str(tmp_path))
    server.call_tool("levels", {})  # missing coin -> clean error, still counted
    assert usage.read_usage()["levels"]["calls"] == 1


def test_guard_output_passes_small_payloads_through():
    assert server._guard_output("levels", "small") == "small"


def test_guard_output_truncates_oversized_payloads(monkeypatch):
    monkeypatch.setattr(server, "MAX_OUTPUT_CHARS", 20)
    out = server._guard_output("levels", "x" * 100)
    payload = json.loads(out)
    assert payload["truncated"] is True and payload["tool"] == "levels"


def test_payload_is_error():
    assert server._payload_is_error({"error": "nope"}) is True
    assert server._payload_is_error({"coin": "BTC"}) is False
    assert server._payload_is_error({"error": None}) is False


def test_handler_registry_matches_the_schema_exactly():
    assert set(server._HANDLERS) == set(TOOL_NAMES)


# ── _route(): the concurrent stdio-loop path ────────────────────────────────

def test_route_sends_exactly_one_response_for_a_tools_call(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from services.research_mcp import tools
    monkeypatch.setattr(tools, "levels_tool", lambda coin: {"coin": coin})
    sent = []
    monkeypatch.setattr(server, "_send", sent.append)
    pool = ThreadPoolExecutor(max_workers=2)
    try:
        server._route({"jsonrpc": "2.0", "id": "r1", "method": "tools/call",
                       "params": {"name": "levels", "arguments": {"coin": "BTC"}}}, pool)
    finally:
        pool.shutdown(wait=True)
    assert len(sent) == 1
    assert sent[0]["id"] == "r1"
    assert json.loads(sent[0]["result"]["content"][0]["text"]) == {"coin": "BTC"}


def test_route_times_out_a_slow_tool_without_blocking_forever(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from services.research_mcp import tools
    monkeypatch.setattr(server, "DEFAULT_TOOL_TIMEOUT_S", 0.05)

    def slow(coin):
        time.sleep(0.5)
        return {"coin": coin}
    monkeypatch.setattr(tools, "levels_tool", slow)
    sent = []
    monkeypatch.setattr(server, "_send", sent.append)
    pool = ThreadPoolExecutor(max_workers=2)
    try:
        start = time.monotonic()
        server._route({"jsonrpc": "2.0", "id": "r2", "method": "tools/call",
                       "params": {"name": "levels", "arguments": {"coin": "BTC"}}}, pool)
        elapsed = time.monotonic() - start
        # Drain the still-running straggler INSIDE this test, while _send is
        # still monkeypatched, so its late real answer lands in `sent`
        # instead of printing to the real stdout after teardown.
        time.sleep(0.6)
    finally:
        pool.shutdown(wait=True)
    assert elapsed < 0.4, "the timeout must bound _route's own wait, not the slow handler's sleep"
    assert len(sent) == 2, "the timeout message, then the late real answer when the handler finishes"
    timeout_payload = json.loads(sent[0]["result"]["content"][0]["text"])
    assert timeout_payload["timed_out"] is True
    late_payload = json.loads(sent[1]["result"]["content"][0]["text"])
    assert late_payload == {"coin": "BTC"}


def test_route_ignores_a_bare_notification():
    sent_calls = []

    class _Pool:
        def submit(self, fn):
            sent_calls.append(fn)
    server._route({"jsonrpc": "2.0", "method": "notifications/cancelled"}, _Pool())
    assert sent_calls == []
