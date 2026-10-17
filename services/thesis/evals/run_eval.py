"""Periodic eval for services/thesis (SWARM_RULES.md section 4: paid, real
LLM calls - run before ship and nightly, NOT part of the free pre-commit
gate). Calls the real local Claude Code CLI through services/llm, once per
case, against the real oil fixture and a synthetic BTC packet
(evals/btc_fixture.py).

Scores each resulting Thesis with a deterministic 7-point rubric:
validator pass, every number grounded, one_liner length, 3 to 5 scenarios,
invalidation present, catalysts inside the horizon, tokens within budget.
A case's score is (checks passed) / 7. The eval passes if the MEAN score
across every case is at least THRESHOLD.

Run:
    PATHIEL_STATE_DIR="$(mktemp -d)" .venv/bin/python -m services.thesis.evals.run_eval

Costs real money (a handful of Claude Code CLI calls on the probed default
model - see services/llm/README.md for per-call cost). Not wired into
pytest on purpose, so `pytest -q` never spends it by accident.
"""
from __future__ import annotations

import sys
from datetime import timedelta
from typing import Any, Dict, List, Tuple

from contracts.strategies.fixtures import _build as oil_fixture
from contracts.strategies.models import DeskAnalytics, DeskSpec, IntelPacket, Thesis
from services.thesis import write_thesis
from services.thesis.evals.btc_fixture import build_btc_packet
from services.thesis.validator import validate_thesis

THRESHOLD = 0.85

_CHECK_NAMES = (
    "validator_pass",
    "numbers_grounded",
    "one_liner_length_ok",
    "scenario_count_ok",
    "invalidation_present",
    "catalysts_in_horizon",
    "tokens_within_budget",
)


def score_thesis(
    thesis: Thesis, spec: DeskSpec, analytics: DeskAnalytics, intel: IntelPacket,
) -> Dict[str, Any]:
    errors = validate_thesis(thesis, spec, analytics, intel)
    grounding_errors = [e for e in errors if "not grounded" in e]

    horizon_end = thesis.created_at + timedelta(days=thesis.horizon_days)
    catalysts_in_horizon = (
        not thesis.catalysts
        or any(thesis.created_at <= c.at <= horizon_end for c in thesis.catalysts)
    )

    in_tok = thesis.usage.input_tokens
    out_tok = thesis.usage.output_tokens
    tokens_within_budget = (
        in_tok is not None and out_tok is not None
        and in_tok <= spec.token_budget.max_input_tokens
        and out_tok <= spec.token_budget.max_output_tokens
    )

    checks = {
        "validator_pass": not errors,
        "numbers_grounded": not grounding_errors,
        "one_liner_length_ok": len(thesis.call.one_liner) <= 160,
        "scenario_count_ok": 3 <= len(thesis.scenarios) <= 5,
        "invalidation_present": bool(thesis.call.invalidation.strip()),
        "catalysts_in_horizon": catalysts_in_horizon,
        "tokens_within_budget": tokens_within_budget,
    }
    score = sum(1 for v in checks.values() if v) / len(_CHECK_NAMES)
    return {"checks": checks, "score": score, "errors": errors}


def run_case(name: str, spec: DeskSpec, analytics: DeskAnalytics, intel: IntelPacket) -> Dict[str, Any]:
    thesis = write_thesis(spec, analytics, intel, None)
    result = score_thesis(thesis, spec, analytics, intel)
    result.update({
        "name": name,
        "thesis": thesis,
        "input_tokens": thesis.usage.input_tokens,
        "output_tokens": thesis.usage.output_tokens,
        "cost_usd": thesis.usage.cost_usd,
        "skipped_reason": thesis.usage.skipped_reason,
    })
    return result


def _cases() -> List[Tuple[str, DeskSpec, DeskAnalytics, IntelPacket]]:
    btc_spec, btc_analytics, btc_intel = build_btc_packet()
    return [
        ("oil", oil_fixture.spec, oil_fixture.analytics, oil_fixture.intel),
        ("btc", btc_spec, btc_analytics, btc_intel),
    ]


def _print_report(results: List[Dict[str, Any]]) -> float:
    print()
    print("=" * 72)
    print("services/thesis eval report")
    print("=" * 72)
    for r in results:
        cost_txt = f"${r['cost_usd']:.4f}" if r["cost_usd"] is not None else "n/a"
        print(f"\n[{r['name']}] score={r['score']:.3f}  "
              f"tokens_in={r['input_tokens']} tokens_out={r['output_tokens']} cost={cost_txt}")
        if r["skipped_reason"]:
            print(f"  DEGRADED: {r['skipped_reason']}")
        for check in _CHECK_NAMES:
            mark = "PASS" if r["checks"][check] else "FAIL"
            print(f"  [{mark}] {check}")
        if r["errors"]:
            print(f"  validator errors ({len(r['errors'])}):")
            for e in r["errors"][:5]:
                print(f"    - {e}")

    mean_score = sum(r["score"] for r in results) / len(results)
    total_in = sum(r["input_tokens"] or 0 for r in results)
    total_out = sum(r["output_tokens"] or 0 for r in results)
    total_cost = sum(r["cost_usd"] or 0.0 for r in results)
    print()
    print("-" * 72)
    print(f"mean score: {mean_score:.3f}  (threshold {THRESHOLD})")
    print(f"total tokens: input={total_in} output={total_out}  total cost: ${total_cost:.4f}")
    print(f"verdict: {'PASS' if mean_score >= THRESHOLD else 'FAIL'}")
    print("=" * 72)
    return mean_score


def main() -> int:
    results = [run_case(*case) for case in _cases()]
    mean_score = _print_report(results)
    return 0 if mean_score >= THRESHOLD else 1


if __name__ == "__main__":
    sys.exit(main())
