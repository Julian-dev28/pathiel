#!/usr/bin/env python3
"""W-X4 — meme_exclusion_overlay. PRE-REGISTERED before first run (swarm rules).

ORIGIN: W-X3 traced the W-X2-B "+1.50% within-L1" side-finding to MEME-EXCLUSION, not
liquidity (mechanical liquidity ranks pull FARTCOIN/PUMP/DOGE into the book and the EV
collapses). This cell tests the exclusion directly as an overlay ON the live book.
Coordinator-approved follow-up, same session, same caches, same exact-replica engine.

DATA: W-X2_cache_daily.json (2026-07-20, 401 daily bars, top-50 crypto by dayNtlVlm).
Shared W-X2 engine imported (decide bar i, fill open[i+1], exit open[i+1+H],
non-overlapping, equal weight, >=61-bar eligibility, start=66). seed=42, no network.

BOOKS (all: pct_k(14) ranking, k=4/leg, H=10 — the live recipe verbatim, per
.agent-config.json 2026-07-20; residual flag ignored under pct_k; ungated sim,
identical to the W-X3 live comparator which reproduced W-X2-D to the digit)
  BASELINE : full top-50 eligible set (the live book as-is).
  PRIMARY  : SECTOR_MAP MEME names (declared in W-X2_xs_widening.py BEFORE cell B ran)
             dropped from the eligible set BEFORE ranking — both legs. Unmapped names
             (ACE, SUSHI, GRAM, AZTEC) STAY eligible: only declared MEME is dropped.
  SENSITIVITY (asymmetric): memes excluded from the LONG leg only — longs = top-4 of the
             meme-free scored list, shorts = bottom-4 of the FULL scored list (must equal
             the baseline short leg by construction; asserted). Mirrors the measured
             asymmetry (meme shorts have been earning live; meme longs are the suspect).

COSTS / NULL / OOS — identical to W-X2/W-X3: tiers 0/12/25/50 bps RT turnover-scaled;
OOS = first/second half of rebalances; null = 2000 seeded matched random books
(diagnostic only here — the decision gate is dominance vs baseline, not the null).
Asymmetric null: k longs drawn from the meme-free eligible pool, k shorts from the full
pool minus the drawn longs (matched to the construction).

DECISION GATE (pre-registered — this is a MODIFICATION of the live book, not a new book,
so the bar is STRICT DOMINANCE, not correlation):
  WIRE-WORTHY iff the variant beats BASELINE on net25 EV AND net25 Sharpe-like
  (mean/pstdev) in BOTH halves. Anything less = keep the live book untouched.
  Applied independently to PRIMARY and to the asymmetric sensitivity.

DECOMPOSITION (pre-registered — the verdict must show WHERE the net comes from):
  In the BASELINE book, each long leg contributes +0.5*fwd/k and each short leg
  -0.5*fwd/k to book EV (sums exactly to EV; asserted). Report meme-leg contributions:
  (a) forfeited meme-SHORT EV (positive contributions the exclusion gives up),
  (b) avoided meme-LONG bleed (negative contributions the exclusion escapes),
  per rebalance and per coin. Then the realized overlay-minus-baseline net25 delta,
  split exactly into long-leg delta + short-leg delta + fee delta (identity asserted),
  full + both halves. For the asymmetric variant the short-leg delta must be 0.

Read-only research. No live wiring. One script, one findings file.
"""
from __future__ import annotations

import importlib.util
import os
import random
import statistics
import sys
from typing import Callable, Dict, List, Optional, Set, Tuple

REPO = "/Users/julian_dev/Documents/code/hermes-trader"
HYP = os.path.join(REPO, "research", "alpha_swarm", "hypotheses")

_spec = importlib.util.spec_from_file_location("wx2", os.path.join(HYP, "W-X2_xs_widening.py"))
wx2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wx2)

T, O, H_, L_, C, V = wx2.T, wx2.O, wx2.H_, wx2.L_, wx2.C, wx2.V

LIVE_K, LIVE_H = 4, 10
MEMES: Set[str] = {c for c, s in wx2.SECTOR_MAP.items() if s == "MEME"}


# ---------------------------------------------------------------------------- asym book
def run_book_asym(w: "wx2.World", elig_long_fn: Callable[[int], List[str]],
                  elig_short_fn: Callable[[int], List[str]],
                  score_fn: Callable[[str, int], Optional[float]],
                  k: int, hold: int, start: int = 66) -> dict:
    """Long leg ranked within elig_long, short leg within elig_short (full universe).
    Same fills/turnover/EV conventions as wx2.run_book."""
    recs = []
    prev_book: set = set()
    i = start
    while i + 1 + hold < len(w.days):
        el, es = elig_long_fn(i), elig_short_fn(i)
        fwd_all: Dict[str, float] = {}
        scores: Dict[str, float] = {}
        for c in set(el) | set(es):
            b1, b2 = w.bar(c, i + 1), w.bar(c, i + 1 + hold)
            if b1 is None or b2 is None or b1[O] <= 0:
                continue
            s = score_fn(c, i)
            if s is None:
                continue
            scores[c] = s
            fwd_all[c] = b2[O] / b1[O] - 1.0
        pool_l = sorted((c for c in el if c in scores), key=lambda c: -scores[c])
        pool_s = sorted((c for c in es if c in scores), key=lambda c: -scores[c])
        if len(pool_l) < k or len(pool_s) < 2 * k:
            i += hold
            continue
        longs = pool_l[:k]
        shorts = pool_s[-k:]
        assert not set(longs) & set(shorts), (longs, shorts)
        ev = 0.5 * statistics.mean([fwd_all[c] for c in longs]) \
            - 0.5 * statistics.mean([fwd_all[c] for c in shorts])
        book = {(c, "L") for c in longs} | {(c, "S") for c in shorts}
        turn = 1.0 if not prev_book else len(book - prev_book) / len(book)
        prev_book = book
        recs.append({"i": i, "t": w.days[i], "ev": ev, "turnover": turn,
                     "longs": longs, "shorts": shorts,
                     "elig": [c for c in pool_s], "elig_long": [c for c in pool_l],
                     "fwd": fwd_all})
        i += hold
    return {"recs": recs}


def null_p_asym(recs: List[dict], k: int, obs_gross: float,
                n_null: int = wx2.N_NULL, seed: int = wx2.SEED) -> float:
    """Matched null for the asym construction: k longs from the long pool, k shorts from
    the full pool minus the drawn longs. Same dates/pools/fills."""
    rng = random.Random(seed)
    ge = 0
    for _ in range(n_null):
        tot = 0.0
        for r in recs:
            lo = rng.sample(r["elig_long"], k)
            sh = rng.sample([c for c in r["elig"] if c not in lo], k)
            tot += 0.5 * statistics.mean([r["fwd"][c] for c in lo]) \
                - 0.5 * statistics.mean([r["fwd"][c] for c in sh])
        if tot / len(recs) >= obs_gross:
            ge += 1
    return ge / n_null


# ---------------------------------------------------------------------------- stats
def net25_series(recs: List[dict]) -> List[float]:
    return [r["ev"] - 0.0025 * r["turnover"] for r in recs]


def ev_sh(xs: List[float]) -> Tuple[float, float]:
    return statistics.mean(xs), statistics.mean(xs) / (statistics.pstdev(xs) + 1e-12)


def dominance_row(name: str, recs: List[dict]) -> dict:
    s = net25_series(recs)
    h = len(s) // 2
    (ef, sf), (e1, s1), (e2, s2) = ev_sh(s), ev_sh(s[:h]), ev_sh(s[h:])
    return {"name": name, "full": (ef, sf), "h1": (e1, s1), "h2": (e2, s2), "n": len(s)}


def fmt_dom(r: dict) -> str:
    return (f"{r['name']:<34} n={r['n']:<3}"
            f" FULL {100*r['full'][0]:+.2f}%/Sh {r['full'][1]:+.3f}"
            f"  h1 {100*r['h1'][0]:+.2f}%/Sh {r['h1'][1]:+.3f}"
            f"  h2 {100*r['h2'][0]:+.2f}%/Sh {r['h2'][1]:+.3f}")


def strict_dominance(var: dict, base: dict) -> Tuple[bool, List[str]]:
    checks = []
    ok = True
    for half in ("h1", "h2"):
        for j, metric in ((0, "EV"), (1, "Sharpe")):
            win = var[half][j] > base[half][j]
            ok &= win
            checks.append(f"{half} {metric}: {'WIN' if win else 'LOSE'} "
                          f"({var[half][j]:+.4f} vs {base[half][j]:+.4f})")
    return ok, checks


# ---------------------------------------------------------------------------- decomposition
def meme_decomposition(recs: List[dict], k: int, memes: Set[str]) -> dict:
    """Per-leg meme contributions inside a book. Contribution identity asserted."""
    long_c, short_c = [], []
    per_coin: Dict[str, float] = {}
    n_long = n_short = 0
    for r in recs:
        contrib = 0.0
        lc = sc = 0.0
        for c in r["longs"]:
            x = 0.5 * r["fwd"][c] / k
            contrib += x
            if c in memes:
                lc += x
                per_coin[c + " L"] = per_coin.get(c + " L", 0.0) + x
                n_long += 1
        for c in r["shorts"]:
            x = -0.5 * r["fwd"][c] / k
            contrib += x
            if c in memes:
                sc += x
                per_coin[c + " S"] = per_coin.get(c + " S", 0.0) + x
                n_short += 1
        assert abs(contrib - r["ev"]) < 1e-12, (contrib, r["ev"])
        long_c.append(lc)
        short_c.append(sc)
    return {"long_mean": statistics.mean(long_c), "short_mean": statistics.mean(short_c),
            "long_total": sum(long_c), "short_total": sum(short_c),
            "n_long_legs": n_long, "n_short_legs": n_short, "per_coin": per_coin,
            "long_series": long_c, "short_series": short_c}


def delta_split(var_recs: List[dict], base_recs: List[dict]) -> dict:
    """overlay-minus-baseline net25 delta, split exactly into long/short/fee deltas."""
    mb = {r["t"]: r for r in base_recs}
    d_net, d_long, d_short, d_fee = [], [], [], []
    for rv in var_recs:
        rb = mb.get(rv["t"])
        if rb is None:
            continue
        dl = 0.5 * (statistics.mean([rv["fwd"][c] for c in rv["longs"]])
                    - statistics.mean([rb["fwd"][c] for c in rb["longs"]]))
        ds = -0.5 * (statistics.mean([rv["fwd"][c] for c in rv["shorts"]])
                     - statistics.mean([rb["fwd"][c] for c in rb["shorts"]]))
        df = -0.0025 * (rv["turnover"] - rb["turnover"])
        dn = (rv["ev"] - 0.0025 * rv["turnover"]) - (rb["ev"] - 0.0025 * rb["turnover"])
        assert abs(dn - (dl + ds + df)) < 1e-12
        d_net.append(dn)
        d_long.append(dl)
        d_short.append(ds)
        d_fee.append(df)
    h = len(d_net) // 2
    return {"n": len(d_net),
            "net": (statistics.mean(d_net), statistics.mean(d_net[:h]), statistics.mean(d_net[h:])),
            "long": (statistics.mean(d_long), statistics.mean(d_long[:h]), statistics.mean(d_long[h:])),
            "short": (statistics.mean(d_short), statistics.mean(d_short[:h]), statistics.mean(d_short[h:])),
            "fee": (statistics.mean(d_fee), statistics.mean(d_fee[:h]), statistics.mean(d_fee[h:]))}


def fmt_delta(name: str, d: dict) -> List[str]:
    out = [f"-- delta {name} (variant - baseline, %/rebal, full/h1/h2) --"]
    for key in ("net", "long", "short", "fee"):
        f, a, b = d[key]
        out.append(f"  {key:<6} {100*f:+.3f}  ({100*a:+.3f} / {100*b:+.3f})")
    return out


# ---------------------------------------------------------------------------- selftest
def selftest() -> None:
    day0 = 1_700_000_000_000

    def mk(drift, days=200, vol=1000.0, px0=100.0):
        rows, px = [], px0
        for t in range(days):
            o = px
            px = px * (1 + drift)
            rows.append([day0 + t * 86_400_000, o, max(o, px), min(o, px), px, vol])
        return rows

    # MUP: meme up-trender (strongest long), MDN: meme down-trender (strongest short)
    candles = {"MUP": mk(+0.02), "MDN": mk(-0.02)}
    for j in range(4):
        candles[f"U{j}"] = mk(+0.01)
        candles[f"D{j}"] = mk(-0.01)
    for j in range(4):
        candles[f"F{j}"] = mk(0.0)
    w = wx2.World(candles, sorted(candles))
    coins = sorted(candles)
    memes = {"MUP", "MDN"}
    score = lambda c, i: wx2.trailing_ret(w, c, i, 7)
    k = 2

    base = wx2.run_book(w, wx2.elig_std(w, coins, 61), score, k, 5)
    over = wx2.run_book(w, wx2.elig_std(w, [c for c in coins if c not in memes], 61), score, k, 5)
    asym = run_book_asym(w, wx2.elig_std(w, [c for c in coins if c not in memes], 61),
                         wx2.elig_std(w, coins, 61), score, k, 5)
    assert base["recs"] and over["recs"] and asym["recs"]
    for rb, ro, ra in zip(base["recs"], over["recs"], asym["recs"]):
        assert "MUP" in rb["longs"] and "MDN" in rb["shorts"]          # baseline trades memes
        assert set(ro["longs"]) <= {f"U{j}" for j in range(4)}          # overlay drops both
        assert set(ro["shorts"]) <= {f"D{j}" for j in range(4)}
        assert set(ra["longs"]) <= {f"U{j}" for j in range(4)}          # asym: meme-free longs
        assert ra["shorts"] == rb["shorts"]                             # asym: baseline shorts

    # decomposition identity + signs: MUP long contributes +, MDN short contributes +
    dec = meme_decomposition(base["recs"], k, memes)
    assert dec["long_mean"] > 0 and dec["short_mean"] > 0
    assert dec["n_long_legs"] == len(base["recs"]) and dec["n_short_legs"] == len(base["recs"])
    # overlay forfeits both meme legs -> long and short deltas negative; identity asserted inside
    d = delta_split(over["recs"], base["recs"])
    assert d["long"][0] < 0 and d["short"][0] < 0
    # asym forfeits only the long meme leg; short delta exactly 0
    da = delta_split(asym["recs"], base["recs"])
    assert da["short"] == (0.0, 0.0, 0.0) and da["long"][0] < 0
    # asym null: perfect selection beats matched random draws
    evs = [r["ev"] for r in asym["recs"]]
    assert null_p_asym(asym["recs"], k, statistics.mean(evs)) < 0.01
    # dominance helper: a book strictly better in both halves must PASS, itself must FAIL
    rb_ = dominance_row("b", base["recs"])
    shifted = [dict(r, ev=r["ev"] + 0.01) for r in base["recs"]]
    ok, _ = strict_dominance(dominance_row("s", shifted), rb_)
    assert ok
    ok_self, _ = strict_dominance(rb_, rb_)
    assert not ok_self
    print("W-X4 selftest OK: baseline/overlay/asym selection exact, asym shorts == baseline "
          "shorts, decomposition identity + signs, asym null, dominance gate")


# ---------------------------------------------------------------------------- main
def main() -> None:
    if "--selftest" in sys.argv:
        selftest()
        return
    d = wx2.load_cache()
    w, coins = wx2.make_crypto_world(d)
    import datetime as dt
    last = max(rows[-1][T] for rows in d["candles"].values() if rows)
    print(f"cache: {len(d['candles'])} coins, last bar "
          f"{dt.datetime.fromtimestamp(last/1000, dt.timezone.utc):%Y-%m-%d}")
    in_univ = sorted(set(coins) & MEMES)
    print(f"declared MEME in top-50 ({len(in_univ)}): {', '.join(in_univ)}")

    pk14 = lambda c, i: wx2.pctk(w, c, i, 14)
    no_meme = [c for c in coins if c not in MEMES]

    base = wx2.run_book(w, wx2.elig_std(w, coins, 61), pk14, LIVE_K, LIVE_H)
    over = wx2.run_book(w, wx2.elig_std(w, no_meme, 61), pk14, LIVE_K, LIVE_H)
    asym = run_book_asym(w, wx2.elig_std(w, no_meme, 61), wx2.elig_std(w, coins, 61),
                         pk14, LIVE_K, LIVE_H)
    # construction check: asym short leg is the baseline short leg
    for ra, rb in zip(asym["recs"], base["recs"]):
        assert ra["t"] == rb["t"] and ra["shorts"] == rb["shorts"]

    print("\n===== books (engine summary, net25 etc.) =====")
    s_base = wx2.summarize_book(base, LIVE_K, LIVE_H, "BASELINE live pct_k14 k4 H10")
    print(wx2.fmt(s_base))
    ok = abs(s_base["gross_pct"] - 3.46) <= 0.01 and abs(s_base["net25"] - 3.25) <= 0.01
    print(f"  reproduction check vs W-X2-D/W-X3 (gross +3.46 / net25 +3.25): "
          f"{'OK' if ok else 'MISMATCH — investigate'}")
    print(wx2.fmt(wx2.summarize_book(over, LIVE_K, LIVE_H, "PRIMARY meme-excluded both legs")))
    s_asym = wx2.summarize_book(asym, LIVE_K, LIVE_H, "SENS long-leg-only exclusion",
                                with_null=False)
    evs = [r["ev"] for r in asym["recs"]]
    s_asym["null_p_gross"] = round(null_p_asym(asym["recs"], LIVE_K,
                                               statistics.mean(evs)), 4)
    print(wx2.fmt(s_asym))

    print("\n===== dominance gate (net25 EV AND Sharpe, BOTH halves) =====")
    rb = dominance_row("BASELINE", base["recs"])
    ro = dominance_row("PRIMARY meme-excluded", over["recs"])
    ra = dominance_row("SENS long-only exclusion", asym["recs"])
    for r in (rb, ro, ra):
        print(fmt_dom(r))
    for name, var in (("PRIMARY", ro), ("SENS long-only", ra)):
        ok, checks = strict_dominance(var, rb)
        print(f"  {name}: {'STRICT DOMINANCE -> WIRE-WORTHY' if ok else 'FAILS -> keep live book'}")
        for c_ in checks:
            print(f"    {c_}")

    print("\n===== decomposition: meme legs INSIDE the baseline book =====")
    dec = meme_decomposition(base["recs"], LIVE_K, MEMES)
    n = len(base["recs"])
    print(f"  meme LONG legs : {dec['n_long_legs']} legs over {n} rebals, "
          f"contribution {100*dec['long_mean']:+.3f}%/rebal (total {100*dec['long_total']:+.1f}pp)"
          f"  -> exclusion {'avoids this bleed' if dec['long_mean'] < 0 else 'FORFEITS this gain'}")
    print(f"  meme SHORT legs: {dec['n_short_legs']} legs over {n} rebals, "
          f"contribution {100*dec['short_mean']:+.3f}%/rebal (total {100*dec['short_total']:+.1f}pp)"
          f"  -> exclusion {'FORFEITS this gain' if dec['short_mean'] > 0 else 'avoids this bleed'}")
    top = sorted(dec["per_coin"].items(), key=lambda x: -abs(x[1]))[:10]
    print("  per-coin totals (pp of book EV): "
          + ", ".join(f"{c} {100*v:+.1f}" for c, v in top))

    print()
    for line in fmt_delta("PRIMARY", delta_split(over["recs"], base["recs"])):
        print(line)
    for line in fmt_delta("SENS long-only", delta_split(asym["recs"], base["recs"])):
        print(line)


if __name__ == "__main__":
    main()
