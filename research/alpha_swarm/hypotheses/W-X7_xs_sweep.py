#!/usr/bin/env python3
"""W-X7 — full xs_momentum recipe sweep: find the most EV+ crypto book, gated.

Operator asked (2026-07-23) to re-examine the live xs_momentum config and test all
variants for the best EV. Sweeps the four knobs the wx2 engine actually models —
ranking {pctk, raw trailing, residual-vs-BTC} x window {7,14,21} x k {3,4,5,8} x
hold {5,7,10,14} x meme-exclusion {on, off} = 288 books — on the shared crypto cache
(W-X2_cache_daily.json, top-50 by volume, 401 daily bars), same engine as W-X2/W-X4/W-X5.

Method: per book, non-overlapping xs long-top-k / short-bottom-k, decide bar i / fill
open[i+1] / exit open[i+1+H]; net25 = ev - 0.0025*turnover; OOS = rebalance halves;
2000-draw matched random-book null on the top candidates only (speed). $/wk at $76.8/leg.

DECISION GATE (pre-registered, strict — this is a LIVE-book change, W-X5 bar): a variant
is WIRE-WORTHY only if it beats the LIVE cell (pctk/14, k4, H10, meme-EXCLUDED — the W-X4
validated recipe) on net25 AND in BOTH OOS halves AND its own null p < 0.05. Sweeping 288
combos is a multiple-comparisons machine; the strict-dominance + both-halves + null gate is
the overfit guard, and the honest default is "live recipe stands" unless something clears
it robustly. NOTE: wx2 sim is UNGATED — vol_gate / vol_managed are NOT modelled here; this
sweeps ranking/window/k/hold/meme only. residual flag is a real ranker (unlike under pctk).
"""
from __future__ import annotations

import importlib.util
import json
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "lib"))
_spec = importlib.util.spec_from_file_location("wx2", HERE / "W-X2_xs_widening.py")
wx2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wx2)

WINDOWS = [7, 14, 21]
KS = [3, 4, 5, 8]
HOLDS = [5, 7, 10, 14]
RANKERS = ["pctk", "raw", "resid"]
MIN_REBALANCES = 15


def score_fn_for(w, kind, win):
    if kind == "pctk":
        return lambda c, i: wx2.pctk(w, c, i, win)
    if kind == "raw":
        return lambda c, i: wx2.trailing_ret(w, c, i, win)
    return lambda c, i: wx2.residual_ret(w, c, i, win, "BTC", 30)


def sweep():
    d = wx2.load_cache()
    w, coins = wx2.make_crypto_world(d)
    memes = {c for c, s in wx2.SECTOR_MAP.items() if s == "MEME"}
    coins_nomeme = [c for c in coins if c not in memes]
    in_univ = sorted(set(coins) & memes)
    print(f"cache: {len(coins)} crypto coins, {len(d['candles'].get('BTC', []))} BTC bars; "
          f"MEME in universe ({len(in_univ)}): {', '.join(in_univ)}")

    rows = []
    for meme_excl in (True, False):
        elig_coins = coins_nomeme if meme_excl else coins
        elig = wx2.elig_std(w, elig_coins, min_hist=61)
        for kind in RANKERS:
            for win in WINDOWS:
                sfn = score_fn_for(w, kind, win)
                for k in KS:
                    for hold in HOLDS:
                        res = wx2.run_book(w, elig, sfn, k, hold)
                        if len(res["recs"]) < MIN_REBALANCES:
                            continue
                        tag = f"{kind}{win} k{k} H{hold} {'no-meme' if meme_excl else 'all'}"
                        row = wx2.summarize_book(res, k, hold, tag, with_null=False)
                        row["meme_excl"] = meme_excl
                        row["kind"] = kind
                        row["win"] = win
                        rows.append(row)
    return w, rows


def is_live(r):
    return (r["kind"] == "pctk" and r["win"] == 14 and r["k"] == 4
            and r["hold"] == 10 and r["meme_excl"])


def is_user_current(r):
    return (r["kind"] == "pctk" and r["win"] == 14 and r["k"] == 4
            and r["hold"] == 10 and not r["meme_excl"])


def main():
    w, rows = sweep()
    # add nulls to the top-15 by net25
    rows.sort(key=lambda r: -r["net25"])
    for r in rows[:15]:
        # rebuild recs to compute null (cheap): re-run the exact book
        elig_coins = [c for c in wx2.make_crypto_world(wx2.load_cache())[1]
                      if not (r["meme_excl"] and c in {c2 for c2, s in wx2.SECTOR_MAP.items() if s == "MEME"})]
        elig = wx2.elig_std(w, elig_coins, min_hist=61)
        res = wx2.run_book(w, elig, score_fn_for(w, r["kind"], r["win"]), r["k"], r["hold"])
        gross = statistics.mean([x["ev"] for x in res["recs"]])
        r["null_p_gross"] = round(wx2.null_p(res["recs"], r["k"], gross), 4)

    live = next((r for r in rows if is_live(r)), None)
    user_cur = next((r for r in rows if is_user_current(r)), None)

    def passes_gate(r):
        # W-X5 strict-dominance bar for a LIVE-book change: beat live on net25 EV AND
        # risk-adjusted Sharpe AND BOTH OOS halves' EV AND clear its own null. Sharpe is
        # in because a longer-hold cell can win net25/rebal purely by holding longer while
        # delivering the SAME weekly $ at MORE drawdown — not a real improvement.
        if r is None or live is None:
            return False
        h1, h2 = r["oos_net25"]
        return (r["net25"] > live["net25"]
                and r["sharpe_like_net25"] > live["sharpe_like_net25"]
                and r.get("null_p_gross", 1.0) < 0.05
                and h1 > live["oos_net25"][0] and h2 > live["oos_net25"][1])

    print("\n=== TOP 15 by net25 ===")
    for r in rows[:15]:
        flag = " <-LIVE" if is_live(r) else (" <-USER-NOW" if is_user_current(r) else "")
        dom = " *DOMINATES*" if passes_gate(r) else ""
        print(" " + wx2.fmt(r) + flag + dom)

    print("\n=== reference cells ===")
    for label, r in (("LIVE recipe (pctk14/k4/H10/no-meme)", live),
                     ("USER-NOW (pctk14/k4/H10/ALL memes in)", user_cur)):
        print(f"  {label}: " + (wx2.fmt(r) if r else "n/a"))

    dominators = [r for r in rows if passes_gate(r)]
    best = rows[0]
    print("\n=== VERDICT ===")
    if user_cur and live:
        delta = round(user_cur["net25"] - live["net25"], 3)
        print(f"meme-exclusion: no-meme net25 {live['net25']:+.2f}% vs all-in "
              f"{user_cur['net25']:+.2f}%  (empty exclude_coins costs {delta:+.2f}%/rebal)")
    if dominators:
        d0 = dominators[0]
        print(f"WIRE-WORTHY: {d0['label']} strictly dominates live "
              f"(net25 {d0['net25']:+.2f}% vs {live['net25']:+.2f}%, "
              f"oos {d0['oos_net25']}, p={d0.get('null_p_gross')})")
    else:
        print(f"NO variant strictly dominates the live recipe. Best-net25 = {best['label']} "
              f"({best['net25']:+.2f}%, oos {best['oos_net25']}, "
              f"p={best.get('null_p_gross','n/a')}) — LIVE RECIPE STANDS. "
              f"Restore meme-exclusion; do not chase the sweep top (overfit).")

    out = {"top15": rows[:15], "live": live, "user_current": user_cur,
           "dominators": dominators, "n_books": len(rows)}
    (HERE / "W-X7_results.json").write_text(json.dumps(out, indent=2))
    print(f"\nwrote W-X7_results.json ({len(rows)} books scored)")


if __name__ == "__main__":
    main()
