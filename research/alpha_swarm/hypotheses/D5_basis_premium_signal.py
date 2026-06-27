"""D5 basis_premium_signal — the `premium` field (perp mark vs oracle) as a basis signal
distinct from funding. Premium extremes expected to CONVERGE: extreme-positive premium (perp
rich) -> SHORT, extreme-negative (perp cheap) -> LONG. Same event-study + matched same-side null
as D4. Premium correlates ~0.72-0.89 with funding but carries distinct instantaneous-basis info.
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

# build per-coin trailing-24h MEAN premium series, lookahead-safe
def prem_trail(c, t, hours=24):
    lo = t - hours * 3_600_000
    xs = [r[fl.PREM] for r in fl.rows(f, c) if lo < r[fl.T] <= t]
    return sum(xs) / len(xs) if xs else None

def prem_z(c, t, win_days=30):
    cur = prem_trail(c, t, 24)
    if cur is None: return None
    hist = [prem_trail(c, t - k * DAY, 24) for k in range(1, win_days + 1)]
    hist = [x for x in hist if x is not None]
    if len(hist) < 10: return None
    m = statistics.mean(hist); s = statistics.pstdev(hist) + 1e-12
    return (cur - m) / s

def fwd_bars(c, t):
    return [cand[c][x] for x in cand_t[c] if x > t]

STOPS = [0.08, 0.15, 0.20, 0.25, 0.40]

def run(T=2.0, h=5, win=30):
    sig = {"long": [], "short": []}; pool = {"long": [], "short": []}
    for c in COINS:
        for t in cand_t[c]:
            if not (fs <= t <= fe): continue
            fb = fwd_bars(c, t)
            if len(fb) < 1 or not fb[0][al.O]: continue
            z = prem_z(c, t, win)
            if z is None: continue
            for side in ("long", "short"):
                r = al.sweep_stop(fb[0][al.O], side, fb[1:], STOPS, h)
                pool[side].append(statistics.mean(r.values()))
            if z >= T: side = "short"
            elif z <= -T: side = "long"
            else: continue
            r = al.sweep_stop(fb[0][al.O], side, fb[1:], STOPS, h)
            sig[side].append((t, statistics.mean(r.values())))
    out = {"T": T, "h": h, "nL": len(sig["long"]), "nS": len(sig["short"])}
    for side in ("long", "short"):
        obs = [r for _, r in sig[side]]
        if len(obs) >= 8:
            res = mc_null.shuffle_label_p(obs, pool[side], n_iter=5000, seed=4)
            out[f"null_{side}"] = {"n": len(obs), "excess": res["excess"], "z": res["z"], "p": res["p_one_sided"]}
        else:
            out[f"null_{side}"] = {"n": len(obs), "thin": True}
    return out, sig

def short_oos(T=2.0, h=5, stop=0.20, win=30):
    tr = []
    for c in COINS:
        for t in cand_t[c]:
            if not (fs <= t <= fe): continue
            fb = fwd_bars(c, t)
            if len(fb) < 1 or not fb[0][al.O]: continue
            z = prem_z(c, t, win)
            if z is None or z < T: continue
            tr.append({"t": t, "ret": al.sweep_stop(fb[0][al.O], "short", fb[1:], [stop], h)[stop]})
    f1, f2 = al.time_split(tr)
    def ev(x, bps): return round(100 * statistics.mean(t["ret"] - bps / 1e4 for t in x), 3) if x else None
    return {"n": len(tr), "net25": ev(tr, 25), "win": round(sum(1 for t in tr if t["ret"] > 0) / len(tr), 3) if tr else None,
            "oos25": (ev(f1, 25), ev(f2, 25))}

if __name__ == "__main__":
    print("=== D5 basis_premium_signal (premium z-spike convergence, EXCESS over matched null) ===")
    for T in [1.5, 2.0]:
        for h in [3, 5]:
            o, _ = run(T, h)
            print(f"T>={T} h={h}: nL={o['nL']} nS={o['nS']} | null_short={o['null_short']} | null_long={o['null_long']}")
    print("\n-- short-side net-of-fee + OOS halves --")
    for T in [1.5, 2.0]:
        for stop in [0.15, 0.20]:
            print(f"T>={T} h=5 stop={int(stop*100)}%:", short_oos(T, 5, stop))
