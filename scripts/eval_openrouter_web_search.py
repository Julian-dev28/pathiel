#!/usr/bin/env python3
"""Paid, opt-in transport eval for OpenRouter server-side web search.

This script deliberately exercises only ``OpenRouterBrain.complete``.  It does
not import the trading loop, executor, exchange client, account state, or live
configuration.  The call is billable and is refused unless the operator sets
``PATHIA_RUN_PAID_OPENROUTER_WEB_EVAL=1`` explicitly.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable, Mapping
from typing import Any, TextIO


OPT_IN_ENV = "PATHIA_RUN_PAID_OPENROUTER_WEB_EVAL"

_SYSTEM_PROMPT = """You are validating a web-grounded research transport.
You must use the available web search tool at least once. Use only sources you
retrieve in this request. After a short sourced explanation, put exactly one
valid JSON object on the final line with this schema:
{"verdict":"PASS","confidence":0.0,"side":null,"entryPx":1,"stopPx":0,"tpPx":0,"reasoning":"brief sourced transport finding"}
Do not write anything after the JSON object."""

_USER_PROMPT = """Search the official OpenRouter documentation for the
openrouter:web_search server tool. State who decides whether a search runs and
cite the official page you found. This is a transport check, not trading advice."""


def _parse_final_verdict(text: str) -> dict[str, Any]:
    """Return a minimally validated final-line verdict JSON object."""
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    if not lines:
        raise ValueError("empty completion text")
    candidate = lines[-1]
    if candidate.startswith("```json"):
        candidate = candidate[len("```json"):].strip()
    candidate = candidate.removesuffix("```").strip()
    try:
        verdict = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ValueError("final line is not valid verdict JSON") from exc
    if not isinstance(verdict, dict):
        raise ValueError("final verdict JSON is not an object")
    if str(verdict.get("verdict", "")).upper() not in {"PASS", "LONG", "SHORT", "CLOSE"}:
        raise ValueError("final verdict has an unsupported verdict value")
    try:
        confidence = float(verdict["confidence"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("final verdict has no numeric confidence") from exc
    if not 0.0 <= confidence <= 1.0:
        raise ValueError("final verdict confidence is outside [0, 1]")
    return verdict


def _citation_url(citation: Any) -> str:
    """Extract a URL from OpenRouter's normalized or raw citation shape."""
    if isinstance(citation, str):
        return citation.strip()
    if not isinstance(citation, Mapping):
        return ""
    direct = citation.get("url")
    if isinstance(direct, str):
        return direct.strip()
    nested = citation.get("url_citation")
    if isinstance(nested, Mapping) and isinstance(nested.get("url"), str):
        return str(nested["url"]).strip()
    return ""


def main(
    *,
    env: Mapping[str, str] | None = None,
    brain_factory: Callable[[], Any] | None = None,
    completion_helpers: tuple[Callable[[Any], str], Callable[[Any], int], Callable[[Any], Any]] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run the eval, returning nonzero for refusal or any contract failure.

    Injection points exist only so deterministic tests can prove the paid-call
    guard without importing or contacting OpenRouter.
    """
    environ = os.environ if env is None else env
    out = sys.stdout if stdout is None else stdout
    err = sys.stderr if stderr is None else stderr

    if environ.get(OPT_IN_ENV) != "1":
        print(
            f"REFUSED: paid eval disabled; set {OPT_IN_ENV}=1 for one explicit run.",
            file=err,
        )
        return 2
    key = environ.get("OPENROUTER_API_KEY", "").strip()
    if not key or key.startswith("sk-or-..." or "your-"):
        print("REFUSED: OPENROUTER_API_KEY is missing or still a placeholder.", file=err)
        return 2

    if brain_factory is None or completion_helpers is None:
        # Import only after both safety guards pass.  Nothing in this module can
        # start the live loop, touch account state, or route an order.
        from pathia.agents.ai_brain import (
            OpenRouterBrain,
            completion_citations,
            completion_text,
            completion_web_search_requests,
        )

        brain_factory = brain_factory or OpenRouterBrain
        completion_helpers = completion_helpers or (
            completion_text,
            completion_web_search_requests,
            completion_citations,
        )

    text_of, request_count_of, citations_of = completion_helpers
    try:
        result = brain_factory().complete(_SYSTEM_PROMPT, _USER_PROMPT, web_search=True)
        text = text_of(result)
        search_requests = int(request_count_of(result))
        citations = list(citations_of(result) or [])
        verdict = _parse_final_verdict(text)
        urls = [url for url in (_citation_url(c) for c in citations) if url]
    except Exception as exc:
        print(f"FAIL: OpenRouter web-search eval raised {type(exc).__name__}: {exc}", file=err)
        return 1

    failures: list[str] = []
    # OpenRouter reports citations but no server_tool_use block (verified live
    # 2026-07-12), so citations are the primary evidence; a positive request
    # count alone also passes (Anthropic-shaped envelopes).
    if not urls and search_requests <= 0:
        failures.append(
            "no evidence of a real search: no citation URLs and "
            "usage.server_tool_use.web_search_requests not positive"
        )
    if failures:
        print("FAIL: " + "; ".join(failures), file=err)
        return 1

    model = environ.get("OPENROUTER_MODEL", "x-ai/grok-4.5")
    print(
        f"PASS model={model} verdict={str(verdict['verdict']).upper()} "
        f"web_search_requests={search_requests} citations={len(urls)}",
        file=out,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
