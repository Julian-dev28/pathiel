#!/usr/bin/env python3
"""Eval: Stage 2 (services/intel/event_score.rule_score) on real past oil
headlines (labels_events.json): direction accuracy, event-type accuracy and
the guardrail rate. Offline, free, deterministic; the gate test
tests/test_eval_event_score.py runs it and asserts the thresholds.

    .venv/bin/python -m services.intel.evals.eval_event_score [--labels PATH] [--json]

Pass: direction accuracy >= DIRECTION_PASS over the headlines with a
non-neutral label, no neutral-labelled headline scored over the
book-guard confidence (a noise headline must never spend venue budget), and
event-type accuracy >= EVENT_TYPE_PASS. Every miss is printed with what the
scorer said.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from services.intel import book_guard, event_score  # noqa: E402

LABELS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "labels_events.json")
DIRECTION_PASS = 0.90
EVENT_TYPE_PASS = 0.80


def load_labels(path: str = LABELS) -> List[Dict[str, Any]]:
    with open(path) as fh:
        return list(json.load(fh)["headlines"])


def run(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Score every row; return the tallies and the misses. Pure."""
    dir_n = dir_ok = et_ok = 0
    misses: List[Dict[str, Any]] = []
    guard_on_noise: List[str] = []
    for r in rows:
        s = event_score.rule_score(r["title"], source=r.get("source"), sentiment=r.get("sentiment"),
                                   sentiment_confidence=0.9 if r.get("sentiment") else None)
        want_dir, want_et = r["direction"], r.get("event_type")
        if want_dir != "neutral":
            dir_n += 1
            if s["expected_oil_direction"] == want_dir:
                dir_ok += 1
            else:
                misses.append({"title": r["title"], "want": want_dir, "got": s["expected_oil_direction"],
                               "event_type": s["event_type"], "confidence": s["confidence_score"]})
        elif s["confidence_score"] >= book_guard.GUARD_CONFIDENCE:
            guard_on_noise.append(r["title"])
        if want_et and s["event_type"] == want_et:
            et_ok += 1
        if r.get("confirmed") and not s["is_confirmed"]:
            misses.append({"title": r["title"], "want": "confirmed", "got": "unconfirmed",
                           "event_type": s["event_type"], "confidence": s["confidence_score"]})
    et_n = sum(1 for r in rows if r.get("event_type"))
    dir_acc = dir_ok / dir_n if dir_n else 1.0
    et_acc = et_ok / et_n if et_n else 1.0
    return {"n": len(rows), "direction_n": dir_n, "direction_accuracy": round(dir_acc, 3),
            "event_type_accuracy": round(et_acc, 3), "guard_on_noise": guard_on_noise, "misses": misses,
            "pass": bool(dir_acc >= DIRECTION_PASS and et_acc >= EVENT_TYPE_PASS and not guard_on_noise)}


def main(argv: Any = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--labels", default=LABELS)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    out = run(load_labels(a.labels))
    if a.json:
        print(json.dumps(out, indent=1))
    else:
        print(f"{out['n']} headlines: direction {out['direction_accuracy']:.0%} of {out['direction_n']} "
              f"(pass >= {DIRECTION_PASS:.0%}), event type {out['event_type_accuracy']:.0%} "
              f"(pass >= {EVENT_TYPE_PASS:.0%}), noise over the book guard: {len(out['guard_on_noise'])}")
        for m in out["misses"]:
            print(f"  MISS want {m['want']} got {m['got']} [{m['event_type']} {m['confidence']}]: {m['title'][:100]}")
        for t in out["guard_on_noise"]:
            print(f"  GUARD on noise: {t[:100]}")
        print("PASS" if out["pass"] else "FAIL")
    return 0 if out["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
