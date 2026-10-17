"""services/thesis - the one latent step in the desk pipeline.

    write_thesis(spec: DeskSpec, analytics: DeskAnalytics,
                 intel: IntelPacket | None, prev: Thesis | None) -> Thesis

Flow: build the system + user prompt (prompt.py) -> ask services/llm for the
latent-only JSON draft (llm_schema.ThesisDraft), schema-constrained -> run
the anti-hallucination validator (validator.py) -> on failure, one repair
round (resuming the first call's session so the repair message can be short
- see services/llm/README.md's probe log) -> on a second failure, reject and
return ``prev`` with a reason, or a deterministic fallback Thesis if there
is no ``prev`` -> on success, assemble the final Thesis deterministically
(assemble.py) and print/log the token report.

write_thesis() never raises and never returns anything but a valid
``Thesis`` - an LLM outage, a timeout, or a draft that fails repair twice all
degrade to ``prev`` (annotated with why) or a flat, zero-confidence
fallback, never an exception the caller has to handle.
"""
from __future__ import annotations

import logging
from typing import List, Optional, Tuple

from pydantic import ValidationError

from contracts.strategies.models import DeskAnalytics, DeskSpec, IntelPacket, Thesis, Usage
from services.llm import LlmError, LlmResult, complete
from services.thesis import assemble, desk_lookup, fallback, validator
from services.thesis import prompt as prompt_mod
from services.thesis.llm_schema import ThesisDraft, thesis_draft_json_schema
from services.thesis.render import render_markdown  # noqa: F401  (public: MCP desk tools)

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 120.0


def write_thesis(
    spec: DeskSpec,
    analytics: DeskAnalytics,
    intel: Optional[IntelPacket],
    prev: Optional[Thesis],
) -> Thesis:
    now = prompt_mod.utc_now()
    tag = f"thesis:{spec.id}"

    try:
        incremental = prev is not None and prompt_mod.is_small_change(spec, analytics, intel, prev)
        system_prompt = prompt_mod.build_system_prompt(spec)
        user_prompt, _ = prompt_mod.build_user_prompt(spec, analytics, intel, prev, incremental=incremental)
        schema = thesis_draft_json_schema(len(desk_lookup.ladder_leverages(analytics)))

        result1 = complete(
            system_prompt,
            user_prompt,
            max_output_tokens=spec.token_budget.max_output_tokens,
            json_schema=schema,
            tag=tag,
            timeout_s=DEFAULT_TIMEOUT_S,
            effort=thesis_effort(),
            persist_session=True,
        )
        calls: List[LlmResult] = [result1]
        draft, parse_err = _parse_draft(result1.text)
        draft = _normalize_instrument(draft, analytics)
        errors = [parse_err] if parse_err else validator.validate_draft(draft, spec, analytics, intel)

        if errors:
            # Trace: a repair round roughly doubles the cost of a thesis, so
            # what triggers it must be visible in the log, not inferred.
            logger.warning(f"[thesis] {spec.id}: draft failed validation, repairing: "
                           + "; ".join(str(e) for e in errors)[:800])
            result2 = _run_repair(system_prompt, user_prompt, errors, result1, spec, schema, tag)
            calls.append(result2)
            draft2, parse_err2 = _parse_draft(result2.text)
            draft2 = _normalize_instrument(draft2, analytics)
            errors2 = [parse_err2] if parse_err2 else validator.validate_draft(draft2, spec, analytics, intel)
            if errors2:
                reason = "validator rejected after repair: " + "; ".join(errors2)
                usage = _combine_usage(calls, skipped_reason=reason)
                _print_token_report(spec, usage, incremental=incremental, n_calls=len(calls))
                return _degrade(spec, analytics, intel, prev, reason, now)
            draft = draft2

        assert draft is not None  # either the first or the repaired parse succeeded
        usage = _combine_usage(calls)
        thesis = assemble.assemble_thesis(
            spec=spec, analytics=analytics, intel=intel, draft=draft, now=now, usage=usage,
        )
        _print_token_report(spec, usage, incremental=incremental, n_calls=len(calls))
        return thesis

    except LlmError as exc:
        reason = f"LLM call failed: {exc}"
        logger.error(f"[thesis] {spec.id}: {reason}")
        return _degrade(spec, analytics, intel, prev, reason, now)


def _run_repair(
    system_prompt: str,
    user_prompt: str,
    errors: List[str],
    result1: LlmResult,
    spec: DeskSpec,
    schema: dict,
    tag: str,
) -> LlmResult:
    """One repair round. Prefers resuming result1's persisted session (short
    message: just the error list - the model already has the market data
    and its own first answer in context). Falls back to a fresh,
    standalone call carrying the full market data again if the resume
    itself fails to run (e.g. the CLI could not find the session) - the
    resume is an optimization, never a single point of failure for the one
    repair round the contract allows."""
    short_repair = prompt_mod.build_repair_prompt(errors)
    if result1.session_id:
        try:
            return complete(
                system_prompt,
                short_repair,
                max_output_tokens=spec.token_budget.max_output_tokens,
                json_schema=schema,
                tag=tag,
                timeout_s=DEFAULT_TIMEOUT_S,
                effort=thesis_effort(),
                resume_session=result1.session_id,
            )
        except LlmError as exc:
            logger.warning(f"[thesis] repair resume failed ({exc}); retrying as a standalone call")

    full_repair = prompt_mod.build_repair_prompt(errors, full_context=user_prompt)
    return complete(
        system_prompt,
        full_repair,
        max_output_tokens=spec.token_budget.max_output_tokens,
        json_schema=schema,
        tag=tag,
        timeout_s=DEFAULT_TIMEOUT_S,
        effort=thesis_effort(),
    )


def _degrade(spec, analytics, intel, prev, reason, now) -> Thesis:
    if prev is not None:
        return fallback.reject_with_prev(prev, reason)
    return fallback.build_fallback_thesis(spec, analytics, intel, reason, now)


# A 7-day bucket bound beyond +/-100% is not a forecast any desk here makes;
# a draft with one wrote percent where the contract says fraction.
_PERCENT_UNITS_THRESHOLD = 1.0


def _coerce_scenario_units(draft: ThesisDraft) -> ThesisDraft:
    """Deterministic unit repair: if any scenario bound exceeds +/-1.0 the
    whole set was written in percent, so divide every bound by 100. Cheaper
    and more reliable than a repair round, which re-reads the full session."""
    bounds = [b for sc in draft.scenarios for b in (sc.move_lo_pct, sc.move_hi_pct)]
    if not bounds or max(abs(b) for b in bounds) <= _PERCENT_UNITS_THRESHOLD:
        return draft
    logger.warning("[thesis] scenario buckets were written in percent; coerced to fractions")
    fixed = [sc.model_copy(update={"move_lo_pct": sc.move_lo_pct / 100.0,
                                   "move_hi_pct": sc.move_hi_pct / 100.0})
             for sc in draft.scenarios]
    return draft.model_copy(update={"scenarios": fixed})


def thesis_effort() -> Optional[str]:
    """CLI effort for thesis calls: PATHIEL_THESIS_EFFORT if set, else the
    measured default THESIS_EFFORT_DEFAULT. Effort trades thinking tokens
    (reported inside output_tokens) for depth."""
    import os
    raw = (os.environ.get("PATHIEL_THESIS_EFFORT") or THESIS_EFFORT_DEFAULT or "").strip().lower()
    return raw or None


# Eval 2026-10-17 (oil + btc): default and medium both scored 0.857; medium
# used 15% fewer output tokens ($0.87 vs $1.04 for the pair). Override with
# PATHIEL_THESIS_EFFORT.
THESIS_EFFORT_DEFAULT: Optional[str] = "medium"


_INSTRUMENT_SUFFIXES = ("-PERP", "PERP", "-USDT", "-USDC", "-USD", "/USDT", "/USDC", "/USD", "USDT")


def _normalize_instrument(draft: Optional[ThesisDraft], analytics) -> Optional[ThesisDraft]:
    """Map the model's spelling of the instrument onto the desk's real coin.
    Eval 2026-10-17 paid a repair round for "BTC-PERP" on the BTC desk. Matches
    the bare symbol (xyz:CL -> CL) and the human label (WTI -> xyz:CL),
    case-insensitively, after stripping venue suffixes. Pair calls and exact
    matches are left alone; an unmatched name stays for the validator."""
    if draft is None or draft.call.side == "pair":
        return draft
    raw = draft.call.instrument.strip()
    coins = {i.coin for i in analytics.instruments}
    if raw in coins:
        return draft
    key = raw.upper()
    for suf in _INSTRUMENT_SUFFIXES:
        if key.endswith(suf) and len(key) > len(suf):
            key = key[: -len(suf)]
            break
    for inst in analytics.instruments:
        symbol = inst.coin.split(":", 1)[-1].upper()
        if key in (inst.coin.upper(), symbol, inst.label.upper()):
            logger.warning(f"[thesis] instrument {raw!r} normalized to {inst.coin!r}")
            return draft.model_copy(update={"call": draft.call.model_copy(update={"instrument": inst.coin})})
    return draft


def _parse_draft(text: str) -> Tuple[Optional[ThesisDraft], Optional[str]]:
    try:
        return _coerce_scenario_units(ThesisDraft.model_validate_json(text)), None
    except (ValidationError, ValueError) as exc:
        return None, f"draft JSON failed to parse/validate against the schema: {exc}"


def _combine_usage(calls: List[LlmResult], *, skipped_reason: Optional[str] = None) -> Usage:
    def _sum_or_none(values):
        present = [v for v in values if v is not None]
        return sum(present) if present else None

    return Usage(
        model=calls[-1].model if calls else None,
        input_tokens=_sum_or_none(c.input_tokens for c in calls),
        output_tokens=_sum_or_none(c.output_tokens for c in calls),
        cost_usd=_sum_or_none(c.cost_usd for c in calls),
        duration_s=(sum(c.duration_s for c in calls) if calls else None),
        skipped_reason=skipped_reason,
    )


def _print_token_report(spec: DeskSpec, usage: Usage, *, incremental: bool, n_calls: int) -> None:
    mode = "incremental" if incremental else "full"
    cost_txt = f"${usage.cost_usd:.4f}" if usage.cost_usd is not None else "n/a"
    budget_in = spec.token_budget.max_input_tokens
    budget_out = spec.token_budget.max_output_tokens
    within = "yes"
    if usage.input_tokens is not None and usage.input_tokens > budget_in:
        within = "NO (input over budget)"
    elif usage.output_tokens is not None and usage.output_tokens > budget_out:
        within = "NO (output over budget)"
    line = (
        f"[thesis] {spec.id}: {n_calls} call(s), mode={mode}, "
        f"input={usage.input_tokens} output={usage.output_tokens} cost={cost_txt} "
        f"budget_in={budget_in} budget_out={budget_out} within_budget={within}"
    )
    print(line)
    logger.info(line)
