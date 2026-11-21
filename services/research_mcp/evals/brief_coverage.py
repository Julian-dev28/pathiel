#!/usr/bin/env python3
"""Periodic eval for the research MCP's flagship tool: for every coin
`research_brief` supports, does a REAL call (live network, no mocks)
actually return something in every section?

This is the eval half of the latent/deterministic split for this server:
research_brief's composition is pure code (tools.py), but whether it
actually delivers real coverage for every supported coin against the live
internet, today, is an empirical claim this eval measures, the same way
services/intel/evals/eval_coverage.py measures build_packet's real coverage
instead of just trusting the code compiled.

Three checks are REQUIRED per coin (levels, catalysts, headlines) because
every one of them has a reliable public source for every supported coin;
`odds` is scored and reported but not required -- a coin's prediction-market
queries legitimately turn up nothing some days (CLAUDE.md: "score coverage
... odds where the source has them", not "odds always"). `thesis` is
reported too, informationally: it is almost always null in this sandbox (no
desk has been ticked here), which is correct behavior, not a gap.

Usage:
    .venv/bin/python -m services.research_mcp.evals.brief_coverage
    .venv/bin/python -m services.research_mcp.evals.brief_coverage --threshold 0.8

Exit 0 if the REQUIRED-field coverage score >= threshold (default 0.8), 1
otherwise. Always live (no --online flag, unlike eval_coverage.py's offline
default): there is no useful offline mode for "does the real internet answer
today."
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List

HERE = Path(__file__).parent
REPO_ROOT = HERE.parents[3]
sys.path.insert(0, str(REPO_ROOT))

from services.research_mcp import coins as coins_mod  # noqa: E402
from services.research_mcp import tools  # noqa: E402

DEFAULT_THRESHOLD = 0.8
REQUIRED_FIELDS = ("levels", "catalysts", "headlines")


def _non_empty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (list, dict, str)):
        return len(value) > 0
    return True


def coverage_for_coin(coin: str) -> Dict[str, Any]:
    brief = tools.research_brief(coin)
    row = {field: _non_empty(brief.get(field)) for field in REQUIRED_FIELDS}
    row["odds"] = _non_empty(brief.get("odds"))
    row["thesis"] = _non_empty(brief.get("thesis"))
    row["sources_failed"] = list(brief.get("sources_failed") or [])
    return row


def report(rows: Dict[str, Dict[str, Any]], threshold: float) -> bool:
    required_checks = [row[f] for row in rows.values() for f in REQUIRED_FIELDS]
    required_score = sum(required_checks) / len(required_checks) if required_checks else 0.0
    odds_score = sum(row["odds"] for row in rows.values()) / len(rows) if rows else 0.0
    passed = required_score >= threshold
    status = "PASS" if passed else "FAIL"

    print(f"=== research_brief coverage === required(levels/catalysts/headlines): "
          f"{required_score:.2f} (threshold {threshold:.2f}) [{status}]  "
          f"odds (informational): {odds_score:.2f}")
    for coin, row in rows.items():
        marks = " ".join(f"{f}={'OK' if row[f] else 'MISS'}" for f in REQUIRED_FIELDS)
        odds_mark = "OK" if row["odds"] else "none"
        thesis_mark = "stored" if row["thesis"] else "none (no desk ticked here)"
        line = f"  {coin:<14} {marks}  odds={odds_mark}  thesis={thesis_mark}"
        if row["sources_failed"]:
            line += f"  sources_failed={len(row['sources_failed'])}"
        print(line)
        for err in row["sources_failed"]:
            print(f"      - {err}")
    return passed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    ap.add_argument("--coins", nargs="*", default=None,
                    help="subset of coins to check (default: every supported coin)")
    args = ap.parse_args()

    targets: List[str] = args.coins or list(coins_mod.SUPPORTED_COINS)
    rows: Dict[str, Dict[str, Any]] = {}
    for coin in targets:
        try:
            rows[coin] = coverage_for_coin(coin)
        except Exception as exc:  # noqa: BLE001 - one coin's crash must not kill the whole eval
            rows[coin] = {f: False for f in REQUIRED_FIELDS}
            rows[coin].update({"odds": False, "thesis": False,
                               "sources_failed": [f"research_brief_raised:{type(exc).__name__}:{exc}"]})

    passed = report(rows, args.threshold)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
