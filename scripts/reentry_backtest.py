#!/usr/bin/env python3
"""LEGACY HEURISTIC exit/regime comparison.

Despite the filename, main() currently compares fixed scalp, trend-ride, and
regime-aware exit modes using deterministic entries. The simulate() helper still
supports BLOCK/REENTRY policies for focused experiments, but the CLI output is
not a current live re-entry EV report.

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
             cooldown_bars, reclaim_pct, min_composite, warmup=100,
             exit_mode="fixed", ride_protect=3.0, ride_retrace=0.55):
    """Returns list of trade dicts with is_reentry flag.

    exit_mode: 'fixed' (use protect_pct/retrace_threshold), 'trendride' (always
    ride params), 'regime' (ride params when the coin's trend is bullish at entry,
    else scalp = protect_pct/retrace_threshold)."""
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
        # regime-aware exit selection: ride params when bullish (up-regime proxy)
        if exit_mode == "trendride":
            use_ride = True
        elif exit_mode == "regime":
            use_ride = bool(bullish)
        else:
            use_ride = False
        pp = ride_protect if use_ride else protect_pct
        rt = ride_retrace if use_ride else retrace_threshold
        open_t = {"coin": coin, "side": side, "entry_px": next_bar.o,
                  "notional": notional, "leverage": lev, "exit_px": None,
                  "pnl_usd": 0.0, "reason": "", "is_reentry": is_reentry}
        open_dsl = DSL(side=side, entry_px=next_bar.o, entry_bar=i + 1,
                       peak_px=next_bar.o, max_loss_pct=max_loss_pct,
                       protect_pct=pp, retrace_threshold=rt)
    return trades


def _stats(trades):
    n = len(trades)
    if not n:
        return (0, 0.0, 0.0)
    w = sum(1 for t in trades if t["pnl_usd"] > 0)
    return (n, w / n * 100, sum(t["pnl_usd"] for t in trades))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--coins", type=int, default=25)
    ap.add_argument("--interval", default="1h", choices=["5m", "15m", "1h", "4h"])
    ap.add_argument("--equity", type=float, default=180.0)
    ap.add_argument("--equity-fraction", type=float, default=0.0,
                    help="margin fraction per trade (default: .agent-config.json)")
    ap.add_argument("--leverage-ceiling", type=int, default=0,
                    help="max leverage to simulate (default: .agent-config.json)")
    ap.add_argument("--max-loss", type=float, default=None,
                    help="DSL max_loss_pct spot stop (default: .agent-config.json)")
    ap.add_argument("--protect", type=float, default=None,
                    help="DSL protect_pct spot profit threshold (default: .agent-config.json)")
    ap.add_argument("--retrace", type=float, default=None,
                    help="DSL phase-2 retrace threshold (default: .agent-config.json)")
    args = ap.parse_args()

    live = _live_cfg()
    live_dsl = live.get("dsl_exit", {}) or {}
    equity_fraction = float(args.equity_fraction or live.get("equity_fraction_per_trade", 0.12))
    leverage_ceiling = int(args.leverage_ceiling or live.get("leverage", 8))
    max_loss = float(args.max_loss if args.max_loss is not None else live_dsl.get("max_loss_pct", 0.75))
    protect = float(args.protect if args.protect is not None else live_dsl.get("protect_pct", 1.5))
    retrace = float(args.retrace if args.retrace is not None else live_dsl.get("retrace_threshold", 0.30))
    # Legacy fixed experiment params. The corresponding production config
    # blocks were removed; this script remains a historical comparison.
    reclaim_pct = 1.0
    min_comp = 30.0
    lc_min = float(live.get("loss_cooldown_min", 180) or 180)
    bars_per_day = {"5m": 288, "15m": 96, "1h": 24, "4h": 6}[args.interval]
    cooldown_bars = max(1, round(lc_min / (1440 / bars_per_day)))
    total_bars = args.days * bars_per_day + 100

    cfg = get_config()
    perps = [m for m in get_universe() if m["type"] == "perp" and not m["coin"].startswith("@")]
    coins = sorted(perps, key=lambda m: m.get("dayNtlVlm", 0), reverse=True)[: args.coins]

    print("=== LEGACY EXIT/REGIME backtest ===")
    print(f"period {args.days}d  interval {args.interval}  top-{args.coins}  lev<= {leverage_ceiling}x")
    print(f"reentry helper params: reclaim >= +{reclaim_pct}% above stop & "
          f"composite >= {min_comp}  | cooldown {lc_min:.0f}min = "
          f"{cooldown_bars} bars  | max_loss {max_loss}%\n")

    # Legacy regime-aware exit comparison: scalp vs trend-ride vs regime (ride
    # when the coin's trend is bullish at entry). This is not a live config path.
    ride_pp = 3.0
    ride_rt = 0.55
    scalp, ride, regime = [], [], []
    for m in coins:
        coin, max_lev = m["coin"], int(m.get("maxLeverage", 5))
        try:
            candles = fetch_hl_candles(coin, args.interval, total_bars)
            if len(candles) < 110:
                continue
            kw = dict(policy="BLOCK", cfg=cfg, equity=args.equity,
                      equity_fraction=equity_fraction, lev_ceiling=leverage_ceiling,
                      max_loss_pct=max_loss, protect_pct=protect,
                      retrace_threshold=retrace, cooldown_bars=cooldown_bars,
                      reclaim_pct=reclaim_pct, min_composite=min_comp,
                      ride_protect=ride_pp, ride_retrace=ride_rt)
            scalp += simulate(coin, candles, max_lev, exit_mode="fixed", **kw)
            ride += simulate(coin, candles, max_lev, exit_mode="trendride", **kw)
            regime += simulate(coin, candles, max_lev, exit_mode="regime", **kw)
        except Exception as e:
            print(f"  {coin}: skip ({e})")

    print("=== REGIME-AWARE EXIT RESULT ===")
    print(f"  (scalp base protect {protect}/retrace {retrace}; "
          f"ride protect {ride_pp}/retrace {ride_rt}; regime = ride when bullish@entry)\n")
    for label, tr_set in (("SCALP (always tight)", scalp),
                          ("TREND-RIDE (always loose)", ride),
                          ("REGIME-AWARE (auto)", regime)):
        n, w, p = _stats(tr_set)
        print(f"  {label:28} {n:4d} trades  win {w:4.1f}%  PnL ${p:+.2f}  "
              f"(avg ${p/n:+.3f}/trade)" if n else f"  {label}: 0 trades")
    print("\nCaveats: heuristic entries (no LLM); regime proxy = coin's own trend "
          "(live uses BTC for crypto); 1 open pos/coin; no funding; past != future.")


if __name__ == "__main__":
    main()
