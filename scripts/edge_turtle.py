#!/usr/bin/env python3
"""The actual Turtle/Dennis trend system on candle history (not a toy breakout). Proper
rules, lookahead-free, ATR(2N) stop, Donchian exit, OOS split, costs:
  - Entry : close breaks the N_ENTRY-day high (long) / low (short)
  - Stop  : 2 x ATR(20) from entry ("2N")
  - Exit  : opposite N_EXIT-day extreme (Turtle exit) OR the 2N stop, whichever first
  - Return: per-trade % move (signed) net of cost; bars processed in time order
Tests System-1 (20/10) and System-2 (55/20), long-only and long+short.
"""
import statistics
import sys
from hermes_trader.client.universe import get_universe
from hermes_trader.client.hl_client import fetch_hl_candles
from hermes_trader.indicators.math import candle_val, atr

VOL_FLOOR = 5e6
TOPN = 40
BARS = 1500          # daily? no — use 4h for more trades over our history
TF = "4h"
COST = 12.0 / 1e4


def run_system(data, n_entry, n_exit, allow_short):
    trades = []   # (entry_idx_global, ret, half_frac)
    per = []
    for coin, cd in data.items():
        closes = [candle_val(c, "c") for c in cd]
        highs = [candle_val(c, "h") for c in cd]
        lows = [candle_val(c, "l") for c in cd]
        atrs = atr(cd, 20)
        if len(cd) < n_entry + 30:
            continue
        i = n_entry
        pos = None  # (side, entry_px, stop_px, entry_i)
        while i < len(cd) - 1:
            c = closes[i]
            if pos is None:
                hh = max(highs[i - n_entry:i]); ll = min(lows[i - n_entry:i])
                a = atrs[i] if i < len(atrs) and atrs[i] == atrs[i] else 0
                if a <= 0:
                    i += 1; continue
                if c > hh:
                    pos = (1, c, c - 2 * a, i)
                elif allow_short and c < ll:
                    pos = (-1, c, c + 2 * a, i)
            else:
                side, ep, sp, ei = pos
                exit_px = None
                # 2N stop (intrabar)
                if side == 1 and lows[i] <= sp:
                    exit_px = sp
                elif side == -1 and highs[i] >= sp:
                    exit_px = sp
                else:
                    # Donchian exit
                    if side == 1 and c < min(lows[i - n_exit:i]):
                        exit_px = c
                    elif side == -1 and c > max(highs[i - n_exit:i]):
                        exit_px = c
                if exit_px is not None:
                    ret = (exit_px / ep - 1) * side - COST
                    trades.append((ei / len(cd), ret))
                    per.append(ret)
                    pos = None
            i += 1
    return trades, per


def metrics(rets):
    if not rets:
        return None
    w = [x for x in rets if x > 0]
    eq = peak = mdd = 0.0
    for x in rets:
        eq += x; peak = max(peak, eq); mdd = min(mdd, eq - peak)
    sh = (statistics.mean(rets) / statistics.pstdev(rets)) if len(rets) > 1 and statistics.pstdev(rets) else 0.0
    return {"n": len(rets), "sum": sum(rets) * 100, "avg": statistics.mean(rets) * 100,
            "win": len(w) / len(rets) * 100, "mdd": mdd * 100, "sharpe": sh}


def main():
    uni = [m for m in get_universe(include_hip3=False) if float(m.get("dayNtlVlm") or 0) >= VOL_FLOOR]
    uni = sorted(uni, key=lambda m: float(m.get("dayNtlVlm") or 0), reverse=True)[:TOPN]
    data = {}
    for m in uni:
        c = m.get("name") or m.get("coin")
        try:
            cd = fetch_hl_candles(c, TF, BARS)
            if len(cd) >= 200:
                data[c] = cd
        except Exception:
            pass
    L = min((len(v) for v in data.values()), default=0)
    print(f"# Turtle/Dennis on {len(data)} coins, {TF} candles (~{L} bars) | cost {COST*1e4:.0f}bps | OOS by trade-time")
    print(f"# {'system':22s} | {'n':>4s} | {'sumRet':>7s} | {'avg/t':>6s} | {'win':>4s} | {'maxDD':>7s} | {'shrp':>6s} | OOS 1st/2nd avg")
    for name, ne, nx in (("S1 20/10", 20, 10), ("S2 55/20", 55, 20)):
        for allow_short in (False, True):
            trades, per = run_system(data, ne, nx, allow_short)
            m = metrics(per)
            if not m:
                print(f"  {name} {'L/S' if allow_short else 'long':6s} | n=0"); continue
            h1 = [r for fr, r in trades if fr < 0.5]; h2 = [r for fr, r in trades if fr >= 0.5]
            a1 = statistics.mean(h1) * 100 if h1 else 0; a2 = statistics.mean(h2) * 100 if h2 else 0
            rob = "Y" if (a1 > 0 and a2 > 0) else "-"
            tag = "long+short" if allow_short else "long-only"
            print(f"  {name+' '+tag:22s} | {m['n']:4d} | {m['sum']:+6.1f}% | {m['avg']:+5.2f}% | {m['win']:3.0f}% | "
                  f"{m['mdd']:6.1f}% | {m['sharpe']:+.3f} | {a1:+.2f}/{a2:+.2f} {rob}")


if __name__ == "__main__":
    main()
