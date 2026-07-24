"""Forecasters for the scout — the LLM edge, behind a tiny injectable contract.

Three implementations, one contract (`forecast(question, description) ->
(yes_prob, reasoning) | None`):

- **BrainForecaster (default)** routes through the project's own AI brain
  (`hermes_trader.agents.ai_brain.get_brain()`), so a Polymarket forecast uses
  the SAME provider the trading engine uses — currently `claude_cli` via
  `AI_BRAIN_PROVIDER` in `.env.local`. One brain, one place to swap models, one
  place that already handles envelopes, timeouts, model pinning and the
  no-hosted-API rule.
- **ClaudeForecaster** is the standalone local-Claude subprocess (kept for
  running this service with no hermes_trader import, e.g. in a bare worktree).
- **StubForecaster** is the deterministic offline stand-in for tests and dry runs.
"""
from __future__ import annotations

import json
import os
import re
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
    "Reply with ONLY a JSON object on the last line: "
    "{\"verdict\": \"YES\"|\"NO\", \"yes_prob\": <0..1 float>, \"reasoning\": \"<2-3 sentences>\"}. "
    "`verdict` is simply which side you lean (yes_prob > 0.5 -> YES); `yes_prob` is the number "
    "that matters."
)
# The hermes brain's CLI envelope validator requires a parseable object carrying a
# "verdict" key (ai_brain._contains_parseable_verdict_json). Asking for it here is
# what lets this lane reuse the trading engine's brain instead of a private client.


class StubForecaster:
    """Deterministic offline forecaster. `fn(question, description) -> (prob, why)`."""
    def __init__(self, fn: Callable[[str, str], Optional[Tuple[float, str]]]):
        self._fn = fn

    def forecast(self, question: str, description: str) -> Optional[Tuple[float, str]]:
        return self._fn(question, description)


def load_env_local() -> None:
    """Pull `.env.local` into os.environ (never overriding what is already set).

    The scout runs as its own module, not through `hermes_trader.server`, so
    nothing else has loaded the operator's env. Without this the brain selector
    falls back to `openrouter` (a hosted API) instead of the configured
    `AI_BRAIN_PROVIDER=claude_cli` — a silent violation of the local-Claude rule
    in CLAUDE.md, and a silent model change.
    """
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for p in (".env.local", os.path.join(root, ".env.local")):
        if not os.path.exists(p):
            continue
        with open(p) as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip())
        return


class BrainForecaster:
    """Forecast through the hermes AI brain — the same seam the trading engine's
    research verdicts go through.

    `web_search=True` is deliberate and lane-specific: perp candle research
    measured search EV-neutral, but event forecasting IS a news-synthesis task,
    which is the whole thesis of this service. Only claude_cli honors the flag;
    the others ignore it without erroring.
    """

    def __init__(self, brain=None, web_search: bool = True):
        self._brain = brain
        self.web_search = web_search

    @property
    def brain(self):
        if self._brain is None:
            load_env_local()
            from hermes_trader.agents.ai_brain import get_brain
            self._brain = get_brain()
        return self._brain

    @property
    def provider(self) -> str:
        return getattr(self.brain, "provider", "unknown")

    def forecast(self, question: str, description: str) -> Optional[Tuple[float, str]]:
        user = (f"QUESTION: {question}\n\n"
                f"CONTEXT (market's own resolution text): {description or '(none)'}\n\n"
                "Reply with ONLY the JSON object.")
        got = self._try(user, self.web_search)
        if got is None and self.web_search:
            # Measured failure (2026-07-24): the search path can burn all 8 turns
            # and exit non-zero with web_search_requests=0 — the model looped
            # instead of answering. One no-search retry turns that dead market
            # into a prior-only forecast, which is worth more than a hole in the
            # board. Tagged so a prior-only read is never mistaken for a
            # researched one.
            got = self._try(user, False)
            if got is not None:
                return got[0], f"[no-search retry] {got[1]}"
        return got

    def _try(self, user: str, web_search: bool) -> Optional[Tuple[float, str]]:
        try:
            body = self.brain.complete(_SYS, user, web_search=web_search)
        except Exception:
            return None
        return _parse_forecast(str(body or ""))


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
    around the JSON. Clamps prob to [0.01, 0.99].

    A web-search completion emits several JSON-ish blobs (tool traffic, quoted
    snippets), so the LAST flat object carrying `yes_prob` wins — that is the
    model's own final answer. The whole-span parse is the fallback for a clean
    single-object reply.
    """
    if not body:
        return None
    candidates = [m.group(0) for m in re.finditer(r'\{[^{}]*"yes_prob"[^{}]*\}', body)]
    candidates.reverse()
    s, e = body.find("{"), body.rfind("}")
    if s >= 0 and e > s:
        candidates.append(body[s:e + 1])
    for cand in candidates:
        try:
            obj = json.loads(re.sub(r"```json\s*|```", "", cand).strip())
            p = float(obj.get("yes_prob"))
        except Exception:
            continue
        if not (0.0 <= p <= 1.0):
            continue
        return max(0.01, min(0.99, p)), _clip(str(obj.get("reasoning") or ""))
    return None


def _clip(s: str, n: int = 500) -> str:
    """Trim the reasoning to the ledger's field width on a word boundary. A hard
    slice ends mid-word ('...more likely than not to miss the Jul') and reads as
    a bug on the dashboard card."""
    if len(s) <= n:
        return s
    cut = s[:n]
    sp = cut.rfind(" ")
    return (cut[:sp] if sp > n * 0.6 else cut).rstrip(" ,;:") + "…"
