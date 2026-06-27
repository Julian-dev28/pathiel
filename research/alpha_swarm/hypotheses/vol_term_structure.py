"""B3 vol_term_structure — short-RV/long-RV ratio routes between fade (vol spike) and
trend (vol compression). Lift over un-routed fade-only / mom-only on the SAME entry days."""
import statistics, random
import alpha_lib as A

d = A.load_dataset()
coins = d["coins"]
random.seed(11)

SRV, LRV = 5, 30
LMOM, LREV = 14, 3
HOR, STOP = 5, 0.25
HI, LO = 1.2, 0.8   # ratio thresholds: >HI spike->fade, <LO compression->trend

def rets(cl, a, b):  # simple returns cl[a..b]
    return [cl[k]/cl[k-1]-1 for k in range(a, b) if cl[k-1]]

rows = []  # (t, entry_px, fwd, mom_side, fade_side, route)  route in {fade,trend,None}
for c in coins:
    cd = A.candles(d, c, "1d")
    if len(cd) < LRV + LMOM + HOR + 2:
        continue
    cl = [b[A.C] for b in cd]
    i = LRV + LMOM
    while i < len(cd) - HOR - 1:
        srv = statistics.pstdev(rets(cl, i-SRV+1, i+1))
        lrv = statistics.pstdev(rets(cl, i-LRV+1, i+1))
        ratio = srv/lrv if lrv > 0 else 1.0
        mom = "long" if cl[i]/cl[i-LMOM]-1 > 0 else "short"
        fade = "short" if cl[i]/cl[i-LREV]-1 > 0 else "long"
        route = "fade" if ratio > HI else ("trend" if ratio < LO else None)
        if route is None:
            i += 1; continue
        rows.append((cd[i+1][A.T], cd[i+1][A.O], cd[i+1:], mom, fade, route))
        i += HOR
    # decluster handled by step on routed entries only; non-routed advance by 1

def trades(which):
    out = []
    for t, px, fwd, mom, fade, route in rows:
        if which == "router":
            side = fade if route == "fade" else mom
        elif which == "mom":
            side = mom
        elif which == "fade":
            side = fade
        else:
            side = random.choice(["long", "short"])
        r = A.sweep_stop(px, side, fwd, [STOP], HOR)[STOP]
        out.append({"t": t, "ret": r})
    return out

print(f"routed entries: {len(rows)}  (fade={sum(1 for r in rows if r[5]=='fade')} trend={sum(1 for r in rows if r[5]=='trend')})")
res = {}
print(f"{'mode':8s} {'n':>5s} {'EV12':>7s} {'win':>5s} {'sharpe':>6s} {'h1':>7s} {'h2':>7s}")
for m in ["router", "mom", "fade", "random"]:
    s = A.summarize(trades(m)); res[m] = s
    sl, oos = s["slip12"], s["oos_12bps"]
    print(f"{m:8s} {s['n']:5d} {sl['mean_ret_pct']:7.3f} {sl['win_rate']:5.3f} {sl['sharpe_like']:6.3f} "
          f"{str(oos['first_half_mean_pct']):>7s} {str(oos['second_half_mean_pct']):>7s}  {s['verdict']}")
def ev(m): return res[m]["slip12"]["mean_ret_pct"]
base = max(["mom", "fade"], key=ev)
print(f"\nrouter EV {ev('router'):.3f} vs best base ({base}) {ev(base):.3f} -> lift {ev('router')-ev(base):+.3f}; vs random {ev('router')-ev('random'):+.3f}")
