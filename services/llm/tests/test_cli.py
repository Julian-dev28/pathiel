"""Unit tests for services/llm/cli.py against a fake subprocess.

Covers the brief's required cases (success, timeout, bad JSON, usage
parsing) plus the other failure modes run_cli distinguishes by exception
type: binary missing, non-zero exit, empty stdout, an is_error envelope, and
a non-dict envelope. Also covers build_args' argv shape for every optional
knob (model, fallback chain, json schema, budget, web-search tools).
"""
from __future__ import annotations

import json
import subprocess

import pytest

from services.llm import cli
from services.llm.errors import (
    LlmBadOutputError,
    LlmBinaryNotFoundError,
    LlmProcessError,
    LlmTimeoutError,
)


class _FakeProc:
    def __init__(self, stdout="", stderr="", returncode=0, raise_timeout=False,
                 wait_raises=False):
        self.stdout_value = stdout
        self.stderr_value = stderr
        self.returncode = returncode
        self._raise_timeout = raise_timeout
        self._wait_raises = wait_raises
        self.pid = 4242

    def communicate(self, input=None, timeout=None):
        if self._raise_timeout:
            raise subprocess.TimeoutExpired(cmd="claude", timeout=timeout)
        return self.stdout_value, self.stderr_value

    def wait(self, timeout=None):
        if self._wait_raises:
            raise subprocess.TimeoutExpired(cmd="claude", timeout=timeout)
        return self.returncode

    def kill(self):
        pass


def _patch_popen(monkeypatch, proc_or_factory):
    def _fake_popen(args, **kwargs):
        if callable(proc_or_factory) and not isinstance(proc_or_factory, _FakeProc):
            return proc_or_factory(args, **kwargs)
        return proc_or_factory

    monkeypatch.setattr(subprocess, "Popen", _fake_popen)


# -- run_cli: success / usage parsing ------------------------------------


def test_run_cli_success_returns_parsed_envelope(monkeypatch):
    envelope = {
        "result": "hello desk",
        "is_error": False,
        "usage": {"input_tokens": 123, "output_tokens": 45},
        "total_cost_usd": 0.0123,
        "modelUsage": {"claude-fable-5-1": {}},
    }
    _patch_popen(monkeypatch, _FakeProc(stdout=json.dumps(envelope)))
    out = cli.run_cli(["claude", "-p"], "hi", timeout_s=5)
    assert out["result"] == "hello desk"
    assert out["usage"]["input_tokens"] == 123
    assert out["usage"]["output_tokens"] == 45
    assert out["total_cost_usd"] == 0.0123


# -- run_cli: failure modes ------------------------------------------------


def test_run_cli_binary_not_found(monkeypatch):
    def _raise(args, **kwargs):
        raise FileNotFoundError()

    monkeypatch.setattr(subprocess, "Popen", _raise)
    with pytest.raises(LlmBinaryNotFoundError, match="not found"):
        cli.run_cli(["nonexistent-binary"], "hi", timeout_s=5)


def test_run_cli_timeout_sends_sigterm_to_process_group(monkeypatch):
    _patch_popen(monkeypatch, _FakeProc(raise_timeout=True))
    sent = []
    monkeypatch.setattr(cli.os, "killpg", lambda pid, sig: sent.append((pid, sig)))
    with pytest.raises(LlmTimeoutError, match="timed out"):
        cli.run_cli(["claude"], "hi", timeout_s=0.01)
    assert sent == [(4242, cli.signal.SIGTERM)]


def test_run_cli_timeout_escalates_to_sigkill_if_sigterm_does_not_land(monkeypatch):
    _patch_popen(monkeypatch, _FakeProc(raise_timeout=True, wait_raises=True))
    sent = []
    monkeypatch.setattr(cli.os, "killpg", lambda pid, sig: sent.append(sig))
    with pytest.raises(LlmTimeoutError):
        cli.run_cli(["claude"], "hi", timeout_s=0.01)
    assert sent == [cli.signal.SIGTERM, cli.signal.SIGKILL]


def test_run_cli_nonzero_exit_raises_process_error_with_stderr_tail(monkeypatch):
    _patch_popen(monkeypatch, _FakeProc(stdout="", stderr="permission denied", returncode=1))
    with pytest.raises(LlmProcessError, match="permission denied"):
        cli.run_cli(["claude"], "hi", timeout_s=5)


def test_run_cli_empty_stdout_raises_bad_output(monkeypatch):
    _patch_popen(monkeypatch, _FakeProc(stdout="   \n  ", returncode=0))
    with pytest.raises(LlmBadOutputError, match="empty"):
        cli.run_cli(["claude"], "hi", timeout_s=5)


def test_run_cli_non_json_stdout_raises_bad_output(monkeypatch):
    _patch_popen(monkeypatch, _FakeProc(stdout="{not json at all", returncode=0))
    with pytest.raises(LlmBadOutputError, match="valid JSON"):
        cli.run_cli(["claude"], "hi", timeout_s=5)


def test_run_cli_non_dict_json_raises_bad_output(monkeypatch):
    _patch_popen(monkeypatch, _FakeProc(stdout=json.dumps([1, 2, 3]), returncode=0))
    with pytest.raises(LlmBadOutputError, match="JSON object"):
        cli.run_cli(["claude"], "hi", timeout_s=5)


def test_run_cli_is_error_envelope_raises_bad_output(monkeypatch):
    envelope = {"is_error": True, "result": "overloaded_error: try again"}
    _patch_popen(monkeypatch, _FakeProc(stdout=json.dumps(envelope), returncode=0))
    with pytest.raises(LlmBadOutputError, match="overloaded_error"):
        cli.run_cli(["claude"], "hi", timeout_s=5)


# -- build_args -------------------------------------------------------------


def test_build_args_minimal_shape():
    args = cli.build_args(binary="claude", model=None, system_prompt="SYS", max_turns=1, tools="")
    assert args[0] == "claude"
    assert args[1] == "-p"
    assert "--output-format" in args and args[args.index("--output-format") + 1] == "json"
    assert args[args.index("--system-prompt") + 1] == "SYS"
    assert args[args.index("--max-turns") + 1] == "1"
    assert args[args.index("--tools") + 1] == ""
    assert "--safe-mode" in args
    assert "--no-session-persistence" in args
    assert "--model" not in args
    assert "--json-schema" not in args
    assert "--fallback-model" not in args
    assert "--max-budget-usd" not in args


def test_build_args_full_shape_with_every_optional_knob():
    schema = {"type": "object", "properties": {"call": {"type": "object"}}}
    args = cli.build_args(
        binary="/opt/claude",
        model="fable",
        system_prompt="You are a desk trader.",
        max_turns=8,
        tools="WebSearch",
        json_schema=schema,
        fallback_models=("opus", "sonnet"),
        max_budget_usd=1.5,
    )
    assert args[0] == "/opt/claude"
    assert args[args.index("--model") + 1] == "fable"
    assert args[args.index("--tools") + 1] == "WebSearch"
    assert args[args.index("--max-turns") + 1] == "8"
    assert args[args.index("--fallback-model") + 1] == "opus,sonnet"
    assert args[args.index("--max-budget-usd") + 1] == "1.5000"
    schema_arg = args[args.index("--json-schema") + 1]
    assert json.loads(schema_arg) == schema


def test_build_args_omits_model_flag_when_model_is_empty_string():
    """An empty model means 'let the CLI pick its own default' - the flag
    must be omitted entirely, not passed as --model ''."""
    args = cli.build_args(binary="claude", model="", system_prompt="SYS", max_turns=1, tools="")
    assert "--model" not in args


def test_build_args_default_always_disables_session_persistence():
    args = cli.build_args(binary="claude", model=None, system_prompt="SYS", max_turns=1, tools="")
    assert "--no-session-persistence" in args
    assert "--resume" not in args


def test_build_args_persist_session_omits_the_no_persistence_flag():
    args = cli.build_args(
        binary="claude", model=None, system_prompt="SYS", max_turns=1, tools="",
        persist_session=True,
    )
    assert "--no-session-persistence" not in args


def test_build_args_resume_session_adds_resume_flag_and_still_disables_persistence():
    args = cli.build_args(
        binary="claude", model=None, system_prompt="SYS", max_turns=1, tools="",
        resume_session="sess-abc-123",
    )
    assert args[args.index("--resume") + 1] == "sess-abc-123"
    # Resuming for one last turn should still clean the session up afterward
    # unless the caller explicitly asks to keep it open further.
    assert "--no-session-persistence" in args
