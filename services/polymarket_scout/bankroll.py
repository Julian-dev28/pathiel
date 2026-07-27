"""Bankroll simulation over the RESOLVED paper trades — 'pretend we started with
$X and Kelly-sized each bet.'

The scoreboard's `mean_pnl_per_$` is a flat, equal-weight average. This turns it
into a real money story: start with a bankroll, size each resolved bet by
(fractional) Kelly on OUR probability at the price we paid, compound sequentially
in resolution order, and show every trade + the equity curve.

Kelly for buying one side at price `c` when we think it wins with prob `p`:
    win pays odds b = (1-c)/c per $ staked;  f* = p - (1-p)/b   (clamped to [0,1])
`kelly_fraction` scales it (0.5 = half-Kelly, the standard conservative choice).
PnL per $ uses the same fee model the ledger grades with (`scout.paper_pnl`), so
the bankroll matches the scoreboard's accounting.

    python -m services.polymarket_scout.bankroll --start 50 --kelly 0.5
    python -m services.polymarket_scout.bankroll --lane updown_5m --json
"""
from __future__ import annotations

import argparse
import json
import time
from typing import Any, Callable, Dict, List, Optional

from services.polymarket_scout import ledger
from services.polymarket_scout.scout import (
    PolymarketClient, make_gamma_resolver, paper_pnl,
)


def kelly_fraction_for(prob_side: float, fill_px: float) -> float:
    """Full-Kelly fraction of bankroll for buying a side at `fill_px` when it wins
    with prob `prob_side`. Clamped to [0, 1] (never bet more than the bankroll,
    never short)."""
    c = min(max(float(fill_px), 1e-6), 1 - 1e-6)
    p = min(max(float(prob_side), 0.0), 1.0)
    b = (1.0 - c) / c
    f = p - (1.0 - p) / b if b > 0 else 0.0
    return max(0.0, min(1.0, f))


def our_side_prob(row: Dict[str, Any]) -> float:
    """Our probability that the side WE took wins."""
    yes = float(row.get("llm_yes") or 0.5)
    return yes if row.get("side") == "YES" else 1.0 - yes


def corrected_fill(row: Dict[str, Any]) -> float:
    """Price of the side we took, recomputed from mkt_yes + side. Repairs the
    historical updown recording bug (DOWN was stored at mkt_up instead of
    1-mkt_up) so the sim prices every trade correctly regardless of what the row
    recorded."""
    mkt = row.get("mkt_yes")
    if mkt is None:
        return float(row.get("fill_px") or 0.5)
    mkt = float(mkt)
    return mkt if row.get("side") == "YES" else round(1.0 - mkt, 4)


def simulate(rows: Optional[List[Dict[str, Any]]] = None,
             resolver: Optional[Callable[[str], Optional[bool]]] = None,
             start: float = 50.0, kelly_fraction: float = 0.5,
             lane: Optional[str] = None) -> Dict[str, Any]:
    """Walk the resolved trades in time order, Kelly-sizing off `start`. Returns
    the trade log, equity curve, and summary. `resolver(market_id)->won?` is
    injected so tests need no network."""
    rows = rows if rows is not None else ledger.load()
    if lane:
        rows = [r for r in rows if ledger.row_lane(r) == lane]
    rows = sorted(rows, key=lambda r: int(r.get("ts") or 0))
    if resolver is None:
        resolver = make_gamma_resolver(PolymarketClient())

    bankroll = float(start)
    peak = bankroll
    max_dd = 0.0
    trades: List[Dict[str, Any]] = []
    wins = 0
    for r in rows:
        yes_won = resolver(str(r.get("market_id")))
        if yes_won is None:
            continue                                  # unresolved — skip
        side_won = yes_won if r.get("side") == "YES" else (not yes_won)
        fill = corrected_fill(r)                      # repair the DOWN-side fill bug
        p = our_side_prob(r)
        f = kelly_fraction_for(p, fill) * float(kelly_fraction)
        bet = round(bankroll * f, 4)
        # per-$ net return on the stake (fee-aware, same as the ledger)
        ret = paper_pnl(bool(side_won), fill)
        pnl = round(bet * ret, 4)
        bankroll = round(bankroll + pnl, 4)
        peak = max(peak, bankroll)
        max_dd = max(max_dd, (peak - bankroll) / peak if peak > 0 else 0.0)
        wins += 1 if side_won else 0
        trades.append({
            "ts": r.get("ts"), "lane": ledger.row_lane(r),
            "q": (r.get("question") or "")[:60], "side": r.get("side"),
            "our_prob": round(p, 3), "fill": round(fill, 3),
            "kelly_frac": round(f, 4), "bet": bet, "won": bool(side_won),
            "pnl": pnl, "bankroll": bankroll,
        })
        if bankroll <= 0:                             # ruin — stop
            break

    n = len(trades)
    out: Dict[str, Any] = {
        "start": start, "kelly_fraction": kelly_fraction, "lane": lane or "all",
        "n_resolved": n, "final_bankroll": round(bankroll, 2),
        "total_return_pct": round((bankroll / start - 1) * 100, 2) if start else 0.0,
        "win_rate": round(wins / n, 3) if n else None,
        "max_drawdown_pct": round(max_dd * 100, 1),
        "trades": trades,
    }
    return out


def _fmt(sim: Dict[str, Any]) -> str:
    L = [f"# Bankroll sim — start ${sim['start']:.0f}, "
         f"{sim['kelly_fraction']:.0%}-Kelly, lane={sim['lane']}",
         f"# {sim['n_resolved']} resolved bets  |  final ${sim['final_bankroll']:.2f}  "
         f"({sim['total_return_pct']:+.1f}%)  |  win {sim['win_rate']}  "
         f"maxDD {sim['max_drawdown_pct']}%\n"]
    if not sim["trades"]:
        L.append("no resolved trades yet — nothing to simulate.")
        return "\n".join(L)
    L.append(f"{'#':>3} {'side':<4} {'ourP':>5} {'fill':>5} {'kelly':>6} "
             f"{'bet$':>8} {'W/L':>3} {'pnl$':>8} {'bank$':>9}  market")
    for i, t in enumerate(sim["trades"], 1):
        L.append(f"{i:>3} {t['side']:<4} {t['our_prob']:>5.2f} {t['fill']:>5.2f} "
                 f"{t['kelly_frac']:>6.3f} {t['bet']:>8.2f} {'W' if t['won'] else 'L':>3} "
                 f"{t['pnl']:>+8.2f} {t['bankroll']:>9.2f}  {t['q']}")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=float, default=50.0)
    ap.add_argument("--kelly", type=float, default=0.5, help="Kelly fraction (0.5=half-Kelly)")
    ap.add_argument("--lane", default="", help="restrict to one lane (e.g. updown_5m, trending)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    sim = simulate(start=args.start, kelly_fraction=args.kelly, lane=args.lane or None)
    print(json.dumps(sim, indent=1) if args.json else _fmt(sim))
    if args.out:
        json.dump(sim, open(args.out, "w"), indent=1)
        print(f"\n# wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
