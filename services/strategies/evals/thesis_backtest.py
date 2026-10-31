"""Score a directory of AI theses for ANY coin under the live desk rules.

Operator, 2026-10-31, after the market scout picked silver as the only
other candidate: "alright run the 30 day test". The replay
(services/thesis/evals/replay.py --desk metals --primary xyz:SILVER) writes
one Opus thesis per day; this eval asks what the live rules would have made
trading those calls, against the plain direction series on the same window:

  ai_theses     the thesis in force at each 15m bar (journaled_direction):
                its side, and never entering above a long's entry (below a
                short's): the oil desk's "never chase"
  always_short  / always_long / daily_sign: the market scout's controls

Rules: `market_scout.scouting_params`, i.e. `desk_tick_sim.live_params()`
(market entry off the 24h range, 2R gate, 0.5% target offset, limit target,
fixed stop, re-entry) with the stop buffer rescaled to the coin's 1h ATR
and leverage capped at the venue max. $64 start, as every other eval.

Also flags the theses that cite the venue's all-time high/low: on a market
the venue listed recently that "all-time" level is only the listing's
range (silver's history starts 2025-12-26), so the realism cap can block a
trade the real chart allows.

    python -m services.strategies.evals.thesis_backtest --coin xyz:SILVER \
        --theses-dir /tmp/thesis-replay-silver/theses [--out /tmp/thesis-backtest/silver.json]

Reads candles through the eval disk cache and the venue's public meta;
never touches the live account or the live journal.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from pathiel.models.types import Candle

from services.strategies.backtest import data as D
from services.strategies.backtest.desk_tick_sim import simulate
from services.strategies.evals.cross_market_backtest import mean_atr
from services.strategies.evals.desk_tick_backtest import journaled_direction
from services.strategies.evals.market_scout import (
    START_EQUITY, direction_series, scouting_params)
from services.strategies.evals.strategy_sweep import per_day

CONTROLS = ("always_short", "always_long", "daily_sign")
_ATH_ATL = re.compile(r"all[- ]time (high|low)|\bATH\b|\bATL\b", re.IGNORECASE)


def cites_all_time_level(thesis_json: Dict[str, Any]) -> bool:
    """True when the thesis's call text leans on the venue ATH/ATL."""
    call = thesis_json.get("call") or {}
    text = " ".join(str(call.get(k) or "") for k in ("one_liner", "invalidation"))
    return bool(_ATH_ATL.search(text))


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%MZ")


def score(c15: Sequence[Candle], c1h: Sequence[Candle], theses: Sequence[D.ThesisDirection],
          venue_max_leverage: float, start_ms: int, end_ms: int) -> Dict[str, Any]:
    """Every series over [start_ms, end_ms) with the same params. Pure."""
    atr, _ = mean_atr(c1h)
    if atr is None:
        return {"note": "under 15 1h bars: no ATR14, cannot size a stop"}
    params = scouting_params(venue_max_leverage, atr)
    w15 = [c for c in c15 if start_ms <= c.t < end_ms]
    w1h = [c for c in c1h if c.t < end_ms]
    out: Dict[str, Any] = {"window": {"start": _iso(start_ms), "end": _iso(end_ms)},
                           "stop_buffer": round(params.stop_buffer, 4), "leverage": params.leverage,
                           "series": {}}
    ai_run = simulate(w15, w1h, journaled_direction(theses), params)
    out["series"]["ai_theses"] = per_day(ai_run, start_ms, end_ms, start_equity=START_EQUITY)
    out["ai_trades"] = [{
        "side": t.side, "entry": _iso(t.entry_time), "entry_px": round(t.entry_px, 4),
        "stop_px": round(t.stop_px, 4), "target_px": round(t.target_px, 4),
        "exit": _iso(t.exit_time) if t.exit_time else None, "exit_px": t.exit_px,
        "exit_reason": t.exit_reason, "net_usd": round(t.net_pnl_usd, 2),
        "r": round(t.r_multiple, 2) if t.r_multiple is not None else None,
    } for t in ai_run.trades]
    for name in CONTROLS:
        run = simulate(w15, w1h, direction_series(name, c1h), params)
        out["series"][name] = per_day(run, start_ms, end_ms, start_equity=START_EQUITY)
    sides: Dict[str, int] = {}
    for th in theses:
        sides[th.side] = sides.get(th.side, 0) + 1
    out["thesis_sides"] = dict(sorted(sides.items()))
    return out


def main(argv: Optional[List[str]] = None) -> int:
    from services.strategies.evals.market_scout import fetch_market_meta, load_coin_candles

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--coin", required=True)
    ap.add_argument("--theses-dir", required=True, help="READ ONLY thesis directory")
    ap.add_argument("--cache-dir", default="/tmp/thesis-backtest/cache")
    ap.add_argument("--out", default=None, help="default /tmp/thesis-backtest/<coin>.json")
    a = ap.parse_args(argv)
    out_path = a.out or f"/tmp/thesis-backtest/{a.coin.replace(':', '_')}.json"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    theses = D.load_journaled_theses(a.theses_dir)
    if not theses:
        print(f"FATAL: no theses in {a.theses_dir}")
        return 2
    flagged = []
    for name in sorted(os.listdir(a.theses_dir)):
        if name.endswith(".json"):
            with open(os.path.join(a.theses_dir, name)) as fh:
                raw = json.load(fh)
            if cites_all_time_level(raw):
                flagged.append({"file": name, "side": raw["call"]["side"],
                                "one_liner": raw["call"]["one_liner"]})

    meta = fetch_market_meta([a.coin]).get(a.coin) or {}
    venue_max = float(meta.get("maxLeverage") or 20)
    candles = load_coin_candles(a.coin, a.cache_dir)
    c15, c1h = candles["c15"], candles["c1h"]
    start_ms = min(t.created_at_ms for t in theses)
    end_ms = max(c.t for c in c15) + 900_000
    report = score(c15, c1h, theses, venue_max, start_ms, end_ms)
    report.update({"coin": a.coin, "venue_max_leverage": venue_max, "n_theses": len(theses),
                   "cites_all_time_level": flagged})
    with open(out_path, "w") as fh:
        json.dump(report, fh, indent=1, default=str)

    print(f"{a.coin}: {len(theses)} theses {report.get('thesis_sides')}, window {report['window']}, "
          f"{report['leverage']:g}x, stop buffer {report['stop_buffer']}")
    print(f"{'series':<13} {'$end':>7} {'%/day':>6} {'%/wk':>6} {'trades':>6} {'win':>5} {'R':>6} {'dd':>5}")
    for name, r in report["series"].items():
        win = f"{r['win_rate']:.0%}" if r["win_rate"] is not None else "-"
        print(f"{name:<13} {r['final_equity']:7.2f} {r['daily_pct']:6.2f} {r['weekly_pct']:6.1f} "
              f"{r['trades']:>6} {win:>5} {r['total_r']:6.2f} {r['max_dd_pct']:4.0f}%")
    print(f"theses citing the venue all-time level: {len(flagged)}")
    print(f"report: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
