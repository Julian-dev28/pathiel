#!/usr/bin/env python3
"""wallet_follow matched random-time null — the extra grading bar from
research/rebuild_2026_07_18/VERIFIED_TRADERS.md §4.

scripts/shadow_status.py stays the single VALIDATED/REFUTED handler; this adds
the book-specific null wallet_follow must ALSO beat before any promotion talk:
for each graded signal, >= 2,000 same-coin same-side random-time entries drawn
from the trailing 90 days, same horizon/stop, price@12bps — require p < 0.01.

Usage:
    python3 scripts/wallet_follow_null.py            # grade + null, prints verdict
    python3 scripts/wallet_follow_null.py --json
    python3 scripts/wallet_follow_null.py --draws 5000

Reads the ledger + public candles only. No trading, no state writes.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
_env = _REPO / ".env.local"
if _env.is_file():
    for _line in _env.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

from hermes_trader.agents import shadow_ledger as SL                    # noqa: E402
from hermes_trader.agents.wallet_follow_recorder import (               # noqa: E402
    BOOK, MC_COST_BPS, MC_N_DRAWS, MC_P_REQUIRED, mc_null_pvalue,
)

_TRAILING_DAYS = 90          # spec: null entries drawn from the trailing 90d


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--draws", type=int, default=MC_N_DRAWS)
    ap.add_argument("--seed", type=int, default=1337)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    now_ms = int(time.time() * 1000)
    recs = [r for r in SL.load(BOOK) if r.get("side") in ("long", "short")]
    recs, deduped = SL.dedup_episodes(recs)
    resolved = [r for r in recs
                if int(r.get("signal_bar_t") or 0) and float(r.get("entry_ref_px") or 0) > 0
                and now_ms >= int(r["signal_bar_t"]) + SL.resolve_after_ms(
                    float(r.get("horizon_days") or 0.0))]
    if not resolved:
        print(f"# wallet_follow null: no resolved episodes yet ({deduped} deduped). "
              "Let the recorder run; the 30-episode bar is 3-6 weeks out.")
        return 0

    from hermes_trader.client.hl_client import fetch_hl_candles
    cost = MC_COST_BPS / 10000.0
    events: List[Dict[str, Any]] = []
    bars_by_coin: Dict[str, List[Any]] = {}
    for r in resolved:
        coin = str(r.get("coin"))
        interval, _, n_bars = SL.grade_interval(float(r["horizon_days"]))
        if interval != "1d":
            continue                       # book records 3d horizons; daily null only
        if coin not in bars_by_coin:
            bars_by_coin[coin] = fetch_hl_candles(coin, "1d", _TRAILING_DAYS + n_bars + 5) or []
        sig_t = int(r["signal_bar_t"])
        fwd = [b for b in bars_by_coin[coin] if int(getattr(b, "t", 0) or (
            b.get("t") if isinstance(b, dict) else 0)) > sig_t]
        sim = SL.simulate_exit(str(r["side"]), float(r["entry_ref_px"]), fwd,
                               float(r["stop_pct"]), n_bars)
        if sim is None:
            continue
        events.append({"coin": coin, "side": r["side"],
                       "horizon_days": float(r["horizon_days"]),
                       "stop_pct": float(r["stop_pct"]), "ret": sim[0] - cost})

    res = mc_null_pvalue(events, bars_by_coin, n_draws=args.draws, seed=args.seed)
    out = {"book": BOOK, "resolved": len(resolved), "graded": len(events),
           "deduped": deduped, "null": res,
           "bar": f"p < {MC_P_REQUIRED} at >= 2000 draws, alongside shadow_status "
                  "VALIDATED at >= 30 episodes"}
    if args.json:
        print(json.dumps(out, indent=2, default=str))
        return 0
    print(f"# wallet_follow matched null @ {time.strftime('%Y-%m-%d %H:%M')}")
    print(f"resolved episodes : {len(resolved)} (deduped {deduped}, graded {len(events)})")
    if res is None:
        print("null              : not computable (no coin has enough trailing bars)")
        return 0
    print(f"observed mean     : {res['obs_mean_pct']:+.3f}%/sig @{MC_COST_BPS:.0f}bps")
    print(f"null p-value      : {res['p']:.4f} ({res['n_draws']} draws)")
    print(f"verdict           : {'BEATS NULL' if res['pass'] else 'DOES NOT beat null'} "
          f"(requires p < {MC_P_REQUIRED}); promotion also needs shadow_status "
          "VALIDATED at >= 30 episodes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
