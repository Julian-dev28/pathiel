"""The live silver config (operator, 2026-11-04: "turn on silver breakout,
chase OK on silver") on the silver test's own data: the breakout entry
(desk_tick_sim mode a5, measured target, 24h lookback, 4h stop) plus leverage
fit at 20/15 only (the operator's floor, "3x WAY TOO LOW"), against live,
combo_best2 (fit 20/15/10/8/5) and the breakout alone. Pinned candle
snapshot, no network, no LLM.

  python -m services.strategies.evals.silver_breakout_live_check \\
      --snapshot-dir ~/Documents/code/pathiel-replays/candles/20261103T224029Z

Writes <out-dir>/summary.csv (default /tmp/silver-breakout-live). Pass bar:
LIVE_NEW never below live on a thesis dataset (journal, replay), and within
2% of the $64 start on each no-thesis control (2026-11-04 run: always_long
-$60.83 vs live -$60.25, one extra losing breakout). The adoption was the
operator's call; this guards that the shipped config is the one scored.
"""
from __future__ import annotations

import argparse
import csv
import os
from typing import Any, Dict, List, Optional

from services.strategies.backtest import snapshot as SNAP
from services.strategies.evals import silver_strategy_test as S

BREAKOUT = {"pullback_entry": "a5", "pb_breakout_lookback_h": 24, "pb_breakout_stop_h": 4, "pb_target": "measured"}
VARIANTS: Dict[str, Dict[str, Any]] = {
    "live": {},
    "combo_best2": {**BREAKOUT, "entry_fit_leverage": tuple(S.LEV_GRID)},
    "LIVE_NEW": {**BREAKOUT, "entry_fit_leverage": (20.0, 15.0)},
    "breakout_only": dict(BREAKOUT),
}


def run(snapshot_dir: str, metals_dir: str, replay_dir: str, max_lev: float = 25.0) -> List[Dict[str, Any]]:
    snap = SNAP.load_snapshot(snapshot_dir, S.DEFAULT_COIN)
    c15, c1h, funding = snap.c15, snap.c1h, snap.funding
    _, b_end = snap.window("15m")
    atr_price, _ = S.mean_atr(c1h)
    base = S.live_silver_params(max_lev, atr_price or 0.4, starting_equity=S.START)
    results: Dict[str, Any] = {}
    for label, tdir, drop in (("journal", metals_dir, True), ("replay", replay_dir, False)):
        raw = S.D.load_journaled_theses(tdir)
        th = S.same_market(raw, c1h) if drop else raw
        if not th:
            continue
        start = min(t.created_at_ms for t in th)
        end = b_end if label == "journal" else min(b_end, max(t.created_at_ms for t in th) + S.DAY_MS)
        results[label] = S.run_dataset(c15, c1h, S.journaled_direction(th), base, start, end, VARIANTS,
                                       baseline="live", funding=funding)
    for s in S.CONTROLS:
        results[s] = S.run_dataset(c15, c1h, S.direction_series(s, c1h), base, b_end - 30 * S.DAY_MS, b_end,
                                   VARIANTS, baseline="live", funding=funding)
    return [{"dataset": ds, "variant": v, "net_usd": round(r["final_equity"] - S.START, 2),
             "max_dd_pct": r["max_dd_pct"], "trades": r["trades"]}
            for ds, res in results.items() for v, r in res["variants"].items()]


THESIS = ("journal", "replay")
CONTROL_SLACK_USD = 0.02 * S.START


def passes(rows: List[Dict[str, Any]]) -> bool:
    """LIVE_NEW at or above live on each thesis dataset, within
    CONTROL_SLACK_USD of live on each control."""
    by = {(r["dataset"], r["variant"]): r["net_usd"] for r in rows}
    for ds in {d for d, v in by if v == "LIVE_NEW"}:
        slack = 1e-9 if ds in THESIS else CONTROL_SLACK_USD
        if by[(ds, "LIVE_NEW")] < by[(ds, "live")] - slack:
            return False
    return True


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--snapshot-dir", default=os.path.expanduser(
        "~/Documents/code/pathiel-replays/candles/20261103T224029Z"))
    ap.add_argument("--metals-theses-dir", default=os.path.expanduser(
        "~/Documents/code/pathia/.state/strategies/metals/theses"), help="READ ONLY")
    ap.add_argument("--replay-theses-dir", default=os.path.expanduser(
        "~/Documents/code/pathiel-replays/metals-silver30/theses"))
    ap.add_argument("--out-dir", default="/tmp/silver-breakout-live")
    a = ap.parse_args(argv)
    rows = run(a.snapshot_dir, a.metals_theses_dir, a.replay_theses_dir)
    os.makedirs(a.out_dir, exist_ok=True)
    path = os.path.join(a.out_dir, "summary.csv")
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    for r in rows:
        print(f"{r['dataset']:13} {r['variant']:14} {r['net_usd']:9.2f} {r['max_dd_pct']:6.1f} {r['trades']:3d}")
    ok = passes(rows)
    print(f"{'PASS' if ok else 'FAIL'}: LIVE_NEW >= live on theses, within ${CONTROL_SLACK_USD:.2f} on controls; {path}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
