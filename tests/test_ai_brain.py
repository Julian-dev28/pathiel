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


def test_openrouter_402_affordability_retry_preserved(monkeypatch):
    from hermes_trader.agents import ai_brain

    calls: list[int] = []

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
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json, headers):
            calls.append(int(json["max_tokens"]))
            if len(calls) == 1:
                return Response(402, text="You requested too much, can only afford 842 tokens")
            return Response(200, data={"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv("OPENROUTER_MAX_TOKENS", raising=False)
    monkeypatch.setattr(ai_brain.httpx, "AsyncClient", FakeAsyncClient)

    assert ai_brain.OpenRouterBrain().complete("system", "user") == "ok"
    assert calls == [2048, 792]


def test_claude_cli_parses_envelope_and_requires_verdict_json(monkeypatch):
    from hermes_trader.agents import ai_brain

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
        return json.dumps({"result": _verdict_text("LONG"), "is_error": False})

    monkeypatch.setattr(ai_brain, "_run_cli", fake_run)

    out = ai_brain.ClaudeCliBrain().complete("SYSTEM", "USER")
    assert '"verdict":"LONG"' in out
    assert seen["prompt"] == "SYSTEM\n\nUSER"
    assert "--tools" in seen["args"]
    assert seen["timeout_s"] == 5


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
