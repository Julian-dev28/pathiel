#!/usr/bin/env python3
"""Eval: does the oil desk-tick method carry over to other markets?

Operator, 2026-10-28: "is it replicable with other markets?" Runs the same
desk-tick simulator (`services.strategies.backtest.desk_tick_sim`, the 15m
clock, fixed stop, 1.5R gate, the 20x rule, no-reserve sizing) on Brent,
gold, BTC and the S&P 500 perp, with the oil eval's direction controls
(always long, always short, daily 20-day trend sign) so every market is
judged on the same mechanics. Oil's journaled-thesis run is a reference row
only: the other markets have no theses, so the controls are the only
apples-to-apples comparison.

Two leverage modes per market:
  - matched: leverage that gives the coin oil's risk per unit of leverage,
    min(venue max, round(20 x oil_atr% / coin_atr%)), where atr% is the mean
    1h ATR14 as a share of price over the window (`matched_leverage`).
  - flat: 20x, capped at the venue max, to show what happens unscaled.

The simulator's stop buffer is in price units ($0.50 on Brent, about one
1h ATR14). Each market gets the same buffer in ATR terms
(`scaled_stop_buffer`), so the stop sits the same structural distance
beyond the 8h edge everywhere.

Runnable as a script; hits the public /info endpoint on an empty cache (a
periodic eval, not a commit gate):

    .venv/bin/python -m services.strategies.evals.cross_market_backtest --cache-dir /tmp/x --out-dir /tmp/y

The pure parts (ATR%, leverage matching, buffer scaling, the row builder)
are gate-tested offline in services/strategies/tests/test_cross_market_backtest.py.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pathiel.models.types import Candle

from services.strategies.backtest import data as D
from services.strategies.backtest.desk_tick_sim import Params, Run, simulate
from services.strategies.evals.desk_tick_backtest import (
    DAILY_SIGN_EXTRA_LOOKBACK_DAYS, DEFAULT_THESES_DIR, aggregate, always_direction,
    build_daily_sign_direction, journaled_direction)

BASELINE = "xyz:BRENTOIL"
# (coin, label). The index: xyz:SP500, the most liquid index perp on xyz
# (2026-10-28 24h volume ~$237M, max 50x); xyz:XYZ100 is the other one.
MARKETS: Tuple[Tuple[str, str], ...] = (
    ("xyz:BRENTOIL", "Brent"),
    ("xyz:GOLD", "Gold"),
    ("BTC", "BTC"),
    ("xyz:SP500", "S&P 500"),
)
BASE_LEVERAGE = 20.0           # what the oil desk runs
OIL_STOP_BUFFER = Params().stop_buffer   # $0.50 beyond the 8h edge on Brent
WINDOWS_DAYS = (30, 50)        # the oil eval's window and the most 15m history the venue returns


# ── pure (gate-tested) ──────────────────────────────────────────────────────

def mean_atr(candles_1h: Sequence[Candle], period: int = 14) -> Tuple[Optional[float], Optional[float]]:
    """(mean ATR in price units, mean ATR as a share of the close) over every
    bar that has `period` true ranges behind it, the same ATR14 the
    simulator's `_levels` uses. (None, None) with too little history."""
    bars = sorted(candles_1h, key=lambda c: c.t)
    if len(bars) < period + 1:
        return None, None
    trs = [max(b.h - b.l, abs(b.h - a.c), abs(b.l - a.c)) for a, b in zip(bars, bars[1:])]
    atrs, pcts = [], []
    for i in range(period, len(trs) + 1):
        atr = sum(trs[i - period:i]) / period
        close = bars[i].c
        if close > 0:
            atrs.append(atr)
            pcts.append(atr / close)
    if not atrs:
        return None, None
    return sum(atrs) / len(atrs), sum(pcts) / len(pcts)


def matched_leverage(oil_atr_pct: float, coin_atr_pct: float, venue_max: float,
                     base: float = BASE_LEVERAGE) -> float:
    """Leverage that gives `coin` the same move-per-unit-of-leverage as oil at
    `base`: round(base x oil_atr% / coin_atr%), at least 1x, at most the
    venue max."""
    if coin_atr_pct <= 0 or oil_atr_pct <= 0:
        raise ValueError("ATR% must be positive")
    return float(max(1, min(int(venue_max), round(base * oil_atr_pct / coin_atr_pct))))


def flat_leverage(venue_max: float, base: float = BASE_LEVERAGE) -> float:
    return float(min(base, venue_max))


def scaled_stop_buffer(coin_atr: float, oil_atr: float, oil_buffer: float = OIL_STOP_BUFFER) -> float:
    """Oil's stop buffer in ATR units, applied to `coin`'s ATR (price units)."""
    if oil_atr <= 0:
        raise ValueError("oil ATR must be positive")
    return oil_buffer / oil_atr * coin_atr


def row(market: str, coin: str, days: int, mode: str, series: str, atr_pct: Optional[float],
        params: Params, run: Run) -> Dict[str, Any]:
    """One table row: the oil eval's aggregate plus the market, the leverage
    actually used and total R."""
    agg = aggregate(f"{coin}_{days}d_{mode}_{series}", run)
    r_values = [t.r_multiple for t in run.trades if t.r_multiple is not None]
    agg.update(market=market, coin=coin, days=days, mode=mode, series=series,
               atr_pct=atr_pct, leverage=params.leverage, venue_max=params.max_leverage,
               stop_buffer=params.stop_buffer, total_r=sum(r_values) if r_values else 0.0)
    return agg


# ── live path ───────────────────────────────────────────────────────────────

def venue_max_leverage(coins: Sequence[str]) -> Dict[str, float]:
    """Max leverage per coin from the venue's meta (main dex + each HIP-3 dex)."""
    from pathiel.client.exchange import _http_post
    out: Dict[str, float] = {}
    for dex in sorted({c.split(":", 1)[0] if ":" in c else "" for c in coins}):
        meta = _http_post("/info", {"type": "meta", **({"dex": dex} if dex else {})})
        for u in (meta or {}).get("universe", []):
            if u.get("name") in coins:
                out[u["name"]] = float(u.get("maxLeverage") or 0)
    return out


def _load(coin: str, days: int, cache_dir: str) -> Tuple[List[Candle], List[Candle], List[Candle], list, int]:
    now_ms = int(time.time() * 1000)
    c15 = D.fetch_candles_cached(coin, "15m", days * 24 * 4 + 20, cache_dir)
    c1h_long = D.fetch_candles_cached(coin, "1h", (days + DAILY_SIGN_EXTRA_LOOKBACK_DAYS) * 24, cache_dir)
    start = now_ms - days * D.DAY_MS
    c15w = [c for c in c15 if c.t >= start]
    c1hw = [c for c in c1h_long if c.t >= start]
    funding = D.fetch_funding_cached(coin, c15w[0].t if c15w else start, now_ms, cache_dir) if c15w else []
    return c15w, c1hw, c1h_long, funding, start


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", default="/tmp/pathiel-backtest/cross-market")
    parser.add_argument("--out-dir", default="/tmp/cross-market-backtest")
    parser.add_argument("--theses-dir", default=DEFAULT_THESES_DIR,
                        help="READ ONLY journaled oil theses (the reference row); empty skips it")
    args = parser.parse_args(argv)
    os.makedirs(args.cache_dir, exist_ok=True)
    os.makedirs(args.out_dir, exist_ok=True)

    coins = [c for c, _ in MARKETS]
    vmax = venue_max_leverage(coins)
    missing = [c for c in coins if not vmax.get(c)]
    if missing:
        print(f"FATAL: no venue max leverage for {missing}")
        return 2
    theses = D.load_journaled_theses(args.theses_dir) if args.theses_dir else []

    rows: List[Dict[str, Any]] = []
    for days in WINDOWS_DAYS:
        data = {coin: _load(coin, days, args.cache_dir) for coin in coins}
        oil_atr, oil_pct = mean_atr(data[BASELINE][1])
        for coin, label in MARKETS:
            c15w, c1hw, c1h_long, funding, _ = data[coin]
            atr, pct = mean_atr(c1hw)
            if not c15w or atr is None or oil_atr is None:
                rows.append({"market": label, "coin": coin, "days": days, "note": "no data"})
                continue
            buffer = scaled_stop_buffer(atr, oil_atr)
            daily_sign_at, _, _ = build_daily_sign_direction(c1h_long)
            series = [("always_long", always_direction("long")), ("always_short", always_direction("short")),
                      ("daily_sign", daily_sign_at)]
            if coin == BASELINE and theses:
                series.insert(0, ("journaled", journaled_direction(theses)))
            modes = [("matched", matched_leverage(oil_pct, pct, vmax[coin])), ("flat20", flat_leverage(vmax[coin]))]
            for mode, lev in modes:
                p = Params(leverage=lev, max_leverage=vmax[coin], stop_buffer=buffer, tick_minutes=15)
                for name, direction_at in series:
                    run = simulate(c15w, c1hw, direction_at, p, funding_history=funding)
                    rows.append(row(label, coin, days, mode, name, pct, p, run))

    report = {"generated_at": datetime.now(timezone.utc).isoformat(), "venue_max_leverage": vmax,
              "windows_days": list(WINDOWS_DAYS), "n_oil_theses": len(theses), "rows": rows}
    path = os.path.join(args.out_dir, "report.json")
    with open(path, "w") as fh:
        json.dump(report, fh, indent=2, default=str)
    _print(rows)
    print(f"\nreport: {path}")
    return 0


def _print(rows: List[Dict[str, Any]]) -> None:
    print(f"{'market':<8} {'d':>3} {'mode':<8} {'series':<13} {'atr%':>6} {'lev':>4} {'trades':>6} "
          f"{'win':>5} {'totR':>7} {'end$':>8} {'maxDD':>6} {'liq':>4}")
    for r in rows:
        if r.get("note"):
            print(f"{r['market']:<8} {r['days']:>3} {r['note']}")
            continue
        win = f"{r['win_rate']:.0%}" if r.get("win_rate") is not None else "n/a"
        print(f"{r['market']:<8} {r['days']:>3} {r['mode']:<8} {r['series']:<13} {r['atr_pct']:>6.2%} "
              f"{r['leverage']:>4g} {r['n_trades']:>6} {win:>5} {r['total_r']:>+7.2f} "
              f"{r['final_equity']:>8.2f} {r['max_drawdown_pct']:>6.1%} {'YES' if r['liquidated'] else 'no':>4}")


if __name__ == "__main__":
    raise SystemExit(main())
