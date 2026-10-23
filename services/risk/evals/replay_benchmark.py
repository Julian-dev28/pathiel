#!/usr/bin/env python3
"""Deterministic eval: replay recent forward-ledger signals against
DerivedLimits at several equities. Reports risk taken versus the derived
limit and how many trades would execute. Pass rule: NO TRADE EXCEEDS ITS
LIMIT.

Not an LLM eval — this feature has no LLM step, so there is nothing to
grade for quality; the deterministic benchmark docs/overhaul/
SWARM_RULES.md section 4 asks every feature to ship, read literally
("runnable as a script").

THE SIGNAL SOURCE
`pathiel.agents.shadow_ledger` is the live system's own "forward ledger":
a normalized {book, coin, side, stop_pct, entry_ref_px, ts, ...} record is
appended for every candidate considered, whether or not capital was
available at the time — unbiased-by-equity signal stream, exactly what
this replay needs. Before 2026-10-23 that meant one of seven strategy
books' ledgers; all seven are retired (headless desk: Claude trades
through the MCP server) and the live ledger today is whatever the
surviving experiments record (e.g. executor.py's thin_short_relax). Either
way `replay()` does not care WHICH tag a historical record carries — see
its own docstring for why every signal is now priced against the one
bookless BookSpec (`services.risk.books.main_engine_spec`), not looked up
by a per-book roster that no longer exists.

SAFETY (docs/overhaul/SWARM_RULES.md section 1): copies ONLY the
shadow_ledger/ subdirectory of --state-dir into a fresh temp directory
before reading anything — "reading live state is allowed only by copying
it to a temp dir first." Never writes to --state-dir. Never signs, sizes
for, or places a real order — this is pure replay arithmetic against
services.risk.derive.derive_limits, offline.

Usage:
    services/risk/evals/replay_benchmark.py --state-dir "$PATHIEL_STATE_DIR" \\
        --equities 15,100,1000,10000,100000 --profile auto
    services/risk/evals/replay_benchmark.py --state-dir /tmp/some/copy --json
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def copy_shadow_ledger(state_dir_src: str) -> Path:
    """Copy ONLY shadow_ledger/ out of `state_dir_src` into a fresh temp
    directory. Never touches the rest of (possibly live) `state_dir_src`,
    never writes back to it. Returns the temp dir (a full PATHIEL_STATE_DIR
    stand-in — shadow_ledger.py resolves <PATHIEL_STATE_DIR>/shadow_ledger)."""
    tmp = Path(tempfile.mkdtemp(prefix="risk_eval_"))
    src_ledger = Path(state_dir_src) / "shadow_ledger"
    dst_ledger = tmp / "shadow_ledger"
    if src_ledger.is_dir():
        shutil.copytree(src_ledger, dst_ledger)
    else:
        dst_ledger.mkdir(parents=True, exist_ok=True)
    return tmp


def load_recent_signals(state_dir: Path, max_age_days: float) -> List[Dict[str, Any]]:
    """Every shadow_ledger record across every book, newer than
    `max_age_days`, oldest first. `state_dir` must already be the COPY
    (see copy_shadow_ledger) — this sets PATHIEL_STATE_DIR itself, so call
    it at most once per process (pathiel.agents.rebalancer_owned resolves
    the state dir at import time; see its own module for why)."""
    os.environ["PATHIEL_STATE_DIR"] = str(state_dir)
    from pathiel.agents import shadow_ledger as sl

    cutoff_ms = int(time.time() * 1000) - int(max_age_days * 86_400_000)
    rows: List[Dict[str, Any]] = []
    for book in sl.list_books():
        for rec in sl.load(book):
            if int(rec.get("ts", 0) or 0) >= cutoff_ms:
                rows.append(rec)
    rows.sort(key=lambda r: r.get("ts", 0))
    return rows


def replay(signals: List[Dict[str, Any]], equities: List[float],
          profile_name: str = "auto") -> Dict[str, Any]:
    """PURE: signals + equities -> a report dict. No I/O, no network, no
    clock reads beyond the one timestamp in the report header.

    Every signal is priced against `services.risk.books.main_engine_spec`
    — the one BookSpec shape left since the strategy books were retired
    2026-10-23 — using the SIGNAL's own recorded `stop_pct` (a historical
    signal, by definition, was live when it was recorded; what matters here
    is only whether ITS risk would fit the derived limit at a given equity).
    There is no more per-book lookup to miss: this eval used to skip a
    signal whose `book` tag was not one of the seven registered books (a
    retired hypothesis, or a fixed/structural book like drawdown_ladder
    with no risk-scaled shape) — with the roster gone, every signal with a
    usable stop is considered, tagged by its own `book` string so
    `book_feasibility` can look the row back up."""
    from contracts.risk.models import RiskProfile
    from pathiel.client.exchange import MIN_ORDER_USD
    from services.risk import books as risk_books
    from services.risk.derive import VenueRules, book_feasibility, derive_limits

    venue = VenueRules(min_order_notional_usd=MIN_ORDER_USD)
    base = risk_books.main_engine_spec({})

    per_equity: List[Dict[str, Any]] = []
    violations: List[str] = []

    for equity in equities:
        profile = RiskProfile(preset=profile_name)
        would_execute = 0
        considered = 0
        risk_taken_usd = 0.0
        for sig in signals:
            book = sig.get("book") or risk_books.MAIN_ENGINE
            stop_pct = float(sig.get("stop_pct") or 0.0)
            if stop_pct <= 0:
                continue   # no usable stop
            considered += 1
            spec = replace(base, name=str(book), enabled=True, stop_pct=stop_pct)
            limits = derive_limits(float(equity), profile, [spec], venue)
            row = book_feasibility(limits, book)
            if row is None or not row.can_trade:
                continue
            would_execute += 1
            risk_taken_usd += float(row.risk_usd or 0.0)
            cap = (limits.profile.max_bumped_risk_pct if row.bumped_to_min
                  else limits.profile.risk_per_trade_pct)
            if cap is not None and (row.risk_pct or 0.0) > cap + 1e-6:
                violations.append(
                    f"equity=${equity:g} book={book} coin={sig.get('coin')} "
                    f"risk_pct={row.risk_pct} exceeds cap={cap} (bumped={row.bumped_to_min})")
        per_equity.append({
            "equity_usd": equity, "signals_considered": considered,
            "would_execute": would_execute, "risk_taken_usd": round(risk_taken_usd, 2),
        })

    return {
        "generated_at_ms": int(time.time() * 1000),
        "signals_total": len(signals),
        "profile": profile_name,
        "per_equity": per_equity,
        "pass_rule": "no trade exceeds its limit",
        "violations": violations,
        "passed": not violations,
    }


def format_report(report: Dict[str, Any]) -> str:
    lines = [f"# Replay: {report['signals_total']} signal(s), profile={report['profile']}\n"]
    for row in report["per_equity"]:
        lines.append(
            f"  ${row['equity_usd']:>10,.2f}  would_execute={row['would_execute']:>4}/"
            f"{row['signals_considered']:<4}  risk_taken=${row['risk_taken_usd']:,.2f}")
    lines.append(f"\nPass rule: {report['pass_rule']}")
    lines.append("PASSED" if report["passed"] else f"FAILED ({len(report['violations'])} violation(s))")
    for v in report["violations"][:20]:
        lines.append(f"  - {v}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--state-dir", default=None,
                   help="state dir to copy shadow_ledger/ from (default: $PATHIEL_STATE_DIR)")
    ap.add_argument("--equities", default="15,100,1000,10000,100000")
    ap.add_argument("--profile", default="auto",
                   choices=["auto", "starter", "small", "standard", "pro", "custom"])
    ap.add_argument("--max-age-days", type=float, default=14.0)
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args(argv)

    state_dir_src = args.state_dir or os.environ.get("PATHIEL_STATE_DIR")
    if not state_dir_src:
        print("no --state-dir given and PATHIEL_STATE_DIR is unset — nothing to replay",
             file=sys.stderr)
        return 2

    tmp_dir = copy_shadow_ledger(state_dir_src)
    signals = load_recent_signals(tmp_dir, args.max_age_days)
    equities = [float(x) for x in args.equities.split(",") if x.strip()]
    report = replay(signals, equities, args.profile)

    print(json.dumps(report, indent=2) if args.as_json else format_report(report))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
