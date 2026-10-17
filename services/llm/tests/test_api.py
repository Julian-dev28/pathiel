"""Unit tests for services/llm/api.py: complete(), LlmResult, and the pure
helpers. run_cli is monkeypatched at the api module boundary, so none of
this touches a real subprocess or spends real money.
"""
from __future__ import annotations

import json
import threading
import time

import pytest

from services.llm import api, config
from services.llm.errors import LlmError, LlmTimeoutError


def _envelope(result="{}", model="claude-fable-5-1", input_tokens=100, output_tokens=50,
              cost=0.01, num_turns=1, session_id="sess-1"):
    return {
        "result": result,
        "is_error": False,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        "total_cost_usd": cost,
        "modelUsage": {model: {}},
        "num_turns": num_turns,
        "session_id": session_id,
    }


def test_complete_returns_llm_result_with_canonical_model(monkeypatch):
    monkeypatch.setenv("PATHIEL_LLM_MODEL", "fable")
    captured = {}

    def _fake_run_cli(args, user_text, timeout_s, env=None):
        captured["args"] = args
        captured["user_text"] = user_text
        return _envelope()

    monkeypatch.setattr(api, "run_cli", _fake_run_cli)
    result = api.complete("SYSTEM", "USER TEXT", max_output_tokens=500)

    assert isinstance(result, api.LlmResult)
    assert result.text == "{}"
    assert result.model == "claude-fable-5-1"  # canonical id, not the "fable" alias we asked for
    assert result.input_tokens == 100
    assert result.output_tokens == 50
    assert result.cost_usd == 0.01
    assert result.num_turns == 1
    assert result.session_id == "sess-1"
    assert result.duration_s >= 0
    assert captured["user_text"] == "USER TEXT"
    assert captured["args"][captured["args"].index("--system-prompt") + 1] == "SYSTEM"
    assert captured["args"][captured["args"].index("--model") + 1] == "fable"


def test_complete_explicit_model_skips_probing(monkeypatch):
    def _probe_must_not_run(*a, **k):
        raise AssertionError("probe_default_model must not run when model= is given")

    monkeypatch.setattr(api.model_probe, "probe_default_model", _probe_must_not_run)
    monkeypatch.setattr(api, "run_cli", lambda *a, **k: _envelope(model="claude-opus-5-5"))
    result = api.complete("S", "U", max_output_tokens=500, model="claude-opus-5-5")
    assert result.model == "claude-opus-5-5"


def test_complete_web_search_requests_web_search_tool_and_more_turns(monkeypatch):
    captured = {}

    def _fake_run_cli(args, user_text, timeout_s, env=None):
        captured["args"] = args
        captured["env"] = env
        return _envelope()

    monkeypatch.setattr(api, "run_cli", _fake_run_cli)
    api.complete("S", "U", max_output_tokens=500, model="fable", web_search=True)

    args = captured["args"]
    assert args[args.index("--tools") + 1] == "WebSearch"
    assert args[args.index("--max-turns") + 1] == str(config.WEB_SEARCH_MAX_TURNS)
    assert captured["env"]["ANTHROPIC_SMALL_FAST_MODEL"] == config.WEB_SEARCH_EXEC_MODEL
    assert captured["env"]["CLAUDE_CODE_SUBAGENT_MODEL"] == "fable"


def test_complete_without_web_search_pins_aux_model_to_the_brain_model(monkeypatch):
    captured = {}

    def _fake_run_cli(args, user_text, timeout_s, env=None):
        captured["args"] = args
        captured["env"] = env
        return _envelope()

    monkeypatch.setattr(api, "run_cli", _fake_run_cli)
    api.complete("S", "U", max_output_tokens=500, model="opus", web_search=False)

    args = captured["args"]
    assert args[args.index("--tools") + 1] == ""
    assert args[args.index("--max-turns") + 1] == str(config.DEFAULT_MAX_TURNS)
    assert captured["env"]["ANTHROPIC_SMALL_FAST_MODEL"] == "opus"
    assert captured["env"]["CLAUDE_CODE_SUBAGENT_MODEL"] == "opus"


def test_complete_passes_json_schema_through_verbatim(monkeypatch):
    schema = {"type": "object", "properties": {"call": {"type": "object"}}, "required": ["call"]}
    captured = {}

    def _fake_run_cli(args, user_text, timeout_s, env=None):
        captured["args"] = args
        return _envelope()

    monkeypatch.setattr(api, "run_cli", _fake_run_cli)
    api.complete("S", "U", max_output_tokens=500, model="fable", json_schema=schema)

    args = captured["args"]
    schema_arg = args[args.index("--json-schema") + 1]
    assert json.loads(schema_arg) == schema


def test_complete_persist_session_and_resume_reach_build_args(monkeypatch):
    captured = {}

    def _fake_run_cli(args, user_text, timeout_s, env=None):
        captured["args"] = args
        return _envelope(session_id="sess-xyz")

    monkeypatch.setattr(api, "run_cli", _fake_run_cli)
    result = api.complete("S", "U", max_output_tokens=500, model="fable", persist_session=True)
    assert "--no-session-persistence" not in captured["args"]
    assert result.session_id == "sess-xyz"

    api.complete("S", "fix it", max_output_tokens=500, model="fable", resume_session="sess-xyz")
    args2 = captured["args"]
    assert args2[args2.index("--resume") + 1] == "sess-xyz"
    assert "--no-session-persistence" in args2


def test_complete_clamps_timeout_to_the_configured_ceiling(monkeypatch):
    captured = {}

    def _fake_run_cli(args, user_text, timeout_s, env=None):
        captured["timeout_s"] = timeout_s
        return _envelope()

    monkeypatch.setattr(api, "run_cli", _fake_run_cli)
    api.complete("S", "U", max_output_tokens=500, model="fable", timeout_s=999999.0)
    assert captured["timeout_s"] == config.MAX_TIMEOUT_S


def test_complete_raises_and_logs_failure_to_usage_log(monkeypatch):
    def _fake_run_cli(args, user_text, timeout_s, env=None):
        raise LlmTimeoutError("CLI timed out after 180s")

    monkeypatch.setattr(api, "run_cli", _fake_run_cli)
    with pytest.raises(LlmError):
        api.complete("S", "U", max_output_tokens=500, model="fable", tag="thesis:oil")

    lines = open(config.usage_log_path()).read().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["ok"] is False
    assert record["tag"] == "thesis:oil"
    assert "timed out" in record["error"]


def test_complete_success_is_logged_with_tag_and_tokens(monkeypatch):
    monkeypatch.setattr(api, "run_cli", lambda *a, **k: _envelope(input_tokens=5200, output_tokens=1400, cost=0.09))
    api.complete("S", "U", max_output_tokens=1800, model="fable", tag="thesis:oil")

    record = json.loads(open(config.usage_log_path()).read().splitlines()[0])
    assert record["ok"] is True
    assert record["tag"] == "thesis:oil"
    assert record["input_tokens"] == 5200
    assert record["output_tokens"] == 1400
    assert record["cost_usd"] == 0.09


def test_complete_warns_when_output_far_exceeds_requested_budget(monkeypatch, caplog):
    import logging
    monkeypatch.setattr(api, "run_cli", lambda *a, **k: _envelope(output_tokens=10_000))
    with caplog.at_level(logging.WARNING):
        api.complete("S", "U", max_output_tokens=100, model="fable")
    assert "far" in caplog.text and "exceeds" in caplog.text


# -- concurrency ------------------------------------------------------------


def test_concurrency_limit_bounds_simultaneous_cli_calls(monkeypatch):
    monkeypatch.setenv("PATHIEL_LLM_CONCURRENCY", "2")
    api._reset_semaphore_for_tests()

    in_flight = {"current": 0, "max_seen": 0}
    lock = threading.Lock()

    def _fake_run_cli(args, user_text, timeout_s, env=None):
        with lock:
            in_flight["current"] += 1
            in_flight["max_seen"] = max(in_flight["max_seen"], in_flight["current"])
        time.sleep(0.05)
        with lock:
            in_flight["current"] -= 1
        return _envelope()

    monkeypatch.setattr(api, "run_cli", _fake_run_cli)

    threads = [
        threading.Thread(target=api.complete, args=("S", "U"), kwargs={"max_output_tokens": 100, "model": "fable"})
        for _ in range(6)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert in_flight["max_seen"] <= 2


# -- pure helpers -------------------------------------------------------------


def test_budget_for_scales_with_max_output_tokens_and_has_a_floor():
    small = api._budget_for("fable", 100)
    large = api._budget_for("fable", 10_000)
    assert large > small
    assert api._budget_for("haiku", 1) >= 0.25  # floor applies even for a tiny request


def test_budget_for_unknown_model_uses_default_rate():
    assert api._budget_for("some-future-model", 1800) == api._budget_for("fable", 1800)


def test_fallback_chain_orders_remaining_tiers_after_the_chosen_model():
    assert api._fallback_chain("fable") == ("opus", "sonnet", "haiku")
    assert api._fallback_chain("opus") == ("sonnet", "haiku")
    assert api._fallback_chain("haiku") == ()


def test_fallback_chain_unknown_model_gets_a_generic_safety_net():
    assert api._fallback_chain("claude-some-unlisted-id") == ("opus", "sonnet")


def test_to_result_prefers_canonical_model_usage_key_over_requested_alias():
    envelope = _envelope(model="claude-opus-5-5")
    result = api._to_result(envelope, requested_model="opus", duration=1.23)
    assert result.model == "claude-opus-5-5"
    assert result.duration_s == 1.23


def test_to_result_falls_back_to_requested_model_when_model_usage_absent():
    envelope = _envelope()
    envelope["modelUsage"] = {}
    result = api._to_result(envelope, requested_model="fable", duration=0.5)
    assert result.model == "fable"


def test_to_result_input_tokens_sums_fresh_plus_cache_creation_plus_cache_read():
    """Regression for the live 2026-10-17 finding: a ~700-token prompt
    reported usage.input_tokens == 2 because Claude Code billed almost all
    of it under cache_creation_input_tokens. The honest total is the sum
    of all three fields - this is the exact shape observed live (fable
    ping, before --system-prompt): input_tokens=2, cache_creation=3846,
    cache_read=649."""
    envelope = {
        "result": "x", "is_error": False,
        "usage": {"input_tokens": 2, "output_tokens": 4,
                   "cache_creation_input_tokens": 3846, "cache_read_input_tokens": 649},
        "total_cost_usd": 0.0773, "modelUsage": {"claude-fable-5-1": {}},
    }
    result = api._to_result(envelope, requested_model="fable", duration=1.0)
    assert result.input_tokens == 2 + 3846 + 649
    assert result.cache_creation_input_tokens == 3846
    assert result.cache_read_input_tokens == 649


def test_to_result_input_tokens_falls_back_to_whatever_fields_are_present():
    envelope = {"result": "x", "is_error": False, "usage": {"input_tokens": 50}}
    result = api._to_result(envelope, requested_model="fable", duration=1.0)
    assert result.input_tokens == 50  # no cache fields present - sum is just the one value


def test_to_result_tolerates_missing_usage_fields():
    envelope = {"result": "x", "is_error": False}
    result = api._to_result(envelope, requested_model="fable", duration=0.1)
    assert result.input_tokens is None
    assert result.output_tokens is None
    assert result.cost_usd is None
    assert result.session_id is None
