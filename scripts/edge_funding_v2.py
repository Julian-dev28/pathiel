#!/usr/bin/env python3
"""Perp positioning filter (COT replacement) — FUNDING RATE, tested BOTH fade and follow.
Funding Z-score vs each coin's own trailing distribution; does it predict forward return?
  FADE (COT-style): Z>+? (crowded long) -> bearish; Z<-? -> bullish.
  FOLLOW (trend-confirm): funding positive & rising -> bullish; negative & falling -> bearish.
Lookahead-safe (Z from funding history <= t; forward return strictly after t). OOS split, cost.
Reports vs baseline (unconditional forward return). OI-quadrant / long-short / liquidations:
flagged DATA-BLOCKED (no historical OI endpoint; aggregator feeds not wired).
"""
import statistics
import time
from hermes_trader.client.universe import get_universe
from hermes_trader.client.hl_client import fetch_hl_candles, fetch_funding_history
from hermes_trader.indicators.math import candle_val

VOL_FLOOR = 5e6
TOPN = 35
DAYS = 30
COST = 0.0012
H = 8                # forward horizon (hours)
ZWIN = 240           # ~10d trailing window for the funding Z-score


def main():
    uni = [m for m in get_universe(include_hip3=False) if float(m.get("dayNtlVlm") or 0) >= VOL_FLOOR]
    uni = sorted(uni, key=lambda m: float(m.get("dayNtlVlm") or 0), reverse=True)[:TOPN]
    start = int(time.time() * 1000) - DAYS * 86_400_000
    fade, follow, base = [], [], []   # (half_frac, signed_ret)
    n_coins = 0
    for m in uni:
        c = m.get("name") or m.get("coin")
        try:
            cd = fetch_hl_candles(c, "1h", DAYS * 24 + 12)
            fh = fetch_funding_history(c, start)
        except Exception:
            continue
        if len(cd) < 240 or not fh or len(fh) < ZWIN + 20:
            continue
        n_coins += 1
        px = {int(x.t) // 3_600_000: candle_val(x, "c") for x in cd}
        fr = sorted(((int(f["time"]) // 3_600_000, float(f.get("fundingRate", 0))) for f in fh))
        hrs = [h for h, _ in fr]
        vals = [v for _, v in fr]
        N = len(fr)
        for i in range(ZWIN, N - H):
            window = vals[i - ZWIN:i]
            mu = statistics.mean(window); sd = statistics.pstdev(window)
            if sd <= 0:
                continue
            z = (vals[i] - mu) / sd
            rising = vals[i] > vals[i - 1]
            h0 = hrs[i]
            if h0 not in px or (h0 + H) not in px or px[h0] <= 0:
                continue
            fwd = px[h0 + H] / px[h0] - 1
            frac = i / N
            base.append((frac, fwd))                       # unconditional (long-only baseline)
            # FADE: extreme funding -> trade opposite. signal = -sign(z) when |z|>1.5
            if z > 1.5:
                fade.append((frac, -fwd - COST))           # crowded long -> short
            elif z < -1.5:
                fade.append((frac, fwd - COST))            # crowded short -> long
            # FOLLOW: funding positive&rising -> long; negative&falling -> short
            if vals[i] > 0 and rising:
                follow.append((frac, fwd - COST))
            elif vals[i] < 0 and not rising:
                follow.append((frac, -fwd - COST))

    def rep(name, rows):
        if not rows:
            print(f"  {name:28s} | n=0"); return
        r = [x for _, x in rows]; w = [x for x in r if x > 0]
        h1 = [x for f, x in rows if f < 0.5]; h2 = [x for f, x in rows if f >= 0.5]
        a1 = statistics.mean(h1) * 100 if h1 else 0; a2 = statistics.mean(h2) * 100 if h2 else 0
        rob = "Y" if (a1 > 0 and a2 > 0) else "-"
        print(f"  {name:28s} | {len(r):5d} | {statistics.mean(r)*100:+.3f}% | {len(w)/len(r)*100:3.0f}% | OOS {a1:+.3f}/{a2:+.3f} {rob}")

    print(f"# FUNDING filter fade vs follow | {n_coins} coins | {DAYS}d | H={H}h fwd | Z-win {ZWIN}h | cost {COST*1e4:.0f}bps")
    print(f"# {'variant':28s} | {'n':>5s} | {'avg/t':>6s} | {'win':>3s} | OOS 1/2 rob")
    rep("BASELINE (uncond. long)", base)
    rep("FADE (extreme funding)", fade)
    rep("FOLLOW (funding+trend)", follow)
    print("# OI four-quadrant: DATA-BLOCKED (no historical OI endpoint — HL gives current OI only)")
    print("# Long/short ratio + Liquidation clusters: DATA-BLOCKED (need 3rd-party aggregator, not wired)")


if __name__ == "__main__":
    main()
