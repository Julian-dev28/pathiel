#!/usr/bin/env python
"""W-Y3: young_mover_short — entry AGE window + exit GEOMETRY sweep.

TRACK A (primary, live-faithful): the signal population is every
history_floor_preflight block episode parsed from the loop's own log
(W-Y3_episodes.json — the EXACT population the live book shorts, PIT by
construction). Entry = open of the first 1h bar at/after the first block
of that (coin, UTC day). SHORT. Sweep hold {1,2,3,5}d x stop {6,10,15,20}%.
Grade on 1h bars STRICTLY after entry. Costs 25 bps/side (50 bps RT) plus
REAL accrued funding from fundingHistory (short receives +rate).

  - Age buckets from the log's own "(Nd < 60d)" — PIT age, no estimation.
  - Live policy is xyz-equities-only -> primary table is equities-only;
    crypto (CASHCAT, GRAM) reported separately at zero weight.
  - One open episode per coin (chronological busy-filter per hold).
  - Incomplete holds are DROPPED unless the stop fired first.
  - OOS: chronological halves (median episode ts).
  - MC null: 2000 iters; for each real trade draw a random 1h entry bar for
    the SAME coin, restricted to bars where the coin was YOUNG (<60 daily
    bars old, boundary estimated from the log ages) inside the same log
    window, same mechanics incl. funding. One-sided p on mean net.
    EXCESS = real mean - null mean (score vs random-timing, not raw).

TRACK B (longer history, proxy signal): W-Y_cache_xyz_daily.json (full
listing history for 86 xyz coins as of 2026-07-10). Proxy for "scan
surfaced" = |daily move| >= 8% AND dollar vol >= $3M (the dailyMover
trigger; the live scan is broader — trend/vol triggers — so this is a
subset). SHORT next open, young bar index [2,59], same sweeps, daily-bar
stop mechanics (short stopped if high >= stop_px; gap-through at open).
Funding unavailable that far back -> 0, bias bounded from the Track A
funding sample. Age bucket = signal bar index.

No network. Reads caches written by W-Y3_fetch.py + W-Y0_fetch.py.
"""
import json
import os
import random
import statistics as st

HERE = os.path.dirname(os.path.abspath(__file__))
EPS = json.load(open(os.path.join(HERE, "W-Y3_episodes.json")))
H1 = json.load(open(os.path.join(HERE, "W-Y3_cache_1h.json")))
FUND = json.load(open(os.path.join(HERE, "W-Y3_cache_funding.json")))
DAILY = json.load(open(os.path.join(HERE, "W-Y_cache_xyz_daily.json")))

HOLDS = [1, 2, 3, 5]
STOPS = [0.06, 0.10, 0.15, 0.20]
RT_COST = 0.0050            # 25 bps/side
MC_ITERS = 2000
SEED = 20260722
HOUR = 3_600_000
DAY = 86_400_000
AGE_BUCKETS = [(2, 15), (15, 25), (25, 40), (40, 60)]

# funding lookup: coin -> sorted [(t, rate)]
_FUND = {c: sorted((int(t), float(r)) for t, r in rows) for c, rows in FUND.items()}


def funding_sum(coin, t0, t1):
    """Sum of hourly funding rates paid in (t0, t1]. Short RECEIVES +rate."""
    return sum(r for t, r in _FUND.get(coin, ()) if t0 < t <= t1)


# ── Track A engine (1h bars) ────────────────────────────────────────────────
def sim_short_1h(coin, entry_idx, hold_days, stop):
    """Short at open of bars[entry_idx]; stop at +stop; exit at the bar
    closing hold_days*24h after entry-bar start. Returns (net, stopped) or
    None if the forward window is incomplete and no stop fired."""
    bars = H1[coin]
    entry_bar = bars[entry_idx]
    entry = entry_bar[1]
    if entry <= 0:
        return None
    stop_px = entry * (1 + stop)
    t_end = entry_bar[0] + int(hold_days * 24) * HOUR
    exit_px, exit_t, stopped = None, None, False
    for j in range(entry_idx, len(bars)):
        t, o, h = bars[j][0], bars[j][1], bars[j][2]
        if t >= t_end:
            break
        if j > entry_idx and o >= stop_px:          # gap through the stop
            exit_px, exit_t, stopped = o, t, True
            break
        if h >= stop_px:
            exit_px, exit_t, stopped = stop_px, t, True
            break
        exit_px, exit_t = bars[j][4], t             # provisional: bar close
    if not stopped:
        # need the data to actually reach t_end (last full bar starts t_end-1h;
        # tolerate a <=2h gap at the boundary)
        if bars[-1][0] < t_end - HOUR or exit_t is None or exit_t < t_end - 3 * HOUR:
            return None
    gross = 1 - exit_px / entry
    net = gross - RT_COST + funding_sum(coin, entry_bar[0], exit_t + HOUR)
    return net, stopped


def entry_index(coin, ts_ms):
    bars = H1[coin]
    for i, b in enumerate(bars):
        if b[0] >= ts_ms:
            return i
    return None


def busy_filter(episodes, hold_days):
    out, busy = [], {}
    for e in sorted(episodes, key=lambda e: e["ts_ms"]):
        if e["ts_ms"] < busy.get(e["coin"], -1):
            continue
        out.append(e)
        busy[e["coin"]] = e["ts_ms"] + int(hold_days * 24) * HOUR
    return out


# young boundary per coin: listing_start ~= median(ts - age*1d) over episodes
_LST = {}
for e in EPS:
    _LST.setdefault(e["coin"], []).append(e["ts_ms"] - e["age"] * DAY)
YOUNG_END = {c: int(st.median(v)) + 60 * DAY for c, v in _LST.items()}
WIN_LO = min(e["ts_ms"] for e in EPS)
WIN_HI = max(e["ts_ms"] for e in EPS)


def null_pool(coin, hold_days):
    """Eligible random entry bars: inside the log window, coin still young,
    enough forward data for the hold (stop can still end a trade early, but
    demanding full-hold coverage keeps the null comparable to real trades
    which are dropped when incomplete)."""
    bars = H1[coin]
    t_hold = int(hold_days * 24) * HOUR
    lo, hi = WIN_LO - HOUR, min(WIN_HI + HOUR, YOUNG_END.get(coin, WIN_HI))
    return [i for i, b in enumerate(bars)
            if lo <= b[0] <= hi and b[0] + t_hold <= bars[-1][0] + HOUR]


def run_cell_A(episodes, hold_days, stop):
    trades = []
    for e in busy_filter(episodes, hold_days):
        idx = entry_index(e["coin"], e["ts_ms"])
        if idx is None:
            continue
        r = sim_short_1h(e["coin"], idx, hold_days, stop)
        if r is not None:
            trades.append((e, r[0], r[1]))
    return trades


def mc_null_A(trades, hold_days, stop, rng):
    pools = {}
    for e, _, _ in trades:
        c = e["coin"]
        if c not in pools:
            pools[c] = null_pool(c, hold_days)
    real = st.mean(v for _, v, _ in trades)
    means, ge = [], 0
    for _ in range(MC_ITERS):
        vals = []
        for e, _, _ in trades:
            pool = pools[e["coin"]]
            if not pool:
                continue
            r = sim_short_1h(e["coin"], rng.choice(pool), hold_days, stop)
            if r is not None:
                vals.append(r[0])
        if not vals:
            continue
        m = st.mean(vals)
        means.append(m)
        if m >= real:
            ge += 1
    if not means:
        return None, None
    return (ge + 1) / (len(means) + 1), st.mean(means)


def cell_stats(trades, med_ts):
    n = len(trades)
    if n == 0:
        return {"n": 0}
    vals = [v for _, v, _ in trades]
    h1 = [v for e, v, _ in trades if e["ts_ms"] < med_ts]
    h2 = [v for e, v, _ in trades if e["ts_ms"] >= med_ts]
    return {"n": n, "ev": st.mean(vals),
            "win": sum(1 for v in vals if v > 0) / n,
            "stop_rate": sum(1 for _, _, s in trades if s) / n,
            "h1": st.mean(h1) if h1 else None, "n1": len(h1),
            "h2": st.mean(h2) if h2 else None, "n2": len(h2)}


def pct(x):
    return f"{x*100:+.2f}%" if isinstance(x, float) else "   --  "


# ── Track B engine (daily bars, proxy signal) ───────────────────────────────
def prep_daily():
    coins = {}
    for coin, rows in DAILY.items():
        rows = [r for r in rows if r[5] is not None]
        if len(rows) < 5:
            continue
        rows.sort(key=lambda r: r[0])
        coins[coin] = rows[:-1]                     # drop in-progress bar
    return coins


BCOINS = prep_daily()


def sim_short_daily(rows, sig_i, hold, stop):
    e = sig_i + 1
    if e >= len(rows):
        return None
    entry = rows[e][1]
    if entry <= 0:
        return None
    stop_px = entry * (1 + stop)
    last_exit = e + hold - 1
    for j in range(e, min(last_exit, len(rows) - 1) + 1):
        o, h = rows[j][1], rows[j][2]
        if j > e and o >= stop_px:
            return 1 - o / entry
        if h >= stop_px:
            return 1 - stop_px / entry
    if last_exit > len(rows) - 1:
        return None
    return 1 - rows[last_exit][4] / entry


def collect_B(lo_age, hi_age):
    """(coin, i, dir) proxy signals with bar-index age in [lo_age, hi_age)."""
    out = []
    for coin, rows in BCOINS.items():
        hi = min(hi_age - 1, len(rows) - 1)
        for i in range(max(lo_age, 1), hi + 1):
            pc = rows[i - 1][4]
            if pc <= 0:
                continue
            r = rows[i][4] / pc - 1
            if rows[i][5] * rows[i][4] < 3_000_000:
                continue
            if abs(r) >= 0.08:
                out.append((coin, i, "up" if r > 0 else "down"))
    return out


def run_cell_B(signals, hold, stop):
    trades, busy = [], {}
    for coin, i, d in sorted(signals):
        if i + 1 <= busy.get(coin, -1):
            continue
        g = sim_short_daily(BCOINS[coin], i, hold, stop)
        if g is not None:
            trades.append((coin, i, d, g - RT_COST))
            busy[coin] = i + hold
    return trades


def mc_null_B(trades, lo_age, hi_age, hold, stop, rng):
    pools = {}
    for coin, _, _, _ in trades:
        if coin in pools:
            continue
        rows = BCOINS[coin]
        hi = min(hi_age - 1, len(rows) - 1)
        pools[coin] = [i for i in range(max(lo_age, 1), hi + 1) if i + 1 < len(rows)]
    real = st.mean(v for _, _, _, v in trades)
    means, ge = [], 0
    for _ in range(MC_ITERS):
        vals = []
        for coin, _, _, _ in trades:
            pool = pools[coin]
            if not pool:
                continue
            g = sim_short_daily(BCOINS[coin], rng.choice(pool), hold, stop)
            if g is not None:
                vals.append(g - RT_COST)
        if not vals:
            continue
        m = st.mean(vals)
        means.append(m)
        if m >= real:
            ge += 1
    if not means:
        return None, None
    return (ge + 1) / (len(means) + 1), st.mean(means)


# ── main ────────────────────────────────────────────────────────────────────
def main():
    rng = random.Random(SEED)
    eq = [e for e in EPS if ":" in e["coin"]]
    cr = [e for e in EPS if ":" not in e["coin"]]
    med_ts = sorted(e["ts_ms"] for e in eq)[len(eq) // 2]
    print(f"TRACK A: {len(EPS)} episodes ({len(eq)} equity / {len(cr)} crypto), "
          f"{len({e['coin'] for e in eq})} equity coins")
    fr = [r for c in _FUND.values() for _, r in c]
    print(f"funding sample: {len(fr)} hourly rates, mean {st.mean(fr)*1e4:+.3f} bp/h "
          f"-> ~{st.mean(fr)*24*1e4:+.1f} bp/day to the SHORT side\n")

    results = {"A_surface": [], "A_age": [], "A_crypto": [], "B": []}

    # (b) hold x stop surface, equities pooled
    print("=== TRACK A: hold x stop EV surface (xyz equities, net 50bps RT + funding) ===")
    print(f"{'':>8}" + "".join(f"  stop {int(s*100):>2}%          " for s in STOPS))
    for h in HOLDS:
        line = f"hold {h}d "
        for s in STOPS:
            tr = run_cell_A(eq, h, s)
            cs = cell_stats(tr, med_ts)
            results["A_surface"].append({"hold": h, "stop": s, **{k: v for k, v in cs.items()}})
            line += (f"  {pct(cs.get('ev'))} n={cs['n']:>3} w={cs.get('win',0)*100:3.0f}%"
                     if cs["n"] else "        --      ")
        print(line)

    # OOS halves for every cell
    print("\n--- OOS halves (h1 = older / h2 = newer episodes) ---")
    for r in results["A_surface"]:
        if r["n"]:
            print(f"  h={r['hold']}d stop={int(r['stop']*100):>2}%  n={r['n']:>3}  "
                  f"ev={pct(r['ev'])}  h1={pct(r['h1'])}(n={r['n1']})  "
                  f"h2={pct(r['h2'])}(n={r['n2']})  stopped={r['stop_rate']*100:.0f}%")

    # (a) age buckets at live geometry and at candidate geometries
    print("\n=== TRACK A: age-bucket EV (xyz equities) ===")
    for h, s, tag in [(1, 0.06, "LIVE 1d/6%"), (1, 0.15, "1d/15%"),
                      (2, 0.15, "2d/15%"), (3, 0.15, "3d/15%"), (5, 0.20, "5d/20%")]:
        print(f"[{tag}]")
        for lo, hi in AGE_BUCKETS:
            sub = [e for e in eq if lo <= e["age"] < hi]
            tr = run_cell_A(sub, h, s)
            cs = cell_stats(tr, med_ts)
            results["A_age"].append({"geom": tag, "lo": lo, "hi": hi, **cs})
            if cs["n"]:
                print(f"  age {lo:>2}-{hi:<2}d  n={cs['n']:>3}  ev={pct(cs['ev'])} "
                      f"win={cs['win']*100:3.0f}%  h1={pct(cs['h1'])}(n={cs['n1']}) "
                      f"h2={pct(cs['h2'])}(n={cs['n2']})")
            else:
                print(f"  age {lo:>2}-{hi:<2}d  n=0")

    # MC null + excess for the headline cells
    print("\n=== TRACK A: same-coin random-entry nulls (2000 iters) ===")
    for h, s, tag in [(1, 0.06, "LIVE 1d/6%"), (1, 0.15, "1d/15%"), (2, 0.15, "2d/15%"),
                      (3, 0.15, "3d/15%"), (5, 0.15, "5d/15%"), (5, 0.20, "5d/20%")]:
        tr = run_cell_A(eq, h, s)
        if not tr:
            continue
        p, null_mean = mc_null_A(tr, h, s, rng)
        real = st.mean(v for _, v, _ in tr)
        print(f"  [{tag:<10}] n={len(tr):>3} real={pct(real)}  null={pct(null_mean)}  "
              f"excess={pct(real - null_mean)}  mc_p={p:.4f}")
        results.setdefault("A_null", []).append(
            {"geom": tag, "n": len(tr), "real": real, "null": null_mean, "p": p})

    # crypto side-note
    tr = run_cell_A(cr, 1, 0.06)
    if tr:
        cs = cell_stats(tr, med_ts)
        results["A_crypto"].append({"geom": "1d/6%", **cs})
        print(f"\ncrypto (zero-capital note): 1d/6% n={cs['n']} ev={pct(cs['ev'])} "
              f"win={cs['win']*100:.0f}%")

    # ── Track B ──
    print("\n=== TRACK B: daily-cache proxy (|move|>=8%, $3M floor), SHORT next open ===")
    print("age-bucket x geometry (net 50bps RT, funding=0):")
    for lo, hi in AGE_BUCKETS + [(2, 60)]:
        sigs = collect_B(lo, hi)
        for h, s in [(1, 0.06), (1, 0.15), (3, 0.15), (5, 0.20)]:
            tr = run_cell_B(sigs, h, s)
            if not tr:
                continue
            vals = [v for _, _, _, v in tr]
            up = [v for _, _, d, v in tr if d == "up"]
            dn = [v for _, _, d, v in tr if d == "down"]
            row = {"lo": lo, "hi": hi, "hold": h, "stop": s, "n": len(tr),
                   "ev": st.mean(vals),
                   "ev_up": st.mean(up) if up else None, "n_up": len(up),
                   "ev_dn": st.mean(dn) if dn else None, "n_dn": len(dn)}
            results["B"].append(row)
            print(f"  age {lo:>2}-{hi:<2} h={h}d s={int(s*100):>2}%  n={len(tr):>3}  "
                  f"ev={pct(row['ev'])}  up-sig={pct(row['ev_up'])}(n={len(up)})  "
                  f"down-sig={pct(row['ev_dn'])}(n={len(dn)})")

    # Track B null on the pooled young window at the live + best candidate geom
    print("\nTrack B same-coin nulls (young window pooled):")
    for h, s in [(1, 0.06), (1, 0.15), (5, 0.20)]:
        sigs = collect_B(2, 60)
        tr = run_cell_B(sigs, h, s)
        if len(tr) < 15:
            continue
        p, null_mean = mc_null_B(tr, 2, 60, h, s, rng)
        real = st.mean(v for _, _, _, v in tr)
        print(f"  h={h}d s={int(s*100):>2}%  n={len(tr):>3} real={pct(real)} "
              f"null={pct(null_mean)} excess={pct(real-null_mean)} mc_p={p:.4f}")
        results.setdefault("B_null", []).append(
            {"hold": h, "stop": s, "n": len(tr), "real": real, "null": null_mean, "p": p})

    out = os.path.join(HERE, "W-Y3_results.json")
    with open(out, "w") as f:
        json.dump(results, f, indent=1, default=str)
    print(f"\nresults -> {out}")


if __name__ == "__main__":
    main()
