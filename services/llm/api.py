"""services/llm - the project's one LLM gateway.

Shells out to the local Claude Code CLI
(``/Users/julian_dev/.local/bin/claude -p ...``). Hosted LLM APIs (Anthropic
API, OpenAI, any hosted inference endpoint) are banned by project rule
(CLAUDE.md, "LLM access") unless Julien explicitly instructs otherwise - this
module is meant to be the only sanctioned path from the rest of the codebase
to an LLM. The invocation mechanics were ported from the bot-era
``pathiel/agents/ai_brain.py`` ``claude_cli`` provider (deleted 2026-10-25;
services/llm/cli.py's docstring spells out the exact reuse and the one
deliberate deviation).

Contract (exact names/types; the four kwargs after ``timeout_s`` are this
implementation's own additions and default such that a caller using only the
documented minimum signature is unaffected)::

    complete(system: str, user: str, *, max_output_tokens: int,
              model: str | None = None, web_search: bool = False,
              timeout_s: float = 180,
              json_schema: Mapping[str, Any] | None = None,
              tag: str | None = None) -> LlmResult

``json_schema`` turns on the CLI's native ``--json-schema`` structured-output
mode (verified live 2026-10-17 against a schema with nested ``$defs``/``$ref``
- see README). services/thesis uses this to make its latent-field JSON
reliably parseable without scraping free text for a trailing ``{...}``.
``tag`` is a free-text label (e.g. ``"thesis:oil"``) that rides into the
usage log so a human can grep ``usage.jsonl`` by caller instead of guessing
from timestamps.

Known limit, stated plainly rather than silently false-advertised: the CLI
has no ``--max-output-tokens`` equivalent (checked: ``claude -p --help``, no
such flag or documented env var). ``max_output_tokens`` is therefore
enforced only indirectly: (1) ``--json-schema`` bounds the *shape* of the
response, which in practice bounds its length too; (2) ``--max-budget-usd``
is passed as a dollar backstop derived from a conservative per-model rate
table, so a genuinely runaway response is aborted on cost, not length; (3)
the caller (services/thesis) is expected to put a token instruction in its
own system prompt text. ``LlmResult.output_tokens`` is always the real,
post-hoc count from the CLI's own usage block - treat it as a measurement to
react to, not evidence the limit was enforced server-side.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from services.llm import config, model_probe, usage_log
from services.llm.cli import build_args, run_cli
from services.llm.errors import LlmError

logger = logging.getLogger(__name__)

# Conservative $/1K-output-token LIST price ceiling per alias, used only to
# derive --max-budget-usd (see module docstring). Deliberately well above the
# rates observed in the 2026-10-17 probe (README has the numbers) so this
# backstop fires on a genuine runaway, not a normal verbose answer.
_RATE_PER_1K_OUTPUT_USD: dict[str, float] = {
    "fable": 0.09,
    "opus": 0.09,
    "sonnet": 0.03,
    "haiku": 0.006,
}
_DEFAULT_RATE_PER_1K_OUTPUT_USD = 0.09
_MODEL_FALLBACK_ORDER = ("fable", "opus", "sonnet", "haiku")

_sem_lock = threading.Lock()
_sem: threading.BoundedSemaphore | None = None


@dataclass(frozen=True)
class LlmResult:
    """What one completion cost and returned.

    ``model`` is the CANONICAL id the CLI actually billed (e.g.
    "claude-fable-5-1"), read back from the envelope's ``modelUsage`` keys -
    not the alias that was requested, so two calls that both pass
    ``model="fable"`` but land on different point releases are
    distinguishable in the usage log.

    ``input_tokens`` is the TOTAL prompt-side size: fresh
    ``usage.input_tokens`` plus ``cache_creation_input_tokens`` plus
    ``cache_read_input_tokens``, not ``usage.input_tokens`` alone. Verified
    live 2026-10-17: a ~700-token system+user prompt reported
    ``usage.input_tokens == 2`` because almost all of it landed in
    ``cache_creation_input_tokens`` (first use of that system prompt text)
    - the bare field would have made every token-budget comparison a lie.
    """

    text: str
    model: str
    input_tokens: Optional[int]
    output_tokens: Optional[int]
    cost_usd: Optional[float]
    duration_s: float
    cache_read_input_tokens: Optional[int] = None
    cache_creation_input_tokens: Optional[int] = None
    num_turns: Optional[int] = None
    session_id: Optional[str] = None
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False)


def complete(
    system: str,
    user: str,
    *,
    max_output_tokens: int,
    model: str | None = None,
    web_search: bool = False,
    timeout_s: float = 180.0,
    json_schema: Mapping[str, Any] | None = None,
    tag: str | None = None,
    persist_session: bool = False,
    resume_session: str | None = None,
    effort: str | None = None,
) -> LlmResult:
    """Run one completion through the local Claude Code CLI.

    Raises ``services.llm.errors.LlmError`` (or a subclass) on any failure:
    binary missing, timeout, non-zero exit, empty/non-JSON/error envelope.
    Every call - success or failure - is appended to
    ``$PATHIEL_STATE_DIR/llm/usage.jsonl``.

    ``persist_session`` / ``resume_session`` are additive, off by default
    (identical behavior to every call made before they existed). Pass
    ``persist_session=True`` to keep this turn's session on disk so a later,
    cheaper follow-up can resume it with ``resume_session=<LlmResult.session_id>``
    instead of re-sending everything - see services/thesis's repair round for
    the motivating use.

    Tools are OFF unless ``web_search=True`` (``--tools ""`` - the CLI's own
    documented "disable every built-in tool" flag, ``claude --help``), so a
    caller that only wants latent-field JSON (every services/thesis call)
    never gives the model anything to invoke. ``max_turns`` is
    config.WEB_SEARCH_MAX_TURNS with web_search, else
    config.JSON_SCHEMA_MAX_TURNS when ``json_schema`` is given (its own
    docstring has the 2026-10-23 incident this fixes - a --json-schema call's
    structured output reports back as stop_reason "tool_use" even with no
    tool registered, and needs room for one internal retry), else
    config.DEFAULT_MAX_TURNS.
    """
    resolved_model = model or model_probe.probe_default_model()
    if web_search:
        max_turns = config.WEB_SEARCH_MAX_TURNS
    elif json_schema is not None:
        # 2026-10-31: a --json-schema call's structured output comes back as
        # stop_reason "tool_use" even with no agentic tool registered - see
        # config.JSON_SCHEMA_MAX_TURNS's docstring for the 2026-10-23 thesis
        # replay incident this fixes (error_max_turns on a pure JSON-out call).
        max_turns = config.JSON_SCHEMA_MAX_TURNS
    else:
        max_turns = config.DEFAULT_MAX_TURNS
    tools = "WebSearch" if web_search else ""
    budget = _budget_for(resolved_model, max_output_tokens)
    fallback_models = _fallback_chain(resolved_model)

    args = build_args(
        binary=config.claude_binary(),
        model=resolved_model or None,
        system_prompt=system,
        max_turns=max_turns,
        tools=tools,
        json_schema=json_schema,
        fallback_models=fallback_models,
        max_budget_usd=budget,
        persist_session=persist_session,
        resume_session=resume_session,
        effort=effort,
    )
    env = _subprocess_env(resolved_model, web_search)
    bounded_timeout = config.timeout_ceiling(timeout_s)

    t0 = time.monotonic()
    try:
        with _semaphore():
            envelope = run_cli(args, user, bounded_timeout, env=env)
    except LlmError as exc:
        duration = time.monotonic() - t0
        usage_log.append_usage({
            "tag": tag,
            "model": resolved_model,
            "web_search": web_search,
            "max_output_tokens": max_output_tokens,
            "ok": False,
            "error": str(exc),
            "duration_s": round(duration, 3),
        })
        raise
    duration = time.monotonic() - t0

    result = _to_result(envelope, resolved_model, duration)
    usage_log.append_usage({
        "tag": tag,
        "model": result.model,
        "web_search": web_search,
        "max_output_tokens": max_output_tokens,
        "ok": True,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "cost_usd": result.cost_usd,
        "duration_s": round(result.duration_s, 3),
    })
    if result.output_tokens is not None and result.output_tokens > max_output_tokens * 1.5:
        logger.warning(
            f"[llm] {tag or resolved_model}: output_tokens={result.output_tokens} far "
            f"exceeds the requested max_output_tokens={max_output_tokens} - the CLI has "
            f"no hard output-token cap; this is a measurement, not an enforcement failure"
        )
    return result


def _semaphore() -> threading.BoundedSemaphore:
    """Lazily built so config.concurrency_limit() is read on first USE, not
    at import time - a test can set PATHIEL_LLM_CONCURRENCY right up until
    the first complete() call in its process."""
    global _sem
    if _sem is None:
        with _sem_lock:
            if _sem is None:
                _sem = threading.BoundedSemaphore(config.concurrency_limit())
    return _sem


def _reset_semaphore_for_tests() -> None:
    """Test-only: drop the cached semaphore so the next complete() call
    re-reads PATHIEL_LLM_CONCURRENCY instead of reusing a stale limit."""
    global _sem
    with _sem_lock:
        _sem = None


def _budget_for(model: str, max_output_tokens: int) -> float:
    rate = _RATE_PER_1K_OUTPUT_USD.get(model, _DEFAULT_RATE_PER_1K_OUTPUT_USD)
    # 3x headroom over a naive "just the output" estimate: cache-creation and
    # thinking tokens are billed too and are invisible ahead of the call.
    budget = (max(1, max_output_tokens) / 1000.0) * rate * 3
    return max(0.25, round(budget, 4))


def _fallback_chain(model: str) -> Sequence[str]:
    if model in _MODEL_FALLBACK_ORDER:
        return tuple(_MODEL_FALLBACK_ORDER[_MODEL_FALLBACK_ORDER.index(model) + 1:])
    return ("opus", "sonnet")


def _subprocess_env(model: str, web_search: bool) -> dict[str, str] | None:
    """Pins every model knob (ported from the retired ai_brain._cli_env):
    knob so no auxiliary model (fable/haiku riding along for free) ever does
    brain work. Only when web_search is requested does a small model
    (haiku) get the nod - to execute the search tool, not to write the
    answer - because the brain model itself 400s on the native WebSearch
    tool at high effort on at least one model family (ai_brain's comment:
    "output_config.effort 'xhigh' not supported")."""
    if not model:
        return None
    env = dict(os.environ)
    env["CLAUDE_CODE_SUBAGENT_MODEL"] = model
    env["ANTHROPIC_SMALL_FAST_MODEL"] = config.WEB_SEARCH_EXEC_MODEL if web_search else model
    return env


def _sum_present(*values: Optional[int]) -> Optional[int]:
    present = [v for v in values if v is not None]
    return sum(present) if present else None


def _to_result(envelope: Mapping[str, Any], requested_model: str, duration: float) -> LlmResult:
    usage = envelope.get("usage") or {}
    if not isinstance(usage, Mapping):
        usage = {}
    model_usage = envelope.get("modelUsage") or {}
    canonical_model = requested_model
    if isinstance(model_usage, Mapping) and model_usage:
        canonical_model = next(iter(model_usage)) or requested_model

    def _int_or_none(v: Any) -> Optional[int]:
        try:
            return int(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    def _float_or_none(v: Any) -> Optional[float]:
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    cache_read = _int_or_none(usage.get("cache_read_input_tokens"))
    cache_creation = _int_or_none(usage.get("cache_creation_input_tokens"))
    fresh_input = _int_or_none(usage.get("input_tokens"))

    return LlmResult(
        text=str(envelope.get("result") or ""),
        model=canonical_model,
        # usage.input_tokens alone is NOT the prompt size: Claude Code bills
        # the system prompt and most of the user message under
        # cache_creation_input_tokens (first use) or cache_read_input_tokens
        # (session resume / repeat prefix), with "input_tokens" left as just
        # the tiny uncached remainder - verified live 2026-10-17 on the real
        # thesis eval, where a ~700-token prompt reported input_tokens=2.
        # The honest total prompt size - and the right number to compare
        # against spec.token_budget.max_input_tokens - is the sum of all
        # three.
        input_tokens=_sum_present(fresh_input, cache_creation, cache_read),
        output_tokens=_int_or_none(usage.get("output_tokens")),
        cost_usd=_float_or_none(envelope.get("total_cost_usd")),
        duration_s=duration,
        cache_read_input_tokens=cache_read,
        cache_creation_input_tokens=cache_creation,
        num_turns=_int_or_none(envelope.get("num_turns")),
        session_id=(str(envelope["session_id"]) if envelope.get("session_id") else None),
        raw=envelope,
    )
