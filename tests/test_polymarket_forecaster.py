"""Forecaster seam: the AI-brain route (provider selection, the verdict-JSON
contract the brain validator enforces) and the reply parser's tolerance of
web-search noise. No LLM is called — the brain is injected."""
from __future__ import annotations

import json

import pytest

from services.polymarket_scout import forecaster
from services.polymarket_scout.forecaster import (
    BrainForecaster, StubForecaster, _clip, _parse_forecast,
)


class FakeBrain:
    provider = "claude_cli"

    def __init__(self, reply="", boom=False, replies=None):
        self.reply, self.boom = reply, boom
        self.replies = list(replies) if replies is not None else None
        self.calls = []

    def complete(self, system_prompt, user_message, web_search=False):
        if self.boom:
            raise RuntimeError("cli died")
        self.calls.append({"sys": system_prompt, "user": user_message, "web": web_search})
        if self.replies is not None:
            return self.replies.pop(0) if self.replies else ""
        return self.reply


# ── the brain route ──────────────────────────────────────────────────────────
def test_brain_forecaster_returns_probability_and_reasoning():
    brain = FakeBrain(json.dumps({"verdict": "NO", "yes_prob": 0.35, "reasoning": "no sign of it"}))
    out = BrainForecaster(brain=brain).forecast("Will X?", "context")
    assert out == (0.35, "no sign of it")


def test_brain_forecaster_asks_for_web_search_by_default():
    brain = FakeBrain(json.dumps({"verdict": "YES", "yes_prob": 0.6, "reasoning": "r"}))
    BrainForecaster(brain=brain).forecast("Will X?", "ctx")
    assert brain.calls[0]["web"] is True
    assert BrainForecaster(brain=FakeBrain('{"verdict":"YES","yes_prob":0.6}'),
                           web_search=False).forecast("q", "c") == (0.6, "")


def test_prompt_demands_the_verdict_key_the_brain_validator_requires():
    # ai_brain._contains_parseable_verdict_json drops any CLI reply without a
    # "verdict" key, so the system prompt MUST ask for it or every call returns ""
    assert '"verdict"' in forecaster._SYS


def test_brain_forecaster_passes_the_question_and_context_through():
    brain = FakeBrain(json.dumps({"verdict": "YES", "yes_prob": 0.6, "reasoning": "r"}))
    BrainForecaster(brain=brain).forecast("Will the ceasefire hold?", "tags: geopolitics")
    user = brain.calls[0]["user"]
    assert "Will the ceasefire hold?" in user and "tags: geopolitics" in user


def test_brain_forecaster_declines_on_an_empty_or_failed_completion():
    assert BrainForecaster(brain=FakeBrain("")).forecast("q", "c") is None
    assert BrainForecaster(brain=FakeBrain(boom=True)).forecast("q", "c") is None


def test_a_failed_search_call_retries_once_without_search():
    """Measured 2026-07-24: the search path can burn all 8 turns and exit
    non-zero having searched zero times. A prior-only forecast beats a hole in
    the board — but it must be labelled, never passed off as researched."""
    brain = FakeBrain(replies=["", json.dumps(
        {"verdict": "NO", "yes_prob": 0.2, "reasoning": "prior only"})])
    got = BrainForecaster(brain=brain).forecast("q", "c")
    assert got[0] == 0.2
    assert got[1].startswith("[no-search retry]")
    assert [c["web"] for c in brain.calls] == [True, False]


def test_no_retry_when_search_was_never_asked_for():
    brain = FakeBrain("")
    assert BrainForecaster(brain=brain, web_search=False).forecast("q", "c") is None
    assert len(brain.calls) == 1


def test_no_retry_when_the_first_call_worked():
    brain = FakeBrain(json.dumps({"verdict": "YES", "yes_prob": 0.7, "reasoning": "r"}))
    assert BrainForecaster(brain=brain).forecast("q", "c") == (0.7, "r")
    assert len(brain.calls) == 1


def test_both_attempts_failing_still_declines():
    brain = FakeBrain(replies=["", ""])
    assert BrainForecaster(brain=brain).forecast("q", "c") is None
    assert len(brain.calls) == 2


def test_brain_forecaster_exposes_the_provider():
    assert BrainForecaster(brain=FakeBrain()).provider == "claude_cli"


# ── parsing ──────────────────────────────────────────────────────────────────
def test_parse_prefers_the_last_answer_over_quoted_search_noise():
    body = ('I searched and found a page quoting {"yes_prob": 0.02, "reasoning": "someone else"}\n'
            'Final answer:\n{"verdict": "YES", "yes_prob": 0.61, "reasoning": "mine"}')
    assert _parse_forecast(body) == (0.61, "mine")


def test_parse_handles_a_fenced_object():
    assert _parse_forecast('```json\n{"verdict":"NO","yes_prob":0.2,"reasoning":"r"}\n```') == (0.2, "r")


def test_parse_handles_prose_around_a_single_object():
    body = 'Here is my estimate.\n{"verdict": "NO", "yes_prob": 0.12, "reasoning": "why"}\nThanks.'
    assert _parse_forecast(body) == (0.12, "why")


@pytest.mark.parametrize("body", [
    "", "no json here", "{}", '{"yes_prob": "banana"}', '{"yes_prob": 1.4}',
    '{"yes_prob": -0.2}', "{unclosed",
])
def test_parse_rejects_garbage(body):
    assert _parse_forecast(body) is None


def test_parse_clamps_the_extremes():
    assert _parse_forecast('{"yes_prob": 0.0}')[0] == 0.01
    assert _parse_forecast('{"yes_prob": 1.0}')[0] == 0.99


def test_reasoning_is_clipped_on_a_word_boundary():
    long = ("word " * 200).strip()
    out = _clip(long)
    assert len(out) <= 501 and out.endswith("…") and not out.endswith("wor…")


def test_clip_leaves_short_text_alone():
    assert _clip("short") == "short"


def test_stub_forecaster_is_still_the_offline_contract():
    assert StubForecaster(lambda q, d: (0.5, "x")).forecast("q", "d") == (0.5, "x")
    assert StubForecaster(lambda q, d: None).forecast("q", "d") is None


# ── env ──────────────────────────────────────────────────────────────────────
def test_load_env_local_never_overrides_an_explicit_setting(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_BRAIN_PROVIDER", "codex_cli")
    forecaster.load_env_local()
    assert forecaster.os.environ["AI_BRAIN_PROVIDER"] == "codex_cli"
