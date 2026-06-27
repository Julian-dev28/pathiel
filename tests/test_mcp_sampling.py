"""Gate tests for MCP sampling in the hermes-trader MCP server.

The `research` tool routes its verdict completion through the connected harness's
own model (server -> client `sampling/createMessage`) instead of ai_brain/OpenRouter,
and falls back to the configured provider when the host cannot sample. These are
deterministic, no network, no LLM.
"""
import importlib.util
import io
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_server():
    script = ROOT / "scripts" / "hermes-mcp-server.py"
    spec = importlib.util.spec_from_file_location("hermes_mcp_server_sampling_test", script)
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


# ── sampling brain: builds the request, parses the verdict text ────────────────
def test_sampling_brain_builds_request_and_parses_text(monkeypatch):
    captured = {}

    def fake_req(method, params, timeout_s=120.0):
        captured["method"] = method
        captured["params"] = params
        return {"role": "assistant", "content": {"type": "text", "text": 'reason\n{"verdict": "LONG"}'}}

    monkeypatch.setattr(MOD, "_server_request", fake_req)
    text = MOD._McpSamplingBrain().complete("SYS", "USER")
    assert '"verdict": "LONG"' in text
    assert captured["method"] == "sampling/createMessage"
    assert captured["params"]["systemPrompt"] == "SYS"
    assert captured["params"]["messages"][0]["content"]["text"] == "USER"


def test_sampling_brain_handles_list_content(monkeypatch):
    monkeypatch.setattr(
        MOD, "_server_request",
        lambda *a, **k: {"content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]},
    )
    assert MOD._McpSamplingBrain().complete("s", "u") == "a b"


# ── gating: sampling brain only when the harness can be the model ──────────────
def test_research_brain_gated_on_capability(monkeypatch):
    monkeypatch.setattr(MOD, "_CLIENT_SUPPORTS_SAMPLING", False)
    assert MOD._research_brain() is None

    monkeypatch.setattr(MOD, "_CLIENT_SUPPORTS_SAMPLING", True)
    monkeypatch.delenv("HERMES_MCP_DISABLE_SAMPLING", raising=False)
    assert isinstance(MOD._research_brain(), MOD._McpSamplingBrain)

    monkeypatch.setenv("HERMES_MCP_DISABLE_SAMPLING", "1")
    assert MOD._research_brain() is None


def test_handle_research_injects_brain_per_capability(monkeypatch):
    import hermes_trader.agents.research as rm
    captured = {}

    def fake_research(coin, perception, brain=None):
        captured["brain"] = brain
        return {"id": "a1", "verdict": "PASS", "confidence": 0.0, "side": None,
                "entry_px": 0, "stop_px": 0, "tp_px": 0, "reasoning": "x"}

    monkeypatch.setattr(rm, "research", fake_research)
    MOD._perception_cache["BTC"] = {"id": "p1", "coin": "BTC", "mid": 1.0, "triggers": [], "composite_score": 0}
    monkeypatch.delenv("HERMES_MCP_DISABLE_SAMPLING", raising=False)

    monkeypatch.setattr(MOD, "_CLIENT_SUPPORTS_SAMPLING", True)
    assert json.loads(MOD.handle_research({"coin": "BTC"}))["status"] == "complete"
    assert isinstance(captured["brain"], MOD._McpSamplingBrain)

    monkeypatch.setattr(MOD, "_CLIENT_SUPPORTS_SAMPLING", False)
    MOD.handle_research({"coin": "BTC"})
    assert captured["brain"] is None


# ── research seam: injected brain wins, empty result falls back ────────────────
def test_call_ai_prefers_injected_brain(monkeypatch):
    from hermes_trader.agents import research as rm

    class FakeBrain:
        provider = "fake"

        def complete(self, s, u):
            return "INJECTED_TEXT"

    def boom(*a, **k):
        raise AssertionError("get_brain must not be called when injected brain works")

    monkeypatch.setattr(rm, "get_brain", boom)
    assert rm._call_ai("sys", "user", brain=FakeBrain()) == "INJECTED_TEXT"


def test_call_ai_falls_back_when_injected_brain_empty(monkeypatch):
    from hermes_trader.agents import research as rm

    class EmptyBrain:
        provider = "empty"

        def complete(self, s, u):
            return ""

    class Configured:
        def complete(self, s, u):
            return "FALLBACK_TEXT"

    monkeypatch.setattr(rm, "get_brain", lambda *a, **k: Configured())
    assert rm._call_ai("sys", "user", brain=EmptyBrain()) == "FALLBACK_TEXT"
