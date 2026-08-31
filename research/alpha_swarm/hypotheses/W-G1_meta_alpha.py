#!/usr/bin/env python3
"""W-G1 — Lane G meta-alpha: mine the bot's own decision exhaust (PIT by construction).

Three studies on ~/.pathia-session-log.jsonl (2026-05-27 → 2026-07-09):
  1. AI verdict calibration — does logged confidence predict forward returns at all?
  2. Gate counterfactuals — which gates saved money, which blocked winners?
  3. Execution quality — slippage vs signal price, time-of-day, fee viability by hold time.

PIT discipline: every decision is timestamped in the log BEFORE the outcome; fills are
priced at the NEXT 1h bar open after the decision ts. Costs: 12 / 25 bps round-trip.
Fabricated pytest coins (C1/C2/C3, pre-2026-07-09 conftest fix) are filtered.
Dedup: per (coin, label) episodes with a 6h refractory window.
MC null: shuffle labels 2000x, two-sided p.

Stages (cached in scratchpad):
  extract  -> episodes.json          (parse + dedup the log)
  fetch    -> candles_1h.json        (one candleSnapshot per coin, full span)
  analyze  -> printed report tables

Usage: .venv/bin/python W-G1_meta_alpha.py [extract|fetch|analyze|all]
"""
import json
import math
import os
import random
import sys
import time
from collections import Counter, defaultdict

LOG = os.path.expanduser("~/.pathia-session-log.jsonl")
SCRATCH = ("/private/tmp/claude-501/-Users-julian-dev-Documents-code-pathia/"
           "4b037816-5b27-4d2d-a13e-a6ebd68a2340/scratchpad")
EPISODES = os.path.join(SCRATCH, "wg1_episodes.json")
CANDLES = os.path.join(SCRATCH, "wg1_candles_1h.json")
FAKE_COINS = {"C1", "C2", "C3"}
DEDUP_MS = 6 * 3600 * 1000
HOUR = 3600 * 1000
COSTS_BPS = (12, 25)
HORIZONS = (1, 6, 24)


# ── stage 1: extract ─────────────────────────────────────────────────

def _classify_execute(e):
    """Map an execute event to a gate/reason class."""
    if e.get("executed"):
        return "EXECUTED"
    b = e.get("blocked_by")
    txt = (b[0] if isinstance(b, list) and b else str(b or "")) or ""
    det = str(e.get("detail") or "")
    s = (txt + " " + det).lower()
    for key, label in [
        ("max positions", "max_positions"),
        ("counter-regime", "counter_regime_conf"),
        ("runner_gate_blocked", "runner_gate"),
        ("trend_filter", "trend_filter"),
        ("short on thin market", "thin_short_floor"),
        ("volume $", "volume_floor"),
        ("below floor", "volume_floor"),
        ("insufficient_free_margin", "margin"),
        ("insufficient margin", "margin"),
        ("killswitch", "killswitch"),
        ("give-back", "giveback"),
        ("cooldown", "cooldown"),
        ("already holding", "already_holding"),
        ("shadow_mode_would_execute", "shadow_mode"),
        ("override_no_volume_confirm", "override_no_vol"),
        ("override_blocked_ai_down", "ai_down"),
        ("signal_veto", "signal_veto"),
        ("gex pin-trap", "gex_pin"),
        ("confidence", "confidence_floor"),
        ("order_failed", "order_failed"),
        ("reentry_cap", "reentry_cap"),
    ]:
        if key in s:
            return label
    return "other"


def extract():
    research, executes, preflights, dsl_exits = [], [], [], []
    with open(LOG) as f:
        for line in f:
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            ev = e.get("event")
            coin = e.get("coin")
            if coin in FAKE_COINS:
                continue
            if ev == "research":
                research.append({"ts": e["ts"], "coin": coin, "verdict": e.get("verdict"),
                                 "conf": e.get("confidence"), "px": e.get("entry_px")})
            elif ev == "execute":
                executes.append({"ts": e["ts"], "coin": coin, "side": e.get("side"),
                                 "executed": bool(e.get("executed")),
                                 "reason": _classify_execute(e),
                                 "fill_px": e.get("entry_px"), "size": e.get("size_usd")})
            elif ev == "entry_preflight":
                r = (e.get("reason") or "").split(" (")[0].split(" ($")[0]
                preflights.append({"ts": e["ts"], "coin": coin, "reason": r,
                                   "score": e.get("score")})
            elif ev == "dsl_exit":
                dsl_exits.append({"ts": e["ts"], "coin": coin, "side": e.get("side"),
                                  "lev": e.get("leverage"), "entry_px": e.get("entry_px"),
                                  "fill_px": e.get("fill_px"),
                                  "spot_pct": e.get("realized_spot_pct"),
                                  "fees_pct": e.get("fees_pct"),
                                  "executed": bool(e.get("executed")),
                                  "reason": e.get("reason")})

    def dedup(rows, key_fn):
        last = {}
        out = []
        for r in sorted(rows, key=lambda x: x["ts"]):
            k = key_fn(r)
            if r["ts"] - last.get(k, -1e18) >= DEDUP_MS:
                out.append(r)
                last[k] = r["ts"]
        return out

    research_d = dedup([r for r in research if r["verdict"] in ("LONG", "SHORT", "PASS")],
                       lambda r: (r["coin"], r["verdict"]))
    exec_d = dedup(executes, lambda r: (r["coin"], r["reason"], r.get("side")))
    pf_d = dedup(preflights, lambda r: (r["coin"], r["reason"]))

    out = {"research": research_d, "executes": exec_d, "preflights": pf_d,
           "dsl_exits": dsl_exits,           # not deduped: real fills
           "executes_raw": executes,          # for entry/exit pairing + slippage
           "research_raw": research}          # for slippage matching
    with open(EPISODES, "w") as f:
        json.dump(out, f)
    print(f"extract: research {len(research_d)} (raw {len(research)}), "
          f"exec {len(exec_d)} (raw {len(executes)}), preflight {len(pf_d)} "
          f"(raw {len(preflights)}), dsl_exits {len(dsl_exits)}")


# ── stage 2: fetch 1h candles per coin ───────────────────────────────

def fetch():
    import requests
    with open(EPISODES) as f:
        ep = json.load(f)
    coins = set()
    for k in ("research", "executes", "preflights"):
        coins.update(r["coin"] for r in ep[k] if r.get("coin"))
    t0 = min(r["ts"] for r in ep["research"]) - 2 * HOUR
    t1 = int(time.time() * 1000)
    have = {}
    if os.path.exists(CANDLES):
        with open(CANDLES) as f:
            have = json.load(f)
    sess = requests.Session()
    done = 0
    for coin in sorted(coins):
        if coin in have:
            continue
        payload = {"type": "candleSnapshot",
                   "req": {"coin": coin, "interval": "1h",
                           "startTime": t0, "endTime": t1}}
        raw = None
        for attempt in range(5):
            try:
                r = sess.post("https://api.hyperliquid.xyz/info", json=payload, timeout=15)
                if r.status_code == 429:
                    time.sleep(2 * (attempt + 1))
                    continue
                r.raise_for_status()
                raw = r.json()
                break
            except Exception:
                time.sleep(1.5 * (attempt + 1))
        if isinstance(raw, list):
            have[coin] = [[int(c["t"]), float(c["o"]), float(c["c"])] for c in raw]
        else:
            have[coin] = []
        done += 1
        if done % 20 == 0:
            with open(CANDLES, "w") as f:
                json.dump(have, f)
            print(f"fetch: {done} coins done ({coin}: {len(have[coin])} bars)")
        time.sleep(0.25)
    with open(CANDLES, "w") as f:
        json.dump(have, f)
    empty = [c for c in coins if not have.get(c)]
    print(f"fetch: {len(coins)} coins, {len(empty)} empty: {sorted(empty)[:20]}")


# ── stage 3: analyze ─────────────────────────────────────────────────

def _fwd(candles, ts, hours):
    """Fill at next 1h open strictly after ts; return (fill_px, horizon_px) or None."""
    lo, hi = 0, len(candles)
    while lo < hi:
        mid = (lo + hi) // 2
        if candles[mid][0] <= ts:
            lo = mid + 1
        else:
            hi = mid
    i = lo
    j = i + hours
    if i >= len(candles) or j >= len(candles):
        return None
    return candles[i][1], candles[j][1]


def _ret(candles, ts, hours, side):
    fp = _fwd(candles, ts, hours)
    if not fp or fp[0] <= 0:
        return None
    sgn = 1.0 if side == "long" else -1.0
    return sgn * (fp[1] / fp[0] - 1.0) * 100.0


def _stats(vals):
    n = len(vals)
    if n == 0:
        return dict(n=0, mean=float("nan"), med=float("nan"), win=float("nan"))
    m = sum(vals) / n
    sv = sorted(vals)
    med = sv[n // 2] if n % 2 else (sv[n // 2 - 1] + sv[n // 2]) / 2
    win = sum(1 for v in vals if v > 0) / n
    return dict(n=n, mean=m, med=med, win=win)


def _mc_diff_p(a, b, iters=2000, seed=7):
    """Two-sided MC p for mean(a)-mean(b) under label shuffle."""
    if not a or not b:
        return float("nan")
    obs = sum(a) / len(a) - sum(b) / len(b)
    pool = a + b
    na = len(a)
    rng = random.Random(seed)
    hits = 0
    for _ in range(iters):
        rng.shuffle(pool)
        d = sum(pool[:na]) / na - sum(pool[na:]) / (len(pool) - na)
        if abs(d) >= abs(obs):
            hits += 1
    return hits / iters


def analyze():
    with open(EPISODES) as f:
        ep = json.load(f)
    with open(CANDLES) as f:
        candles = json.load(f)

    def rets(rows, side_fn, hours):
        out = []
        for r in rows:
            c = candles.get(r["coin"])
            if not c:
                continue
            side = side_fn(r)
            if side is None:
                continue
            v = _ret(c, r["ts"], hours, side)
            if v is not None:
                out.append(v)
        return out

    print("=" * 78)
    print("1. AI VERDICT CALIBRATION (deduped episodes, fill = next 1h open, gross %)")
    print("=" * 78)
    bands = [("0.62-0.70", 0.62, 0.70), ("0.70-0.80", 0.70, 0.80), ("0.80+", 0.80, 9.9),
             ("<0.62", -1, 0.62)]
    for verdict, side in (("LONG", "long"), ("SHORT", "short")):
        rows = [r for r in ep["research"] if r["verdict"] == verdict]
        print(f"\n-- {verdict} verdicts (episodes n={len(rows)}) --")
        print(f"{'band':>10} {'h':>3} {'n':>5} {'mean%':>8} {'med%':>8} {'win':>6} "
              f"{'net12':>8} {'net25':>8}")
        for name, lo, hi in bands:
            sub = [r for r in rows if lo <= (r["conf"] or 0) < hi]
            for h in HORIZONS:
                s = _stats(rets(sub, lambda r: side, h))
                if s["n"]:
                    print(f"{name:>10} {h:>3} {s['n']:>5} {s['mean']:>8.3f} "
                          f"{s['med']:>8.3f} {s['win']:>6.2f} "
                          f"{s['mean']-0.12:>8.3f} {s['mean']-0.25:>8.3f}")
        # MC: does high conf beat low conf at 24h?
        hi_r = rets([r for r in rows if (r["conf"] or 0) >= 0.75], lambda r: side, 24)
        lo_r = rets([r for r in rows if 0 < (r["conf"] or 0) < 0.70], lambda r: side, 24)
        if hi_r and lo_r:
            d = sum(hi_r)/len(hi_r) - sum(lo_r)/len(lo_r)
            print(f"  MC null conf>=0.75 (n={len(hi_r)}) vs <0.70 (n={len(lo_r)}) @24h: "
                  f"diff={d:+.3f}% p={_mc_diff_p(hi_r, lo_r):.3f}")

    # PASS counterfactual: long-assumed forward return of PASSes vs executed longs
    print("\n-- PASS counterfactual (long-assumed) vs executed trades --")
    passes = [r for r in ep["research"] if r["verdict"] == "PASS"]
    exec_long = [r for r in ep["executes"] if r["reason"] == "EXECUTED" and r["side"] == "long"]
    exec_short = [r for r in ep["executes"] if r["reason"] == "EXECUTED" and r["side"] == "short"]
    for h in HORIZONS:
        p = _stats(rets(passes, lambda r: "long", h))
        el = _stats(rets(exec_long, lambda r: "long", h))
        es = _stats(rets(exec_short, lambda r: "short", h))
        print(f"  h={h:>2}: PASS-as-long n={p['n']} mean={p['mean']:+.3f}% | "
              f"exec LONG n={el['n']} mean={el['mean']:+.3f}% | "
              f"exec SHORT n={es['n']} mean={es['mean']:+.3f}%")
    pl = rets(passes, lambda r: "long", 24)
    el24 = rets(exec_long, lambda r: "long", 24)
    if pl and el24:
        print(f"  MC PASS-as-long vs exec-long @24h: "
              f"diff={sum(pl)/len(pl)-sum(el24)/len(el24):+.3f}% p={_mc_diff_p(pl, el24):.3f}")

    print()
    print("=" * 78)
    print("2. GATE COUNTERFACTUALS (execute-stage blocks have a KNOWN side)")
    print("=" * 78)
    by_gate = defaultdict(list)
    for r in ep["executes"]:
        by_gate[r["reason"]].append(r)
    base24 = rets(by_gate.get("EXECUTED", []), lambda r: r["side"], 24)
    bmean = sum(base24) / len(base24) if base24 else float("nan")
    print(f"\nBaseline EXECUTED @24h: n={len(base24)} mean={bmean:+.3f}% (gross)")
    print(f"{'gate':>20} {'n24':>5} {'mean6h%':>9} {'mean24h%':>9} {'win24':>6} "
          f"{'net12':>8} {'vs exec':>8} {'p(MC)':>6}  verdict")
    rows_out = []
    for gate, rows in sorted(by_gate.items(), key=lambda kv: -len(kv[1])):
        if gate == "EXECUTED":
            continue
        sided = [r for r in rows if r.get("side") in ("long", "short")]
        r6 = rets(sided, lambda r: r["side"], 6)
        r24 = rets(sided, lambda r: r["side"], 24)
        if len(r24) < 5:
            continue
        s6, s24 = _stats(r6), _stats(r24)
        p = _mc_diff_p(r24, base24) if base24 else float("nan")
        diff = s24["mean"] - bmean
        verdict = ("DESTROYS (blocked winners)" if s24["mean"] - 0.12 > max(bmean, 0) + 0.05
                   else "SAVES (blocked losers)" if s24["mean"] - 0.12 < min(bmean, 0) - 0.05
                   else "neutral")
        print(f"{gate:>20} {s24['n']:>5} {s6['mean']:>9.3f} {s24['mean']:>9.3f} "
              f"{s24['win']:>6.2f} {s24['mean']-0.12:>8.3f} {diff:>+8.3f} {p:>6.3f}  {verdict}")
        rows_out.append((gate, s24, diff, p, verdict))

    print("\n-- Pre-research preflight gates (NO side logged; long-assumed + |move|) --")
    pf = defaultdict(list)
    for r in ep["preflights"]:
        pf[r["reason"]].append(r)
    print(f"{'gate':>32} {'n24':>5} {'long24h%':>9} {'win':>6} {'|move|24h%':>10}")
    for gate, rows in sorted(pf.items(), key=lambda kv: -len(kv[1])):
        r24 = rets(rows, lambda r: "long", 24)
        if len(r24) < 5:
            continue
        s = _stats(r24)
        amoves = [abs(v) for v in r24]
        print(f"{gate:>32} {s['n']:>5} {s['mean']:>9.3f} {s['win']:>6.2f} "
              f"{sum(amoves)/len(amoves):>10.3f}")

    print()
    print("=" * 78)
    print("3. EXECUTION QUALITY")
    print("=" * 78)
    # 3a. slippage: fill vs most recent research signal px within 15 min
    res_by_coin = defaultdict(list)
    for r in ep["research_raw"]:
        if r["verdict"] in ("LONG", "SHORT") and r.get("px"):
            res_by_coin[r["coin"]].append(r)
    for v in res_by_coin.values():
        v.sort(key=lambda x: x["ts"])
    slips = []  # (bps, hour_utc, ts)
    for e in ep["executes_raw"]:
        if not e["executed"] or not e.get("fill_px"):
            continue
        cands = res_by_coin.get(e["coin"], [])
        best = None
        for r in cands:
            if 0 <= e["ts"] - r["ts"] <= 15 * 60 * 1000:
                best = r
        if not best or not best["px"]:
            continue
        sgn = 1.0 if e["side"] == "long" else -1.0
        bps = sgn * (e["fill_px"] / best["px"] - 1.0) * 1e4
        hour = int((e["ts"] // HOUR) % 24)
        slips.append((bps, hour))
    if slips:
        vals = [s[0] for s in slips]
        s = _stats(vals)
        print(f"\n3a. Entry slippage vs signal px (n={s['n']}): mean={s['mean']:+.1f}bps "
              f"med={s['med']:+.1f}bps  (positive = paid worse than signal)")
        byh = defaultdict(list)
        for bps, hour in slips:
            byh[hour].append(bps)
        worst = sorted(byh.items(), key=lambda kv: -abs(sum(kv[1])/len(kv[1])))[:6]
        print("    worst hours (UTC):",
              ", ".join(f"{h:02d}h {sum(v)/len(v):+.0f}bps(n={len(v)})" for h, v in worst))

    # 3b. round-trips: pair execute(true) -> next dsl_exit same coin+side
    entries = defaultdict(list)
    for e in ep["executes_raw"]:
        if e["executed"] and e.get("side"):
            entries[(e["coin"], e["side"])].append(e)
    for v in entries.values():
        v.sort(key=lambda x: x["ts"])
    rts = []
    used = set()
    for x in sorted(ep["dsl_exits"], key=lambda r: r["ts"]):
        if not x["executed"] or x.get("spot_pct") is None:
            continue
        key = (x["coin"], x["side"])
        best = None
        for e in entries.get(key, []):
            if e["ts"] < x["ts"] and (id(e) not in used):
                best = e
        if best is None:
            continue
        used.add(id(best))
        hold_min = (x["ts"] - best["ts"]) / 60000.0
        lev = x.get("lev") or 1
        fee_spot = (x.get("fees_pct") or 0) / lev
        rts.append(dict(hold=hold_min, spot=x["spot_pct"], fee_spot=fee_spot,
                        net=x["spot_pct"] - 0,  # spot_pct is gross move
                        hour=int((best["ts"] // HOUR) % 24)))
    print(f"\n3b. Round-trips paired (n={len(rts)}) — fee-viability by holding time")
    buckets = [(0, 15), (15, 60), (60, 180), (180, 360), (360, 1440), (1440, 1e9)]
    print(f"{'hold':>12} {'n':>5} {'gross%':>8} {'fee%':>7} {'net%':>8} {'win(net)':>8} "
          f"{'net@12bps':>9} {'net@25bps':>9}")
    for lo, hi in buckets:
        sub = [r for r in rts if lo <= r["hold"] < hi]
        if not sub:
            continue
        g = sum(r["spot"] for r in sub) / len(sub)
        fee = sum(r["fee_spot"] for r in sub) / len(sub)
        net = [r["spot"] - r["fee_spot"] for r in sub]
        winn = sum(1 for v in net if v > 0) / len(net)
        lab = f"{int(lo)}-{int(hi) if hi < 1e9 else 'inf'}m"
        print(f"{lab:>12} {len(sub):>5} {g:>8.3f} {fee:>7.3f} {sum(net)/len(net):>8.3f} "
              f"{winn:>8.2f} {g-0.12:>9.3f} {g-0.25:>9.3f}")
    # entry-hour PnL
    byh = defaultdict(list)
    for r in rts:
        byh[r["hour"]].append(r["spot"] - r["fee_spot"])
    hs = sorted(byh.items(), key=lambda kv: sum(kv[1])/len(kv[1]))
    if hs:
        print("    worst entry hours (net%):",
              ", ".join(f"{h:02d}h {sum(v)/len(v):+.2f}(n={len(v)})" for h, v in hs[:4]))
        print("    best  entry hours (net%):",
              ", ".join(f"{h:02d}h {sum(v)/len(v):+.2f}(n={len(v)})" for h, v in hs[-4:]))


def robust():
    """Half-splits, coin concentration, and matched-coin random-time nulls for the
    headline claims (controls tape beta + coin mix; overlap caveat still applies)."""
    with open(EPISODES) as f:
        ep = json.load(f)
    with open(CANDLES) as f:
        candles = json.load(f)

    def halves(rows, side_fn, label):
        vals = []
        for r in sorted(rows, key=lambda x: x["ts"]):
            c = candles.get(r["coin"])
            if not c:
                continue
            v = _ret(c, r["ts"], 24, side_fn(r))
            if v is not None:
                vals.append((r["ts"], v, r["coin"]))
        if not vals:
            return
        mid = vals[len(vals) // 2][0]
        h1 = [v[1] for v in vals if v[0] < mid]
        h2 = [v[1] for v in vals if v[0] >= mid]
        cc = Counter(v[2] for v in vals)
        top = cc.most_common(1)[0][0]
        rest = [v[1] for v in vals if v[2] != top]
        print(f"{label}: n={len(vals)} H1={sum(h1)/len(h1):+.3f}% H2={sum(h2)/len(h2):+.3f}% "
              f"| excl-{top}: {sum(rest)/len(rest):+.3f}% | top={cc.most_common(3)}")

    def matched_null(rows, side, label, iters=1000, seed=3):
        obs, per = [], []
        for r in rows:
            sd = side if isinstance(side, str) else r["side"]
            c = candles.get(r["coin"])
            if not c:
                continue
            v = _ret(c, r["ts"], 24, sd)
            if v is not None:
                obs.append(v)
                per.append((r["coin"], sd))
        om = sum(obs) / len(obs)
        rng = random.Random(seed)
        ge = 0
        nsum = 0.0
        for _ in range(iters):
            tot = n = 0
            for coin, sd in per:
                c = candles[coin]
                if len(c) < 30:
                    continue
                i = rng.randrange(0, len(c) - 25)
                tot += (1 if sd == "long" else -1) * (c[i + 24][1] / c[i][1] - 1) * 100
                n += 1
            nm = tot / n
            nsum += nm
            if nm >= om:
                ge += 1
        print(f"{label}: n={len(obs)} obs={om:+.3f}% null={nsum/iters:+.3f}% "
              f"excess={om - nsum/iters:+.3f}% p={ge/iters:.3f}")

    longs78 = [r for r in ep["research"] if r["verdict"] == "LONG" and 0.70 <= (r["conf"] or 0) < 0.80]
    shorts7 = [r for r in ep["research"] if r["verdict"] == "SHORT" and (r["conf"] or 0) >= 0.70]
    tsf = [r for r in ep["executes"] if r["reason"] == "thin_short_floor" and r.get("side") == "short"]
    crc = [r for r in ep["executes"] if r["reason"] == "counter_regime_conf" and r.get("side")]
    rg = [r for r in ep["executes"] if r["reason"] == "runner_gate" and r.get("side")]
    halves(longs78, lambda r: "long", "LONG 0.70-0.80 @24h")
    halves(shorts7, lambda r: "short", "SHORT >=0.70 @24h")
    halves(tsf, lambda r: "short", "thin_short_floor @24h")
    halves(crc, lambda r: r["side"], "counter_regime @24h")
    halves(rg, lambda r: r["side"], "runner_gate @24h")
    matched_null(shorts7, "short", "SHORT >=0.70 vs matched null")
    matched_null(tsf, "short", "thin_short_floor vs matched null")
    matched_null(longs78, "long", "LONG 0.70-0.80 vs matched null")


if __name__ == "__main__":
    stage = sys.argv[1] if len(sys.argv) > 1 else "all"
    if stage in ("extract", "all"):
        extract()
    if stage in ("fetch", "all"):
        fetch()
    if stage in ("analyze", "all"):
        analyze()
    if stage in ("robust", "all"):
        robust()
