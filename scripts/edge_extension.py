#!/usr/bin/env python3
"""Why did we miss TNSR(+70%)/RESOLV/ACE/MET/W/CHIP? The day's block tally said the
dominant gates were 'late trend-only chase; no fresh breakout/burst' (39x), the 1.5x
volume-confirm (16x), and the 30% extension cap (5x). This backtests the two structural
ones together by bucketing momentum entries on (a) how EXTENDED the coin already is
(24h move) and (b) whether it's a FRESH breakout vs a LATE CHASE (uptrend, no new high).

Each candidate is taken through the LIVE exit (2.5% stop + 0.10 trail + 1.25% protect)
and scored forward. Lookahead-safe (entry decided from bars<=i; ext from the trailing
24h; forward path strictly after). OOS = first/second half. cost 12bps.

Read: where does momentum-continuation EV go negative by extension? Are LATE-CHASE
entries (the 39x gate) +EV or -EV? -> should the 30% cap / late-chase gate move?
"""
import os
import sys
import time
import statistics

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hermes_trader.client.universe import get_universe
from hermes_trader.client.hl_client import fetch_hl_candles
from hermes_trader.indicators.math import candle_val

TF, BARS, LB24 = "5m", 5000, 288
STOP, PROTECT, RETRACE = 2.5, 1.25, 0.10     # live exit
FWD = 24                                     # ~2h forward cap
COST = 0.0012
# Volume band: default = liquid top names. Override on CLI to test the LOW-liquidity
# band we currently exclude:  python edge_extension.py <floor_usd> <ceil_usd>
VOL_FLOOR = float(sys.argv[1]) if len(sys.argv) > 1 else 5e6
VOL_CEIL = float(sys.argv[2]) if len(sys.argv) > 2 else 1e15
TOPN = 60
FETCH_SLEEP_S = 0.25
BREAK_LB = 48                                # 4h high defines a "fresh breakout"
VOL_MULT = 1.5                               # mirrors override_volume_confirm
EXT_BUCKETS = [(-1e9, 10), (10, 20), (20, 30), (30, 50), (50, 1e9)]


def _fetch_one(c):
    b = fetch_hl_candles(c, TF, BARS)
    if len(b) < LB24 + 500:
        return None
    return ([candle_val(x, "c") for x in b], [candle_val(x, "h") for x in b],
            [candle_val(x, "l") for x in b], [candle_val(x, "v") for x in b])


def load():
    uni = [m for m in get_universe(include_hip3=False)
           if VOL_FLOOR <= float(m.get("dayNtlVlm") or 0) < VOL_CEIL]
    uni = sorted(uni, key=lambda m: float(m.get("dayNtlVlm") or 0), reverse=True)[:TOPN]
    out, failed = {}, []
    for m in uni:
        c = m.get("name") or m.get("coin")
        try:
            r = _fetch_one(c)
            if r:
                out[c] = r
            else:
                failed.append(c)
        except Exception:
            failed.append(c)
        time.sleep(FETCH_SLEEP_S)
    for c in failed:                          # retry 429-knocked coins (integrity > speed)
        time.sleep(FETCH_SLEEP_S * 3)
        try:
            r = _fetch_one(c)
            if r:
                out[c] = r
        except Exception:
            pass
    return out


def fwd_exit(cl, hi, lo, i):
    """Long from cl[i] through the live exit. Returns GROSS return (cost applied later
    so we can sweep liquidity/slippage levels)."""
    e = cl[i]
    if e <= 0:
        return None
    peak, armed = e, False
    for j in range(i + 1, min(i + 1 + FWD, len(cl))):
        if lo[j] <= e * (1 - STOP / 100):
            return -STOP / 100
        peak = max(peak, hi[j])
        if (peak - e) / e * 100 >= PROTECT:
            armed = True
        if armed:
            floor = peak - (peak - e) * RETRACE
            if lo[j] <= floor:
                return floor / e - 1
    return cl[min(i + FWD, len(cl) - 1)] / e - 1


def main():
    print(f"# loading top {TOPN} movers {TF} ~{BARS}b...")
    S = load()
    N = min(len(v[0]) for v in S.values())
    # rows: (kind, ext_pct, ret, frac)  kind in {fresh, late}
    rows = []
    for c, (cl, hi, lo, vol) in S.items():
        last = -99
        for i in range(LB24 + 2, N - FWD):
            base = cl[i - LB24]
            if base <= 0:
                continue
            ext = (cl[i] / base - 1) * 100              # 24h extension at entry
            avgv = sum(vol[i - 20:i]) / 20 if i >= 20 else 0
            ma20 = sum(cl[i - 20:i]) / 20
            uptrend = cl[i] > ma20 and cl[i] > cl[i - 12]    # rising / trend-aligned
            if not uptrend or i - last < 6:
                continue
            hh = max(hi[i - BREAK_LB:i])
            fresh = cl[i] > hh and avgv > 0 and vol[i] >= VOL_MULT * avgv  # fresh breakout+vol
            late = (not fresh) and cl[i] <= hh                # trend-aligned, no new high = LATE CHASE
            if not (fresh or late):
                continue
            r = fwd_exit(cl, hi, lo, i)
            if r is None:
                continue
            rows.append(("fresh" if fresh else "late", ext, r, i / N))
            last = i

    def rep(name, rs, cost=COST):
        if not rs:
            print(f"  {name:26s} | n=0"); return
        r = [x[2] - cost for x in rs]
        w = sum(1 for x in r if x > 0)
        a1 = statistics.mean([x[2] - cost for x in rs if x[3] < 0.5] or [0]) * 100
        a2 = statistics.mean([x[2] - cost for x in rs if x[3] >= 0.5] or [0]) * 100
        print(f"  {name:26s} | {len(r):5d} | {statistics.mean(r)*100:+.2f}% | {w/len(r)*100:3.0f}% "
              f"| OOS {a1:+.2f}/{a2:+.2f} {'Y' if a1>0 and a2>0 else '-'}")

    band_lab = (f"vol band ${VOL_FLOOR/1e6:.2f}M-"
                + (f"${VOL_CEIL/1e6:.2f}M" if VOL_CEIL < 1e14 else "inf"))
    print(f"# {len(S)} coins | {band_lab} | ~{(N-LB24)*5/60/24:.0f}d | exit 2.5%stop/0.10trail\n")
    print("## BY EXTENSION x SLIPPAGE — does the continuation survive low-liq slippage at each level?")
    print(f"  {'extension bucket':16s} | {'n':>5s} | {'gross':>6s} | {'12bps':>6s} {'40bps':>6s} {'70bps':>6s} {'100bps':>6s}")
    for lo_b, hi_b in EXT_BUCKETS:
        rs = [x for x in rows if lo_b <= x[1] < hi_b]
        lab = f"{lo_b if lo_b>-1e8 else 0:.0f}-{hi_b if hi_b<1e8 else 999:.0f}% ext"
        if not rs:
            print(f"  {lab:16s} | n=0"); continue
        g = statistics.mean([x[2] for x in rs]) * 100
        cells = " ".join(f"{(g - bps*100):>+6.2f}" for bps in (0.0012, 0.0040, 0.0070, 0.0100))
        print(f"  {lab:16s} | {len(rs):5d} | {g:>+5.2f}% | {cells}")
    print("\n## BY EXTENSION (detail at live 12bps)")
    print(f"  {'extension bucket':26s} | {'n':>5s} | {'avg/t':>6s} | {'win':>3s} | OOS h1/h2 rob")
    for lo_b, hi_b in EXT_BUCKETS:
        lab = f"{lo_b if lo_b>-1e8 else 0:.0f}-{hi_b if hi_b<1e8 else 999:.0f}% ext"
        rep(lab, [x for x in rows if lo_b <= x[1] < hi_b])
    print("\n## FRESH BREAKOUT (+vol, gate ALLOWS) vs LATE CHASE (no new high, the 39x gate BLOCKS)")
    print(f"  {'entry type':26s} | {'n':>5s} | {'avg/t':>6s} | {'win':>3s} | OOS h1/h2 rob")
    rep("fresh breakout (allowed)", [x for x in rows if x[0] == "fresh"])
    rep("late chase (blocked 39x)", [x for x in rows if x[0] == "late"])
    print("\n## LATE CHASE split by extension (does blocking it cost us, and where?)")
    for lo_b, hi_b in EXT_BUCKETS:
        lab = f"late {lo_b if lo_b>-1e8 else 0:.0f}-{hi_b if hi_b<1e8 else 999:.0f}%"
        rep(lab, [x for x in rows if x[0] == "late" and lo_b <= x[1] < hi_b])
    print("\n## SLIPPAGE SENSITIVITY of the +EV 20-30% band (can we afford to catch low-liq movers early?)")
    band = [x for x in rows if 20 <= x[1] < 30]
    print(f"  {'cost assumption':26s} | {'n':>5s} | {'avg/t':>6s} | {'win':>3s} | OOS h1/h2 rob")
    for bps in (0.0012, 0.0025, 0.0040, 0.0060):
        rep(f"{bps*1e4:.0f}bps roundtrip", band, cost=bps)


if __name__ == "__main__":
    main()
