#!/usr/bin/env python3
"""OI/price four-quadrant positioning backtest. Runs on the self-collected OI time-series
(.oi-timeseries.jsonl from oi_logger). Tests whether OI-delta + price-delta quadrants
predict forward return — both FOLLOW (trade with new-position quadrants) and FADE.
  OI↑ Px↑ = new longs | OI↑ Px↓ = new shorts | OI↓ Px↑ = short-cover | OI↓ Px↓ = long-liq
Lookahead-safe (forward return strictly after the quadrant obs), OOS split, cost-aware.
Prints INSUFFICIENT-DATA until enough history accrues (this is expected early — the logger
needs ~1-2 weeks running before this validates anything).
"""
import json
import os
import statistics

FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".oi-timeseries.jsonl")
H = 4               # forward horizon in snapshots (~40min at 10min cadence; resamples up as data grows)
COST = 0.0012
MIN_SNAPSHOTS = 200   # need a meaningful series before drawing conclusions


def main():
    if not os.path.exists(FILE):
        print(f"# OI-quadrant: no data yet ({FILE} not created — logger starts on next loop restart).")
        return
    rows = [json.loads(l) for l in open(FILE) if l.strip()]
    print(f"# OI-timeseries snapshots collected: {len(rows)}")
    if len(rows) < MIN_SNAPSHOTS:
        need_days = (MIN_SNAPSHOTS - len(rows)) * 600 / 86400
        print(f"# INSUFFICIENT DATA — have {len(rows)}, need >={MIN_SNAPSHOTS} "
              f"(~{need_days:.1f} more days of collection at 10min cadence). Re-run later.")
        return
    # build per-coin (ts, oi, px) series
    series = {}
    for r in rows:
        ts = r["ts"]
        for coin, (oi, px) in r.get("oi", {}).items():
            series.setdefault(coin, []).append((ts, oi, px))
    follow, fade, base = [], [], []
    for coin, s in series.items():
        s.sort()
        for i in range(1, len(s) - H):
            (_, oi0, px0), (_, oi1, px1) = s[i - 1], s[i]
            if px0 <= 0 or oi0 <= 0:
                continue
            d_oi = oi1 - oi0
            d_px = px1 - px0
            fwd = s[i + H][2] / px1 - 1 if px1 > 0 else None
            if fwd is None:
                continue
            frac = i / len(s)
            base.append((frac, fwd))
            # FOLLOW: OI↑Px↑ -> long ; OI↑Px↓ -> short (trade the new-position quadrant)
            if d_oi > 0 and d_px > 0:
                follow.append((frac, fwd - COST))
            elif d_oi > 0 and d_px < 0:
                follow.append((frac, -fwd - COST))
            # FADE: opposite
            if d_oi > 0 and d_px > 0:
                fade.append((frac, -fwd - COST))
            elif d_oi > 0 and d_px < 0:
                fade.append((frac, fwd - COST))

    def rep(name, rk):
        if not rk:
            print(f"  {name:24s} | n=0"); return
        r = [x for _, x in rk]; w = [x for x in r if x > 0]
        h = len(rk) // 2
        a1 = statistics.mean([x for f, x in rk if f < 0.5] or [0]) * 100
        a2 = statistics.mean([x for f, x in rk if f >= 0.5] or [0]) * 100
        print(f"  {name:24s} | {len(r):5d} | {statistics.mean(r)*100:+.3f}% | {len(w)/len(r)*100:3.0f}% | "
              f"OOS {a1:+.3f}/{a2:+.3f} {'Y' if a1>0 and a2>0 else '-'}")
    print(f"# {'variant':24s} | {'n':>5s} | {'avg/t':>6s} | {'win':>3s} | OOS 1/2 rob")
    rep("BASELINE (uncond.)", base)
    rep("FOLLOW (new-pos quad)", follow)
    rep("FADE (new-pos quad)", fade)


if __name__ == "__main__":
    main()
