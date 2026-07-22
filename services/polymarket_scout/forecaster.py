"""Forecasters for the scout — the LLM edge, behind a tiny injectable contract.

ClaudeForecaster shells out to LOCAL Claude Code (per CLAUDE.md: route LLM calls
through local Claude, never a hosted API), pinned to the operator's model with
web search ON — event forecasting is exactly where search PAYS (unlike perp
candle research, where it was measured EV-neutral and disabled). Returns a
calibrated YES probability + one-paragraph reasoning, or None if it declines.

StubForecaster is the deterministic offline stand-in for tests and dry runs.
"""
from __future__ import annotations

import json
import os
import subprocess
from typing import Callable, Optional, Tuple

_MODEL = os.environ.get("POLY_SCOUT_MODEL", "claude-opus-4-8")
_CLI = os.environ.get("CLAUDE_CLI_COMMAND", "/Users/julian_dev/.local/bin/claude")
# search executor for the WebSearch tool (opus 400s on it) — Haiku, never fable.
_SEARCH_EXEC = "claude-haiku-4-5-20251001"

_SYS = (
    "You are a calibrated event forecaster. Given a prediction-market question that "
    "resolves YES or NO by a fixed date, estimate the TRUE probability of YES. Use web "
    "search for current facts. Be honest about uncertainty; do not anchor on the market. "
    "Reply with ONLY a JSON object: {\"yes_prob\": <0..1 float>, \"reasoning\": \"<2-3 sentences>\"}."
)


class StubForecaster:
    """Deterministic offline forecaster. `fn(question, description) -> (prob, why)`."""
    def __init__(self, fn: Callable[[str, str], Optional[Tuple[float, str]]]):
        self._fn = fn

    def forecast(self, question: str, description: str) -> Optional[Tuple[float, str]]:
        return self._fn(question, description)


class ClaudeForecaster:
    def __init__(self, model: str = _MODEL, timeout_s: float = 120.0,
                 runner: Optional[Callable[[list, str], str]] = None):
        self.model = model
        self.timeout_s = timeout_s
        self._runner = runner or self._run_cli

    def _run_cli(self, args: list, prompt: str) -> str:
        env = dict(os.environ)
        env["CLAUDE_CODE_SUBAGENT_MODEL"] = self.model          # never fable
        env["ANTHROPIC_SMALL_FAST_MODEL"] = _SEARCH_EXEC        # search exec = Haiku
        try:
            p = subprocess.run(args, input=prompt, capture_output=True, text=True,
                               timeout=self.timeout_s, env=env)
            return p.stdout or ""
        except Exception:
            return ""

    def forecast(self, question: str, description: str) -> Optional[Tuple[float, str]]:
        prompt = f"{_SYS}\n\nQUESTION: {question}\n\nCONTEXT: {description or '(none)'}"
        args = [_CLI, "-p", "--output-format", "json", "--max-turns", "8",
                "--tools", "WebSearch", "--safe-mode", "--no-session-persistence",
                "--model", self.model]
        raw = self._runner(args, prompt)
        if not raw:
            return None
        try:
            env = json.loads(raw)
            if env.get("is_error"):
                return None
            body = env.get("result") if isinstance(env, dict) else raw
        except Exception:
            body = raw
        return _parse_forecast(body)


def _parse_forecast(body: str) -> Optional[Tuple[float, str]]:
    """Pull {yes_prob, reasoning} out of the model's reply, tolerant of prose
    around the JSON. Clamps prob to [0.01, 0.99]."""
    if not body:
        return None
    s = body.find("{")
    e = body.rfind("}")
    if s < 0 or e <= s:
        return None
    try:
        obj = json.loads(body[s:e + 1])
        p = float(obj.get("yes_prob"))
    except Exception:
        return None
    if not (0.0 <= p <= 1.0):
        return None
    p = max(0.01, min(0.99, p))
    return p, str(obj.get("reasoning") or "")[:500]
