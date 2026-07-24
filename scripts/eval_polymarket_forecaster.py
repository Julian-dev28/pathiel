#!/usr/bin/env python3
"""Paid, opt-in eval for the Polymarket forecaster seam.

Measures the two things the shadow ledger cannot tell us for weeks:

  1. FORMAT  — does the AI brain reliably return a parseable {verdict, yes_prob,
     reasoning} object? A parse failure is a silently skipped market, not a
     visible error, so this must be 100%.
  2. CALIBRATION FLOOR — on anchor questions whose answer is not in dispute, does
     the model put its probability on the right side and near the right end? A
     forecaster that says 0.5 to "will the sun rise tomorrow" has no business
     pricing a geopolitical market.

This is the floor, not the edge. The edge is graded forward on real resolutions
(`services/polymarket_scout/ledger.py`); no eval on resolved markets can measure
it without leaking the outcome.

The call is billable and is refused unless the operator opts in explicitly:

    HERMES_RUN_PAID_POLYMARKET_EVAL=1 python scripts/eval_polymarket_forecaster.py
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Callable, Dict, List, Mapping, Optional, TextIO, Tuple

OPT_IN_ENV = "HERMES_RUN_PAID_POLYMARKET_EVAL"

# (question, context, expected_yes_prob). Anchors are chosen to be stable facts
# or physical near-impossibilities so the "right answer" does not drift with the
# news or the model's cutoff.
ANCHORS: Tuple[Tuple[str, str, float], ...] = (
    ("Will the sun rise over New York City tomorrow?", "Resolves tomorrow.", 1.0),
    ("Will Bitcoin trade above $10,000,000 at any point in the next 7 days?",
     "Resolves in 7 days on any spot print.", 0.0),
    ("Will a crewed human landing take place on the surface of Mars within the next 30 days?",
     "Resolves in 30 days.", 0.0),
    ("Will the United States hold a scheduled federal general election in November 2028?",
     "Resolves November 2028.", 1.0),
    ("Will the Earth be destroyed by an asteroid impact this calendar month?",
     "Resolves at month end.", 0.0),
    ("Will water still boil at a lower temperature at high altitude than at sea level "
     "one month from now?", "Physical-constants market. Resolves in 30 days.", 1.0),
)

# Pass thresholds. Directional accuracy is scored against the anchor's side of
# 0.5; the Brier bound is what separates "leans right" from "is confident and
# right", which is the property that makes a divergence trade worth taking.
THRESHOLDS = {"parse_rate": 1.0, "accuracy": 0.83, "brier": 0.10}


def score(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate anchor results. `results` rows are
    {expected: float, prob: float|None}. A None prob is a parse failure and is
    counted against parse_rate AND accuracy — a skipped market is not a free pass.
    """
    n = len(results)
    if n == 0:
        return {"n": 0, "passed": False}
    parsed = [r for r in results if r.get("prob") is not None]
    correct = sum(1 for r in parsed
                  if (r["prob"] > 0.5) == (r["expected"] > 0.5))
    brier = (sum((r["prob"] - r["expected"]) ** 2 for r in parsed) / len(parsed)
             if parsed else 1.0)
    out = {
        "n": n,
        "parse_rate": round(len(parsed) / n, 4),
        "accuracy": round(correct / n, 4),
        "brier": round(brier, 4),
    }
    out["passed"] = bool(out["parse_rate"] >= THRESHOLDS["parse_rate"]
                         and out["accuracy"] >= THRESHOLDS["accuracy"]
                         and out["brier"] <= THRESHOLDS["brier"])
    out["thresholds"] = dict(THRESHOLDS)
    return out


def main(env: Optional[Mapping[str, str]] = None,
         forecaster_factory: Optional[Callable[[], Any]] = None,
         anchors: Tuple[Tuple[str, str, float], ...] = ANCHORS,
         stdout: Optional[TextIO] = None,
         stderr: Optional[TextIO] = None) -> int:
    env = os.environ if env is None else env
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    if str(env.get(OPT_IN_ENV, "")).strip() != "1":
        print(f"refusing to run a billable eval: set {OPT_IN_ENV}=1 to opt in", file=err)
        return 2

    if forecaster_factory is None:
        from services.polymarket_scout.forecaster import BrainForecaster
        forecaster_factory = BrainForecaster
    fc = forecaster_factory()
    print(f"# polymarket forecaster eval · provider={getattr(fc, 'provider', '?')} "
          f"· {len(anchors)} anchors", file=out)

    results: List[Dict[str, Any]] = []
    for question, context, expected in anchors:
        got = fc.forecast(question, context)
        prob = got[0] if got else None
        results.append({"question": question, "expected": expected, "prob": prob})
        mark = "??" if prob is None else ("ok" if (prob > 0.5) == (expected > 0.5) else "XX")
        print(f"  [{mark}] want~{expected:.2f} got={'PARSE-FAIL' if prob is None else f'{prob:.2f}'}"
              f"  {question[:64]}", file=out)

    summary = score(results)
    print(json.dumps(summary, indent=1), file=out)
    print("PASS" if summary["passed"] else "FAIL", file=out)
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
