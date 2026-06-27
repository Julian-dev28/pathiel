"""D4 funding_extreme_reversion — extreme funding = crowded positioning. Does a funding
SPIKE precede a price REVERSAL? Event study: flag (coin,day) where trailing-24h funding is an
EXTREME (per-coin z over trailing 30d, OR cross-sectional decile). FADE: extreme-positive->SHORT,
extreme-negative->LONG. Forward h-day return, STOP-WIDTH SWEPT. Score EXCESS over a matched
SAME-SIDE random-entry null (the -44% tape flatters shorts, so the null carries it too).
"""
from __future__ import annotations
import statistics
import alpha_lib as al, funding_lib as fl, mc_null

d = al.load_dataset(); f = fl.load_funding()
DAY = 86_400_000
COINS = [c for c in d["coins"] if fl.rows(f, c)]
cand = {c: {b[al.T]: b for b in al.candles(d, c, "1d")} for c in COINS}
cand_t = {c: sorted(cand[c]) for c in COINS}
fs = min(fl.rows(f, c)[0][0] for c in COINS); fe = max(fl.rows(f, c)[-1][0] for c in COINS)

def fwd_bars(c, t):
    ts = [x for x in cand_t[c] if x > t]
    return [cand[c][x] for x in ts]

def coin_z(c, t, win_days=30):
    """z-score of trailing-24h funding vs its own distribution over trailing win_days (lookahead-safe)."""
    cur = fl.trailing_funding(f, c, t, 24)
    if cur is None: return None
    hist = []
    for k in range(1, win_days + 1):
        v = fl.trailing_funding(f, c, t - k * DAY, 24)
        if v is not None: hist.append(v)
    if len(hist) < 10: return None
    m = statistics.mean(hist); s = statistics.pstdev(hist) + 1e-12
    return (cur - m) / s

STOPS = [0.08, 0.15, 0.20, 0.25, 0.40]

def run(z_thresh=2.0, horizon=3, win_days=30):
    sig = {"long": [], "short": []}     # signal returns at each stop
    pool = {"long": [], "short": []}    # matched same-side all-bar returns
    sig_stop = {s: {"long": [], "short": []} for s in STOPS}
    for c in COINS:
        for t in cand_t[c]:
            if not (fs <= t <= fe): continue
            fb = fwd_bars(c, t)
            if len(fb) < 1: continue
            entry = fb[0][al.O]
            if not entry: continue
            z = coin_z(c, t, win_days)
            if z is None: continue
            # pool: BOTH sides get every bar's forward return (matched random entry)
            for side in ("long", "short"):
                r = al.sweep_stop(entry, side, fb[1:], STOPS, horizon)
                pool[side].append(statistics.mean(r.values()))
            # signal: only extremes, faded
            if z >= z_thresh:
                side = "short"
            elif z <= -z_thresh:
                side = "long"
            else:
                continue
            r = al.sweep_stop(entry, side, fb[1:], STOPS, horizon)
            sig[side].append((t, statistics.mean(r.values())))
            for s in STOPS:
                sig_stop[s][side].append(r[s])
    out = {"z": z_thresh, "h": horizon, "n_long": len(sig["long"]), "n_short": len(sig["short"])}
    # combined fade book (both sides) net of 25bps
    allsig = [r for side in sig for _, r in sig[side]]
    out["mean_net25_pct"] = round(100 * (statistics.mean(allsig) - 0.0025), 3) if allsig else None
    # stop sweep mean (gross)
    out["stop_sweep"] = {s: round(100 * statistics.mean(
        sig_stop[s]["long"] + sig_stop[s]["short"]), 3) for s in STOPS if (sig_stop[s]["long"] + sig_stop[s]["short"])}
    # null per side
    for side in ("long", "short"):
        obs = [r for _, r in sig[side]]
        if len(obs) >= 8:
            res = mc_null.shuffle_label_p(obs, pool[side], n_iter=5000, seed=3)
            out[f"null_{side}"] = {"n": len(obs), "obs": res["obs_mean"], "null": res["null_mean"],
                                   "excess": res["excess"], "z": res["z"], "p": res["p_one_sided"]}
        else:
            out[f"null_{side}"] = {"n": len(obs), "thin": True}
    # OOS halves on combined
    trades = [{"t": t, "ret": r - 0.0025} for side in sig for t, r in sig[side]]
    if trades:
        f1, f2 = al.time_split(trades)
        out["oos25"] = (round(100 * statistics.mean(x["ret"] for x in f1), 3) if f1 else None,
                        round(100 * statistics.mean(x["ret"] for x in f2), 3) if f2 else None)
    return out

if __name__ == "__main__":
    print("=== D4 funding_extreme_reversion (fade, EXCESS over matched same-side null) ===")
    for z in [1.5, 2.0, 2.5]:
        for h in [2, 3, 5]:
            r = run(z_thresh=z, horizon=h)
            print(f"\nz>={z} h={h}d: nL={r['n_long']} nS={r['n_short']} "
                  f"net25={r['mean_net25_pct']}% OOS25={r.get('oos25')}")
            print(f"   stop_sweep(gross%): {r['stop_sweep']}")
            print(f"   null_long : {r['null_long']}")
            print(f"   null_short: {r['null_short']}")
