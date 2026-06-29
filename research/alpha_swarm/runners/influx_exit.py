#!/usr/bin/env python3
"""Lane 3 - EXIT/ride study for the volume-influx runner play.

Entry = green 5m volume-influx candle (close>open, vol >= 1.5x trailing-6 mean
of the PRIOR 6 bars). Enter at the i+1 open (lookahead-safe). One open trade per
coin at a time. Hold horizon capped at 288 bars (24h).

On the SUBSET of entries that reach >= +5% MFE (a real move started), compare
exit policies by net-of-12bps EV, median capture (realized / MFE), give-back,
win rate, and OOS both halves.

All exits are simulated lookahead-safe: at each bar the trailing floor / stop is
computed from the running peak through the PREVIOUS bar; we check the current
bar's LOW against that floor BEFORE updating the peak with the current bar's
high. Fills are at the floor/level price (no optimistic intrabar ordering).

Survivor universe (180 perps that exist NOW) = UPPER BOUND on capture; coins
that influx-pumped then delisted are absent, so realized EV here is optimistic.
"""
import json, statistics as st

PATH = "/private/tmp/claude-501/-Users-julian-dev-Documents-code-hermes-trader/02a15a26-058b-42af-a0f8-8bc0ce9ab5f5/scratchpad/movers_5m.json"
COST = 0.0012          # round-trip fee+slip, 12 bps
COST_PARTIAL = 0.0006  # extra exit leg for scale-out
HORIZON = 288          # max hold bars (24h)
VOL_MULT = 1.5         # influx threshold vs trailing-6 mean
ATR_N = 14
ATR_MULT = 3.0
MFE_GATE = 0.05        # subset: reached >= +5% favorable

def atr(candles, i, n=ATR_N):
    if i < n: return None
    trs = []
    for k in range(i-n+1, i+1):
        h, l, pc = candles[k][2], candles[k][3], candles[k-1][4]
        trs.append(max(h-l, abs(h-pc), abs(l-pc)))
    return sum(trs)/n

COOLDOWN = 12  # bars (1h) blocked after an entry so one influx cluster = one trade

def find_entries(candles):
    """Return list of entry dicts (lookahead-safe). Cooldown dedups clusters so
    the entry set is common across all exit policies."""
    entries = []
    open_until = -1
    for i in range(6, len(candles)-1):
        if i <= open_until:
            continue
        o, h, l, c, v = candles[i][1:6]
        if c <= o:
            continue
        mean6 = sum(candles[k][5] for k in range(i-6, i))/6.0
        if mean6 <= 0 or v < VOL_MULT*mean6:
            continue
        entry_px = candles[i+1][1]  # i+1 open
        if entry_px <= 0:
            continue
        entries.append({"i": i+1, "entry": entry_px, "t": candles[i+1][0]})
        open_until = i + 1 + COOLDOWN
    return entries

def trade_window(candles, j):
    """Bars [j, end). end = min(j+HORIZON, len)."""
    return range(j, min(j+HORIZON, len(candles)))

def mfe_of(candles, j, entry):
    m = 0.0
    for k in trade_window(candles, j):
        m = max(m, (candles[k][2]-entry)/entry)
    return m

# ---- exit policies. Each returns (realized_gross_fraction, exit_bar) ----

def exit_giveback(candles, j, entry, gb, arm=0.01):
    peak_g = 0.0; armed = False
    last = candles[min(j+HORIZON, len(candles))-1][4]
    for k in trade_window(candles, j):
        # check breach against floor from peak through previous bar
        if armed:
            floor_g = peak_g*(1-gb)
            floor_px = entry*(1+floor_g)
            if candles[k][3] <= floor_px:
                return floor_g, k
        # update peak with this bar's high
        g_hi = (candles[k][2]-entry)/entry
        if g_hi > peak_g:
            peak_g = g_hi
            if peak_g >= arm: armed = True
    return (last-entry)/entry, min(j+HORIZON, len(candles))-1

def exit_atr(candles, j, entry):
    peak_px = entry
    last = candles[min(j+HORIZON, len(candles))-1][4]
    a = atr(candles, j)
    for k in trade_window(candles, j):
        if a:
            stop = peak_px - ATR_MULT*a
            if candles[k][3] <= stop:
                return (stop-entry)/entry, k
        peak_px = max(peak_px, candles[k][2])
        na = atr(candles, k)
        if na: a = na
    return (last-entry)/entry, min(j+HORIZON, len(candles))-1

def exit_scaleout(candles, j, entry, X, gb=0.10):
    """Bank 50% at +X (level fill), trail the other 50% on a 10% give-back floor."""
    tp_px = entry*(1+X)
    banked = None; bank_bar = None
    peak_g = 0.0; armed = False
    last = candles[min(j+HORIZON, len(candles))-1][4]
    for k in trade_window(candles, j):
        if banked is None and candles[k][2] >= tp_px:
            banked = X; bank_bar = k
            peak_g = max(peak_g, X); armed = True
        if armed:
            floor_g = peak_g*(1-gb)
            floor_px = entry*(1+floor_g)
            if candles[k][3] <= floor_px:
                runner = floor_g
                if banked is None:
                    return runner, k
                return 0.5*banked + 0.5*runner, k
        g_hi = (candles[k][2]-entry)/entry
        if g_hi > peak_g:
            peak_g = g_hi
            if peak_g >= 0.01: armed = True
    runner = (last-entry)/entry
    if banked is None:
        return runner, min(j+HORIZON, len(candles))-1
    return 0.5*banked + 0.5*runner, min(j+HORIZON, len(candles))-1

def exit_volrev(candles, j, entry, arm=0.02):
    armed = False; last_green_vol = None
    last = candles[min(j+HORIZON, len(candles))-1][4]
    for k in trade_window(candles, j):
        o, h, l, c, v = candles[k][1:6]
        g_close = (c-entry)/entry
        if g_close >= arm: armed = True
        red = c < o
        if armed and red and last_green_vol and v >= 0.8*last_green_vol:
            return (c-entry)/entry, k
        if c >= o:
            last_green_vol = v
    return (last-entry)/entry, min(j+HORIZON, len(candles))-1

def exit_fixed_tp(candles, j, entry, X):
    tp_px = entry*(1+X)
    last = candles[min(j+HORIZON, len(candles))-1][4]
    for k in trade_window(candles, j):
        if candles[k][2] >= tp_px:
            return X, k
    return (last-entry)/entry, min(j+HORIZON, len(candles))-1

POLICIES = {
    "floor_giveback_10%":  ("floor", lambda c,j,e: exit_giveback(c,j,e,0.10), COST),
    "floor_giveback_20%":  ("floor", lambda c,j,e: exit_giveback(c,j,e,0.20), COST),
    "floor_giveback_35%":  ("floor", lambda c,j,e: exit_giveback(c,j,e,0.35), COST),
    "atr_trail_3x":        ("atr",   lambda c,j,e: exit_atr(c,j,e),           COST),
    "scaleout_5%":         ("scale", lambda c,j,e: exit_scaleout(c,j,e,0.05), COST+COST_PARTIAL),
    "scaleout_10%":        ("scale", lambda c,j,e: exit_scaleout(c,j,e,0.10), COST+COST_PARTIAL),
    "scaleout_20%":        ("scale", lambda c,j,e: exit_scaleout(c,j,e,0.20), COST+COST_PARTIAL),
    "volrev_after_2%":     ("volrev",lambda c,j,e: exit_volrev(c,j,e),        COST),
    "fixed_tp_10%":        ("tp",    lambda c,j,e: exit_fixed_tp(c,j,e,0.10),  COST),
    "fixed_tp_20%":        ("tp",    lambda c,j,e: exit_fixed_tp(c,j,e,0.20),  COST),
    "fixed_tp_30%":        ("tp",    lambda c,j,e: exit_fixed_tp(c,j,e,0.30),  COST),
    "floor35_arm10%":      ("floor", lambda c,j,e: exit_giveback(c,j,e,0.35,arm=0.10), COST),
    "floor35_arm5%":       ("floor", lambda c,j,e: exit_giveback(c,j,e,0.35,arm=0.05), COST),
    "floor20_arm10%":      ("floor", lambda c,j,e: exit_giveback(c,j,e,0.20,arm=0.10), COST),
}

def pct(x): return f"{100*x:+.2f}%"

def main():
    d = json.load(open(PATH))
    coins = d["meta"]["coins"]
    # gather subset trades (reached >= +5% MFE)
    trades = []  # each: dict(coin, j, entry, t, mfe)
    for coin in coins:
        candles = d["candles"][coin]["5m"]
        for e in find_entries(candles):
            j, entry = e["i"], e["entry"]
            m = mfe_of(candles, j, entry)
            if m >= MFE_GATE:
                trades.append({"coin": coin, "j": j, "entry": entry, "t": e["t"], "mfe": m})
    n = len(trades)
    # OOS split by entry time, median split
    ts = sorted(t["t"] for t in trades)
    mid = ts[len(ts)//2]
    print(f"reached-+5% subset n = {n}")

    # MFE distribution (ceiling)
    mfes = sorted(t["mfe"] for t in trades)
    def frac_ge(x): return sum(1 for m in mfes if m >= x)/n
    print("\n=== MFE distribution on reached-+5% subset (ceiling) ===")
    for thr in (0.05,0.10,0.20,0.30,0.50,1.00,2.00):
        print(f"  >= +{int(thr*100):>4}%  : {frac_ge(thr)*100:5.1f}%  (n={sum(1 for m in mfes if m>=thr)})")
    print(f"  median MFE = {pct(st.median(mfes))}   mean MFE = {pct(sum(mfes)/n)}   max = {pct(max(mfes))}")

    # evaluate policies
    rows = []
    for name,(kind,fn,cost) in POLICIES.items():
        nets=[]; caps=[]; nets_h1=[]; nets_h2=[]
        for t in trades:
            candles = d["candles"][t["coin"]]["5m"]
            gross,_ = fn(candles, t["j"], t["entry"])
            net = gross - cost
            nets.append(net)
            caps.append(max(0.0, gross)/t["mfe"] if t["mfe"]>0 else 0.0)
            (nets_h1 if t["t"]<mid else nets_h2).append(net)
        ev = sum(nets)/len(nets)
        win = sum(1 for x in nets if x>0)/len(nets)
        cap = st.median(caps)
        ev1 = sum(nets_h1)/len(nets_h1) if nets_h1 else 0
        ev2 = sum(nets_h2)/len(nets_h2) if nets_h2 else 0
        rows.append((name, n, ev, cap, win, ev1, ev2))

    rows.sort(key=lambda r: -r[2])
    print("\n=== Exit policy table (sorted by net EV) ===")
    print(f"{'policy':22} {'n':>4} {'EV':>9} {'med_cap':>8} {'win':>6} {'EV_h1':>9} {'EV_h2':>9}")
    for name,nn,ev,cap,win,ev1,ev2 in rows:
        print(f"{name:22} {nn:>4} {pct(ev):>9} {cap*100:6.1f}% {win*100:5.1f}% {pct(ev1):>9} {pct(ev2):>9}")

    # === the crux: condition on the size of the run (MFE bucket) ===
    buckets = [("5-10%",0.05,0.10),("10-20%",0.10,0.20),("20-50%",0.20,0.50),(">=50%",0.50,9.9)]
    headline = ["fixed_tp_10%","fixed_tp_30%","floor_giveback_10%","floor_giveback_35%",
                "floor35_arm5%","floor35_arm10%","scaleout_20%","volrev_after_2%","atr_trail_3x"]
    print("\n=== EV by MFE bucket (which exit wins ON real runs) ===")
    hdr = f"{'policy':20}" + "".join(f"{b[0]:>12}" for b in buckets)
    print(hdr)
    for name in headline:
        kind,fn,cost = POLICIES[name]
        cells=[]
        for _,lo,hi in buckets:
            sub=[t for t in trades if lo<=t["mfe"]<hi]
            if not sub: cells.append("  -"); continue
            evs=[]
            for t in sub:
                candles=d["candles"][t["coin"]]["5m"]
                g,_=fn(candles,t["j"],t["entry"]); evs.append(g-cost)
            cells.append(f"{pct(sum(evs)/len(evs))}")
        print(f"{name:20}" + "".join(f"{c:>12}" for c in cells))
    print("\nbucket counts: " + ", ".join(f"{b[0]}={sum(1 for t in trades if b[1]<=t['mfe']<b[2])}" for b in buckets))

    # median capture by MFE bucket for headline policies
    print("\n=== median capture (realized/MFE) by MFE bucket ===")
    print(hdr)
    for name in headline:
        kind,fn,cost = POLICIES[name]
        cells=[]
        for _,lo,hi in buckets:
            sub=[t for t in trades if lo<=t["mfe"]<hi]
            if not sub: cells.append("  -"); continue
            cs=[]
            for t in sub:
                candles=d["candles"][t["coin"]]["5m"]
                g,_=fn(candles,t["j"],t["entry"]); cs.append(max(0,g)/t["mfe"])
            cells.append(f"{st.median(cs)*100:.1f}%")
        print(f"{name:20}" + "".join(f"{c:>12}" for c in cells))

    return rows, n, mfes, mid

if __name__ == "__main__":
    main()
