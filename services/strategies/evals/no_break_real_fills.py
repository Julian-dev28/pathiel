"""Eval: the no-break rule (next_trade.NO_BREAK_ENTRY) on the desk's REAL
closed trades.

    python3 -m services.strategies.evals.no_break_real_fills [--ledger PATH]

Reads the trade review's ledger ($PATHIEL_STATE_DIR/review/ledger.jsonl,
built from public fills by `python3 -m services.review run`). A trade whose
entry sat past its own 24h edge (`range_pos` under 0: a long under the 24h
low of the 24 closed 1h bars before it, a short over their high) is one the
rule now refuses. Prints what refusing those would have saved and missed,
and exits non-zero when the rule would have cost money on the real fills
(the pass threshold: net change >= 0).

The review's range uses the 24 closed bars before the entry; the live rule
reads the levels script's 24h range at the tick. They can differ by the
current bar, so this is the ledger's view of the rule, not a re-simulation.
Read only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, Iterable, List, Optional


def latest_rows(lines: Iterable[str]) -> List[Dict[str, Any]]:
    """One row per trade id, the last one written wins (the ledger appends
    revisions)."""
    rows: Dict[str, Dict[str, Any]] = {}
    for line in lines:
        line = line.strip()
        if line:
            d = json.loads(line)
            rows[d["id"]] = d
    return sorted(rows.values(), key=lambda d: d["entry_ms"])


def refused(row: Dict[str, Any]) -> bool:
    pos = row.get("range_pos")
    return pos is not None and pos < 0


def score(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    hit = [r for r in rows if refused(r)]
    saved = -sum(r["net_usd"] for r in hit if r["net_usd"] < 0)
    missed = sum(r["net_usd"] for r in hit if r["net_usd"] > 0)
    return {
        "trades": len(rows), "refused": len(hit),
        "refused_wins": sum(1 for r in hit if r["net_usd"] > 0),
        "saved_usd": round(saved, 2), "missed_usd": round(missed, 2),
        "net_change_usd": round(saved - missed, 2),
        "actual_net_usd": round(sum(r["net_usd"] for r in rows), 2),
        "with_rule_net_usd": round(sum(r["net_usd"] for r in rows if not refused(r)), 2),
        "refused_entries": [f"{r['entry']} {r['desk']} {r['side']} {r['net_usd']:+.2f}" for r in hit],
        "pass": saved - missed >= 0,
    }


def default_ledger() -> str:
    state = os.environ.get("PATHIEL_STATE_DIR") or os.getcwd()
    return os.path.join(state, "review", "ledger.jsonl")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--ledger", default=None)
    a = ap.parse_args(argv)
    path = a.ledger or default_ledger()
    with open(path) as fh:
        out = score(latest_rows(fh))
    print(json.dumps(out, indent=1))
    return 0 if out["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
