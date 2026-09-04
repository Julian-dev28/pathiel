#!/usr/bin/env python3
"""H3 — short the 3-day cross-sectional winners. The trade H2 actually found.

WHERE THIS CAME FROM, AND WHY THAT MATTERS
------------------------------------------
H1 (short crowded funding) failed: -0.285%/trade. Its decile table showed the
carry leg paying exactly as theory says (+0.245% on D10) while the price leg ran
-0.323% the other way. High funding was tracking momentum, not crowding.

H2 then asked whether 3-day cross-sectional momentum CONTINUES. It does not. The
ladder came out monotone in the opposite direction: bottom decile +0.24% over
the next 24h, top decile -1.89%, IC -0.084, and the sign held in both halves of
the sample.

So the trade is the reversal, and this file tests it on its own terms rather
than reading it out of a table built to answer a different question. That
distinction is the whole point: H2's p-value tested "does momentum pay", which
is the wrong tail for a reversal, and reporting -1.89% from a run whose stated
threshold was never met would be fitting the story to the output.

THE CLAIM
---------
H3: shorting the top decile of trailing 3-day return, held 24h, is profitable
    net of funding paid by the short and 25bps of round-trip slippage.

H3b: the edge is larger when open interest FELL over the same three days. Price
     up on falling OI is short covering — a rally with no new buyers behind it,
     which is the one most likely to give the move straight back.

WHAT WOULD MAKE THIS FALSE, STATED IN ADVANCE
----------------------------------------------
Three ways a spread this large is usually an artefact rather than an edge, each
checked below:

  liquidity   the movers are small coins, and 25bps is fiction on them. Tested
              by raising the volume floor to $5M and re-running; if the edge
              lives only below the floor it is a spread-capture illusion.
  listings    a new listing prints an enormous trailing return against a short
              history. Coins are required to have a full trailing window.
  one period  a single violent week carries the whole result. Checked by
              quartile, not just halves.

Costs are quoted from the SHORT's side throughout and never by flipping the
sign of a long: slippage is subtracted in both directions, so negating a long
return silently turns a 25bps cost into a 25bps credit.

    python research/regime_2026_09/H3_reversal.py
    python research/regime_2026_09/H3_reversal.py --min-vol 5000000
"""
from __future__ import annotations

import argparse
import random
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from H1_funding_carry import DAY_MS, HOUR_MS, SLIP_PCT, _f, load_panel, spearman  # noqa: E402

LOOKBACK_D = 3.0


def build(horizon_h: float, min_vol: float) -> List[dict]:
    stamps, panel = load_panel()
    need = len(stamps) * 0.9
    universe = [c for c, s in panel.items() if len(s) >= need]
    horizon_ms, look_ms = int(horizon_h * HOUR_MS), int(LOOKBACK_D * DAY_MS)

    per_ts: Dict[int, List[dict]] = defaultdict(list)
    for coin in universe:
        series = panel[coin]
        ts_sorted = sorted(series)
        for i, t in enumerate(ts_sorted):
            px_now, vol = _f(series[t], "px"), _f(series[t], "v") or 0.0
            if not px_now or vol < min_vol:
                continue
            past = [u for u in ts_sorted[:i] if u <= t - look_ms]
            if not past:
                continue
            px_past = _f(series[past[-1]], "px")
            oi_past, oi_now = _f(series[past[-1]], "oi"), _f(series[t], "oi")
            if not px_past:
                continue
            exit_t = next((u for u in ts_sorted[i + 1:] if u >= t + horizon_ms), None)
            if exit_t is None:
                continue
            px_exit = _f(series[exit_t], "px")
            if not px_exit:
                continue
            f_now, f_exit = _f(series[t], "f"), _f(series[exit_t], "f")
            f_avg = ((f_now + f_exit) / 2.0
                     if (f_now is not None and f_exit is not None) else (f_now or 0.0))
            hours = (exit_t - t) / HOUR_MS
            price_move = (px_exit / px_now - 1.0) * 100.0
            per_ts[t].append({
                "coin": coin, "t": t, "vol": vol,
                "mom": (px_now / px_past - 1.0) * 100.0,
                "oi_chg": ((oi_now / oi_past - 1.0) * 100.0
                           if oi_past and oi_now and oi_past > 0 else None),
                # The SHORT: price falling pays, funding received pays, slippage
                # is a cost on this side too. Never a negated long.
                "short_ret": -price_move + f_avg * hours * 100.0 - SLIP_PCT,
            })

    obs: List[dict] = []
    for t, rows in per_ts.items():
        if len(rows) < 20:
            continue
        rows.sort(key=lambda r: r["mom"])
        n = len(rows)
        for rank, r in enumerate(rows):
            r["mom_pct"] = rank / (n - 1) * 100.0
            obs.append(r)
    return obs


def bootstrap_p(rows: List[dict], n_iter: int = 3000) -> float:
    """P(mean short return <= 0), resampling snapshots, not observations."""
    by_t: Dict[int, List[float]] = defaultdict(list)
    for o in rows:
        by_t[o["t"]].append(o["short_ret"])
    keys = list(by_t)
    if len(keys) < 10:
        return 1.0
    rng = random.Random(23)
    worse = 0
    for _ in range(n_iter):
        pool: List[float] = []
        for _ in range(len(keys)):
            pool.extend(by_t[rng.choice(keys)])
        if pool and st.mean(pool) <= 0:
            worse += 1
    return worse / n_iter


def report(rows: List[dict], label: str) -> Optional[float]:
    if len(rows) < 150:
        print(f"  {label:<34} n={len(rows)}  too few to score")
        return None
    m = st.mean(o["short_ret"] for o in rows)
    win = 100.0 * sum(1 for o in rows if o["short_ret"] > 0) / len(rows)
    print(f"  {label:<34} n={len(rows):>6}  {m:+.3f}%/trade   win {win:.0f}%")
    return m


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="H3_reversal")
    ap.add_argument("--horizon-h", type=float, default=24.0)
    ap.add_argument("--min-vol", type=float, default=1_000_000)
    a = ap.parse_args(argv)

    print(f"H3 — short the 3d cross-sectional winners, {a.horizon_h:.0f}h hold")
    print(f"     net of funding received and {SLIP_PCT}% round trip, "
          f"vol floor ${a.min_vol/1e6:.0f}M\n")
    obs = build(a.horizon_h, a.min_vol)
    if not obs:
        print("  not enough panel history")
        return 1
    print(f"  {len(obs)} observations, {len({o['coin'] for o in obs})} coins, "
          f"{len({o['t'] for o in obs})} snapshots\n")

    top = [o for o in obs if o["mom_pct"] >= 90]
    print("HEADLINE")
    mean_top = report(top, "short top-decile momentum")
    report([o for o in obs if o["mom_pct"] <= 10], "short bottom decile (control)")
    report(obs, "short everything (control)")
    ic = spearman([o["mom_pct"] for o in obs], [o["short_ret"] for o in obs])
    p = bootstrap_p(top)
    print(f"\n  IC {ic:+.4f}  (positive = higher momentum shorts better)")
    print(f"  bootstrap p {p:.4f}  (clustered on entry snapshot)")

    print("\nH3b — does open interest sharpen it?")
    report([o for o in top if o["oi_chg"] is not None and o["oi_chg"] <= 0],
           "  OI fell (squeeze)")
    report([o for o in top if o["oi_chg"] is not None and o["oi_chg"] > 0],
           "  OI rose (new money)")

    print("\nROBUSTNESS")
    ts = sorted({o["t"] for o in obs})
    qs = [ts[len(ts) * i // 4] for i in range(1, 4)]
    for i, (lo, hi) in enumerate(zip([0] + qs, qs + [ts[-1] + 1])):
        report([o for o in top if lo <= o["t"] < hi], f"  quartile {i+1}")
    med_vol = st.median(o["vol"] for o in top)
    report([o for o in top if o["vol"] >= med_vol], "  most liquid half")
    report([o for o in top if o["vol"] < med_vol], "  least liquid half")

    ok = (mean_top is not None and mean_top > 0 and ic > 0 and p < 0.05)
    print(f"\n  VERDICT: {'clears the pre-registered bar' if ok else 'does NOT clear the bar'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
