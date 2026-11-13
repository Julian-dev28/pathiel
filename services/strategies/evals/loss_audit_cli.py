"""Command-line runner for the loss audit:

    python -m services.strategies.evals.loss_audit_cli \\
        [--oil-theses-dir <pathia>/.state/strategies/oil/theses] \\
        [--metals-theses-dir <pathia>/.state/strategies/metals/theses]

Split out of loss_audit.py (2026-11-13), on purpose: this file imports
services.strategies.backtest.data and services.strategies.evals.stop_cap_test
(which itself reaches desk_tick_backtest, live_params, profit_lock_test,
market_scout, strategy_sweep, scenario_sweep and cross_market_backtest --
the rest of the "paid or slow eval lanes" DATA_EXCLUDE keeps out of the
plugin bundle). loss_audit.py's own pure functions (reconstruct_trades,
attach_funding, enrich, ...) carry none of that, so services/pool/grade.py
can import them for real grading without scripts/build_plugin_bundle.py's
import-closure walk (every import reachable from the MCP server, lazy ones
inside functions included) pulling this whole chain into what ships.

Network: the public Hyperliquid /info endpoint only, through
loss_audit.load_inputs, read only. Writes /tmp/loss-audit/trades.csv,
causes.csv and audit.json, same as before the split.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import time
from typing import Any, Dict, List, Optional

from services.strategies.backtest import data as D
from services.strategies.evals import loss_audit as LA
from services.strategies.evals.stop_cap_test import same_market


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=LA.__doc__.split("\n")[0])
    ap.add_argument("--oil-theses-dir", default=os.environ.get("PATHIEL_OIL_THESES_DIR", ""))
    ap.add_argument("--metals-theses-dir", default=os.environ.get("PATHIEL_METALS_THESES_DIR", ""))
    ap.add_argument("--raw-dir", default="/tmp/loss-audit/raw")
    ap.add_argument("--cache-dir", default="/tmp/loss-audit/cache")
    ap.add_argument("--out-dir", default="/tmp/loss-audit")
    ap.add_argument("--refresh", action="store_true", help="refetch the account reads")
    a = ap.parse_args(argv)
    os.makedirs(a.out_dir, exist_ok=True)
    inputs = LA.load_inputs(a.raw_dir, a.refresh)
    bars = {c: D.fetch_candles_cached(c, "5m", 5000, a.cache_dir) for c in LA.COINS}
    oil = D.load_journaled_theses(a.oil_theses_dir) if a.oil_theses_dir else []
    metals = D.load_journaled_theses(a.metals_theses_dir) if a.metals_theses_dir else []
    if metals:
        metals = same_market(metals, D.fetch_candles_cached("xyz:SILVER", "1h", 1800, a.cache_dir))
    rep = LA.run_audit(inputs, bars, {"oil": oil, "metals": metals})
    rep["generated_at"] = time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime())
    rep["bars_end"] = {c: LA._iso(b[-1].t + LA.FIVE_MS) if b else None for c, b in bars.items()}
    rep["theses"] = {"oil": len(oil), "metals": len(metals)}
    with open(os.path.join(a.out_dir, "trades.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=LA.TRADE_FIELDS)
        w.writeheader()
        w.writerows(LA.trade_row(t) for t in rep["trades"])
    with open(os.path.join(a.out_dir, "causes.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["window", "view", "cause", "trades", "net_usd", "lost_usd",
                                           "at_stake_usd"])
        w.writeheader()
        for win, ct in rep["causes"].items():
            for view in ("by_tag", "by_primary"):
                for r in ct[view]:
                    w.writerow({"window": win, "view": view, **r})
    with open(os.path.join(a.out_dir, "audit.json"), "w") as fh:
        json.dump({**rep, "trades": [LA.trade_row(t) for t in rep["trades"]],
                   "open": [LA.trade_row(t) for t in rep["open"]]}, fh, indent=1, default=str)
    _print(rep)
    print(f"\n{a.out_dir}/trades.csv\n{a.out_dir}/causes.csv\n{a.out_dir}/audit.json")
    return 0


def _print(rep: Dict[str, Any]) -> None:
    print(f"bars end {rep['bars_end']}, theses {rep['theses']}")
    print(f"{'coin':<13} {'side':<5} {'entry':<17} {'exit':<17} {'in':>8} {'out':>8} {'kind':<6} {'net':>7} "
          f"{'eq':>7} {'%eq':>6} {'lev':>5} {'mfeR':>5} {'exR':>5} {'back':>5} {'flip':>5} tags")
    for t in rep["trades"]:
        r = LA.trade_row(t)
        f = lambda v, w=5: (f"{v:>{w}}" if v is not None else f"{'-':>{w}}")  # noqa: E731
        print(f"{r['coin']:<13} {r['side']:<5} {r['entry']:<17} {str(r['exit']):<17} {r['entry_px']:>8} "
              f"{str(r['exit_px']):>8} {r['exit_kind']:<6} {r['net_usd']:>7} {f(r['equity_at_size'], 7)} "
              f"{f(r['pct_equity'], 6)} {f(r['eff_leverage'])} {f(r['mfe_r'])} {f(r['exit_r'])} "
              f"{f(r['back_to_entry_h'])} {f(r['flip_after_h'])} {r['tags']}")
    for win, ct in rep["causes"].items():
        print(f"\n{win}: {json.dumps(ct['total'])}")
        for view in ("by_tag", "by_primary"):
            print(f"  {view}")
            for row in ct[view]:
                print(f"    {json.dumps(row)}")


if __name__ == "__main__":
    raise SystemExit(main())
