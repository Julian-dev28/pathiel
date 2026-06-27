"""extreme_surface: full extreme-move response surface.

Trigger: coin k-day return crosses +/- threshold (fresh cross only).
4 quadrants: pump-fade(short), pump-continue(long), crash-fade(long), crash-continue(short).
Exit: alpha_lib.sweep_stop, stop {8,15,20,25,40}%, horizon {3,5,10} daily bars.
Regime: BTC 20d SMA (up/down) at decision bar. Liquidity floor: snapshot dayNtlVlm.
Lookahead-safe: decide on close of bar i, FILL at i+1 open; fwd plays out from entry bar.
"""
import alpha_lib as al
import statistics

d = al.load_dataset()
coins = [c for c in d["coins"] if c != "BTC"]  # BTC is the regime instrument; still tradeable but keep
coins = d["coins"]
btc = al.candles(d, "BTC", "1d")
btc_t = [b[al.T] for b in btc]
btc_c = [b[al.C] for b in btc]

def btc_regime_at(ts):
    """up/down from BTC close vs 20d SMA at the bar whose t <= ts (the decision bar)."""
    # find latest btc bar index with t <= ts
    idx = None
    for i, t in enumerate(btc_t):
        if t <= ts:
            idx = i
        else:
            break
    if idx is None or idx < 20:
        return None
    sma = statistics.mean(btc_c[idx-19:idx+1])
    return "up" if btc_c[idx] > sma else "down"

STOPS = [0.08, 0.15, 0.20, 0.25, 0.40]
THRS = [0.08, 0.10, 0.12, 0.15, 0.20, 0.25]
KS = [1, 2, 3]
HORIZONS = [3, 5, 10]
LIQ_FLOORS = {"all": 0, ">20M": 20e6, ">50M": 50e6}

QUADRANTS = {
    "pump_fade":     ("pump",  "short"),
    "pump_continue": ("pump",  "long"),
    "crash_fade":    ("crash", "long"),
    "crash_continue":("crash", "short"),
}

def gen_triggers():
    """yield dict per trigger: coin, t(entry ms), kret, dir, k, side->, entry_px, fwd, regime, vol"""
    out = []
    for coin in coins:
        cc = al.candles(d, coin, "1d")
        if len(cc) < 30:
            continue
        vol = d["universe"].get(coin, {}).get("dayNtlVlm", 0)
        closes = [b[al.C] for b in cc]
        for k in KS:
            for i in range(k, len(cc) - 1):  # need i-k and i+1
                kret = al.pct(closes[i-k], closes[i])
                prev = al.pct(closes[i-k-1], closes[i-1]) if i-k-1 >= 0 else 0.0
                ei = i + 1  # entry bar (fill at its open)
                entry_px = cc[ei][al.O]
                fwd = cc[ei:]  # entry bar plays out + forward
                if not fwd:
                    continue
                reg = btc_regime_at(cc[i][al.T])
                for thr in THRS:
                    if kret >= thr and prev < thr:      # fresh pump cross
                        out.append(dict(coin=coin, t=cc[ei][al.T], kret=kret, dir="pump",
                                        k=k, thr=thr, entry_px=entry_px, fwd=fwd, reg=reg, vol=vol))
                    elif kret <= -thr and prev > -thr:  # fresh crash cross
                        out.append(dict(coin=coin, t=cc[ei][al.T], kret=kret, dir="crash",
                                        k=k, thr=thr, entry_px=entry_px, fwd=fwd, reg=reg, vol=vol))
    return out

TRIGS = gen_triggers()

def cell_trades(k, thr, quad, horizon, stop, reg_filter, liq_floor):
    direction, side = QUADRANTS[quad]
    trades = []
    for tr in TRIGS:
        if tr["k"] != k or tr["thr"] != thr or tr["dir"] != direction:
            continue
        if reg_filter != "all" and tr["reg"] != reg_filter:
            continue
        if tr["vol"] < liq_floor:
            continue
        res = al.sweep_stop(tr["entry_px"], side, tr["fwd"], [stop], horizon)
        trades.append({"t": tr["t"], "ret": res[stop]})
    return trades

def best_cell(k, thr, quad, reg_filter, liq_floor):
    """Pick stop+horizon maximizing robustness: require both halves >0, then max min(h1,h2)."""
    best = None
    for horizon in HORIZONS:
        for stop in STOPS:
            trades = cell_trades(k, thr, quad, horizon, stop, reg_filter, liq_floor)
            if len(trades) < 12:
                continue
            s = al.summarize(trades)
            oos = s["oos_12bps"]
            h1, h2 = oos["first_half_mean_pct"], oos["second_half_mean_pct"]
            ev12 = s["slip12"]["mean_ret_pct"]
            ev25 = s["slip25"]["mean_ret_pct"]
            robust = (h1 is not None and h2 is not None and h1 > 0 and h2 > 0)
            score = min(h1, h2) if robust else -999 + (ev12 or -999)
            rec = dict(k=k, thr=thr, quad=quad, reg=reg_filter, liq=liq_floor_name(liq_floor),
                       stop=stop, horizon=horizon, n=s["n"], ev12=ev12, ev25=ev25,
                       h1=h1, h2=h2, wr=s["slip12"]["win_rate"], robust=robust, score=score)
            if best is None or score > best["score"]:
                best = rec
    return best

def liq_floor_name(v):
    for k, vv in LIQ_FLOORS.items():
        if vv == v:
            return k
    return str(v)

# ---- build the surface ----
rows = []
for k in KS:
    for thr in THRS:
        for quad in QUADRANTS:
            for reg in ["all", "up", "down"]:
                for lname, lfloor in LIQ_FLOORS.items():
                    rec = best_cell(k, thr, quad, reg, lfloor)
                    if rec:
                        rows.append(rec)

robust_rows = [r for r in rows if r["robust"]]
# also require survives 25bps for "strong"
robust_rows.sort(key=lambda r: r["score"], reverse=True)

print("=== ROBUST +EV both-halves cells (sorted by min-half EV) ===")
print(f"{'k':>2} {'thr':>5} {'quad':<15} {'reg':<5} {'liq':<6} {'stop':>5} {'hz':>3} {'n':>4} {'ev12':>7} {'ev25':>7} {'h1':>7} {'h2':>7} {'wr':>5}")
for r in robust_rows[:40]:
    print(f"{r['k']:>2} {r['thr']:>5.2f} {r['quad']:<15} {r['reg']:<5} {r['liq']:<6} {r['stop']:>5.2f} {r['horizon']:>3} {r['n']:>4} {r['ev12']:>7.3f} {r['ev25']:>7.3f} {r['h1']:>7.3f} {r['h2']:>7.3f} {r['wr']:>5.2f}")

print(f"\nrobust cells: {len(robust_rows)} / total cells {len(rows)}")
print(f"robust AND ev25>0: {sum(1 for r in robust_rows if r['ev25'] and r['ev25']>0)}")

# ---- where do the two LIVE edges land? ----
print("\n=== LIVE EDGE LOCATIONS ===")
def show(label, k, thr, quad, reg, liq):
    rec = best_cell(k, thr, quad, reg, liq)
    print(f"\n{label}")
    if not rec:
        print("  no cell (thin sample)")
        return
    print(f"  best stop={rec['stop']} hz={rec['horizon']} n={rec['n']} ev12={rec['ev12']} ev25={rec['ev25']} "
          f"h1={rec['h1']} h2={rec['h2']} wr={rec['wr']} ROBUST={rec['robust']}")

# extreme_fade: long after ~-12% crash (live: long-only). Try regimes/liq.
show("extreme_fade  crash_fade(long) k=1 thr=0.12 reg=all liq=all", 1, 0.12, "crash_fade", "all", 0)
show("extreme_fade  crash_fade(long) k=2 thr=0.12 reg=all liq=all", 2, 0.12, "crash_fade", "all", 0)
show("extreme_fade  crash_fade(long) k=1 thr=0.12 reg=down liq=all", 1, 0.12, "crash_fade", "down", 0)
show("extreme_fade  crash_fade(long) k=1 thr=0.12 reg=up liq=all", 1, 0.12, "crash_fade", "up", 0)
# rally_exhaustion: short after >=12%/2d rally in BTC-down tape, wide 25% stop
show("rally_exhaustion pump_fade(short) k=2 thr=0.12 reg=down liq=all", 2, 0.12, "pump_fade", "down", 0)
show("rally_exhaustion pump_fade(short) k=2 thr=0.12 reg=all  liq=all", 2, 0.12, "pump_fade", "all", 0)
show("rally_exhaustion pump_fade(short) k=2 thr=0.12 reg=up   liq=all", 2, 0.12, "pump_fade", "up", 0)

# ============================================================
# RIGOR PASS: excess EV over matched random entry (same side/stop/horizon/regime/liq).
# Controls for the -44% BTC tape so we don't reward beta.
# ============================================================
print("\n=== MATCHED-BASELINE EXCESS (cell EV minus same side/stop/hz/regime/liq random entry) ===")
import statistics as _st

def matched_baseline(side, stop, horizon, reg_filter, liq_floor):
    rets=[]
    for coin in coins:
        cc=al.candles(d,coin,'1d')
        if len(cc)<30: continue
        vol=d['universe'].get(coin,{}).get('dayNtlVlm',0)
        if vol<liq_floor: continue
        for i in range(20,len(cc)-1):
            if reg_filter!='all':
                if btc_regime_at(cc[i][al.T])!=reg_filter: continue
            ei=i+1
            res=al.sweep_stop(cc[ei][al.O], side, cc[ei:], [stop], horizon)
            rets.append(res[stop])
    return 100*_st.mean(rets) if rets else None

def excess(rec):
    _,side=QUADRANTS[rec['quad']]
    liq={'all':0,'>20M':20e6,'>50M':50e6}[rec['liq']]
    base=matched_baseline(side,rec['stop'],rec['horizon'],rec['reg'],liq)
    return rec['ev12']-base if base is not None else None, base

print(f"{'k':>2} {'thr':>5} {'quad':<15} {'reg':<5} {'liq':<6} {'stop':>5} {'hz':>3} {'n':>4} {'ev12':>7} {'base':>7} {'excess':>7}")
top=[r for r in robust_rows if r['n']>=20][:25]
scored=[]
for r in top:
    exc,base=excess(r)
    scored.append((exc,base,r))
scored.sort(key=lambda x:(x[0] if x[0] is not None else -999),reverse=True)
for exc,base,r in scored:
    print(f"{r['k']:>2} {r['thr']:>5.2f} {r['quad']:<15} {r['reg']:<5} {r['liq']:<6} {r['stop']:>5.2f} {r['horizon']:>3} {r['n']:>4} {r['ev12']:>7.2f} {base:>7.2f} {exc:>7.2f}")

print("\n=== LIVE EDGES: excess over matched baseline ===")
def live_excess(label,k,thr,quad,reg,liq):
    rec=best_cell(k,thr,quad,reg,liq)
    if not rec:
        print(f"{label}: thin"); return
    exc,base=excess(rec)
    print(f"{label}\n   stop={rec['stop']} hz={rec['horizon']} n={rec['n']} ev12={rec['ev12']:.2f} base={base:.2f} EXCESS={exc:.2f} robust={rec['robust']}")
live_excess("extreme_fade crash_fade(long) k1 thr.12 all",1,0.12,'crash_fade','all',0)
live_excess("extreme_fade crash_fade(long) k1 thr.12 down",1,0.12,'crash_fade','down',0)
live_excess("rally_exhaustion pump_fade(short) k2 thr.12 down",2,0.12,'pump_fade','down',0)
live_excess("rally_exhaustion pump_fade(short) k2 thr.12 up",2,0.12,'pump_fade','up',0)
live_excess("rally_exhaustion pump_fade(short) k2 thr.12 all",2,0.12,'pump_fade','all',0)
