#!/usr/bin/env python
"""
Lane 2 - MAGNITUDE REGIME study for the volume-influx runner play.

Question: the raw 1.5x volume rule is breakeven across ~15k events. Does EXTREME
influx magnitude (the operator's MEME example: 80k -> 13M -> 50M on consecutive
5m candles, 100x+) change the runner odds?

Event definition (lookahead-safe):
  - bar i is GREEN (close > open)
  - influx multiple m = vol[i] / trailing-mean(vol over [i-L, i-1])
  - m >= 1.5 qualifies as an influx event
  - DECISION on bar i, ENTRY at bar i+1 open
  - forward window = 96 bars (8h) MFE from entry price

Metrics per bucket:
  - n
  - runner-rate >=10% and >=20% (fwd-96 MFE on entry open)
  - tight-floor net EV @ 12bps round-trip cost
  - win rate
  - OOS both-halves EV (split dataset by time midpoint)
  - EXCESS over matched random-entry null (same coin, random green bar)

Tight-floor exit model: a "bank-quick" exit. Enter at i+1 open. Walk forward bar
by bar; trail a stop at TRAIL below the running peak (peak measured on highs).
Exit when low <= stop, else exit at end of window on close. This mirrors the live
tight profit-floor breakout exit (bank quickly, let the trail clip). Return pct.
"""
import json, sys, math, random
import numpy as np

SP = "/private/tmp/claude-501/-Users-julian-dev-Documents-code-hermes-trader/02a15a26-058b-42af-a0f8-8bc0ce9ab5f5/scratchpad/movers_5m.json"
L = 48          # trailing-mean lookback (4h)
FWD = 96        # forward window (8h)
COST = 0.0012   # 12 bps round trip
TRAIL = 0.05    # 5% trailing stop on peak (tight-floor proxy)
MIN_PRELUDE = L + 5
random.seed(7)
np.random.seed(7)

def load():
    d = json.load(open(SP))
    return d["candles"]

def simulate_exit(highs, lows, closes, entry):
    """Tight-floor trailing exit. Returns gross pct return (before cost)."""
    peak = entry
    for k in range(len(highs)):
        h = highs[k]; lo = lows[k]
        if h > peak:
            peak = h
        stop = peak * (1.0 - TRAIL)
        if lo <= stop:
            # exit at stop (conservative: assume filled at stop)
            return stop / entry - 1.0
    return closes[-1] / entry - 1.0

def build_events(candles):
    events = []  # dict per event
    for coin, tf in candles.items():
        bars = tf.get("5m", [])
        if len(bars) < MIN_PRELUDE + FWD + 2:
            continue
        arr = np.array(bars, dtype=float)
        t = arr[:,0]; o = arr[:,1]; h = arr[:,2]; lo = arr[:,3]; c = arr[:,4]; v = arr[:,5]
        n = len(bars)
        for i in range(MIN_PRELUDE, n - FWD - 1):
            if c[i] <= o[i]:
                continue  # green only
            tm = v[i-L:i].mean()
            if tm <= 0:
                continue
            m = v[i] / tm
            if m < 1.5:
                continue
            entry = o[i+1]
            if entry <= 0:
                continue
            fh = h[i+1:i+1+FWD]; fl = lo[i+1:i+1+FWD]; fc = c[i+1:i+1+FWD]
            mfe = fh.max() / entry - 1.0
            gross = simulate_exit(fh, fl, fc, entry)
            net = gross - COST
            # candle % move (the influx candle's own body/range)
            cand_move = c[i]/o[i] - 1.0
            dollar_vol = v[i] * c[i]  # approx notional of the influx candle
            # escalation: are the prior 2 bars also rising volume into i?
            esc = (v[i] > v[i-1] > v[i-2]) if i >= 2 else False
            events.append(dict(
                coin=coin, i=i, t=t[i], m=m, entry=entry,
                mfe=mfe, gross=gross, net=net,
                cand_move=cand_move, dollar_vol=dollar_vol, esc=esc,
            ))
    return events

def bucket_of(m):
    if m < 3: return "1.5-3x"
    if m < 10: return "3-10x"
    if m < 50: return "10-50x"
    return "50x+"

def summarize(evs):
    if not evs:
        return dict(n=0)
    mfe = np.array([e["mfe"] for e in evs])
    net = np.array([e["net"] for e in evs])
    return dict(
        n=len(evs),
        run10=float((mfe>=0.10).mean()),
        run20=float((mfe>=0.20).mean()),
        ev=float(net.mean()),
        win=float((net>0).mean()),
        med_mfe=float(np.median(mfe)),
    )

def oos_split(evs):
    if len(evs) < 10:
        return (None, None)
    ts = sorted(e["t"] for e in evs)
    mid = ts[len(ts)//2]
    a = [e for e in evs if e["t"] < mid]
    b = [e for e in evs if e["t"] >= mid]
    eva = np.array([e["net"] for e in a]).mean() if a else float("nan")
    evb = np.array([e["net"] for e in b]).mean() if b else float("nan")
    return (eva, evb)

def random_null(candles, n_samples, coins_weight):
    """Matched random null: pick random green bars (any volume) from the same coin
    distribution, run the same exit model. Returns net EV array."""
    nets = []
    coin_list = list(coins_weight.keys())
    weights = np.array([coins_weight[c] for c in coin_list], dtype=float)
    weights = weights / weights.sum()
    cache = {}
    tries = 0
    while len(nets) < n_samples and tries < n_samples*40:
        tries += 1
        coin = np.random.choice(coin_list, p=weights)
        bars = candles[coin]["5m"]
        if coin not in cache:
            cache[coin] = np.array(bars, dtype=float)
        arr = cache[coin]
        nb = len(arr)
        i = random.randint(MIN_PRELUDE, nb - FWD - 2)
        o=arr[i,1]; c=arr[i,4]
        if c <= o:
            continue
        entry = arr[i+1,1]
        if entry <= 0:
            continue
        fh = arr[i+1:i+1+FWD,2]; fl = arr[i+1:i+1+FWD,3]; fc = arr[i+1:i+1+FWD,4]
        gross = simulate_exit(fh, fl, fc, entry)
        nets.append(gross - COST)
    return np.array(nets)

def random_null_mfe(candles, n_samples, coins_weight):
    mfes = []
    coin_list = list(coins_weight.keys())
    weights = np.array([coins_weight[c] for c in coin_list], dtype=float)
    weights = weights / weights.sum()
    cache = {}
    tries = 0
    while len(mfes) < n_samples and tries < n_samples*40:
        tries += 1
        coin = np.random.choice(coin_list, p=weights)
        if coin not in cache:
            cache[coin] = np.array(candles[coin]["5m"], dtype=float)
        arr = cache[coin]; nb = len(arr)
        i = random.randint(MIN_PRELUDE, nb - FWD - 2)
        if arr[i,4] <= arr[i,1]:
            continue
        entry = arr[i+1,1]
        if entry <= 0:
            continue
        fh = arr[i+1:i+1+FWD,2]
        mfes.append(fh.max()/entry - 1.0)
    return np.array(mfes)

def reconstruct_paths(candles, evs):
    """Return list of (highs, lows, closes, entry) for each event."""
    cache = {}
    paths = []
    for e in evs:
        coin = e["coin"]; i = e["i"]
        if coin not in cache:
            cache[coin] = np.array(candles[coin]["5m"], dtype=float)
        arr = cache[coin]
        entry = arr[i+1,1]
        fh = arr[i+1:i+1+FWD,2]; fl = arr[i+1:i+1+FWD,3]; fc = arr[i+1:i+1+FWD,4]
        paths.append((fh, fl, fc, entry))
    return paths

def exit_hold(fh, fl, fc, entry):
    return fc[-1]/entry - 1.0

def exit_trail(trail):
    def f(fh, fl, fc, entry):
        peak = entry
        for k in range(len(fh)):
            if fh[k] > peak: peak = fh[k]
            stop = peak*(1.0-trail)
            if fl[k] <= stop:
                return stop/entry - 1.0
        return fc[-1]/entry - 1.0
    return f

def exit_target_stop(target, stop):
    def f(fh, fl, fc, entry):
        tp = entry*(1.0+target); sl = entry*(1.0-stop)
        for k in range(len(fh)):
            # stop checked first (conservative)
            if fl[k] <= sl:
                return -stop
            if fh[k] >= tp:
                return target
        return fc[-1]/entry - 1.0
    return f

def test_exits(candles, evs):
    paths = reconstruct_paths(candles, evs)
    models = [
        ("tight-trail5%", exit_trail(0.05)),
        ("trail10%", exit_trail(0.10)),
        ("trail15%", exit_trail(0.15)),
        ("hold-8h", exit_hold),
        ("tp20/sl10", exit_target_stop(0.20, 0.10)),
        ("tp30/sl15", exit_target_stop(0.30, 0.15)),
    ]
    for name, fn in models:
        rets = np.array([fn(*p) - COST for p in paths])
        print(f"  {name:14s} netEV={rets.mean()*100:+.2f}% win={(rets>0).mean()*100:.1f}% "
              f"median={np.median(rets)*100:+.2f}%")

def fmt_pct(x):
    return f"{x*100:+.2f}%" if x==x else "n/a"

def main():
    candles = load()
    evs = build_events(candles)
    print(f"total influx events (m>=1.5, green): {len(evs)}")

    # coin weighting for null = number of events per coin
    from collections import Counter
    cw = Counter(e["coin"] for e in evs)

    buckets = ["1.5-3x","3-10x","10-50x","50x+"]
    by = {b:[] for b in buckets}
    for e in evs:
        by[bucket_of(e["m"])].append(e)

    # null distribution (large sample) for EV-excess baseline
    null = random_null(candles, 20000, cw)
    null_ev = null.mean()
    null_run10 = float((np.array([0])).mean())  # placeholder; compute MFE null below

    # need MFE for null too -> rebuild null with mfe
    print(f"null net EV (random green entry): {fmt_pct(null_ev)}  (n={len(null)})")

    lines = []
    lines.append("| bucket | n | run>=10% | run>=20% | netEV/12bps | win | medMFE | OOS-A | OOS-B | EV excess vs null |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    results = {}
    for b in buckets:
        s = summarize(by[b])
        results[b] = s
        if s["n"]==0:
            lines.append(f"| {b} | 0 | - | - | - | - | - | - | - | - |")
            continue
        a,bv = oos_split(by[b])
        excess = s["ev"] - null_ev
        lines.append(f"| {b} | {s['n']} | {s['run10']*100:.1f}% | {s['run20']*100:.1f}% | "
                     f"{fmt_pct(s['ev'])} | {s['win']*100:.1f}% | {fmt_pct(s['med_mfe'])} | "
                     f"{fmt_pct(a)} | {fmt_pct(bv)} | {fmt_pct(excess)} |")
    print("\n".join(lines))

    # ---- Interaction (a): dollar-volume of influx candle, within high-magnitude (m>=10) ----
    hi = [e for e in evs if e["m"]>=10]
    print(f"\n[a] high-mag (m>=10) n={len(hi)}")
    dv = np.array([e["dollar_vol"] for e in hi])
    inter_a = []
    if len(hi) >= 20:
        q = np.quantile(dv, [0, .5, 1.0])
        for label, lo_, hi_ in [("low-$vol", q[0], q[1]), ("high-$vol", q[1], q[2]+1)]:
            sub = [e for e in hi if lo_ <= e["dollar_vol"] < hi_]
            s = summarize(sub)
            inter_a.append((label, s))
            if s["n"]:
                print(f"  {label}: n={s['n']} run10={s['run10']*100:.1f}% run20={s['run20']*100:.1f}% ev={fmt_pct(s['ev'])} win={s['win']*100:.1f}%")

    # ---- Interaction (b): big price move vs pure-volume spike (within m>=10) ----
    print(f"\n[b] price-move split within high-mag (m>=10)")
    inter_b = []
    if len(hi) >= 20:
        for label, cond in [("body>=3%", lambda e: e["cand_move"]>=0.03),
                            ("body<3%", lambda e: e["cand_move"]<0.03)]:
            sub = [e for e in hi if cond(e)]
            s = summarize(sub)
            inter_b.append((label, s))
            if s["n"]:
                print(f"  {label}: n={s['n']} run10={s['run10']*100:.1f}% run20={s['run20']*100:.1f}% ev={fmt_pct(s['ev'])} win={s['win']*100:.1f}%")

    # ---- Interaction (c): escalation shape vs single spike (within m>=10) ----
    print(f"\n[c] escalation (rising vol 3 bars) vs single spike, m>=10")
    inter_c = []
    if len(hi) >= 20:
        for label, cond in [("escalating", lambda e: e["esc"]),
                            ("single-spike", lambda e: not e["esc"])]:
            sub = [e for e in hi if cond(e)]
            s = summarize(sub)
            inter_c.append((label, s))
            if s["n"]:
                a,bv = oos_split(sub)
                print(f"  {label}: n={s['n']} run10={s['run10']*100:.1f}% run20={s['run20']*100:.1f}% ev={fmt_pct(s['ev'])} win={s['win']*100:.1f}% OOS=({fmt_pct(a)},{fmt_pct(bv)})")

    # combined best-shape: escalating + body>=3% + m>=10
    combo = [e for e in evs if e["m"]>=10 and e["esc"] and e["cand_move"]>=0.03]
    sc = summarize(combo)
    print(f"\n[combo] m>=10 & escalating & body>=3%: ", sc)
    if sc.get("n",0)>=8:
        a,bv = oos_split(combo)
        print(f"  OOS=({fmt_pct(a)},{fmt_pct(bv)}) excess={fmt_pct(sc['ev']-null_ev)}")

    # extreme 50x+ detail
    ext = by["50x+"]
    print(f"\n[extreme 50x+] n={len(ext)}")
    for e in sorted(ext, key=lambda x:-x["m"])[:15]:
        print(f"  {e['coin']:8s} m={e['m']:7.1f} body={e['cand_move']*100:+5.1f}% $vol={e['dollar_vol']:.2e} mfe={e['mfe']*100:+6.1f}% net={e['net']*100:+6.1f}% esc={e['esc']}")

    # ---- Null runner-rates (matched random green entries) for proper excess ----
    null_mfe = random_null_mfe(candles, 20000, cw)
    print(f"\n[null] runner-rate >=10%={float((null_mfe>=0.10).mean())*100:.2f}% "
          f">=20%={float((null_mfe>=0.20).mean())*100:.2f}% net EV={fmt_pct(null_ev)}")

    # ---- Exit-model test on the standout subset: extreme vol + body>=3% ----
    # The tight-floor whipsaws. Is the MFE jump capturable with other exits?
    standout = [e["ev_paths"] for e in []]  # placeholder
    sub = [e for e in evs if e["m"]>=10 and e["cand_move"]>=0.03]
    print(f"\n[exit test] extreme-vol(m>=10) + body>=3%  n={len(sub)}")
    test_exits(candles, sub)
    # baseline: same exits on the full 1.5x rule for comparison
    print(f"\n[exit test] ALL influx (m>=1.5) baseline  n={len(evs)} (subsample 4000)")
    test_exits(candles, random.sample(evs, min(4000,len(evs))))

    # dump json for the md writer
    out = dict(total=len(evs), null_ev=null_ev,
               buckets={b:results[b] for b in buckets},
               inter_a=inter_a, inter_b=inter_b, inter_c=inter_c, combo=sc)
    import os
    json.dump(out, open(os.path.dirname(SP)+"/influx_mag_results.json","w"), default=float, indent=2)
    print("\nwrote results json")

if __name__ == "__main__":
    main()
