"""Daily shadow-scout cron: record new divergences + grade resolved markets, then
print + append one status line. Zero capital — this only accrues the shadow
ledger toward the go-live gate (README.md). Run by cron; safe to run by hand.

    python -m services.polymarket_scout.daily
"""
from __future__ import annotations

import os
import time

from services.polymarket_scout import ledger
from services.polymarket_scout.forecaster import ClaudeForecaster
from services.polymarket_scout.run import CFG, scan
from services.polymarket_scout.scout import PolymarketClient, make_gamma_resolver


def main() -> int:
    client = PolymarketClient()
    try:
        rec = scan(client, ClaudeForecaster(), CFG, limit=int(os.environ.get("POLY_SCOUT_LIMIT", "15")))
    except Exception as exc:                          # never let a scan error skip grading
        rec = []
        print(f"[scout-daily] scan error: {exc}")
    g = ledger.grade(make_gamma_resolver(client))
    ts = time.strftime("%Y-%m-%d %H:%M")
    line = (f"[{ts}] polymarket_scout +{len(rec)} new | graded n={g['n']} "
            f"pending={g.get('pending', 0)}")
    if g.get("n"):
        line += (f" | pnl/$={g['mean_pnl_per_$']:+.3f} win={g['win_rate']} "
                 f"brierLLM={g['brier_llm']} brierMKT={g['brier_mkt']} "
                 f"LLM_beats_mkt={g['llm_beats_market']}")
    # gate reminder once there is real n
    if g.get("n", 0) >= 150 and g.get("llm_beats_market") and g.get("mean_pnl_per_$", 0) >= 0.03:
        line += "  <<< GATE: n>=150 + LLM beats market + >=+3%/pos — review for live"
    print(line)
    try:
        with open(os.path.join(ledger._state_dir(), "daily.log"), "a") as fh:
            fh.write(line + "\n")
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
