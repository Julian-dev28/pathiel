from __future__ import annotations

import json


def _verdict_text(verdict: str = "PASS") -> str:
    return (
        "Reasoning\n"
        f'{{"verdict":"{verdict}","confidence":0.1,"side":"null",'
        '"entryPx":100,"stopPx":0,"tpPx":0,"reasoning":"test"}}'
    )


def test_selected_provider_hot_read_env_overrides_config(monkeypatch):
    from hermes_trader.agents.ai_brain import selected_ai_brain_provider

    cfg = {"ai_brain": {"provider": "claude_cli"}}
    monkeypatch.setenv("AI_BRAIN_PROVIDER", "codex")
    assert selected_ai_brain_provider(cfg) == "codex_cli"

    monkeypatch.delenv("AI_BRAIN_PROVIDER", raising=False)
    assert selected_ai_brain_provider(cfg) == "claude_cli"

    monkeypatch.setenv("AI_BRAIN_PROVIDER", "bad-provider")
    assert selected_ai_brain_provider(cfg) == "openrouter"


def test_openrouter_web_tool_only_sent_when_gated(monkeypatch):
    from hermes_trader.agents import ai_brain

    payloads: list[dict] = []
    monkeypatch.delenv("OPENROUTER_WEB_ENGINE", raising=False)
    monkeypatch.delenv("OPENROUTER_WEB_MAX_RESULTS", raising=False)
    monkeypatch.delenv("OPENROUTER_WEB_MAX_TOTAL_RESULTS", raising=False)

    class Response:
        status_code = 200
        text = ""
        is_success = True

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}]}

    class FakeAsyncClient:
        def __init__(self, timeout):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json, headers):
            payloads.append(json)
            return Response()

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(ai_brain.httpx, "AsyncClient", FakeAsyncClient)

    assert ai_brain.OpenRouterBrain().complete("system", "user", web_search=False) == "ok"
    assert "tools" not in payloads[-1]

    assert ai_brain.OpenRouterBrain().complete("system", "user", web_search=True) == "ok"
    assert payloads[-1]["tools"] == [{
        "type": "openrouter:web_search",
        "parameters": {
            "engine": "native",
            "max_results": 5,
            "max_total_results": 10,
        },
    }]


def test_openrouter_web_tool_caps_env_overrides(monkeypatch):
    from hermes_trader.agents import ai_brain

    monkeypatch.setenv("OPENROUTER_WEB_ENGINE", "exa")
    monkeypatch.setenv("OPENROUTER_WEB_MAX_RESULTS", "999")
    monkeypatch.setenv("OPENROUTER_WEB_MAX_TOTAL_RESULTS", "999")
    tool = ai_brain._openrouter_web_search_tool()
    assert tool["parameters"] == {
        "engine": "exa",
        "max_results": 10,
        "max_total_results": 25,
    }


def test_openrouter_result_preserves_usage_citations_and_string_api(monkeypatch):
    from hermes_trader.agents import ai_brain

    annotation = {
        "type": "url_citation",
        "url_citation": {"url": "https://example.test/news", "title": "News"},
    }

    class Response:
        status_code = 200
        text = ""
        is_success = True

        def json(self):
            return {
                "id": "gen-test",
                "model": "x-ai/grok-test",
                "choices": [{"message": {"content": "grounded answer", "annotations": [annotation]}}],
                "usage": {
                    "input_tokens": 11,
                    "output_tokens": 7,
                    "server_tool_use": {"web_search_requests": 2},
                },
            }

    class FakeAsyncClient:
        def __init__(self, timeout):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json, headers):
            return Response()

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(ai_brain.httpx, "AsyncClient", FakeAsyncClient)

    result = ai_brain.OpenRouterBrain().complete("system", "user", web_search=True)
    assert isinstance(result, str)
    assert result.upper() == "GROUNDED ANSWER"
    assert result.usage["input_tokens"] == 11
    assert result.web_search_requests == 2
    assert result.citations == (annotation,)
    assert result.metadata == {"id": "gen-test", "model": "x-ai/grok-test"}


def test_openrouter_402_retry_preserves_web_tool_and_final_metadata(monkeypatch):
    from hermes_trader.agents import ai_brain

    payloads: list[dict] = []

    class Response:
        def __init__(self, status_code: int, *, text: str = "", data: dict | None = None):
            self.status_code = status_code
            self.text = text
            self._data = data or {}

        @property
        def is_success(self) -> bool:
            return 200 <= self.status_code < 300

        def json(self) -> dict:
            return self._data

    class FakeAsyncClient:
        def __init__(self, timeout):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json, headers):
            payloads.append(json)
            if len(payloads) == 1:
                return Response(402, text="You requested too much, can only afford 842 tokens")
            return Response(200, data={
                "choices": [{"message": {"content": "ok", "annotations": []}}],
                "usage": {"server_tool_use": {"web_search_requests": 1}},
            })

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv("OPENROUTER_MAX_TOKENS", raising=False)
    monkeypatch.setattr(ai_brain.httpx, "AsyncClient", FakeAsyncClient)

    result = ai_brain.OpenRouterBrain().complete("system", "user", web_search=True)
    assert result == "ok"
    assert result.web_search_requests == 1
    assert [p["max_tokens"] for p in payloads] == [2048, 792]
    assert payloads[0]["tools"] == payloads[1]["tools"]


def test_claude_cli_parses_envelope_and_requires_verdict_json(monkeypatch):
    from hermes_trader.agents import ai_brain

    # server.py loads .env.local into os.environ at import time, and the env var
    # outranks the mocked config in _cli_timeout_s — without this the test
    # red/greens with the dev machine's .env.local (AI_BRAIN_TIMEOUT_S=120).
    monkeypatch.delenv("AI_BRAIN_TIMEOUT_S", raising=False)
    monkeypatch.delenv("CLAUDE_CLI_COMMAND", raising=False)
    monkeypatch.delenv("CLAUDE_CLI_MAX_TURNS", raising=False)

    seen: dict[str, object] = {}
    monkeypatch.setattr(
        ai_brain,
        "_read_ai_brain_config",
        lambda: {"timeout_s": 5, "claude_cli": {"command": "claude", "max_turns": 1}},
    )

    def fake_run(args, prompt, timeout_s):
        seen["args"] = args
        seen["prompt"] = prompt
        seen["timeout_s"] = timeout_s
        return json.dumps({
            "result": _verdict_text("LONG"),
            "is_error": False,
            "usage": {"input_tokens": 9, "output_tokens": 4},
            "total_cost_usd": 0.01,
            "num_turns": 1,
        })

    monkeypatch.setattr(ai_brain, "_run_cli", fake_run)

    out = ai_brain.ClaudeCliBrain().complete("SYSTEM", "USER")
    assert '"verdict":"LONG"' in out
    assert seen["prompt"] == "SYSTEM\n\nUSER"
    assert "--tools" in seen["args"]
    assert seen["timeout_s"] == 5
    assert out.usage == {"input_tokens": 9, "output_tokens": 4}
    assert out.metadata == {"total_cost_usd": 0.01, "num_turns": 1}


def test_claude_cli_error_envelope_maps_to_ai_down(monkeypatch):
    from hermes_trader.agents import ai_brain

    monkeypatch.setattr(ai_brain, "_read_ai_brain_config", lambda: {"timeout_s": 5})
    monkeypatch.setattr(
        ai_brain,
        "_run_cli",
        lambda args, prompt, timeout_s: json.dumps({"result": "failed", "is_error": True}),
    )

    assert ai_brain.ClaudeCliBrain().complete("SYSTEM", "USER") == ""


def test_codex_cli_uses_read_only_sandbox_and_rejects_jsonless_output(monkeypatch):
    from hermes_trader.agents import ai_brain

    seen: dict[str, object] = {}
    monkeypatch.setattr(ai_brain, "_read_ai_brain_config", lambda: {"timeout_s": 5})

    def fake_run(args, prompt, timeout_s):
        seen["args"] = args
        return "I would go long, but I forgot the JSON."

    monkeypatch.setattr(ai_brain, "_run_cli", fake_run)

    assert ai_brain.CodexCliBrain().complete("SYSTEM", "USER") == ""
    args = seen["args"]
    assert "--sandbox" in args
    assert "read-only" in args
    assert "--ephemeral" in args

def test_claude_cli_web_search_flag_switches_tools_and_turns(monkeypatch):
    """web_search=True must grant exactly the WebSearch tool with multi-turn
    headroom; web_search=False keeps the sealed zero-tool single-turn call."""
    from hermes_trader.agents import ai_brain

    monkeypatch.delenv("AI_BRAIN_TIMEOUT_S", raising=False)
    monkeypatch.delenv("CLAUDE_CLI_COMMAND", raising=False)
    monkeypatch.delenv("CLAUDE_CLI_MAX_TURNS", raising=False)
    monkeypatch.delenv("CLAUDE_CLI_WEB_MAX_TURNS", raising=False)
    monkeypatch.setattr(
        ai_brain,
        "_read_ai_brain_config",
        lambda: {"timeout_s": 5, "claude_cli": {"command": "claude", "max_turns": 1}},
    )

    seen: dict[str, list] = {}

    def fake_run(args, prompt, timeout_s):
        seen["args"] = args
        return json.dumps({"result": _verdict_text("PASS"), "is_error": False})

    monkeypatch.setattr(ai_brain, "_run_cli", fake_run)

    ai_brain.ClaudeCliBrain().complete("S", "U", web_search=True)
    args = seen["args"]
    assert args[args.index("--tools") + 1] == "WebSearch"
    assert args[args.index("--max-turns") + 1] == "8"
    assert "--safe-mode" in args

    ai_brain.ClaudeCliBrain().complete("S", "U", web_search=False)
    args = seen["args"]
    assert args[args.index("--tools") + 1] == ""
    assert args[args.index("--max-turns") + 1] == "1"


def test_claude_cli_web_max_turns_config_override(monkeypatch):
    from hermes_trader.agents import ai_brain

    monkeypatch.delenv("AI_BRAIN_TIMEOUT_S", raising=False)
    monkeypatch.delenv("CLAUDE_CLI_COMMAND", raising=False)
    monkeypatch.delenv("CLAUDE_CLI_WEB_MAX_TURNS", raising=False)
    monkeypatch.setattr(
        ai_brain,
        "_read_ai_brain_config",
        lambda: {"timeout_s": 5, "claude_cli": {"web_search_max_turns": 4}},
    )
    seen: dict[str, list] = {}

    def fake_run(args, prompt, timeout_s):
        seen["args"] = args
        return json.dumps({"result": _verdict_text("PASS"), "is_error": False})

    monkeypatch.setattr(ai_brain, "_run_cli", fake_run)
    ai_brain.ClaudeCliBrain().complete("S", "U", web_search=True)
    args = seen["args"]
    assert args[args.index("--max-turns") + 1] == "4"


def test_openrouter_and_codex_accept_web_search_kwarg(monkeypatch):
    """The research seam passes web_search unconditionally to the configured
    provider — non-claude providers must swallow it, not raise."""
    from hermes_trader.agents import ai_brain

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert ai_brain.OpenRouterBrain().complete("S", "U", web_search=True) == ""

    monkeypatch.setattr(ai_brain, "_read_ai_brain_config", lambda: {"timeout_s": 5})
    monkeypatch.setattr(
        ai_brain,
        "_run_cli",
        lambda args, prompt, timeout_s: json.dumps({"result": "no json", "is_error": False}),
    )
    assert ai_brain.CodexCliBrain().complete("S", "U", web_search=True) == ""


def test_citations_never_glue_url_to_url():
    from hermes_trader.agents import ai_brain

    class R:
        citations = ({"url_citation": {"url": "https://x.test/a", "title": "https://x.test/a"}},
                     {"url_citation": {"url": "https://x.test/b", "title": ""}},
                     {"url_citation": {"url": "https://x.test/c", "title": "Real Title"}})

    out = ai_brain.completion_citations(R())
    assert out[0] == "https://x.test/a"          # title==url -> url only
    assert out[1] == "https://x.test/b"          # no title -> url only
    assert out[2] == "Real Title — https://x.test/c"
