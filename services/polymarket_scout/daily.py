"""Scout cron: forecast both lanes, grade what resolved, refresh the dashboard
board, print + append one status line. Zero capital — this only accrues the
shadow ledger toward the go-live gate (README.md). Safe to run by hand.

    python -m services.polymarket_scout.daily                 # both lanes + board
    python -m services.polymarket_scout.daily --board-only    # no LLM: refresh cache
"""
from __future__ import annotations

import argparse
import os
import time

from services.polymarket_scout import board, ledger
from services.polymarket_scout.forecaster import BrainForecaster
from services.polymarket_scout.run import CFG, TRENDING_CFG, scan, scan_trending
from services.polymarket_scout.scout import PolymarketClient, make_gamma_resolver


def _grade_line(client: PolymarketClient) -> str:
    """One segment per lane. The lanes are separate hypotheses — pooling them
    would let a good lane launder a bad one through the go-live gate."""
    resolver = make_gamma_resolver(client)          # cached: one fetch per market
    rows = ledger.load()
    parts = []
    for lane in ledger.LANES:
        g = ledger.grade(resolver, rows=rows, lane=lane)
        if not g.get("n") and not g.get("pending"):
            continue
        seg = f"{lane}: n={g['n']} pending={g.get('pending', 0)}"
        if g.get("n"):
            seg += (f" pnl/$={g['mean_pnl_per_$']:+.3f} win={g['win_rate']} "
                    f"brierLLM={g['brier_llm']} brierMKT={g['brier_mkt']} "
                    f"beats_mkt={g['llm_beats_market']}")
            if (g["n"] >= board.GATE["min_n"] and g.get("llm_beats_market")
                    and g["mean_pnl_per_$"] >= board.GATE["min_mean_pnl"]):
                seg += "  <<< GATE PASSED — review for live"
        parts.append(seg)
    return " | ".join(parts) or "no ledger rows yet"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--board-only", action="store_true",
                    help="skip the LLM lanes; just refresh the dashboard cache")
    # Raised 2026-07-25: the gate needs n>=150 per lane and the queues are now
    # event- and tag-deduped, so each extra read buys a genuinely new outcome
    # rather than another row on the same ceasefire. ~24 reads/day gets the
    # trending lane to a first verdict in weeks, not months.
    ap.add_argument("--judgment-limit", type=int,
                    default=int(os.environ.get("POLY_SCOUT_LIMIT", "14")))
    ap.add_argument("--trending-limit", type=int,
                    default=int(os.environ.get("POLY_SCOUT_TRENDING_LIMIT", "10")))
    args = ap.parse_args()

    client = PolymarketClient()
    n_j = n_t = 0
    provider = ""
    if not args.board_only:
        fc = BrainForecaster()
        provider = fc.provider
        # One skip set shared by both lanes: the trending lane must not
        # re-forecast a market the judgment lane just recorded — that is a
        # correlated duplicate, not independent evidence.
        skip = {str(r.get("market_id")) for r in ledger.load()}
        try:
            rec_j = scan(client, fc, CFG, limit=args.judgment_limit, skip_ids=skip)
        except Exception as exc:            # a scan error must never skip grading
            rec_j = []
            print(f"[scout-daily] judgment scan error: {exc}")
        n_j = len(rec_j)
        skip |= {str(r.get("market_id")) for r in rec_j}
        try:
            rec_t = scan_trending(client, fc, cfg=TRENDING_CFG,
                                  limit=args.trending_limit, skip_ids=skip)
        except Exception as exc:
            rec_t = []
            print(f"[scout-daily] trending scan error: {exc}")
        n_t = len(rec_t)

    try:
        payload = board.refresh(client, provider=provider)
        board_seg = (f"board: universe={payload['universe']} "
                     f"trend={payload['counts']['trending']} "
                     f"brk={payload['counts']['breaking']}")
    except Exception as exc:
        board_seg = f"board: refresh FAILED ({exc})"

    line = (f"[{time.strftime('%Y-%m-%d %H:%M')}] polymarket_scout "
            f"+{n_j} judgment +{n_t} trending | {_grade_line(client)} | {board_seg}")
    print(line)
    try:
        with open(os.path.join(ledger._state_dir(), "daily.log"), "a") as fh:
            fh.write(line + "\n")
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
