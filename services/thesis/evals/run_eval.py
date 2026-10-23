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
from typing import Any, Dict, List, Optional, Tuple

from contracts.strategies.fixtures import _build as oil_fixture
from contracts.strategies.models import DeskAnalytics, DeskSpec, IntelPacket, Thesis
from services.thesis import desk_lookup, write_thesis
from services.thesis.evals.btc_fixture import build_btc_packet
from services.thesis.validator import validate_thesis

THRESHOLD = 0.85

# Stop-sanity cap for stop_within_2_atr: the daily InstrumentSnapshot.atr14_pct
# convention this codebase uses everywhere else (the market snapshot table,
# check_entry_near_mid's band) - a second, looser opinion on the generated
# call's stop distance alongside check_r_multiple's hard ladder-relative gate.
MAX_STOP_ATR_MULTIPLE = 2.0

_CHECK_NAMES = (
    "validator_pass",
    "numbers_grounded",
    "one_liner_length_ok",
    "scenario_count_ok",
    "invalidation_present",
    "catalysts_in_horizon",
    "tokens_within_budget",
    "r_gate_pass",
    "stop_within_2_atr",
)


def call_r_multiple(thesis: Thesis) -> Optional[float]:
    """R multiple to the call's first target, via
    pathiel.agents.bracket.check_levels - the same function
    services/thesis/validator.py's check_r_multiple and
    services/thesis/render.py's quick-mode R line both use. None when
    there's nothing to compute it from (flat/pair call, no stop, no
    target)."""
    call = thesis.call
    if call.side not in ("long", "short") or call.entry_px is None or call.stop_px is None or not call.targets:
        return None
    from pathiel.agents.bracket import check_levels

    return check_levels(call.entry_px, call.stop_px, call.targets[0]).get("r_multiple")


def stop_distance_in_atr(thesis: Thesis, analytics: DeskAnalytics) -> Optional[float]:
    """The call's stop distance from entry, as a multiple of the primary
    instrument's daily ATR14 (InstrumentSnapshot.atr14_pct). None when
    there's no stop/entry or no ATR to divide by."""
    call = thesis.call
    if call.side not in ("long", "short") or call.entry_px is None or call.stop_px is None:
        return None
    snap = desk_lookup.find_instrument(call.instrument, analytics)
    if snap is None or not snap.atr14_pct:
        return None
    stop_dist_pct = abs(call.entry_px - call.stop_px) / call.entry_px
    return stop_dist_pct / snap.atr14_pct


def score_thesis(
    thesis: Thesis, spec: DeskSpec, analytics: DeskAnalytics, intel: IntelPacket,
) -> Dict[str, Any]:
    errors = validate_thesis(thesis, spec, analytics, intel)
    grounding_errors = [e for e in errors if "not grounded" in e]
    r_errors = [e for e in errors if "pays" in e and "R to its first target" in e]
    ladder_stop_errors = [e for e in errors if "wider than the" in e and "ladder rung" in e]

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

    r_multiple = call_r_multiple(thesis)
    stop_atr_multiple = stop_distance_in_atr(thesis, analytics)

    checks = {
        "validator_pass": not errors,
        "numbers_grounded": not grounding_errors,
        "one_liner_length_ok": len(thesis.call.one_liner) <= 160,
        "scenario_count_ok": 3 <= len(thesis.scenarios) <= 5,
        "invalidation_present": bool(thesis.call.invalidation.strip()),
        "catalysts_in_horizon": catalysts_in_horizon,
        "tokens_within_budget": tokens_within_budget,
        # Root-cause check for the 2026-10-23 live Brent bug: the generated
        # call must actually pay a real R, and never widen its stop past the
        # chosen leverage's ladder limit. True when there's nothing to gate
        # (flat call) - score_thesis never fails a flat call on a check
        # that doesn't apply to it.
        "r_gate_pass": not r_errors and not ladder_stop_errors,
        "stop_within_2_atr": stop_atr_multiple is None or stop_atr_multiple <= MAX_STOP_ATR_MULTIPLE,
    }
    score = sum(1 for v in checks.values() if v) / len(_CHECK_NAMES)
    return {"checks": checks, "score": score, "errors": errors,
            "r_multiple": r_multiple, "stop_atr_multiple": stop_atr_multiple}


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
        r_txt = f"{r['r_multiple']:.2f}R" if r["r_multiple"] is not None else "n/a"
        atr_txt = f"{r['stop_atr_multiple']:.2f} ATR" if r["stop_atr_multiple"] is not None else "n/a"
        print(f"\n[{r['name']}] score={r['score']:.3f}  "
              f"tokens_in={r['input_tokens']} tokens_out={r['output_tokens']} cost={cost_txt}")
        print(f"  call: {r_txt} to first target, stop {atr_txt} from entry")
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
