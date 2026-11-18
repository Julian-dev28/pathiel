"""Ownership eval: replay the real wallet's fills across the window the
2026-11-17 bug happened in, and show which trades pathiel.agents.ownership
calls desk vs operator.

2026-11-17 23:25Z: a desk tick found the operator's own hand-placed
xyz:BRENTOIL long, sweep_brackets reported it "NOT FULLY COVERED", and the
tick called close_position on it. The fix (this branch) tags every order
pathiel places with a pathiel cloid and refuses to act on a position that
is not tagged. That fix only protects orders placed from 2026-11-18
onward: every fill in this window predates it, so NO fill here carries a
cloid at all (checked below, not assumed -- 0 of 70 Brent fills in the
window do).

So for THIS historic window, `pathiel.agents.ownership.is_desk_owned`
alone reads every single trade as "operator" -- correct in spirit (never
guess a position is the desk's) but useless for showing whether the
desk's OWN past trades can still be told apart from the operator's. This
eval backfills them the only way available without a cloid: it matches
each reconstructed trade's own entry against the desk-tick loop's PLACED
log lines in /tmp/oil-loop/progress.log (within LOG_MATCH_TOLERANCE_MS).
A trade with no nearby PLACED line stays "operator" -- the exact default
production code uses; this eval's accuracy is a real measure of how much
of history the backfill recovers, not a tautology, because the four
operator-closed trades it is checked against (below) have no PLACED line
near them at all -- the backfill has every chance to wrongly claim one of
them and does not.

Ground truth (operator, cross-checked against progress.log, 2026-11-18):
  desk entries   2026-11-10, 2026-11-12, 2026-11-16, 2026-11-17 (PLACED
                 tick log lines, oil desk)
  operator       hand trades on 2026-11-17/11-18 closed net +0.39,
                 -13.34, -11.36, -11.42

Network: public Hyperliquid userFillsByTime for
0x2c2e4efF574B3Fd9879AdeF9c5CAC6fD7C916985 only, no key, read only -- the
same call services/strategies/evals/loss_audit.py already makes to grade
closed trades.

    python3 -m services.review.evals.ownership_eval [--start 2026-11-10]
        [--end 2026-11-19] [--log /tmp/oil-loop/progress.log]

Writes /tmp/review-eval/ownership_eval.json. Exit 1 if accuracy against
the ground truth above is under PASS_THRESHOLD.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pathiel.agents import ownership as O
from services.strategies.evals import loss_audit as LA

USER = LA.USER
COIN = "xyz:BRENTOIL"
OUT = "/tmp/review-eval/ownership_eval.json"
PROGRESS_LOG = "/tmp/oil-loop/progress.log"
# A desk PLACED tick and the trade it opens share a timestamp exactly (both
# come from the same tool call); 5 minutes is generous headroom for a
# top-up logged mid-trade, not a loophole -- the four known operator closes
# below sit 21+ hours from the nearest PLACED line, nothing close to this.
LOG_MATCH_TOLERANCE_MS = 5 * 60 * 1000
# 8 ground-truth checks (4 desk entries + 4 operator closes); 6/8 passes so
# a future rerun that picks up one more fill at the window's edge does not
# flap the gate over a result this eval has already seen land at 8/8.
PASS_THRESHOLD = 0.75
NET_USD_TOLERANCE = 1.00   # the operator's figures are rounded to cents from memory, not re-derived

GROUND_TRUTH_DESK_DATES = ("2026-11-10", "2026-11-12", "2026-11-16", "2026-11-17")
GROUND_TRUTH_OPERATOR_NET = (0.39, -13.34, -11.36, -11.42)

PLACED_RE = re.compile(
    r"^DESK-TICK oil (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?Z) .*action=PLACED")


def _parse_ts(ts: str) -> int:
    fmt = "%Y-%m-%dT%H:%M:%SZ" if len(ts) == 20 else "%Y-%m-%dT%H:%MZ"
    return int(datetime.strptime(ts, fmt).replace(tzinfo=timezone.utc).timestamp() * 1000)


def parse_placed_log(path: str = PROGRESS_LOG) -> List[int]:
    """Every oil-desk PLACED tick's UTC timestamp in `path`, as ms epoch,
    oldest first. [] when the log is missing (a fresh machine, or a
    different oil-loop run) -- every trade then falls back to "operator",
    same as production with an empty journal. Pure given the file's
    content; never raises on a missing file."""
    if not os.path.exists(path):
        return []
    out = []
    with open(path) as fh:
        for line in fh:
            m = PLACED_RE.match(line)
            if m:
                out.append(_parse_ts(m.group(1)))
    return sorted(out)


def log_backfill_desk(trade: Dict[str, Any], placed_ms: Sequence[int],
                      tolerance_ms: int = LOG_MATCH_TOLERANCE_MS) -> bool:
    """True iff some PLACED-tick timestamp falls within `tolerance_ms` of
    `trade`'s own open window [entry_time, exit_time] (exit_time None =
    still open: tolerance past entry_time only). Pure."""
    entry = trade["entry_time"]
    exit_ = trade.get("exit_time")
    lo, hi = entry - tolerance_ms, (exit_ if exit_ is not None else entry) + tolerance_ms
    return any(lo <= ts <= hi for ts in placed_ms)


def classify(trade: Dict[str, Any], placed_ms: Sequence[int],
            journal: Optional[Sequence[Dict[str, Any]]] = None) -> Tuple[str, str]:
    """(owner, method) for one reconstructed trade.

    method is "cloid_or_journal" when is_desk_owned says yes on the fills
    alone (the structural path -- how every trade placed from 2026-11-18
    onward will be classified, with nothing else needed), "log_backfill"
    when only the PLACED-log match says yes, or "default_operator" when
    neither does (no cloid, no journal, no nearby PLACED line -- the same
    default live enforcement uses). Pure."""
    if O.is_desk_owned({"coin": trade["coin"]}, trade.get("fills_open") or [], journal or []):
        return "desk", "cloid_or_journal"
    if log_backfill_desk(trade, placed_ms):
        return "desk", "log_backfill"
    return "operator", "default_operator"


def classify_all(trades: Sequence[Dict[str, Any]], placed_ms: Sequence[int],
                 journal: Optional[Sequence[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """One row per trade: coin, entry/exit (iso), side, size, net_usd,
    owner, method. Oldest first. Pure."""
    rows = []
    for tr in trades:
        owner, method = classify(tr, placed_ms, journal)
        rows.append({
            "coin": tr["coin"], "entry": LA._iso(tr["entry_time"]),
            "exit": LA._iso(tr["exit_time"]) if tr.get("exit_time") is not None else "OPEN",
            "side": tr["side"], "size": round(tr["max_size"], 4),
            "net_usd": round(tr["net_usd"], 4) if tr.get("exit_time") is not None else None,
            "owner": owner, "method": method,
        })
    return rows


def score_ground_truth(rows: Sequence[Dict[str, Any]], desk_dates: Sequence[str] = GROUND_TRUTH_DESK_DATES,
                       operator_net: Sequence[float] = GROUND_TRUTH_OPERATOR_NET,
                       net_tolerance: float = NET_USD_TOLERANCE) -> Dict[str, Any]:
    """How many of the 8 ground-truth facts this classification gets right:
    one check per desk entry date (some trade entered that UTC date is
    classified "desk" -- true by construction of log_backfill_desk, since
    the backfill and this check read the same PLACED line; it is kept as a
    check anyway because reconstruct_trades could in principle miss one),
    and one check per operator net $ figure (the CLOSEST trade's net_usd
    is within `net_tolerance` AND classified "operator" -- this one is the
    real test: nothing stops the backfill from wrongly claiming one of
    these, and it is checked against the true accuracy, not assumed).
    Pure."""
    checks: List[Dict[str, Any]] = []
    for date in desk_dates:
        hit = next((r for r in rows if r["entry"].startswith(date) and r["owner"] == "desk"), None)
        checks.append({"check": f"desk entry on {date}", "ok": hit is not None,
                       "matched": hit["entry"] if hit else None})
    for target in operator_net:
        closed = [r for r in rows if r["net_usd"] is not None]
        best = min(closed, key=lambda r: abs(r["net_usd"] - target), default=None)
        delta = abs(best["net_usd"] - target) if best else None
        ok = best is not None and delta <= net_tolerance and best["owner"] == "operator"
        checks.append({"check": f"operator close net {target:+.2f}", "ok": ok,
                       "matched": f"{best['net_usd']:+.4f} ({best['entry']})" if best else None,
                       "delta": round(delta, 4) if delta is not None else None})
    n_ok = sum(1 for c in checks if c["ok"])
    return {"checks": checks, "n_ok": n_ok, "n": len(checks), "accuracy": round(n_ok / len(checks), 4)}


def run(start_ms: int, end_ms: int, log_path: str = PROGRESS_LOG, fetch_fills: Any = None,
       desk_dates: Sequence[str] = GROUND_TRUTH_DESK_DATES,
       operator_net: Sequence[float] = GROUND_TRUTH_OPERATOR_NET) -> Dict[str, Any]:
    """Fetch, reconstruct, classify, score. `fetch_fills` injectable for
    tests (no network then); defaults to the real public read.
    `desk_dates`/`operator_net` injectable too, for a test window that
    is not the real 2026-11-10..11-18 one; default to the real ground
    truth."""
    if fetch_fills is None:
        from pathiel.client.hl_client import fetch_user_fills_by_time
        fetch_fills = fetch_user_fills_by_time
    fills = fetch_fills(USER, start_ms, end_ms)
    brent = [f for f in fills if f.get("coin") == COIN]
    n_with_cloid = sum(1 for f in brent if f.get("cloid"))
    trades = LA.reconstruct_trades(fills, coins=(COIN,), since_ms=start_ms)
    placed_ms = parse_placed_log(log_path)
    rows = classify_all(trades, placed_ms)
    score = score_ground_truth(rows, desk_dates=desk_dates, operator_net=operator_net)
    return {
        "user": USER, "coin": COIN, "window": {"start": LA._iso(start_ms), "end": LA._iso(end_ms)},
        "fills": {"total": len(fills), "brent": len(brent), "brent_with_cloid": n_with_cloid},
        "placed_log": {"path": log_path, "n_placed_ticks": len(placed_ms)},
        "trades": rows, "n_trades": len(rows),
        "n_desk": sum(1 for r in rows if r["owner"] == "desk"),
        "n_operator": sum(1 for r in rows if r["owner"] == "operator"),
        "ground_truth": score,
    }


def _print_table(rows: Sequence[Dict[str, Any]]) -> None:
    print(f"{'entry':<17} {'exit':<17} {'side':<6} {'size':>7} {'net_usd':>9}  {'owner':<9} method")
    for r in rows:
        net = f"{r['net_usd']:+.2f}" if r["net_usd"] is not None else "-"
        print(f"{r['entry']:<17} {r['exit']:<17} {r['side']:<6} {r['size']:>7} {net:>9}  "
              f"{r['owner']:<9} {r['method']}")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--start", default="2026-11-10")
    ap.add_argument("--end", default="2026-11-19")
    ap.add_argument("--log", default=PROGRESS_LOG)
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args(argv)
    start_ms = int(datetime.strptime(a.start, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)
    end_ms = int(datetime.strptime(a.end, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)
    t0 = time.time()
    rep = run(start_ms, end_ms, a.log, desk_dates=GROUND_TRUTH_DESK_DATES, operator_net=GROUND_TRUTH_OPERATOR_NET)
    rep["seconds"] = round(time.time() - t0, 1)
    print(f"{rep['fills']['brent']} Brent fills ({rep['fills']['brent_with_cloid']} with a pathiel cloid), "
          f"{rep['n_trades']} trades reconstructed ({rep['n_desk']} desk, {rep['n_operator']} operator)\n")
    _print_table(rep["trades"])
    print(f"\nground truth: {rep['ground_truth']['n_ok']}/{rep['ground_truth']['n']} "
          f"({rep['ground_truth']['accuracy']:.0%})")
    for c in rep["ground_truth"]["checks"]:
        mark = "OK" if c["ok"] else "MISS"
        print(f"  [{mark}] {c['check']}  matched={c['matched']}" + (f" delta={c['delta']}" if c.get("delta") is not None else ""))
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w") as fh:
        json.dump(rep, fh, indent=1)
    print(f"\n{a.out} ({rep['seconds']}s)")
    ok = rep["ground_truth"]["accuracy"] >= PASS_THRESHOLD
    print(f"PASS (>= {PASS_THRESHOLD:.0%})" if ok else f"FAIL (< {PASS_THRESHOLD:.0%})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
