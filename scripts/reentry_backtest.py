#!/usr/bin/env python3
"""Dedicated backtest of the momentum-continuation RE-ENTRY rule.

Isolates the rule's PnL by replaying the SAME entry/exit engine under two policies:
  BLOCK    — after a losing close, loss-cooldown blocks ALL re-entry (old live behavior)
  REENTRY  — during cooldown, allow re-entry IF price reclaims >= reclaim_pct ABOVE
             the stop-out price AND composite >= min_composite (the shipped fix)

Δ(REENTRY − BLOCK) = the rule's contribution. We also report the re-entry trades'
OWN win-rate/PnL — do the re-entries themselves make money, or whipsaw?

No lookahead: entry decision uses only the window up to bar i; fills at next bar open.

Usage: python3 scripts/reentry_backtest.py            # 14d, top-25, 1h
       python3 scripts/reentry_backtest.py --days 21 --coins 30 --interval 1h
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backtest import (  # reuse the validated primitives
    Candle, DSL, _evaluate, _trend_and_atr_pct, _heuristic_verdict, _ta_confirmed,
    ROUND_TRIP_FEE_BPS,
)
from hermes_trader.agents.config_store import read_agent_config as _live_cfg
from hermes_trader.client.hl_client import fetch_hl_candles
from hermes_trader.agents.config import get_config
from hermes_trader.client.universe import get_universe


def simulate(coin, candles, max_lev, *, policy, cfg, equity, equity_fraction,
             lev_ceiling, max_loss_pct, protect_pct, retrace_threshold,
             cooldown_bars, reclaim_pct, min_composite, warmup=100):
    """Returns list of trade dicts with is_reentry flag."""
    trades = []
    open_t = open_dsl = None
    fee = ROUND_TRIP_FEE_BPS / 10000.0
    cooldown_until = -1          # bar index until which we're in loss-cooldown
    last_stop_px = None          # stop-out price of the last loss (for reclaim test)

    for i in range(warmup, len(candles) - 1):
        window = candles[: i + 1]
        bar = candles[i]
        next_bar = candles[i + 1]

        if open_t and open_dsl:
            done, exit_px, reason = open_dsl.check_bar(i, bar)
            if done:
                gp = ((exit_px - open_t["entry_px"]) / open_t["entry_px"]
                      if open_t["side"] == "long"
                      else (open_t["entry_px"] - exit_px) / open_t["entry_px"])
                open_t["exit_px"] = exit_px
                open_t["pnl_usd"] = open_t["notional"] * (gp - fee)
                open_t["reason"] = reason
                trades.append(open_t)
                # losing close arms the cooldown
                if open_t["pnl_usd"] < 0:
                    cooldown_until = i + cooldown_bars
                    last_stop_px = exit_px
                open_t = open_dsl = None
            else:
                continue

        score, hits = _evaluate(window, cfg)
        bullish, atr_pct, adx14 = _trend_and_atr_pct(window)
        verdict = _heuristic_verdict(score, hits, bullish, atr_pct)
        if verdict is None:
            continue
        burst = any(h["name"] == "momentumBurst" and h["fired"] for h in hits)
        if not _ta_confirmed(bullish, atr_pct, adx14, score) and not burst:
            continue

        side = "long" if verdict == "LONG" else "short"
        is_reentry = False
        # cooldown gate
        if i + 1 <= cooldown_until:
            if policy == "BLOCK":
                continue
            # REENTRY: only LONG, only if price reclaimed above the stop + strong composite
            if (side != "long" or last_stop_px is None
                    or next_bar.o < last_stop_px * (1 + reclaim_pct / 100.0)
                    or score < min_composite):
                continue
            is_reentry = True

        lev = min(lev_ceiling, max_lev)
        notional = equity * equity_fraction * lev
        open_t = {"coin": coin, "side": side, "entry_px": next_bar.o,
                  "notional": notional, "leverage": lev, "exit_px": None,
                  "pnl_usd": 0.0, "reason": "", "is_reentry": is_reentry}
        open_dsl = DSL(side=side, entry_px=next_bar.o, entry_bar=i + 1,
                       peak_px=next_bar.o, max_loss_pct=max_loss_pct,
                       protect_pct=protect_pct, retrace_threshold=retrace_threshold)
    return trades


def _stats(trades):
    n = len(trades)
    if not n:
        return (0, 0.0, 0.0)
    w = sum(1 for t in trades if t["pnl_usd"] > 0)
    return (n, w / n * 100, sum(t["pnl_usd"] for t in trades))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--coins", type=int, default=25)
    ap.add_argument("--interval", default="1h", choices=["5m", "15m", "1h", "4h"])
    ap.add_argument("--equity", type=float, default=180.0)
    ap.add_argument("--equity-fraction", type=float, default=0.28)
    ap.add_argument("--leverage-ceiling", type=int, default=8)
    ap.add_argument("--max-loss", type=float, default=2.25)   # 18% ROE / 8x
    ap.add_argument("--protect", type=float, default=3.0)
    ap.add_argument("--retrace", type=float, default=0.55)
    args = ap.parse_args()

    live = _live_cfg()
    mr = live.get("momentum_reentry", {})
    reclaim_pct = float(mr.get("reclaim_pct", 1.0))
    min_comp = float(mr.get("min_composite", 30))
    lc_min = float(live.get("loss_cooldown_min", 180) or 180)
    bars_per_day = {"5m": 288, "15m": 96, "1h": 24, "4h": 6}[args.interval]
    cooldown_bars = max(1, round(lc_min / (1440 / bars_per_day)))
    total_bars = args.days * bars_per_day + 100

    cfg = get_config()
    perps = [m for m in get_universe() if m["type"] == "perp" and not m["coin"].startswith("@")]
    coins = sorted(perps, key=lambda m: m.get("dayNtlVlm", 0), reverse=True)[: args.coins]

    print("=== RE-ENTRY backtest ===")
    print(f"period {args.days}d  interval {args.interval}  top-{args.coins}  lev<= {args.leverage_ceiling}x")
    print(f"rule: reclaim >= +{reclaim_pct}% above stop & composite >= {min_comp}  | "
          f"cooldown {lc_min:.0f}min = {cooldown_bars} bars  | max_loss {args.max_loss}%\n")

    block_all, reentry_all = [], []
    for m in coins:
        coin, max_lev = m["coin"], int(m.get("maxLeverage", 5))
        try:
            candles = fetch_hl_candles(coin, args.interval, total_bars)
            if len(candles) < 110:
                continue
            kw = dict(cfg=cfg, equity=args.equity, equity_fraction=args.equity_fraction,
                      lev_ceiling=args.leverage_ceiling, max_loss_pct=args.max_loss,
                      protect_pct=args.protect, retrace_threshold=args.retrace,
                      cooldown_bars=cooldown_bars, reclaim_pct=reclaim_pct, min_composite=min_comp)
            block_all += simulate(coin, candles, max_lev, policy="BLOCK", **kw)
            reentry_all += simulate(coin, candles, max_lev, policy="REENTRY", **kw)
        except Exception as e:
            print(f"  {coin}: skip ({e})")

    bn, bw, bp = _stats(block_all)
    rn, rw, rp = _stats(reentry_all)
    re_only = [t for t in reentry_all if t.get("is_reentry")]
    en, ew, ep = _stats(re_only)
    print("=== RESULT ===")
    print(f"  BLOCK   (cooldown blocks re-entry): {bn:4d} trades  win {bw:4.1f}%  PnL ${bp:+.2f}")
    print(f"  REENTRY (momentum re-entry on):     {rn:4d} trades  win {rw:4.1f}%  PnL ${rp:+.2f}")
    print(f"  --> Δ from the re-entry rule: ${rp - bp:+.2f}  ({rn - bn:+d} trades)")
    print(f"  re-entry trades ALONE: {en} trades  win {ew:4.1f}%  PnL ${ep:+.2f}  "
          f"(avg ${ep/en:+.2f}/trade)" if en else "  re-entry trades ALONE: 0 (none fired in sample)")
    print("\nCaveats: heuristic entries (no LLM); 1 open pos/coin; no funding; "
          "fills at next-bar open; past != future.")


if __name__ == "__main__":
    main()
