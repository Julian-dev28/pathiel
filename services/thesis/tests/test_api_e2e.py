"""End-to-end tests for services/thesis/api.py:write_thesis(), against a
fake LLM (no subprocess, no real CLI, no cost) that returns scripted
LlmResult objects or raises scripted errors. Proves the full flow: prompt
building -> schema-constrained call -> validate -> repair (resumed, or
falling back to a full standalone call) -> assemble, or degrade to prev /
a deterministic fallback - and that write_thesis never raises and always
returns a contract-valid Thesis.
"""
from __future__ import annotations

from contracts.strategies.models import Thesis
from services.llm import LlmResult
from services.llm.errors import LlmError, LlmTimeoutError
from services.thesis import api
from services.thesis.llm_schema import ThesisDraft


def _draft_json(oil, *, scenarios=None, bear=None) -> str:
    draft = ThesisDraft(
        call=oil.thesis.call,
        scenarios=scenarios if scenarios is not None else oil.thesis.scenarios,
        bull=oil.thesis.bull,
        bear=bear if bear is not None else oil.thesis.bear,
        what_changes_my_mind=oil.thesis.what_changes_my_mind,
        lessons=oil.thesis.lessons,
        leverage_reasons=[lv.reason for lv in oil.thesis.leverage_view],
    )
    return draft.model_dump_json()


def _bad_probabilities_json(oil) -> str:
    scenarios = list(oil.thesis.scenarios)
    scenarios[0] = scenarios[0].model_copy(update={"probability": scenarios[0].probability - 0.1})
    return _draft_json(oil, scenarios=scenarios)


def _result(text, *, session_id="sess-1", input_tokens=700, output_tokens=400, cost=0.09,
            model="claude-fable-5-1"):
    return LlmResult(
        text=text, model=model, input_tokens=input_tokens, output_tokens=output_tokens,
        cost_usd=cost, duration_s=1.0, session_id=session_id,
    )


class _FakeLlm:
    """Scripted stand-in for services.llm.complete. Each entry in
    ``script`` is either an LlmResult to return or an exception instance to
    raise, consumed in order. Records every call's kwargs for inspection."""

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[dict] = []

    def __call__(self, system, user, **kwargs):
        self.calls.append({"system": system, "user": user, **kwargs})
        if not self.script:
            raise AssertionError(f"fake LLM called more times ({len(self.calls)}) than scripted")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _install(monkeypatch, script) -> _FakeLlm:
    fake = _FakeLlm(script)
    monkeypatch.setattr(api, "complete", fake)
    return fake


# -- happy path ---------------------------------------------------------------


def test_happy_path_round_trips_the_contract_with_one_call(oil, monkeypatch):
    fake = _install(monkeypatch, [_result(_draft_json(oil))])
    thesis = api.write_thesis(oil.spec, oil.analytics, oil.intel, None)

    assert isinstance(thesis, Thesis)
    assert Thesis.model_validate_json(thesis.model_dump_json()).id == thesis.id
    assert thesis.call.one_liner == oil.thesis.call.one_liner
    assert thesis.desk_id == "oil"
    assert thesis.usage.skipped_reason is None
    assert thesis.usage.input_tokens == 700
    assert thesis.usage.output_tokens == 400
    assert len(fake.calls) == 1


def test_happy_path_uses_the_full_prompt_when_there_is_no_prev(oil, monkeypatch):
    fake = _install(monkeypatch, [_result(_draft_json(oil))])
    api.write_thesis(oil.spec, oil.analytics, oil.intel, None)
    assert "MARKET SNAPSHOT" in fake.calls[0]["user"]
    assert "SMALL update" not in fake.calls[0]["user"]


def test_happy_path_first_call_persists_its_session(oil, monkeypatch):
    fake = _install(monkeypatch, [_result(_draft_json(oil))])
    api.write_thesis(oil.spec, oil.analytics, oil.intel, None)
    assert fake.calls[0]["persist_session"] is True
    assert fake.calls[0].get("resume_session") is None


def test_json_schema_pins_leverage_reasons_to_the_ladder_length(oil, monkeypatch):
    fake = _install(monkeypatch, [_result(_draft_json(oil))])
    api.write_thesis(oil.spec, oil.analytics, oil.intel, None)
    schema = fake.calls[0]["json_schema"]
    assert schema["properties"]["leverage_reasons"]["minItems"] == len(oil.analytics.ladder)
    assert schema["properties"]["leverage_reasons"]["maxItems"] == len(oil.analytics.ladder)


def test_tag_identifies_the_desk(oil, monkeypatch):
    fake = _install(monkeypatch, [_result(_draft_json(oil))])
    api.write_thesis(oil.spec, oil.analytics, oil.intel, None)
    assert fake.calls[0]["tag"] == "thesis:oil"


def test_max_output_tokens_comes_from_the_desks_token_budget(oil, monkeypatch):
    fake = _install(monkeypatch, [_result(_draft_json(oil))])
    api.write_thesis(oil.spec, oil.analytics, oil.intel, None)
    assert fake.calls[0]["max_output_tokens"] == oil.spec.token_budget.max_output_tokens


def test_prints_a_token_report_line(oil, monkeypatch, capsys):
    _install(monkeypatch, [_result(_draft_json(oil))])
    api.write_thesis(oil.spec, oil.analytics, oil.intel, None)
    out = capsys.readouterr().out
    assert "[thesis] oil:" in out
    assert "input=700" in out and "output=400" in out


# -- incremental mode -----------------------------------------------------------


def test_incremental_prompt_used_when_prev_exists_and_change_is_small(oil, monkeypatch):
    fake = _install(monkeypatch, [_result(_draft_json(oil))])
    api.write_thesis(oil.spec, oil.analytics, oil.intel, oil.thesis)
    assert "SMALL update" in fake.calls[0]["user"]


def test_incremental_report_line_says_incremental(oil, monkeypatch, capsys):
    _install(monkeypatch, [_result(_draft_json(oil))])
    api.write_thesis(oil.spec, oil.analytics, oil.intel, oil.thesis)
    assert "mode=incremental" in capsys.readouterr().out


def test_full_prompt_used_when_prev_exists_but_price_moved_a_lot(oil, monkeypatch):
    moved = oil.analytics.model_copy(update={
        "instruments": [
            snap.model_copy(update={"mid": snap.mid * 1.10}) if snap.coin == "xyz:CL" else snap
            for snap in oil.analytics.instruments
        ],
    })
    # The draft still carries the OLD entry_px (99.339), now far from the
    # moved mid (~109.3) - check_entry_near_mid correctly fails it, so this
    # scripts a repair round too. The repair draft is the same stale content
    # and fails the same way, which is fine: this test only cares what the
    # FIRST call's prompt looked like.
    fake = _install(monkeypatch, [_result(_draft_json(oil)), _result(_draft_json(oil))])
    api.write_thesis(oil.spec, moved, oil.intel, oil.thesis)
    assert "SMALL update" not in fake.calls[0]["user"]
    assert "MARKET SNAPSHOT" in fake.calls[0]["user"]


# -- repair loop: resumed ------------------------------------------------------


def test_validator_failure_triggers_one_resumed_repair_call(oil, monkeypatch):
    fake = _install(monkeypatch, [
        _result(_bad_probabilities_json(oil), session_id="sess-123"),
        _result(_draft_json(oil)),
    ])
    thesis = api.write_thesis(oil.spec, oil.analytics, oil.intel, None)

    assert len(fake.calls) == 2
    assert fake.calls[1]["resume_session"] == "sess-123"
    assert "MARKET SNAPSHOT" not in fake.calls[1]["user"]  # short repair message, not a resend
    assert "sum to" in fake.calls[1]["user"]
    assert thesis.usage.skipped_reason is None
    assert thesis.usage.input_tokens == 700 + 700  # both calls counted


def test_repair_resume_failure_falls_back_to_a_full_standalone_call(oil, monkeypatch):
    fake = _install(monkeypatch, [
        _result(_bad_probabilities_json(oil), session_id="sess-dead"),
        LlmTimeoutError("could not resume sess-dead"),
        _result(_draft_json(oil)),
    ])
    thesis = api.write_thesis(oil.spec, oil.analytics, oil.intel, None)

    assert len(fake.calls) == 3
    assert fake.calls[1]["resume_session"] == "sess-dead"
    assert fake.calls[2].get("resume_session") is None
    assert "MARKET SNAPSHOT" in fake.calls[2]["user"]  # fell back to full context
    assert thesis.usage.skipped_reason is None


def test_repair_failing_twice_with_prev_rejects_and_returns_prev(oil, monkeypatch):
    fake = _install(monkeypatch, [
        _result(_bad_probabilities_json(oil)),
        _result(_bad_probabilities_json(oil)),
    ])
    thesis = api.write_thesis(oil.spec, oil.analytics, oil.intel, oil.thesis)

    assert len(fake.calls) == 2
    assert thesis.id == oil.thesis.id  # it is prev, annotated
    assert thesis.call == oil.thesis.call
    assert thesis.usage.skipped_reason is not None
    assert "validator rejected after repair" in thesis.usage.skipped_reason


def test_repair_failing_twice_with_no_prev_returns_a_flat_fallback(oil, monkeypatch):
    fake = _install(monkeypatch, [
        _result(_bad_probabilities_json(oil)),
        _result(_bad_probabilities_json(oil)),
    ])
    thesis = api.write_thesis(oil.spec, oil.analytics, oil.intel, None)

    assert len(fake.calls) == 2
    assert thesis.call.side == "flat"
    assert thesis.call.confidence == 0.0
    assert thesis.usage.skipped_reason is not None


def test_malformed_json_on_first_call_triggers_repair_too(oil, monkeypatch):
    fake = _install(monkeypatch, [
        _result("this is not JSON at all {{{"),
        _result(_draft_json(oil)),
    ])
    thesis = api.write_thesis(oil.spec, oil.analytics, oil.intel, None)
    assert len(fake.calls) == 2
    assert thesis.usage.skipped_reason is None
    assert "parse/validate" in fake.calls[1]["user"]


# -- hard LLM failures ----------------------------------------------------------


def test_llm_error_on_the_only_call_with_prev_rejects_and_returns_prev(oil, monkeypatch):
    _install(monkeypatch, [LlmTimeoutError("CLI timed out after 120s")])
    thesis = api.write_thesis(oil.spec, oil.analytics, oil.intel, oil.thesis)
    assert thesis.id == oil.thesis.id
    assert "LLM call failed" in thesis.usage.skipped_reason
    assert "timed out" in thesis.usage.skipped_reason


def test_llm_error_on_the_only_call_with_no_prev_returns_fallback(oil, monkeypatch):
    _install(monkeypatch, [LlmError("binary not found")])
    thesis = api.write_thesis(oil.spec, oil.analytics, oil.intel, None)
    assert thesis.call.side == "flat"
    assert "LLM call failed" in thesis.usage.skipped_reason


def test_write_thesis_never_raises_regardless_of_what_the_llm_does(oil, monkeypatch):
    """Unlike the scripted tests above, these fakes never run out - the
    point is that write_thesis degrades no matter how long the LLM keeps
    misbehaving, not that it stops after some specific call count."""

    def _always_llm_error(system, user, **kwargs):
        raise LlmError("down")

    def _always_garbage(system, user, **kwargs):
        return _result("garbage, not JSON")

    calls = {"n": 0}

    def _garbage_then_always_down(system, user, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return _result("garbage, not JSON")
        raise LlmError("down")

    for fake_complete in (_always_llm_error, _always_garbage, _garbage_then_always_down):
        calls["n"] = 0
        monkeypatch.setattr(api, "complete", fake_complete)
        thesis = api.write_thesis(oil.spec, oil.analytics, oil.intel, None)
        assert isinstance(thesis, Thesis)
        assert Thesis.model_validate_json(thesis.model_dump_json()).id == thesis.id


# -- usage combination ----------------------------------------------------------


def test_combine_usage_sums_tokens_and_cost_across_calls():
    calls = [
        LlmResult(text="a", model="m", input_tokens=100, output_tokens=50, cost_usd=0.01, duration_s=1.0),
        LlmResult(text="b", model="m", input_tokens=200, output_tokens=30, cost_usd=0.02, duration_s=2.0),
    ]
    usage = api._combine_usage(calls)
    assert usage.input_tokens == 300
    assert usage.output_tokens == 80
    assert abs(usage.cost_usd - 0.03) < 1e-9
    assert usage.duration_s == 3.0
    assert usage.model == "m"


def test_combine_usage_handles_missing_fields_gracefully():
    calls = [LlmResult(text="a", model="m", input_tokens=None, output_tokens=None, cost_usd=None, duration_s=1.0)]
    usage = api._combine_usage(calls)
    assert usage.input_tokens is None
    assert usage.cost_usd is None
    assert usage.duration_s == 1.0


def test_combine_usage_empty_list():
    usage = api._combine_usage([])
    assert usage.model is None
    assert usage.input_tokens is None
