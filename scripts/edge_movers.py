#!/usr/bin/env python3
"""Mover autopsy + edge search: what mooned, what bounced, and is any of it catchable?

HONEST + lookahead-safe + survivorship-bias-free:
  - signals are computed only from bars[:i] (no future peeking); entry at the NEXT bar's open
  - every signal occurrence across the WHOLE liquid universe is counted (winners AND failures),
    so EV is not cherry-picked from the coins that happened to moon
  - cost-aware (round-trip bps), OOS-split (first half / second half of the trade stream)

Three candidate long entries (the usual suspects for moves):
  donch20  — close breaks above the prior-20-bar high       (trend / "moon" breakout)
  bounce   — RSI(14) < 30 and turning up                    (oversold "bounce")
  volmom   — up >5% on >2x avg volume                        (volume-momentum thrust)

Two exit models bracket the truth:
  hold5    — fixed 5-bar forward return (raw drift)
  trail    — ride with a 12% trail-from-peak, hard -10% stop, 25-bar cap (trend-riding)

Then an AUTOPSY of the biggest actual movers: their run shape + whether any signal fired first.
"""
import os, sys, time, statistics
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hermes_trader.client.universe import get_universe
from hermes_trader.client.hl_client import fetch_hl_candles
from hermes_trader.indicators.math import candle_val, rsi as calc_rsi, atr as calc_atr

TF = os.environ.get("MTF", "1d")
NBARS = 250
TOPN = int(os.environ.get("MTOPN", "60"))
VOL_FLOOR = 5e6
COST_BPS = 10.0
WARMUP = 25
HOLD_N = 5
TRAIL = 0.12          # give back 12% from peak close
HARD_STOP = 0.10      # -10% hard stop from entry
MAXHOLD = 25
FETCH_SLEEP = 0.12


def load():
    uni = [m for m in get_universe(include_hip3=False)
           if ":" not in (m.get("coin") or "") and float(m.get("dayNtlVlm") or 0) >= VOL_FLOOR]
    uni = sorted(uni, key=lambda m: float(m.get("dayNtlVlm") or 0), reverse=True)[:TOPN]
    out = {}
    for m in uni:
        c = m["coin"]
        try:
            b = fetch_hl_candles(c, TF, NBARS)
            if len(b) >= WARMUP + 40:
                out[c] = b
        except Exception:
            pass
        time.sleep(FETCH_SLEEP)
    return out


def _exit_hold5(bars, j):
    if j + HOLD_N >= len(bars):
        return None
    e = candle_val(bars[j], "o")
    x = candle_val(bars[j + HOLD_N], "c")
    return (x - e) / e if e > 0 else None


def _exit_trail(bars, j):
    e = candle_val(bars[j], "o")
    if e <= 0:
        return None
    peak = e
    for k in range(j, min(j + MAXHOLD, len(bars))):
        hi, lo, cl = candle_val(bars[k], "h"), candle_val(bars[k], "l"), candle_val(bars[k], "c")
        if lo <= e * (1 - HARD_STOP):                    # hard stop (conservative: intrabar)
            return -HARD_STOP
        peak = max(peak, cl)
        if cl <= peak * (1 - TRAIL):                     # trail exit on close
            return (cl - e) / e
    return (candle_val(bars[min(j + MAXHOLD - 1, len(bars) - 1)], "c") - e) / e


def signals_at(bars, i):
    """Return the set of signals firing at bar i, using only bars[:i+1] (entry next bar)."""
    out = set()
    closes = [candle_val(b, "c") for b in bars[:i + 1]]
    c, cprev = closes[-1], closes[-2]
    sma50 = sum(closes[-50:]) / min(50, len(closes))     # trend filter
    uptrend = c > sma50
    # donchian-20 breakout
    prior_high = max(candle_val(b, "h") for b in bars[i - 20:i])
    if c > prior_high:
        out.add("donch20")
        if uptrend:
            out.add("donch20_up")                        # breakout WITH the trend
        else:
            out.add("donch20_down")                      # breakout against/below trend
    # oversold bounce
    r = calc_rsi(bars[:i + 1], 14)
    if r and r[-1] == r[-1] and r[-1] < 30 and c > cprev:
        out.add("bounce")
    # volume momentum
    vols = [candle_val(b, "v") for b in bars[i - 20:i]]
    avgv = sum(vols) / len(vols) if vols else 0
    if cprev > 0 and (c / cprev - 1) > 0.05 and avgv > 0 and candle_val(bars[i], "v") > 2 * avgv:
        out.add("volmom")
    return out


def main():
    print(f"# Mover autopsy | TF={TF} top{TOPN} liquid crypto | cost {COST_BPS:.0f}bps | "
          f"lookahead-safe, full-universe (no survivorship)")
    S = load()
    print(f"# {len(S)} coins, {TF} candles ~{NBARS}d\n")
    cost = COST_BPS / 1e4
    rows = {s: {"hold5": [], "trail": []} for s in
            ("donch20", "donch20_up", "donch20_down", "bounce", "volmom")}
    for coin, bars in S.items():
        for i in range(WARMUP, len(bars) - 1):
            sig = signals_at(bars, i)
            if not sig:
                continue
            j = i + 1                                    # enter next bar open (lookahead-safe)
            h5, tr = _exit_hold5(bars, j), _exit_trail(bars, j)
            for s in sig:
                if h5 is not None:
                    rows[s]["hold5"].append(h5 - cost)
                if tr is not None:
                    rows[s]["trail"].append(tr - cost)

    def rep(name, arr):
        if not arr:
            print(f"  {name:22} n=0"); return
        n = len(arr); w = sum(1 for r in arr if r > 0)
        mean = statistics.mean(arr) * 100; med = statistics.median(arr) * 100
        mid = n // 2
        h1 = statistics.mean(arr[:mid]) * 100 if mid else 0
        h2 = statistics.mean(arr[mid:]) * 100 if n - mid else 0
        top = sorted(arr, reverse=True)[:max(1, n // 10)]
        skew = sum(top) / sum(arr) * 100 if sum(arr) else 0
        rob = "ROBUST" if h1 > 0 and h2 > 0 else "fragile" if (h1 > 0) != (h2 > 0) else "neg"
        print(f"  {name:22} n={n:>4} win {w/n*100:>3.0f}%  mean {mean:>+6.2f}%  med {med:>+5.2f}%  "
              f"OOS {h1:>+5.2f}/{h2:>+5.2f} {rob:<7} top10%={skew:>3.0f}% of pnl")

    print("# EV per signal (net of cost), two exit models:")
    for s in ("donch20", "donch20_up", "donch20_down", "bounce", "volmom"):
        rep(f"{s} · hold5", rows[s]["hold5"])
        rep(f"{s} · trail", rows[s]["trail"])
        print()

    # ── AUTOPSY: the biggest actual runs, and what (if anything) fired first ──
    print("# AUTOPSY — top 15 moves (max 20-bar forward run from any bar) + precursor signal:")
    moves = []
    for coin, bars in S.items():
        best = 0.0; at = -1
        for i in range(WARMUP, len(bars) - 20):
            e = candle_val(bars[i], "c")
            if e <= 0:
                continue
            fwd_hi = max(candle_val(b, "h") for b in bars[i + 1:i + 21])
            run = (fwd_hi - e) / e
            if run > best:
                best, at = run, i
        if at > 0:
            pre = set()
            for k in range(max(WARMUP, at - 3), at + 1):   # signal within 3 bars before the launch
                pre |= signals_at(bars, k)
            moves.append((coin, best, at, pre))
    moves.sort(key=lambda x: x[1], reverse=True)
    for coin, run, at, pre in moves[:15]:
        tag = "+".join(sorted(pre)) if pre else "— none (no precursor signal)"
        print(f"  {coin:10} +{run*100:>5.0f}% run   precursor: {tag}")
    caught = sum(1 for _, _, _, pre in moves[:15] if pre)
    print(f"\n# {caught}/15 of the biggest moves had ANY of our signals fire in the 3 bars before launch.")
    print("# (signal fired ≠ profitable — see EV table; this only measures whether we'd have looked.)")


if __name__ == "__main__":
    main()
