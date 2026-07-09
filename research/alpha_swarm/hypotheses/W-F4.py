"""W-F4 oi_price_quadrants — PRE-REGISTERED four-quadrant OI x price study.

THE PRE-REGISTRATION (thresholds, scoring, and required n fixed BEFORE any outcome
was computed — see findings/W-F4.md; do not tune these after looking):

Data: .state/.oi-timeseries.jsonl (ts SECONDS, oi[coin]=[oi_contracts, px], ~10min
snapshots, read-only). Universe = coins in BOTH dataset.json 40-coin set and the
logger. Hourly grid: last snapshot within (h-30min, h].

Signals at hour t (lookahead-safe, snapshot data only):
  dOI = oi(t)/oi(t-24h) - 1 ;  dP = px(t)/px(t-24h) - 1
  valid iff both endpoints exist AND >=18 of the 24 hourly points in the window
  exist (guards the 6.8-day logger hole).
Quadrants (|dOI| >= 0.05 AND |dP| >= 0.03):
  Q1 dP>0,dOI>0 new-longs continuation  -> trade LONG
  Q2 dP>0,dOI<0 short-covering rally    -> trade SHORT (fade: fuel spent)
  Q3 dP<0,dOI>0 new-shorts continuation -> trade SHORT
  Q4 dP<0,dOI<0 long-capitulation flush -> trade LONG (fade)
Entry: px at t+1h grid point. Exit: px at t+25h (24h hold, no stop; 48h recorded).
Dedup: per coin+quadrant, no new episode until 24h since entry AND one non-
qualifying hour. Independence gate: score a cell ONLY when n_episodes >= 15 AND
episodes span >= 8 distinct UTC days.
Scoring (fixed): signed EV net of 12/25 bps; funding term from
.state/.data_funding_oi.jsonl nearest 'f' within 6h x 24h (approximate — sparse);
null = mc_null.shuffle_label_p vs ALL valid same-side (coin,hour) 24h forward
returns, 3000 iters; OOS halves by time. VALIDATED needs net25>0, both halves>0,
p<=0.01 (8 primary cells -> multiple-comparison guard).
Secondary (funding-joint): each quadrant x sign(funding), same gates per sub-cell.
"""
from __future__ import annotations
import bisect, json, statistics, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import alpha_lib as al
import mc_null

REPO = Path(__file__).resolve().parent.parent.parent.parent
OI_FILE = REPO / ".state" / ".oi-timeseries.jsonl"
FOI_FILE = REPO / ".state" / ".data_funding_oi.jsonl"
HOUR_S = 3600

d = al.load_dataset()
UNIVERSE = set(d["coins"])

def load_oi():
    """coin -> sorted [(ts_s, oi, px)]"""
    out: dict[str, list] = {}
    with open(OI_FILE) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            ts = rec["ts"]
            for c, v in rec.get("oi", {}).items():
                if c in UNIVERSE and isinstance(v, list) and len(v) >= 2 and v[0] and v[1]:
                    out.setdefault(c, []).append((ts, float(v[0]), float(v[1])))
    for c in out:
        out[c].sort()
    return out

def load_funding_snaps():
    """coin -> sorted [(ts_s, hourly_rate)]"""
    out: dict[str, list] = {}
    with open(FOI_FILE) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            ts = rec["ts"] / 1000.0
            for row in rec.get("rows", []):
                c = row.get("c")
                if c in UNIVERSE and row.get("f") is not None:
                    out.setdefault(c, []).append((ts, float(row["f"])))
    for c in out:
        out[c].sort()
    return out

def hourly_grid(series):
    """(ts,oi,px) snapshots -> {hour_ts: (oi,px)} using last snapshot in (h-1800, h]."""
    g = {}
    for ts, oi, px in series:
        h = (int(ts) // HOUR_S) * HOUR_S
        for cand in (h, h + HOUR_S):
            if cand - 1800 < ts <= cand:
                g[cand] = (oi, px)   # later snapshots in the window overwrite = last
    return g

D_OI, D_P = 0.05, 0.03
QUAD_SIDE = {"Q1": "long", "Q2": "short", "Q3": "short", "Q4": "long"}

def episodes_and_pool():
    oi = load_oi()
    ev = {q: [] for q in QUAD_SIDE}
    pool = {"long": [], "short": []}
    for c, series in oi.items():
        g = hourly_grid(series)
        hours = sorted(g)
        hset = set(hours)
        last_entry = {q: -1e18 for q in QUAD_SIDE}
        prev_qual = {q: False for q in QUAD_SIDE}
        for t in hours:
            t0 = t - 24 * HOUR_S
            if t0 not in hset:
                continue
            n_in = sum(1 for k in range(1, 24) if (t0 + k * HOUR_S) in hset)
            if n_in < 18:
                continue
            e_t, x_t = t + HOUR_S, t + 25 * HOUR_S
            if e_t not in hset or x_t not in hset:
                continue
            x48 = t + 49 * HOUR_S
            oi0, px0 = g[t0]
            oi1, px1 = g[t]
            if not oi0 or not px0:
                continue
            doi, dp = oi1 / oi0 - 1, px1 / px0 - 1
            entry_px = g[e_t][1]
            fwd = g[x_t][1] / entry_px - 1
            fwd48 = (g[x48][1] / entry_px - 1) if x48 in hset else None
            pool["long"].append(fwd)
            pool["short"].append(-fwd)
            q = None
            if abs(doi) >= D_OI and abs(dp) >= D_P:
                if dp > 0:
                    q = "Q1" if doi > 0 else "Q2"
                else:
                    q = "Q3" if doi > 0 else "Q4"
            for qq in QUAD_SIDE:
                if qq != q:
                    prev_qual[qq] = False
            if q is None:
                continue
            if t < last_entry[q] + 24 * HOUR_S or prev_qual[q]:
                # inside the 24h lockout OR no non-qualifying hour since last episode
                prev_qual[q] = True
                continue
            prev_qual[q] = True
            last_entry[q] = e_t
            sgn = 1.0 if QUAD_SIDE[q] == "long" else -1.0
            ev[q].append({"c": c, "t": t, "doi": doi, "dp": dp,
                          "ret": sgn * fwd,
                          "ret48": (sgn * fwd48) if fwd48 is not None else None})
    return ev, pool

def ripeness(ev):
    print("cell | side | n_episodes | distinct_days | ripe(n>=15 & days>=8)?")
    ripe = {}
    for q in ("Q1", "Q2", "Q3", "Q4"):
        days = {e["t"] // (24 * HOUR_S) for e in ev[q]}
        ok = len(ev[q]) >= 15 and len(days) >= 8
        ripe[q] = ok
        print(f"  {q}  | {QUAD_SIDE[q]:5} | {len(ev[q]):3} | {len(days):3} | {'RIPE' if ok else 'not ripe'}")
    return ripe

def score(q, ev, pool):
    """pre-registered scoring — run ONLY on ripe cells."""
    fs = load_funding_snaps()
    rows = sorted(ev[q], key=lambda e: e["t"])
    rets = [e["ret"] for e in rows]
    # funding term approx: nearest snapshot within 6h, x24h; long pays f, short collects
    fterm = []
    for e in rows:
        snaps = fs.get(e["c"], [])
        ts = [s[0] for s in snaps]
        i = bisect.bisect_right(ts, e["t"]) - 1
        fr = snaps[i][1] if (i >= 0 and e["t"] - ts[i] <= 6 * HOUR_S) else 0.0
        sgn = -1.0 if QUAD_SIDE[q] == "long" else 1.0
        fterm.append(sgn * fr * 24)
    tot = [r + ft for r, ft in zip(rets, fterm)]
    res = mc_null.shuffle_label_p(tot, pool[QUAD_SIDE[q]], n_iter=3000, seed=23)
    half = len(tot) // 2
    out = {
        "cell": q, "side": QUAD_SIDE[q], "n": len(tot),
        "gross_pct": round(100 * statistics.mean(rets), 2),
        "funding_term_pct": round(100 * statistics.mean(fterm), 3),
        "net12_pct": round(100 * (statistics.mean(tot) - 0.0012), 2),
        "net25_pct": round(100 * (statistics.mean(tot) - 0.0025), 2),
        "oos25": (round(100 * (statistics.mean(tot[:half]) - 0.0025), 2),
                  round(100 * (statistics.mean(tot[half:]) - 0.0025), 2)),
        "null_p": res["p_one_sided"], "null_excess_pct": round(100 * res["excess"], 2),
        "ret48_gross_pct": round(100 * statistics.mean(
            [e["ret48"] for e in rows if e["ret48"] is not None] or [0]), 2),
    }
    return out

if __name__ == "__main__":
    print("=== W-F4 OI x price quadrants — ripeness check against pre-registration ===")
    ev, pool = episodes_and_pool()
    print(f"pool sizes: long/short candidates n={len(pool['long'])}")
    ripe = ripeness(ev)
    for q in ("Q1", "Q2", "Q3", "Q4"):
        if ripe[q]:
            print(f"\n-- scoring ripe cell {q} (pre-registered spec) --")
            print("  ", score(q, ev, pool))
    if not any(ripe.values()):
        print("\nNo cell ripe. Re-run when the logger has accrued more days.")
