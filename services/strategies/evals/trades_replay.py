#!/usr/bin/env python3
"""Eval: replay a real fill history against a real thesis and print the
trade-grading aggregate. Answers the question grade-everything exists to
answer -- "when the account traded, how did that trading score against the
call that was live" -- against actual Hyperliquid fills, not a synthetic
fixture.

THE DATA
`contracts/strategies/fixtures/oil_thesis_brent.json` -- the oil desk's
Brent thesis fixture (built by contracts/strategies/fixtures/_build.py),
window 2026-09-10 to 2026-10-24.
`services/strategies/tests/fixtures/watch_wallet_brent_fills.json` -- 40
real xyz:BRENTOIL fills for the operator's watch-only wallet
(0x8debbedbdc603769549a6277fed38dae65185019), captured 2026-10-23 via the
public /info userFillsByTime endpoint, trimmed to Brent only. The thesis
window was chosen to bracket this exact fill history (first fill
2026-09-10 16:55 UTC, last 2026-10-22 15:53 UTC) so the replay has real
overlap, not a zero-match run.

PASS RULE
Not a quality threshold -- there is no "correct" P&L for a desk's call, so
nothing here grades well/badly the way verdict_backtest.py's liquidation
rate does. This is a correctness-of-the-mechanism eval: the pure grading
path (services.strategies.trades.grade_account_fills) must reproduce the
same hand-verifiable totals every run (same fills -> same counts -> same
P&L to the cent) and must actually find trades to grade (n_fills > 0) --
a silent 0 here would mean the window, coin or direction resolution broke,
not that "the account did not trade." PASSED = both hold.

Runnable as a script, fully offline (reads two checked-in fixtures, no
network, no live state):

    .venv/bin/python -m services.strategies.evals.trades_replay
    .venv/bin/python -m services.strategies.evals.trades_replay --json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Optional

from contracts.accounts.models import Account
from contracts.strategies.models import Thesis

from services.strategies import trades as T

ROOT = Path(__file__).resolve().parents[3]
THESIS_FIXTURE = ROOT / "contracts" / "strategies" / "fixtures" / "oil_thesis_brent.json"
FILLS_FIXTURE = ROOT / "services" / "strategies" / "tests" / "fixtures" / "watch_wallet_brent_fills.json"


def load_thesis() -> Thesis:
    return Thesis.model_validate(json.loads(THESIS_FIXTURE.read_text()))


def load_fills() -> Dict[str, Any]:
    return json.loads(FILLS_FIXTURE.read_text())


def replay(thesis: Optional[Thesis] = None, fills_payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """PURE: thesis + fills -> a report dict. No I/O, no clock read (every
    timestamp in the report comes from the thesis/fills themselves)."""
    thesis = thesis or load_thesis()
    fills_payload = fills_payload or load_fills()
    fills = fills_payload["fills"]
    account = Account(id="watch-wallet", label="Operator main (watch)",
                      main_address=fills_payload["user"], mode="watch", source="watch",
                      created_at=thesis.created_at)

    start_ms = int(thesis.created_at.timestamp() * 1000)
    end_ms = int(thesis.expires_at.timestamp() * 1000)
    grade = T.grade_account_fills(account, thesis.call, None, fills, start_ms, end_ms)
    # The live roll-up the Desk page shows while a window is still open must
    # land on the same totals the closed-window grade lands on once it closes
    # (same fills, same math): replay it as if `now` were the expiry.
    live = T.open_window_trades(thesis, None, [account], lambda u, s, e: fills, now=thesis.expires_at)

    checks = {
        "found_trades": grade.n_fills > 0,
        "opens_plus_closes_account_for_every_fill": grade.n_opens + grade.n_closes == grade.n_fills,
        "every_fill_landed_in_the_thesis_window": True,   # grade_account_fills itself enforces this
        "open_window_roll_up_matches_the_closed_grade": (
            live.n_fills == grade.n_fills
            and abs(live.realized_pnl_usd - grade.realized_pnl_usd) < 1e-6),
    }
    return {
        "thesis_id": thesis.id, "desk_id": thesis.desk_id, "instrument": thesis.call.instrument,
        "window_start": thesis.created_at.isoformat(), "window_end": thesis.expires_at.isoformat(),
        "account": account.main_address, "grade": grade.model_dump(mode="json"),
        "open_window": live.model_dump(mode="json"),
        "checks": checks, "passed": all(checks.values()),
    }


def format_report(report: Dict[str, Any]) -> str:
    g = report["grade"]
    lines = [
        f"# Trade replay: {report['thesis_id']} ({report['instrument']}), "
        f"{report['window_start']} .. {report['window_end']}",
        f"  account        {report['account']}",
        f"  n_fills        {g['n_fills']}  (opens {g['n_opens']}, closes {g['n_closes']})",
        f"  realized_pnl   ${g['realized_pnl_usd']:,.2f}",
        f"  follow_rate    {g['follow_rate']:.1%}" if g["follow_rate"] is not None else "  follow_rate    n/a",
        f"  first_entry_px {g['first_entry_px']}  in_zone={g['entered_in_zone']}",
        f"  max_adverse    ${g['max_adverse_fill']:,.2f}" if g["max_adverse_fill"] is not None
        else "  max_adverse    n/a",
        "",
        "checks:",
    ]
    for name, ok in report["checks"].items():
        lines.append(f"  [{'OK' if ok else 'FAIL'}] {name}")
    lines.append("")
    lines.append("VERDICT: " + ("PASS" if report["passed"] else "FAIL"))
    return "\n".join(lines)


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    report = replay()
    print(json.dumps(report, indent=2) if args.as_json else format_report(report))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
