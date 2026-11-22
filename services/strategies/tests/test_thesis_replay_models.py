"""Gate tests for services.strategies.evals.thesis_replay_models.

ClaudeCodeAdapter is tested with services.llm.complete monkeypatched (no
real CLI call, no network -- conftest.py's socket block would fail this
test otherwise). CommandAdapter is tested against a real local
subprocess (``/bin/cat`` / a tiny python one-liner) -- that is local
process execution, not network, so it stays inside the gate lane."""
from __future__ import annotations

import sys

import pytest

import services.llm as llm_mod
from services.strategies.evals import thesis_replay_models as M


# ── resolve_adapter ──────────────────────────────────────────────────────

def test_resolve_adapter_claude_code_alias():
    adapter = M.resolve_adapter("sonnet")
    assert isinstance(adapter, M.ClaudeCodeAdapter)
    assert adapter.alias == "sonnet"


@pytest.mark.parametrize("alias", M.CLAUDE_CODE_ALIASES)
def test_resolve_adapter_every_alias(alias):
    assert isinstance(M.resolve_adapter(alias), M.ClaudeCodeAdapter)


def test_resolve_adapter_command_spec():
    adapter = M.resolve_adapter("command:cat -")
    assert isinstance(adapter, M.CommandAdapter)
    assert adapter.argv == ("cat", "-")


def test_resolve_adapter_unknown_spec_raises():
    with pytest.raises(ValueError):
        M.resolve_adapter("not-a-real-model")


def test_resolve_adapter_empty_command_raises():
    with pytest.raises(ValueError):
        M.resolve_adapter("command:")


# ── ClaudeCodeAdapter ────────────────────────────────────────────────────

def test_claude_code_adapter_wraps_services_llm_complete(monkeypatch):
    captured = {}

    def fake_complete(system, user, *, max_output_tokens, model=None, json_schema=None, tag=None,
                      timeout_s=180.0, **kw):
        captured.update(system=system, user=user, max_output_tokens=max_output_tokens, model=model,
                        json_schema=json_schema, tag=tag)

        class _R:
            text = '{"ok": true}'
            model_ = "claude-sonnet-5-5"
            model = "claude-sonnet-5-5"
            input_tokens = 10
            output_tokens = 5
            cost_usd = 0.001
            duration_s = 0.2
        return _R()

    monkeypatch.setattr(llm_mod, "complete", fake_complete)
    adapter = M.ClaudeCodeAdapter("sonnet")
    result = adapter.complete("sys", "usr", max_output_tokens=100, json_schema={"a": 1}, tag="t")

    assert result.text == '{"ok": true}'
    assert result.model == "claude-sonnet-5-5"
    assert result.input_tokens == 10 and result.output_tokens == 5
    assert captured["model"] == "sonnet" and captured["tag"] == "t"
    assert captured["json_schema"] == {"a": 1}


# ── CommandAdapter ───────────────────────────────────────────────────────

def test_command_adapter_echoes_stdin_via_cat():
    adapter = M.CommandAdapter(("cat",))
    result = adapter.complete("SYS", "USR", max_output_tokens=10, json_schema=None, tag="t")
    assert "SYS" in result.text and "USR" in result.text
    assert result.model == "command:cat"


def test_command_adapter_nonzero_exit_raises():
    adapter = M.CommandAdapter((sys.executable, "-c", "import sys; sys.exit(3)"))
    with pytest.raises(RuntimeError):
        adapter.complete("sys", "usr", max_output_tokens=10, json_schema=None, tag="t")


def test_command_adapter_returns_exact_stdout():
    adapter = M.CommandAdapter((sys.executable, "-c",
                               "import sys; sys.stdin.read(); print('{\"call\": {}}')"))
    result = adapter.complete("sys", "usr", max_output_tokens=10, json_schema=None, tag="t")
    assert result.text == '{"call": {}}'


# ── ollama_installed ─────────────────────────────────────────────────────

def test_ollama_installed_returns_bool():
    assert isinstance(M.ollama_installed(), bool)
