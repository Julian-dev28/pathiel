#!/usr/bin/env python3
"""Comprehensive combinatorial backtest of legendary-trader techniques on OUR 109 realized
closes (.agent-memory.json) — every technique ISOLATED, then ALL combinations (enabled +
disabled). Apples-to-apples vs baseline: filters include/exclude trades, sizing transforms
rescale/cap the PnL sequence. Lookahead-controlled: every per-close feature is computed
from candles SLICED to t <= entry_time. Costs already in realized PnL. Reports full metrics
+ chronological OOS split; ranks isolated, all-combos, and OOS-robust subsets.

Techniques (cleanly testable on completed trades):
  Trend filters : PTJ 200d-MA, 50d-MA, Schwartz 10h-EMA
  Williams      : volume-confirmation at entry
  Soros/Druck   : regime (up-only / exclude-down)  [macro-regime proxy]
  McKay         : cut size after a losing streak
  Schwartz      : "uncle point" hard per-trade loss cap
NOT testable on closes (need path/setup sim — stated, not faked): Turtle/ Dennis pyramid,
PTJ probe sizing, 5:1 reward:risk (our stop/TP are config-fixed).
"""
import itertools
import json
import os
import statistics
import time
from hermes_trader.client.hl_client import fetch_hl_candles
from hermes_trader.indicators.math import candle_val, ema

CACHE = ".edge-combo-cache.json"


def _fetch_cached(coin, tf, n):
    key = f"{coin}|{tf}|{n}"
    cache = {}
    if os.path.exists(CACHE):
        try:
            cache = json.load(open(CACHE))
        except Exception:
            cache = {}
    if key in cache:
        return cache[key]
    bars = []
    for _ in range(3):
        try:
            cd = fetch_hl_candles(coin, tf, n)
            if cd:
                bars = [{"t": c.t, "o": candle_val(c, "o"), "h": candle_val(c, "h"),
                         "l": candle_val(c, "l"), "c": candle_val(c, "c"), "v": candle_val(c, "v")} for c in cd]
                break
        except Exception:
            time.sleep(1.5)
    cache[key] = bars
    try:
        json.dump(cache, open(CACHE, "w"))
    except Exception:
        pass
    return bars


def _ma_dir(bars, entry_ms, period):
    past = [b for b in bars if b["t"] <= entry_ms]
    if len(past) < max(15, period // 4):
        return 0
    closes = [b["c"] for b in past]
    m = ema(closes, min(period, len(closes)))
    return 1 if (m and closes[-1] > m[-1]) else -1 if m else 0


def _vol_confirm(bars, entry_ms, mult=1.5, look=20):
    past = [b for b in bars if b["t"] <= entry_ms]
    if len(past) < look + 1:
        return False
    avg = sum(b["v"] for b in past[-look - 1:-1]) / look
    return avg > 0 and past[-1]["v"] >= avg * mult


def metrics(pnls):
    if not pnls:
        return None
    w = [x for x in pnls if x > 0]; l = [x for x in pnls if x <= 0]
    gp, gl = sum(w), abs(sum(l))
    eq = peak = mdd = 0.0
    for x in pnls:
        eq += x; peak = max(peak, eq); mdd = min(mdd, eq - peak)
    avgL = abs(statistics.mean(l)) if l else 1.0
    sh = (statistics.mean(pnls) / statistics.pstdev(pnls)) if len(pnls) > 1 and statistics.pstdev(pnls) else 0.0
    return {"n": len(pnls), "net": sum(pnls), "win": len(w) / len(pnls) * 100,
            "pf": (gp / gl) if gl else 9.99, "mdd": mdd, "sharpe": sh,
            "avgR": statistics.mean([x / avgL for x in pnls])}


def main():
    cl = sorted(json.load(open(".agent-memory.json"))["closes"], key=lambda c: c.get("closed_at", 0))
    pnlf = lambda c: c.get("realized_pnl_usd") or 0
    coins = sorted({c["coin"] for c in cl})
    print(f"# fetching candles for {len(coins)} coins (cached to {CACHE})...")
    daily, hourly = {}, {}
    for c in coins:
        daily[c] = _fetch_cached(c, "1d", 400)
        hourly[c] = _fetch_cached(c, "1h", 240)

    # precompute per-close feature flags (lookahead-safe)
    feat = []
    for c in cl:
        et = c.get("entry_time") or c.get("closed_at") or 0
        side = 1 if c.get("side") == "long" else -1
        d200 = _ma_dir(daily.get(c["coin"], []), et, 200)
        d50 = _ma_dir(daily.get(c["coin"], []), et, 50)
        h10 = _ma_dir(hourly.get(c["coin"], []), et, 10)
        feat.append({
            "pnl": pnlf(c), "roe": c.get("realized_pnl_pct") or 0, "side": side,
            "ma200": (d200 == side) if d200 else None,
            "ma50": (d50 == side) if d50 else None,
            "ema10": (h10 == side) if h10 else None,
            "vol": _vol_confirm(hourly.get(c["coin"], []), et),
            "regime_up": c.get("regime_at_entry") == "up",
            "not_down": c.get("regime_at_entry") != "down",
        })

    # FILTERS: name -> predicate(keep?). None feature => trade excluded (strict) for MA/EMA.
    filters = {
        "MA200": lambda f: f["ma200"] is True,
        "MA50": lambda f: f["ma50"] is True,
        "EMA10": lambda f: f["ema10"] is True,
        "VOL": lambda f: f["vol"],
        "RGM_up": lambda f: f["regime_up"],
        "RGM_ndn": lambda f: f["not_down"],
    }
    # SIZING transforms on the kept sequence
    def s_flat(rows): return [f["pnl"] for f in rows]

    def s_mckay(rows, trig=2, cut=0.4, length=3):
        out, losses, cd = [], 0, 0
        for f in rows:
            out.append(f["pnl"] * (cut if cd > 0 else 1.0))
            cd = cd - 1 if cd > 0 else 0
            if f["pnl"] <= 0:
                losses += 1
                if losses >= trig:
                    cd = length; losses = 0
            else:
                losses = 0
        return out

    def s_uncle(rows, cap_roe=-6.0):
        # Schwartz uncle point: assume we'd have exited at cap_roe; cap each loss there.
        # scale pnl proportionally when realized ROE was worse than the cap.
        out = []
        for f in rows:
            p = f["pnl"]
            if f["roe"] < cap_roe and f["roe"] != 0:
                p = p * (cap_roe / f["roe"])   # less-negative
            out.append(p)
        return out

    sizings = {"flat": s_flat, "McKay": s_mckay, "Uncle": s_uncle,
               "McKay+Uncle": lambda r: s_mckay([{"pnl": p, "roe": f["roe"]} for p, f in zip(s_uncle(r), r)])}

    def apply(filter_names, sizing_name):
        kept = [f for f in feat if all(filters[n](f) for n in filter_names)]
        if not kept:
            return None
        pnls = sizings[sizing_name](kept)
        return pnls

    def fmt(name, pnls):
        m = metrics(pnls)
        if not m:
            return None
        h = m["n"] // 2
        m1 = metrics(pnls[:h]) or {"net": 0}; m2 = metrics(pnls[h:]) or {"net": 0}
        rob = (m1["net"] > 0 and m2["net"] > 0)
        return {**m, "name": name, "oos1": m1["net"], "oos2": m2["net"], "robust": rob}

    base = fmt("BASELINE", s_flat(feat))
    print(f"\n# BASELINE: n={base['n']} net=${base['net']:.2f} win={base['win']:.0f}% "
          f"pf={base['pf']:.2f} maxDD=${base['mdd']:.2f} sharpe={base['sharpe']:+.3f} "
          f"OOS ${base['oos1']:+.2f}/${base['oos2']:+.2f}")

    # 1) ISOLATED — each filter alone (flat sizing) + each sizing alone (no filter)
    print(f"\n=== ISOLATED (one technique on) ===")
    print(f"  {'technique':16s} | {'n':>3s} | {'net':>8s} | {'win':>4s} | {'pf':>4s} | {'maxDD':>8s} | {'shrp':>6s} | OOS 1/2 | rob")
    iso = []
    for fn in filters:
        r = fmt(fn, apply([fn], "flat"))
        if r:
            iso.append(r)
    for sn in ("McKay", "Uncle", "McKay+Uncle"):
        r = fmt(sn, apply([], sn))
        if r:
            iso.append(r)
    for r in sorted(iso, key=lambda x: -x["net"]):
        print(f"  {r['name']:16s} | {r['n']:3d} | ${r['net']:7.2f} | {r['win']:3.0f}% | {r['pf']:4.2f} | "
              f"${r['mdd']:7.2f} | {r['sharpe']:+.3f} | ${r['oos1']:+5.1f}/${r['oos2']:+5.1f} | {'Y' if r['robust'] else '-'}")

    # 2) ALL COMBINATIONS of filters (0..3 deep to keep n meaningful) x each sizing
    print(f"\n=== ALL COMBINATIONS (filters x sizing) — top 25 by net, n>=15 ===")
    print(f"  {'combo':38s} | {'n':>3s} | {'net':>8s} | {'win':>4s} | {'pf':>4s} | {'maxDD':>8s} | OOS 1/2 | rob")
    combos = []
    fnames = list(filters)
    for k in range(0, 4):
        for fset in itertools.combinations(fnames, k):
            for sn in sizings:
                r = fmt("+".join(fset) + (f" [{sn}]" if sn != "flat" else ""), apply(list(fset), sn))
                if r and r["n"] >= 15:
                    combos.append(r)
    for r in sorted(combos, key=lambda x: -x["net"])[:25]:
        print(f"  {r['name']:38s} | {r['n']:3d} | ${r['net']:7.2f} | {r['win']:3.0f}% | {r['pf']:4.2f} | "
              f"${r['mdd']:7.2f} | ${r['oos1']:+5.1f}/${r['oos2']:+5.1f} | {'Y' if r['robust'] else '-'}")

    # 3) OOS-ROBUST winners (both halves positive, n>=20) ranked by Sharpe
    print(f"\n=== OOS-ROBUST (both halves +, n>=20) — ranked by Sharpe ===")
    rob = [r for r in combos if r["robust"] and r["n"] >= 20]
    for r in sorted(rob, key=lambda x: -x["sharpe"])[:15]:
        print(f"  {r['name']:38s} | {r['n']:3d} | ${r['net']:7.2f} | win {r['win']:3.0f}% | "
              f"pf {r['pf']:4.2f} | DD ${r['mdd']:7.2f} | sh {r['sharpe']:+.3f} | ${r['oos1']:+.1f}/${r['oos2']:+.1f}")
    if not rob:
        print("  (none)")
    print(f"\n# vs BASELINE net ${base['net']:.2f} / maxDD ${base['mdd']:.2f} / Sharpe {base['sharpe']:+.3f}")


if __name__ == "__main__":
    main()
