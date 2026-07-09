"""W-H3 — 1h liquidation-cascade aftershock structure: idiosyncratic vs systemic.

Extends liquidation_cascade_fade (C2, REFUTED at 5m on fees) honestly: 1h
frequency, bigger flushes, and ONE new conditional split the C2 study never
tested — was the flush idiosyncratic (BTC did NOT flush simultaneously) or
systemic (BTC flushed too)?

PRE-REGISTERED SPEC:
- Event: coin 1h close-to-close ret[i] <= -THR, THR grid {6%, 8%} (8% primary,
  6% declared secondary for n), coin dayNtlVlm >= $10M (static universe snapshot,
  survivorship caveat), per-coin dedup 24h, cross-coin independence dedup NOT
  applied within the idio cell by construction check (reported).
- Split at bar i: IDIO = BTC ret[i] > -2 * sigma_168(BTC, strictly past);
  SYSTEMIC = BTC ret[i] <= -2 * sigma_168.
- Descriptive aftershock map per cell: mean cumulative return from open[i+1] at
  +1/+3/+6/+12/+24h; retest probability (any low in the 24 bars after entry
  below the flush bar's low); post-vol ratio (mean |1h ret| next 6h / prior 24h).
- ONE tradeable rule (declared before running): LONG at open[i+1], horizon 12
  bars (exit close of bar i+12), ONLY in the IDIO cell. Stop-width sweep
  {8,15,20,25,40}% via alpha_lib.sweep_stop (mean-reversion shape -> mandatory).
  Costs 12/25bps; hold 12h >= 8h -> funding adj (-cum_funding for a long) where
  funding.json covers the window (2026-03-29..06-27), mean adj reported.
- MC null (>=2000): stop-free 12h variant vs pool of random-bar 12h longs on the
  same liquid universe (side/horizon-matched random entry).
- OOS halves at 12bps on the stop-free variant. n >= 15 per cell or NOT-RIPE.
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
THRS = [0.06, 0.08]
MIN_VOL = 10_000_000
HOLD = 12
STOPS = [0.08, 0.15, 0.20, 0.25, 0.40]


def main() -> None:
    cs = wh0.load_ext()
    d = al.load_dataset()
    fund = fl.load_funding()
    vol = {c: d["universe"].get(c, {}).get("dayNtlVlm", 0.0) for c in cs}
    coins = [c for c in cs if c != "BTC" and vol[c] >= MIN_VOL and len(cs[c]) > 400]
    print(f"liquid universe (>= $10M dayNtlVlm): {len(coins)} coins")

    btc = cs["BTC"]
    btc_rets_l = wh0.hourly_rets(btc)
    btc_rets = dict(btc_rets_l)
    sig = wh0.rolling_sigma(btc_rets_l, SIGMA_W)

    for thr in THRS:
        events = []
        for c in coins:
            bars = cs[c]
            last_t = -1
            for i in range(1, len(bars) - HOLD - 2):
                p0, p1 = bars[i - 1][C], bars[i][C]
                if p0 <= 0:
                    continue
                r = p1 / p0 - 1.0
                t = int(bars[i][T])
                if r <= -thr and t - last_t >= 24 * HOUR:
                    br, s = btc_rets.get(t), sig.get(t)
                    if br is None or s is None:
                        continue
                    last_t = t
                    events.append({"t": t, "coin": c, "i": i, "flush_ret": r,
                                   "idio": br > -2 * s})
        idio = [e for e in events if e["idio"]]
        syst = [e for e in events if not e["idio"]]
        # same-hour clustering check (idio cell should be mostly 1 coin/hour)
        from collections import Counter
        hrs = Counter(e["t"] for e in idio)
        clus = sum(1 for v in hrs.values() if v > 1)
        print(f"\n==== THR {thr:.0%}: events={len(events)} idio={len(idio)} "
              f"systemic={len(syst)} (idio same-hour clusters: {clus}) ====")

        def aftershock(evs, name):
            if len(evs) < 15:
                print(f"  {name}: n={len(evs)} NOT-RIPE (descriptives skipped)")
                return
            cums = {h: [] for h in (1, 3, 6, 12, 24)}
            retest = 0; retest_n = 0; volr = []
            for e in evs:
                bars = cs[e["coin"]]; i = e["i"]
                if i + 25 >= len(bars):
                    continue
                entry = bars[i + 1][O]
                if entry <= 0:
                    continue
                for h in cums:
                    cums[h].append(bars[i + h][C] / entry - 1.0)
                retest_n += 1
                if min(b[L] for b in bars[i + 1:i + 25]) < bars[i][L]:
                    retest += 1
                prior = [abs(bars[j][C] / bars[j - 1][C] - 1) for j in range(i - 24, i)]
                post = [abs(bars[j][C] / bars[j - 1][C] - 1) for j in range(i + 1, i + 7)]
                if prior and post and statistics.mean(prior) > 0:
                    volr.append(statistics.mean(post) / statistics.mean(prior))
            path = " ".join(f"+{h}h:{100*statistics.mean(v):+.2f}%" for h, v in cums.items())
            print(f"  {name} aftershock (n={retest_n}): {path}")
            print(f"    retest-of-low(24h)={retest/max(1,retest_n):.0%} "
                  f"post/prior vol ratio={statistics.mean(volr):.2f}")

        aftershock(idio, "IDIO")
        aftershock(syst, "SYSTEMIC")

        # ── the ONE pre-registered rule: idio-only long, 12h, stop sweep ──
        # INTEGRITY CHECK (the btc_leadlag "7 macro candles" trap): systemic
        # flushes cluster in the same BTC hour -> also score an EPISODE-deduped
        # variant keeping only the DEEPEST flush within any 12h window.
        def dedup_cluster(evs):
            out = []
            for e in sorted(evs, key=lambda x: (x["t"], x["flush_ret"])):
                if out and e["t"] - out[-1]["t"] < 12 * HOUR:
                    if e["flush_ret"] < out[-1]["flush_ret"]:
                        out[-1] = e
                else:
                    out.append(e)
            return out

        for name, evs in (("IDIO", idio), ("SYSTEMIC(control)", syst),
                          ("IDIO-dedup", dedup_cluster(idio)),
                          ("SYSTEMIC-dedup", dedup_cluster(syst))):
            trades_free = []   # stop-free 12h, net of funding where covered
            stop_rets = {sp: [] for sp in STOPS}
            for e in evs:
                bars = cs[e["coin"]]; i = e["i"]
                if i + HOLD + 1 >= len(bars):
                    continue
                entry = bars[i + 1][O]
                if entry <= 0:
                    continue
                gross = bars[i + HOLD][C] / entry - 1.0
                cf = fl.cum_funding(fund, e["coin"], int(bars[i + 1][T]),
                                    int(bars[i + HOLD][T]))
                trades_free.append({"t": e["t"], "ret": gross - cf,
                                    "covered": bool(cf)})
                fwd = bars[i + 1:i + 1 + HOLD]
                sw = al.sweep_stop(entry, "long", fwd, STOPS, HOLD)
                for sp, r in sw.items():
                    stop_rets[sp].append(r - cf)
            if len(trades_free) < 15:
                print(f"  RULE {name}: n={len(trades_free)} NOT-RIPE")
                continue
            g = statistics.mean(x["ret"] for x in trades_free)
            h1, h2 = al.time_split(trades_free)
            e1 = statistics.mean(x["ret"] for x in h1) - 0.0012
            e2 = statistics.mean(x["ret"] for x in h2) - 0.0012
            # MC null: random-bar 12h longs, same universe
            import random
            rng = random.Random(3)
            pool = []
            for _ in range(6000):
                c = coins[rng.randrange(len(coins))]
                bars = cs[c]
                j = rng.randrange(SIGMA_W, len(bars) - HOLD - 2)
                ent = bars[j + 1][O]
                if ent > 0:
                    pool.append(bars[j + HOLD][C] / ent - 1.0)
            mc = mc_null.shuffle_label_p([x["ret"] for x in trades_free], pool,
                                         n_iter=3000, seed=5)
            cov = sum(1 for x in trades_free if x["covered"])
            print(f"  RULE {name} long 12h stop-free: n={len(trades_free)} "
                  f"gross(net-fund)={100*g:+.3f}% net12={100*(g-0.0012):+.3f}% "
                  f"net25={100*(g-0.0025):+.3f}% OOS12 {100*e1:+.3f}/{100*e2:+.3f} "
                  f"mc_p={mc['p_one_sided']} excess={100*(mc['excess'] or 0):+.3f}% "
                  f"fund-covered={cov}/{len(trades_free)}")
            row = " ".join(
                f"{int(100*sp)}%:{100*(statistics.mean(rs)-0.0012):+.3f}%"
                for sp, rs in stop_rets.items())
            print(f"    stop sweep net12: {row}")


def posthoc_short() -> None:
    """POST-HOC (labeled as such — NOT the pre-registered rule): the inverse of
    the refuted idio-fade. SHORT the idiosyncratic flusher (1h ret <= -6%, BTC
    NOT down-shocked) at open[i+1], 12h horizon, short-side stop sweep
    {8,15,20,25,40}%, funding credit for the short, MC null vs random same-
    universe 12h shorts, episode-deduped (deepest flush per 12h window).
    This is the hourly cousin of the LIVE shadow book crash_continue_div_short;
    scored here only to decide whether an hourly arm is worth a shadow wire."""
    cs = wh0.load_ext()
    d = al.load_dataset()
    fund = fl.load_funding()
    vol = {c: d["universe"].get(c, {}).get("dayNtlVlm", 0.0) for c in cs}
    coins = [c for c in cs if c != "BTC" and vol[c] >= MIN_VOL and len(cs[c]) > 400]
    btc_rets_l = wh0.hourly_rets(cs["BTC"])
    btc_rets = dict(btc_rets_l)
    sig = wh0.rolling_sigma(btc_rets_l, SIGMA_W)

    evs = []
    for c in coins:
        bars = cs[c]
        last_t = -1
        for i in range(1, len(bars) - HOLD - 2):
            p0, p1 = bars[i - 1][C], bars[i][C]
            if p0 <= 0:
                continue
            r = p1 / p0 - 1.0
            t = int(bars[i][T])
            if r <= -0.06 and t - last_t >= 24 * HOUR:
                br, s = btc_rets.get(t), sig.get(t)
                if br is None or s is None or br <= -2 * s:
                    continue
                last_t = t
                evs.append({"t": t, "coin": c, "i": i, "flush_ret": r})
    # episode dedup: deepest flush per 12h window
    out = []
    for e in sorted(evs, key=lambda x: x["t"]):
        if out and e["t"] - out[-1]["t"] < 12 * HOUR:
            if e["flush_ret"] < out[-1]["flush_ret"]:
                out[-1] = e
        else:
            out.append(e)
    evs = out

    trades, stop_rets = [], {sp: [] for sp in STOPS}
    for e in evs:
        bars = cs[e["coin"]]; i = e["i"]
        entry = bars[i + 1][O]
        if entry <= 0 or i + HOLD + 1 >= len(bars):
            continue
        gross = -(bars[i + HOLD][C] / entry - 1.0)
        cf = fl.cum_funding(fund, e["coin"], int(bars[i + 1][T]),
                            int(bars[i + HOLD][T]))  # short COLLECTS cf
        trades.append({"t": e["t"], "ret": gross + cf})
        fwd = bars[i + 1:i + 1 + HOLD]
        sw = al.sweep_stop(entry, "short", fwd, STOPS, HOLD)
        for sp, r in sw.items():
            stop_rets[sp].append(r + cf)
    if len(trades) < 15:
        print(f"POST-HOC short: n={len(trades)} NOT-RIPE")
        return
    import random
    rng = random.Random(9)
    pool = []
    for _ in range(6000):
        c = coins[rng.randrange(len(coins))]
        bars = cs[c]
        j = rng.randrange(SIGMA_W, len(bars) - HOLD - 2)
        ent = bars[j + 1][O]
        if ent > 0:
            pool.append(-(bars[j + HOLD][C] / ent - 1.0))
    g = statistics.mean(x["ret"] for x in trades)
    h1, h2 = al.time_split(trades)
    e1 = statistics.mean(x["ret"] for x in h1) - 0.0012
    e2 = statistics.mean(x["ret"] for x in h2) - 0.0012
    mc = mc_null.shuffle_label_p([x["ret"] for x in trades], pool,
                                 n_iter=3000, seed=17)
    print(f"\nPOST-HOC idio-flush SHORT 12h (deduped): n={len(trades)} "
          f"gross(+fund)={100*g:+.3f}% net12={100*(g-0.0012):+.3f}% "
          f"net25={100*(g-0.0025):+.3f}% OOS12 {100*e1:+.3f}/{100*e2:+.3f} "
          f"mc_p={mc['p_one_sided']} excess={100*(mc['excess'] or 0):+.3f}%")
    row = " ".join(f"{int(100*sp)}%:{100*(statistics.mean(rs)-0.0012):+.3f}%"
                   for sp, rs in stop_rets.items())
    print(f"  short stop sweep net12: {row}")


if __name__ == "__main__":
    main()
    posthoc_short()
