"""Eval: walk-forward backtest of the verdict rule, across every desk's
primary instrument(s). Answers the question the build brief asks for
directly — "how often was a 'hold' rung actually liquidated, historically"
— WITHOUT the circularity of just re-reading p_liq off the same sample it
was fit on.

For each day i (after a warmup window), it classifies the rung's verdict
using ONLY data up to and including day i (`analytics.simulate_ladder_rung`
run on `daily[:i+1]` — no lookahead), then checks whether the ONE actual
forward window starting at day i went on to breach that leverage's
liquidation distance. Aggregated across every such day, "what fraction of
'hold'-classified days were actually liquidated" is the number that matters:
verdict_for_rung's hold bucket promises < 10% p_liq in-sample, so its true
out-of-sample liquidation rate should land in the same neighborhood. A rate
that blows well past the threshold below means the rule is overconfident.

Pass threshold: PASS_THRESHOLD = 0.15 (hold's own promise is <10%; the eval
gives 5 points of slack for out-of-sample noise on top of the in-sample
cutoff). Runnable as a script — hits live Hyperliquid data, so this is a
periodic/nightly eval, not a commit-gate test:

    PATHIEL_STATE_DIR=$(mktemp -d) .venv/bin/python -m services.strategies.evals.verdict_backtest
    PATHIEL_STATE_DIR=$(mktemp -d) .venv/bin/python -m services.strategies.evals.verdict_backtest --desk oil --pass-threshold 0.2

The pure walk-forward loop (`walk_forward_liq_check`) takes candles as a
plain argument and has its own OFFLINE gate test
(tests/test_verdict_backtest_eval.py) on synthetic data; only the
live-candle-fetching __main__ path needs a network.
"""
from __future__ import annotations

import argparse
from typing import Dict, List, Optional, Sequence, Tuple

from contracts.strategies.models import Side
from pathiel.models.types import Candle

from services.strategies import analytics as A
from services.strategies import desks

PASS_THRESHOLD = 0.15
DEFAULT_WARMUP_DAYS = 60


def walk_forward_liq_check(coin: str, daily: Sequence[Candle], side: Side, leverage: float,
                          max_leverage: float, horizon_days: int,
                          warmup_days: int = DEFAULT_WARMUP_DAYS) -> List[Tuple[str, bool]]:
    """One (verdict, actually_liquidated) pair per eligible day. `verdict`
    is classified from data strictly BEFORE and INCLUDING day i; whether day
    i's own forward window actually got liquidated is checked independently
    against days i+1..i+horizon_days — no lookahead in either direction."""
    out: List[Tuple[str, bool]] = []
    if leverage > max_leverage:
        return out
    for i in range(warmup_days, len(daily) - horizon_days):
        trailing = daily[: i + 1]
        rung = A.simulate_ladder_rung(coin, side, leverage, max_leverage, 1.0, trailing,
                                      horizon_days, funding_hourly=0.0)
        if rung is None:
            continue
        entry = daily[i].c
        window = daily[i + 1: i + 1 + horizon_days]
        d_liq = abs(rung.liq_dist_pct)
        if side == "long":
            actually_liquidated = any((c.l / entry - 1.0) <= -d_liq for c in window)
        else:
            actually_liquidated = any(-(c.h / entry - 1.0) <= -d_liq for c in window)
        out.append((rung.verdict, actually_liquidated))
    return out


def hold_liquidation_rate(results: Sequence[Tuple[str, bool]]) -> Optional[float]:
    holds = [liq for verdict, liq in results if verdict == "hold"]
    return (sum(holds) / len(holds)) if holds else None


def evaluate(results: Sequence[Tuple[str, bool]], *, pass_threshold: float = PASS_THRESHOLD) -> Dict:
    rate = hold_liquidation_rate(results)
    n_hold = sum(1 for v, _ in results if v == "hold")
    return {
        "n_days": len(results), "n_hold": n_hold, "hold_liq_rate": rate,
        "passed": (rate is None) or (rate <= pass_threshold),
    }


def _default_daily_fetcher(coin: str, count: int = 400) -> List[Candle]:
    from pathiel.client.hl_client import fetch_hl_candles
    return fetch_hl_candles(coin, "1d", count)


def run_for_desk(desk_id: str, *, pass_threshold: float = PASS_THRESHOLD,
                 daily_fetcher=None, meta_fetcher=None) -> List[Dict]:
    """daily_fetcher: coin -> list[Candle]. meta_fetcher: () -> {coin: row}
    (for maxLeverage). Both default to live HL reads; injected by tests to
    stay offline."""
    daily_fetcher = daily_fetcher or _default_daily_fetcher
    meta_fetcher = meta_fetcher or A.default_meta_fetcher
    spec = desks.require_spec(desk_id)
    meta = meta_fetcher()

    rows = []
    for instrument in spec.instruments:
        if instrument.role != "primary":
            continue
        daily = daily_fetcher(instrument.coin)
        max_lev = _fnum((meta.get(instrument.coin) or {}).get("maxLeverage")) or max(spec.leverage_ladder)
        for leverage in spec.leverage_ladder:
            for side in ("long", "short"):
                results = walk_forward_liq_check(instrument.coin, daily, side, float(leverage),
                                                 max_lev, spec.horizon_days)
                report = evaluate(results, pass_threshold=pass_threshold)
                report.update(desk=desk_id, coin=instrument.coin, side=side, leverage=leverage)
                rows.append(report)
    return rows


def _fnum(v) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--desk", help="one desk id; default: every registered desk")
    parser.add_argument("--pass-threshold", type=float, default=PASS_THRESHOLD)
    args = parser.parse_args(argv)

    desk_ids = [args.desk] if args.desk else desks.list_desk_ids()
    all_rows: List[Dict] = []
    for desk_id in desk_ids:
        all_rows.extend(run_for_desk(desk_id, pass_threshold=args.pass_threshold))

    print(f"{'desk':<10} {'coin':<14} {'side':<6} {'lev':>5} {'n_hold':>7} {'hold_liq%':>10} {'pass':>5}")
    overall_pass = True
    for r in all_rows:
        rate = f"{r['hold_liq_rate']:.1%}" if r["hold_liq_rate"] is not None else "n/a"
        print(f"{r['desk']:<10} {r['coin']:<14} {r['side']:<6} {r['leverage']:>5g} "
             f"{r['n_hold']:>7} {rate:>10} {'OK' if r['passed'] else 'FAIL':>5}")
        overall_pass = overall_pass and r["passed"]

    print(f"\npass threshold: hold-bucket out-of-sample liquidation rate <= {args.pass_threshold:.0%}")
    print("VERDICT:", "PASS" if overall_pass else "FAIL")
    return 0 if overall_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
