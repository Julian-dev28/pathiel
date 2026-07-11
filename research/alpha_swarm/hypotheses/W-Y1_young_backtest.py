#!/usr/bin/env python
"""W-Y1: young-listing (bars 2-60) behavior on the xyz HIP-3 dex.

Pre-registered hypotheses (before looking at results):
  H1 MOVER CONTINUATION: young coin prints >= +8% day on >= $3M dollar vol
     -> LONG next open. Sweep hold {1,2,3,5}d x stop {8,15,20}%.
  H2 CRASH SIDE: young coin prints <= -8% day (same $3M floor)
     -> BOTH continuation-SHORT and fade-LONG. Same sweeps.
  H3 POST-LISTING DRIFT: sign of first 3 completed days -> enter open of
     bar 3, hold 10d same direction (no stop + 20% stop variant).

Discipline:
  - Lookahead-safe: signal at day-i close, fill at day-(i+1) open.
  - Costs: 25 bps PER SIDE (0.50% round trip) minimum; also 40 bps/side.
  - Independent episodes: one open trade per coin at a time.
  - Trades that cannot complete their hold window (data ends) are DROPPED
    unless the stop fired first.
  - MC null: 2000 iters, same-coin same-window random entry bars, same
    hold/stop mechanics, one-sided p on mean net return @25bps.
  - OOS by LISTING DATE: coins split at median first-bar timestamp into
    EARLY vs LATE listing cohorts; EV reported for both.
  - CONTROL: identical rules on the same coins' MATURE windows (bar > 60).
  - Young window: signal bar index in [2, 59] since first bar (>=2 dodges
    day-1 chaos). Mature: signal bar index >= 61.
  - Survivorship: only coins listed TODAY are visible; delisted xyz coins
    (if any) are absent. Results are an upper bound.

Data: W-Y_cache_xyz_daily.json (written by W-Y0_fetch.py; no network here).
"""
import json
import os
import random
import statistics as st
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DAILY = json.load(open(os.path.join(HERE, "W-Y_cache_xyz_daily.json")))

MOVE = 0.08
DVOL_FLOOR = 3_000_000.0
HOLDS = [1, 2, 3, 5]
STOPS = [0.08, 0.15, 0.20]
COSTS_BPS = [25, 40]          # per side
MC_ITERS = 2000
YOUNG_LO, YOUNG_HI = 2, 59    # signal bar index range (young)
MATURE_LO = 61
SEED = 20260710

# ---------------------------------------------------------------- data prep
coins = {}
for coin, rows in DAILY.items():
    rows = [r for r in rows if r[5] is not None]
    if len(rows) < 5:
        continue
    rows.sort(key=lambda r: r[0])
    # drop the last (in-progress) daily bar
    coins[coin] = rows[:-1]

listing_ts = {c: rows[0][0] for c, rows in coins.items()}
med_listing = sorted(listing_ts.values())[len(listing_ts) // 2]


def cohort(coin):
    return "EARLY" if listing_ts[coin] < med_listing else "LATE"


def window_bounds(rows, window):
    if window == "young":
        return YOUNG_LO, min(YOUNG_HI, len(rows) - 1)
    return MATURE_LO, len(rows) - 1


# ------------------------------------------------------------- trade engine
def simulate(rows, sig_i, direction, hold, stop):
    """Enter at open of bar sig_i+1, hold `hold` bars, stop at `stop` frac.
    Returns gross return (before costs) or None if the window is incomplete."""
    e = sig_i + 1
    if e >= len(rows):
        return None
    entry = rows[e][1]
    if entry <= 0:
        return None
    if direction > 0:
        stop_px = entry * (1 - stop)
    else:
        stop_px = entry * (1 + stop)
    last_exit = e + hold - 1
    for j in range(e, min(last_exit, len(rows) - 1) + 1):
        o, h, l = rows[j][1], rows[j][2], rows[j][3]
        if direction > 0:
            if j > e and o <= stop_px:
                return o / entry - 1          # gap through stop
            if l <= stop_px:
                return stop_px / entry - 1
        else:
            if j > e and o >= stop_px:
                return 1 - o / entry
            if h >= stop_px:
                return 1 - stop_px / entry
    if last_exit > len(rows) - 1:
        return None                            # incomplete hold, no stop hit
    c = rows[last_exit][4]
    return (c / entry - 1) if direction > 0 else (1 - c / entry)


def collect_signals(window, kind):
    """kind: 'up' (>=+8% & $3M) or 'down' (<=-8% & $3M). Returns
    [(coin, sig_i)] deduped later per-hold by episode logic."""
    out = []
    for coin, rows in coins.items():
        lo, hi = window_bounds(rows, window)
        for i in range(max(lo, 1), hi + 1):
            pc = rows[i - 1][4]
            if pc <= 0:
                continue
            r = rows[i][4] / pc - 1
            dvol = rows[i][5] * rows[i][4]
            if dvol < DVOL_FLOOR:
                continue
            if kind == "up" and r >= MOVE:
                out.append((coin, i))
            elif kind == "down" and r <= -MOVE:
                out.append((coin, i))
    return out


def episode_filter(signals, hold):
    """One open episode per coin: skip signals that fire while a prior trade
    on the same coin is still open (entry..exit = sig_i+1 .. sig_i+hold)."""
    out, busy_until = [], {}
    for coin, i in sorted(signals, key=lambda x: (x[0], x[1])):
        if i + 1 <= busy_until.get(coin, -1):
            continue
        out.append((coin, i))
        busy_until[coin] = i + hold
    return out


def run_cell(signals, direction, hold, stop):
    trades = []
    for coin, i in episode_filter(signals, hold):
        g = simulate(coins[coin], i, direction, hold, stop)
        if g is not None:
            trades.append((coin, i, g))
    return trades


def net(g, bps):
    return g - 2 * bps / 10_000.0


def mc_pvalue(trades, window, direction, hold, stop, rng):
    """Null: same coins, same window, random signal bars, same mechanics."""
    if not trades:
        return None
    real = st.mean(net(g, 25) for _, _, g in trades)
    valid = {}
    for coin, _, _ in trades:
        if coin in valid:
            continue
        rows = coins[coin]
        lo, hi = window_bounds(rows, window)
        valid[coin] = [i for i in range(lo, hi + 1) if i + 1 < len(rows)]
    ge = 0
    n_eff = 0
    for _ in range(MC_ITERS):
        vals = []
        for coin, _, _ in trades:
            pool = valid[coin]
            if not pool:
                continue
            g = simulate(coins[coin], rng.choice(pool), direction, hold, stop)
            if g is not None:
                vals.append(net(g, 25))
        if not vals:
            continue
        n_eff += 1
        if st.mean(vals) >= real:
            ge += 1
    return (ge + 1) / (n_eff + 1) if n_eff else None


def cell_report(name, signals, direction, window, hold, stop, rng):
    trades = run_cell(signals, direction, hold, stop)
    n = len(trades)
    row = {"cell": name, "window": window, "hold": hold, "stop": stop, "n": n}
    if n == 0:
        return row
    for bps in COSTS_BPS:
        vals = [net(g, bps) for _, _, g in trades]
        row[f"ev{bps}"] = st.mean(vals)
    row["win25"] = sum(1 for _, _, g in trades if net(g, 25) > 0) / n
    early = [net(g, 25) for c, _, g in trades if cohort(c) == "EARLY"]
    late = [net(g, 25) for c, _, g in trades if cohort(c) == "LATE"]
    row["ev25_early"] = st.mean(early) if early else None
    row["n_early"] = len(early)
    row["ev25_late"] = st.mean(late) if late else None
    row["n_late"] = len(late)
    if n >= 15:
        row["mc_p"] = mc_pvalue(trades, window, direction, hold, stop, rng)
    row["trades"] = [(c, coins[c][i][0], round(net(g, 25), 4)) for c, i, g in trades]
    return row


def fmt(row):
    def pct(x):
        return f"{x*100:+.2f}%" if isinstance(x, float) else "  --  "
    p = row.get("mc_p")
    return (f"{row['cell']:<12} {row['window']:<6} h={row['hold']}d "
            f"stop={int(row['stop']*100):>2}% n={row['n']:>3} "
            f"ev25={pct(row.get('ev25'))} ev40={pct(row.get('ev40'))} "
            f"win={row.get('win25', 0)*100:4.0f}% "
            f"early={pct(row.get('ev25_early'))}(n={row.get('n_early', 0)}) "
            f"late={pct(row.get('ev25_late'))}(n={row.get('n_late', 0)}) "
            f"p={p if p is None else f'{p:.3f}'}")


def main():
    rng = random.Random(SEED)
    print(f"coins with history: {len(coins)}  "
          f"median listing: {datetime.fromtimestamp(med_listing/1000, tz=timezone.utc).date()}")
    yb = sum(1 for r in coins.values() for _ in range(YOUNG_LO, min(YOUNG_HI, len(r)-1)+1))
    mb = sum(max(0, len(r) - 1 - MATURE_LO + 1) for r in coins.values())
    print(f"young bars: {yb}  mature bars: {mb}")

    results = []
    # H1 + H2 sweeps, young + mature control
    for window in ("young", "mature"):
        up = collect_signals(window, "up")
        dn = collect_signals(window, "down")
        print(f"\n[{window}] raw up-signals={len(up)} down-signals={len(dn)}")
        for hold in HOLDS:
            for stop in STOPS:
                results.append(cell_report("H1_long", up, +1, window, hold, stop, rng))
                results.append(cell_report("H2_short", dn, -1, window, hold, stop, rng))
                results.append(cell_report("H2_fadelong", dn, +1, window, hold, stop, rng))

    # H3: post-listing drift (young-only by construction)
    h3 = {"cell": "H3_drift", "window": "young", "hold": 10}
    for stop, tag in ((1.0, "nostop"), (0.20, "stop20")):
        trades = []
        for coin, rows in coins.items():
            if len(rows) < 14:
                continue
            r3 = rows[2][4] / rows[0][1] - 1
            if r3 == 0:
                continue
            d = 1 if r3 > 0 else -1
            g = simulate(rows, 2, d, 10, stop)
            if g is not None:
                trades.append((coin, 2, g, d))
        n = len(trades)
        vals25 = [net(g, 25) for _, _, g, _ in trades]
        vals40 = [net(g, 40) for _, _, g, _ in trades]
        early = [net(g, 25) for c, _, g, _ in trades if cohort(c) == "EARLY"]
        late = [net(g, 25) for c, _, g, _ in trades if cohort(c) == "LATE"]
        print(f"\nH3_drift[{tag}]: n={n} ev25={st.mean(vals25)*100:+.2f}% "
              f"ev40={st.mean(vals40)*100:+.2f}% "
              f"win={sum(1 for v in vals25 if v>0)/n*100:.0f}% "
              f"early={st.mean(early)*100:+.2f}%(n={len(early)}) "
              f"late={st.mean(late)*100:+.2f}%(n={len(late)})" if n else f"H3[{tag}]: n=0")
        if n >= 15:
            # MC null for H3: random direction assignment per coin (sign null)
            real = st.mean(vals25)
            ge = 0
            for _ in range(MC_ITERS):
                vals = []
                for coin, _, g, d in trades:
                    rd = rng.choice((1, -1))
                    gg = g if rd == d else simulate(coins[coin], 2, rd, 10, stop)
                    if gg is not None:
                        vals.append(net(gg, 25))
                if vals and st.mean(vals) >= real:
                    ge += 1
            print(f"  H3[{tag}] MC sign-null p={(ge+1)/(MC_ITERS+1):.3f}")

    print("\n================ CELL TABLE ================")
    for r in results:
        print(fmt(r))

    out = os.path.join(HERE, "W-Y1_results.json")
    with open(out, "w") as f:
        json.dump(results, f, indent=1, default=str)
    print(f"\nresults -> {out}")


if __name__ == "__main__":
    main()
