"""W-H4 — WILDCARD (declared): BTC down-shock x funding-crowding flush.

HYPOTHESIS (invented for this lane, declared before running): after a BTC 1h
DOWN-shock (> 2 sigma), alts with CROWDED LONG positioning (top-tercile trailing
24h funding) underperform uncrowded alts (bottom tercile) over the next 3-6h,
because forced long deleveraging concentrates where positioning is crowded.
Conditional/relative structure: a cross-sectional spread that should exist ONLY
in shock hours (the MC null at non-shock bars tests exactly that).

PRE-REGISTERED SPEC:
- Window: funding.json coverage (2026-03-29..2026-06-27) intersected with the
  extended 1h cache. Survivorship caveat applies.
- Event: BTC 1h close-to-close ret[i] < -2 * sigma_168 (strictly-past sigma),
  deduped to 6h episode spacing.
- crowd_j at bar i = trailing 24h mean hourly funding (funding_lib.trailing_funding,
  timestamps <= i -> lookahead-safe). Eligible alts: crowd + fill data, >= 12 names.
- Book: LONG bottom-tercile crowd (uncrowded), SHORT top-tercile crowd (crowded
  longs). Equal weight. Fill open[i+1], exit open[i+1+H], H in {3,6}.
- Unit = per-event spread = mean(long-leg fwd) - mean(short-leg fwd).
  Costs 2*tier (two alt legs), tiers {0,12,25}. Hold < 8h -> funding accrual not
  modeled (shorting positive-funding coins would COLLECT funding -> omission is
  conservative for the hypothesized direction).
- MC null (>=2000): identical spread built at every NON-shock bar = pool;
  shuffle_label_p of the event-cell mean vs pool. OOS halves at 2x12bps.
- SECONDARY (declared): the mirror at UP-shocks — after a BTC up-shock, do
  crowded-SHORT alts (bottom-tercile/most-negative funding) squeeze, i.e. the
  same long-uncrowded/short-crowded book should LOSE (or the reversed book win)?
  Reported for structure, not promoted without its own confirmation.
- n >= 15 events per cell or NOT-RIPE.
"""
from __future__ import annotations

import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "lib"))

import importlib
wh0 = importlib.import_module("W-H0_fetch")
import mc_null  # noqa: E402
import alpha_lib as al  # noqa: E402
import funding_lib as fl  # noqa: E402

T, O, H, L, C, V = 0, 1, 2, 3, 4, 5
HOUR = 3_600_000
SIGMA_W = 168
HOLDS = [3, 6]
DEDUP_MS = 6 * HOUR


def main() -> None:
    cs = wh0.load_ext()
    fund = fl.load_funding()
    coins = [c for c in cs if c != "BTC" and len(cs[c]) > 400 and fl.rows(fund, c)]
    fr_all = [r[fl.T] for c in coins for r in fl.rows(fund, c)]
    f_lo, f_hi = min(fr_all), max(fr_all)

    btc = cs["BTC"]
    bidx = wh0.bar_index(btc)
    btc_rets_l = wh0.hourly_rets(btc)
    sig = wh0.rolling_sigma(btc_rets_l, SIGMA_W)
    idx = {c: wh0.bar_index(cs[c]) for c in coins}

    # precompute trailing 24h funding per coin as sorted arrays for speed
    import bisect
    ftimes = {c: [r[fl.T] for r in fl.rows(fund, c)] for c in coins}
    frates = {c: [r[fl.RATE] for r in fl.rows(fund, c)] for c in coins}
    fcum = {}
    for c in coins:
        acc, cum = 0.0, [0.0]
        for r in frates[c]:
            acc += r; cum.append(acc)
        fcum[c] = cum

    def crowd(c: str, t: int) -> float | None:
        lo = bisect.bisect_right(ftimes[c], t - 24 * HOUR)
        hi = bisect.bisect_right(ftimes[c], t)
        if hi - lo < 12:
            return None
        return (fcum[c][hi] - fcum[c][lo]) / (hi - lo)

    def spread_at(t: int, hold: int):
        cand = []
        for c in coins:
            cw = crowd(c, t)
            if cw is not None:
                cand.append((cw, c))
        if len(cand) < 12:
            return None
        cand.sort()
        k = len(cand) // 3
        lows = [c for _, c in cand[:k]]        # uncrowded -> LONG
        highs = [c for _, c in cand[-k:]]      # crowded longs -> SHORT
        lf = [r for c in lows
              if (r := wh0.fwd_open_ret(cs[c], idx[c], t, hold)) is not None]
        sf = [r for c in highs
              if (r := wh0.fwd_open_ret(cs[c], idx[c], t, hold)) is not None]
        if len(lf) < max(2, k // 2) or len(sf) < max(2, k // 2):
            return None
        return statistics.mean(lf) - statistics.mean(sf)

    # classify bars inside the funding window
    dn_bars, up_bars, pool_bars = [], [], []
    for t, r in btc_rets_l:
        if not (f_lo + 25 * HOUR <= t <= f_hi - 7 * HOUR):
            continue
        s = sig.get(t)
        if s is None or s <= 0:
            continue
        if r < -2 * s:
            dn_bars.append({"t": t})
        elif r > 2 * s:
            up_bars.append({"t": t})
        else:
            pool_bars.append({"t": t})
    dn_ev = wh0.dedup_episodes(dn_bars, DEDUP_MS)
    up_ev = wh0.dedup_episodes(up_bars, DEDUP_MS)
    print(f"window {f_lo}->{f_hi}: down-shock events={len(dn_ev)} "
          f"up-shock events={len(up_ev)} pool bars={len(pool_bars)}")

    for hold in HOLDS:
        pool = [s for p in pool_bars[::2]
                if (s := spread_at(p["t"], hold)) is not None]

        def report(name, evs):
            trs = [{"t": e["t"], "ret": s} for e in evs
                   if (s := spread_at(e["t"], hold)) is not None]
            if len(trs) < 15:
                print(f"  H={hold} {name:<10} n={len(trs)} NOT-RIPE")
                return
            g = statistics.mean(x["ret"] for x in trs)
            h1, h2 = al.time_split(trs)
            e1 = statistics.mean(x["ret"] for x in h1) - 2 * 0.0012
            e2 = statistics.mean(x["ret"] for x in h2) - 2 * 0.0012
            mc = mc_null.shuffle_label_p([x["ret"] for x in trs], pool,
                                         n_iter=3000, seed=13)
            print(f"  H={hold} {name:<10} n={len(trs):<3} gross={100*g:+.3f}% "
                  f"net12={100*(g-0.0024):+.3f}% net25={100*(g-0.005):+.3f}% "
                  f"OOS12 {100*e1:+.3f}/{100*e2:+.3f} mc_p={mc['p_one_sided']} "
                  f"excess={100*(mc['excess'] or 0):+.3f}% "
                  f"(pool mean {100*statistics.mean(pool):+.3f}%, n={len(pool)})")

        print(f"\n== HOLD {hold}h (long uncrowded / short crowded, cost=2x tier) ==")
        report("DOWN-shock", dn_ev)
        report("UP-shock", up_ev)


if __name__ == "__main__":
    main()
