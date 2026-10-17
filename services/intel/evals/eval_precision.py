#!/usr/bin/env python3
"""Precision@10 eval for services/intel/scoring.py's deterministic
relevance function, against hand-labeled real headlines.

This is the "eval" half of the deterministic/latent split for this
service: scoring.relevance() itself is pure code (no model call), but
whether its ranking actually matches human judgment about what belongs
in a trader's brief is an empirical claim that needs measuring, same as
any other ranking function. Labels live in labels_oil.json / labels_btc.json
(~30 real headlines per desk, hand-judged relevant/not-relevant — see each
file's "source_note").

Usage:
    .venv/bin/python -m services.intel.evals.eval_precision
    .venv/bin/python -m services.intel.evals.eval_precision --threshold 0.8

Exit code 0 if every desk clears the threshold, 1 otherwise — wired for a
CI gate or a pre-ship check, per docs/overhaul/SWARM_RULES.md section 4's
"every feature with an LLM-adjacent scoring step gets an eval suite with a
pass threshold, runnable as a script" (this one has no LLM step at all,
but the same bar applies to any ranking function the thesis depends on).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

HERE = Path(__file__).parent
REPO_ROOT = HERE.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from contracts.strategies.models import DeskSpec, Instrument  # noqa: E402
from services.intel.scoring import relevance  # noqa: E402
from services.intel.sources.news import query_phrases  # noqa: E402

DEFAULT_THRESHOLD = 0.8
NOW_ANCHOR = datetime(2026, 10, 17, 3, 0, tzinfo=timezone.utc)


def _oil_spec() -> DeskSpec:
    fixture = REPO_ROOT / "contracts" / "strategies" / "fixtures" / "oil_spec.json"
    return DeskSpec.model_validate(json.loads(fixture.read_text()))


def _btc_spec() -> DeskSpec:
    return DeskSpec(
        id="btc", name="Bitcoin", tagline="BTC perp on Hyperliquid.",
        instruments=[Instrument(coin="BTC", label="BTC", role="primary")],
        news_queries=["bitcoin price", "bitcoin ETF", "BTC"],
    )


DESKS = {"oil": _oil_spec, "btc": _btc_spec}


def load_labels(desk_id: str) -> Dict[str, Any]:
    return json.loads((HERE / f"labels_{desk_id}.json").read_text())


def score_labels(desk_id: str, labels: Dict[str, Any]) -> List[Dict[str, Any]]:
    spec = DESKS[desk_id]()
    phrases = query_phrases(spec)
    captured = datetime.fromisoformat(labels["captured"].replace("Z", "+00:00"))
    scored = []
    for row in labels["headlines"]:
        published_at = captured - timedelta(hours=row["hours_ago"])
        r = relevance(row["title"], queries=phrases, source=row["source"],
                       published_at=published_at, now=captured)
        scored.append({**row, "relevance": r})
    scored.sort(key=lambda r: r["relevance"], reverse=True)
    return scored


def precision_at_k(scored: List[Dict[str, Any]], k: int = 10) -> float:
    top_k = scored[:k]
    if not top_k:
        return 0.0
    return sum(1 for r in top_k if r["relevant"]) / len(top_k)


def report(desk_id: str, scored: List[Dict[str, Any]], k: int, threshold: float) -> bool:
    p_at_k = precision_at_k(scored, k)
    n_relevant = sum(1 for r in scored if r["relevant"])
    passed = p_at_k >= threshold
    status = "PASS" if passed else "FAIL"
    print(f"\n=== {desk_id} === precision@{k} = {p_at_k:.2f} "
          f"(threshold {threshold:.2f}) [{status}]  "
          f"({n_relevant}/{len(scored)} labeled relevant overall)")
    for i, row in enumerate(scored[:k], 1):
        mark = "OK " if row["relevant"] else "BAD"
        print(f"  {i:>2}. [{mark}] rel={row['relevance']:.3f}  {row['source']:<20} {row['title'][:70]}")
    missed = [r for r in scored[:k] if not r["relevant"]]
    if missed:
        print(f"  -> {len(missed)} irrelevant item(s) made the top {k}:")
        for r in missed:
            print(f"       \"{r['title'][:70]}\" ({r['source']})")
    return passed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--desk", action="append", choices=list(DESKS),
                     help="restrict to one or more desks (default: all)")
    args = ap.parse_args()

    desk_ids = args.desk or list(DESKS)
    all_passed = True
    for desk_id in desk_ids:
        labels = load_labels(desk_id)
        scored = score_labels(desk_id, labels)
        passed = report(desk_id, scored, args.k, args.threshold)
        all_passed = all_passed and passed

    print(f"\n{'ALL DESKS PASS' if all_passed else 'FAILED'} "
          f"(precision@{args.k} >= {args.threshold:.2f})")
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
