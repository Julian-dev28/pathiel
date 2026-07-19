#!/usr/bin/env python
"""W-P3: score the LLM's LONG/SHORT/SKIP signs through the W-P1 harness.

Reads W-P3_cache_events.json (the verified 308-event rebuild — returns are
asserted identical to W-P1_results.json) + W-P3_results.json["llm"]. Writes
scores back into W-P3_results.json["scores"] and prints the report.

Spec (pre-registered, findings/W-P3_llm_signed_edgar.md):
  EV25 per event = sign * r - 25bps; SKIP excluded (skip-rate reported).
  Cells: POST (acc >= 2026-02-01, VERDICT cell) + its halves; ALL + PRE as
  context; diagnostics POST_8K, POST_ITEMS(1.01/2.02/8.01), POST_CONV>=0.6.
  Null: 2000x sign-permutation over the same non-SKIP events (per cell &
  horizon), one-sided MC p toward the observed mean. Seed fixed.
"""
from __future__ import annotations

import json
import os
import random
import statistics

HERE = os.path.dirname(os.path.abspath(__file__))
EV_CACHE = os.path.join(HERE, "W-P3_cache_events.json")
RESULTS = os.path.join(HERE, "W-P3_results.json")

COST_RT = 0.0025
N_NULL = 2000
CUTOFF_ISO = "2026-02-01"
HORIZONS = ("1h", "4h", "24h")
DIAG_ITEMS = ("1.01", "2.02", "8.01")
RNG = random.Random(20260719)


def mean(xs):
    return statistics.fmean(xs) if xs else float("nan")


def one_sided_p(obs, null_means):
    if obs >= 0:
        k = sum(1 for x in null_means if x >= obs)
    else:
        k = sum(1 for x in null_means if x <= obs)
    return (1 + k) / (1 + len(null_means))


def score_cell(evs, label):
    """evs: events with attached llm rec (status ok)."""
    signed = [e for e in evs if e["_dir"] in ("LONG", "SHORT")]
    skips = [e for e in evs if e["_dir"] == "SKIP"]
    out = {"label": label, "n_llm_scored": len(evs),
           "n_signed": len(signed), "n_skip": len(skips),
           "skip_rate": len(skips) / len(evs) if evs else float("nan"),
           "n_long": sum(1 for e in signed if e["_dir"] == "LONG"),
           "n_short": sum(1 for e in signed if e["_dir"] == "SHORT")}
    for h in HORIZONS:
        pairs = [((1 if e["_dir"] == "LONG" else -1), e["r"][h])
                 for e in signed if e["r"][h] is not None]
        n = len(pairs)
        hres = {"n": n}
        if n >= 5:
            obs = mean([s * r - COST_RT for s, r in pairs])
            wins = sum(1 for s, r in pairs if s * r - COST_RT > 0)
            signs = [s for s, _ in pairs]
            rets = [r for _, r in pairs]
            nulls = []
            for _ in range(N_NULL):
                perm = signs[:]
                RNG.shuffle(perm)
                nulls.append(mean([s * r - COST_RT
                                   for s, r in zip(perm, rets)]))
            hres.update({"ev_net": obs, "win_rate": wins / n,
                         "null_mean": mean(nulls),
                         "p_perm": one_sided_p(obs, nulls)})
        out[h] = hres
    return out


def main():
    events = json.load(open(EV_CACHE))
    res = json.load(open(RESULTS))
    llm = res.get("llm", {})

    for e in events:
        rec = llm.get(e["accession"], {})
        e["_ok"] = rec.get("status") == "ok"
        e["_dir"] = rec.get("direction")
        e["_conv"] = rec.get("conviction", 0.0)

    scored = [e for e in events if e["_ok"]]
    unscored = [e for e in events if not e["_ok"]]
    pre = [e for e in scored if e["acc_iso"] < CUTOFF_ISO]
    post = [e for e in scored if e["acc_iso"] >= CUTOFF_ISO]
    post.sort(key=lambda e: e["acc_ms"])
    half = len(post) // 2

    cells = {
        "ALL": score_cell(scored, "all scored"),
        "PRE_CUTOFF": score_cell(pre, f"acc < {CUTOFF_ISO} (CONTAMINATED upper bound)"),
        "POST_CUTOFF": score_cell(post, f"acc >= {CUTOFF_ISO} (VERDICT cell)"),
        "POST_H1": score_cell(post[:half], "post-cutoff first half"),
        "POST_H2": score_cell(post[half:], "post-cutoff second half"),
        "POST_8K": score_cell([e for e in post if e["form"] == "8-K"],
                              "post-cutoff 8-K only (diagnostic)"),
        "POST_ITEMS": score_cell(
            [e for e in post if any(i in (e["items"] or "") for i in DIAG_ITEMS)],
            "post-cutoff items 1.01/2.02/8.01 (diagnostic)"),
        "POST_CONV60": score_cell(
            [e for e in post
             if e["_dir"] in ("LONG", "SHORT") and e["_conv"] >= 0.6],
            "post-cutoff conviction >= 0.6 (diagnostic)"),
    }

    skip_by_form = {}
    for form in ("8-K", "6-K"):
        fe = [e for e in scored if e["form"] == form]
        skip_by_form[form] = {
            "n": len(fe),
            "skip_rate": mean([1.0 * (e["_dir"] == "SKIP") for e in fe])}

    conv = {d: [e["_conv"] for e in scored if e["_dir"] == d]
            for d in ("LONG", "SHORT", "SKIP")}
    res["scores"] = {
        "n_events": len(events), "n_llm_ok": len(scored),
        "n_unscored": len(unscored),
        "unscored": [{"acc": e["accession"], "ticker": e["ticker"],
                      "err": llm.get(e["accession"], {}).get("err", "missing")}
                     for e in unscored],
        "skip_by_form": skip_by_form,
        "conviction_mean_by_dir": {d: mean(v) for d, v in conv.items()},
        "cells": cells,
    }
    tmp = RESULTS + ".tmp"
    with open(tmp, "w") as f:
        json.dump(res, f, indent=1)
    os.replace(tmp, RESULTS)
    print(f"-> {RESULTS}\n")

    print(f"LLM scored {len(scored)}/{len(events)} (unscored {len(unscored)}); "
          f"skip 8-K {skip_by_form['8-K']['skip_rate']:.0%}, "
          f"6-K {skip_by_form['6-K']['skip_rate']:.0%}")
    for name, c in cells.items():
        print(f"\n[{name}] {c['label']}: llm={c['n_llm_scored']} "
              f"signed={c['n_signed']} (L{c['n_long']}/S{c['n_short']}) "
              f"skip={c['skip_rate']:.0%}" if c["n_llm_scored"] else
              f"\n[{name}] empty")
        for h in HORIZONS:
            r = c.get(h, {})
            if "ev_net" in r:
                print(f"  +{h:<3} n={r['n']:>3} EV25 {r['ev_net']*100:+.3f}% "
                      f"win {r['win_rate']:.0%} null {r['null_mean']*100:+.3f}% "
                      f"p_perm={r['p_perm']:.4f}")
            else:
                print(f"  +{h:<3} n={r.get('n', 0)} (too few)")


if __name__ == "__main__":
    main()
