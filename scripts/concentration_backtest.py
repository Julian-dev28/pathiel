#!/usr/bin/env python3
"""LEGACY HEURISTIC concentration sandbox.

This answers one narrow question with deterministic candle heuristics: if a fixed
gross budget is split across K slots, does concentration improve capture? It does
not replay logged AI verdicts, current runner gates, primary-stop sizing, or live
portfolio margin logic. Use backtest_portfolio.py for current capital-contention
evidence.

Usage: python3 scripts/concentration_backtest.py --days 21 --coins 40 --interval 1h
"""
import argparse, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest import (Candle, DSL, _evaluate, _trend_and_atr_pct,
                      _heuristic_verdict, _ta_confirmed, ROUND_TRIP_FEE_BPS)
from hermes_trader.agents.config import get_config
from hermes_trader.client.hl_client import fetch_hl_candles
from hermes_trader.client.universe import get_universe

GROSS_MULT = 10.0
MAX_LOSS = 0.40
PROTECT, RETRACE = 1.25, 0.20


def run_k(K, coins_data, cfg, equity0, warmup=100):
    equity = equity0
    per_slot_frac = GROSS_MULT / K          # notional multiple of equity per slot
    fee = ROUND_TRIP_FEE_BPS / 10000.0
    L = min(len(c) for c, _ in coins_data.values())
    open_pos = {}                            # coin -> dict(dsl, entry, notional, side)
    closes = []
    peak = equity; maxdd = 0.0
    for i in range(warmup, L - 1):
        # ---- exits ----
        for coin, st in list(open_pos.items()):
            candles = coins_data[coin][0]
            done, exit_px, reason = st["dsl"].check_bar(i, candles[i])
            if done:
                gp = ((exit_px - st["entry"]) / st["entry"]) if st["side"] == "long" \
                    else ((st["entry"] - exit_px) / st["entry"])
                pnl = st["notional"] * (gp - fee)
                equity += pnl
                closes.append({"coin": coin, "pnl": pnl, "gp": gp})
                del open_pos[coin]
        # ---- collect + rank candidates ----
        if len(open_pos) < K:
            cands = []
            for coin, (candles, max_lev) in coins_data.items():
                if coin in open_pos:
                    continue
                window = candles[: i + 1]
                score, hits = _evaluate(window, cfg)
                bullish, atr_pct, adx14 = _trend_and_atr_pct(window)
                verdict = _heuristic_verdict(score, hits, bullish, atr_pct)
                if verdict != "LONG":          # long-only, like live
                    continue
                burst = any(h["name"] == "momentumBurst" and h["fired"] for h in hits)
                if not _ta_confirmed(bullish, atr_pct, adx14, score) and not burst:
                    continue
                cands.append((score, coin))
            cands.sort(reverse=True)            # best composite first
            for score, coin in cands:
                if len(open_pos) >= K:
                    break
                candles = coins_data[coin][0]
                entry = candles[i + 1].o
                notional = equity * per_slot_frac
                open_pos[coin] = {
                    "dsl": DSL(side="long", entry_px=entry, entry_bar=i + 1,
                               peak_px=entry, max_loss_pct=MAX_LOSS,
                               protect_pct=PROTECT, retrace_threshold=RETRACE),
                    "entry": entry, "notional": notional, "side": "long"}
        peak = max(peak, equity); maxdd = max(maxdd, peak - equity)
    p = [c["pnl"] for c in closes]
    w = [x for x in p if x > 0]; l = [x for x in p if x < 0]
    gps = [c["gp"] for c in closes]
    rippers = sum(1 for g in gps if g > 0.05)   # >5% spot moves captured
    return dict(K=K, n=len(p), win=(len(w)/len(p)*100 if p else 0), net=sum(p),
                aw=(sum(w)/len(w) if w else 0), al=(sum(l)/len(l) if l else 0),
                pf=(sum(w)/abs(sum(l)) if l else 0), maxdd=maxdd,
                endeq=equity, rippers=rippers)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=21)
    ap.add_argument("--coins", type=int, default=40)
    ap.add_argument("--interval", default="1h", choices=["15m", "1h", "4h"])
    ap.add_argument("--equity", type=float, default=180.0)
    args = ap.parse_args()
    cfg = get_config()
    bars = {"15m": 96, "1h": 24, "4h": 6}[args.interval] * args.days + 110
    perps = [m for m in get_universe() if m["type"] == "perp" and not m["coin"].startswith("@")]
    coins = sorted(perps, key=lambda m: m.get("dayNtlVlm", 0), reverse=True)[:args.coins]
    data = {}
    for m in coins:
        try:
            c = fetch_hl_candles(m["coin"], args.interval, bars)
            if len(c) >= 110:
                data[m["coin"]] = (c, int(m.get("maxLeverage", 5)))
        except Exception:
            pass
    print(f"=== LEGACY CONCENTRATION backtest | {args.days}d {args.interval} {len(data)} coins | "
          f"total gross {GROSS_MULT:.0f}x split into K slots | scalp exits ===")
    print(f"{'K(slots)':>9} {'legsize':>8} {'n':>5} {'win%':>5} {'net$':>9} {'avgW':>7} "
          f"{'avgL':>7} {'PF':>5} {'maxDD$':>8} {'rippers':>7} {'endEq':>8}")
    for K in (2, 3, 5, 8, 12):
        r = run_k(K, data, cfg, args.equity)
        print(f"{K:>9} {GROSS_MULT/K:>7.1f}x {r['n']:>5} {r['win']:>5.0f} {r['net']:>+9.1f} "
              f"{r['aw']:>+7.2f} {r['al']:>+7.2f} {r['pf']:>5.2f} {r['maxdd']:>+8.1f} "
              f"{r['rippers']:>7} {r['endeq']:>8.0f}")
    print("\nRead: if LOW K lifts net/avgW/rippers here, confirm in "
          "backtest_portfolio.py before changing live concurrency or gross caps.")


if __name__ == "__main__":
    main()
