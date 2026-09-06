#!/usr/bin/env python3
"""Cycle 5 — is there a LONG edge in the xyz universe, or only a short one?

WHY THIS QUESTION
Every live book is short-only: xs_reversal, news_surge_short, news_surge_multi,
unlock_short, thin_short_relax. Checked 2026-09-06, zero of them emit a long.
The account's only long exposure is the discretionary AI runner, which has no
measured edge behind it. The operator wants to trade both ways, so the question
is whether a long edge EXISTS here, not how to wire one up.

THE TWO CANDIDATES, BOTH DECLARED BEFORE THE RUN
L1 (mirror): LONG the BOTTOM decile of 3d cross-sectional return. This is
    xs_reversal reflected. If the reversal is a real mean-reversion effect it
    should work on both tails; if it only works short, the effect is not
    reversion but something asymmetric (crowding, borrow, funding).
L2 (momentum): LONG the TOP decile. The opposite mechanism - trend continuation
    rather than reversion. Included because if L1 fails, the honest follow-up is
    whether the tail is trending, not to keep re-cutting L1.

Two hypotheses, so the 5% bar becomes 2.5% each (Bonferroni). Stated up front
rather than after seeing which one won.

WHY THE LONG IS NOT JUST THE SHORT WITH A MINUS SIGN
Funding flips from income to cost. A short in the top decile RECEIVES funding
when funding is positive; a long PAYS it. Slippage is a cost on both sides, so
it subtracts from both rather than mirroring. A naive sign flip of the short
result would overstate the long by roughly twice the carry.

WHAT WOULD MAKE THIS NOISE, AND THE GUARD
Same guards as H3/C4, because they are what the short side had to clear:
clustered bootstrap on ENTRY TIMESTAMP (positions opened in the same snapshot
are one bet, not twenty), both time halves must agree in sign, and a minimum of
300 observations before any result is allowed to drive a decision.

    python research/regime_2026_09/C5_long_side.py
"""
from __future__ import annotations

import random
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent))
from C1_session_structure import load_panel  # noqa: E402
from H1_funding_carry import DAY_MS, HOUR_MS, SLIP_PCT, _f  # noqa: E402

LOOKBACK_D = 3.0
HOLD_H = 24.0
MIN_VOL = 1_000_000
BASELINE_F = 1.25e-05
AWAKE_MIN = 0.67
MIN_N = 300
ALPHA = 0.025          # 0.05 / 2 hypotheses


def build() -> List[dict]:
    """Every entry, carrying BOTH the long and short net return.

    Built once and scored both ways so the two sides are compared on an
    identical sample - rebuilding per side would let a coin drop out of one and
    not the other and silently compare different populations.
    """
    panel = {c: s for c, s in load_panel().items() if ":" in c}
    if not panel:
        return []
    all_ts = sorted({t for s in panel.values() for t in s})
    need = len(all_ts) * 0.9
    universe = [c for c, s in panel.items() if len(s) >= need]

    per_ts: Dict[int, List[dict]] = defaultdict(list)
    for coin in universe:
        series = panel[coin]
        ts = sorted(series)
        for i, t in enumerate(ts):
            px, vol = _f(series[t], "px"), _f(series[t], "v") or 0.0
            if not px or vol < MIN_VOL:
                continue
            past = [u for u in ts[:i] if u <= t - LOOKBACK_D * DAY_MS]
            if not past:
                continue
            p0 = _f(series[past[-1]], "px")
            if not p0:
                continue
            xt = next((u for u in ts[i + 1:] if u >= t + HOLD_H * HOUR_MS), None)
            p2 = _f(series[xt], "px") if xt else None
            if not p2:
                continue
            recent = [x for x in (_f(series[u], "f") for u in ts
                                  if t - 7 * DAY_MS <= u <= t) if x is not None]
            if len(recent) < 8:
                continue
            awake = sum(1 for x in recent if abs(x - BASELINE_F) > 1e-9) / len(recent)
            f0, f1 = _f(series[t], "f"), _f(series[xt], "f")
            favg = (f0 + f1) / 2 if (f0 is not None and f1 is not None) else (f0 or 0.0)
            hours = (xt - t) / HOUR_MS
            raw = (p2 / px - 1) * 100
            carry = favg * hours * 100
            per_ts[t].append({
                "coin": coin, "t": t, "awake": awake,
                "mom": (px / p0 - 1) * 100,
                # Long PAYS carry, short RECEIVES it. Slippage costs both.
                "long_ret": raw - carry - SLIP_PCT,
                "short_ret": -raw + carry - SLIP_PCT,
            })

    obs: List[dict] = []
    for t, rows in per_ts.items():
        if len(rows) < 20:
            continue
        rows.sort(key=lambda r: r["mom"])
        n = len(rows)
        for rank, r in enumerate(rows):
            r["mom_pct"] = rank / (n - 1) * 100
            obs.append(r)
    return obs


def boot(rows: List[dict], key: str, n_iter: int = 2000) -> float:
    """Clustered on entry timestamp. Resampling observations instead would
    treat twenty coins opened in one snapshot as twenty independent bets and
    shrink the p-value by roughly sqrt(20)."""
    by_t: Dict[int, List[float]] = defaultdict(list)
    for o in rows:
        by_t[o["t"]].append(o[key])
    keys = list(by_t)
    if len(keys) < 10:
        return 1.0
    rng = random.Random(71)
    worse = 0
    for _ in range(n_iter):
        pool: List[float] = []
        for _ in range(len(keys)):
            pool.extend(by_t[rng.choice(keys)])
        if pool and st.mean(pool) <= 0:
            worse += 1
    return worse / n_iter


def score(rows: List[dict], key: str, label: str) -> dict:
    if len(rows) < 100:
        print(f"  {label:<34} n={len(rows):>5}   under 100, not reported")
        return {}
    ev = st.mean(o[key] for o in rows)
    win = 100 * sum(1 for o in rows if o[key] > 0) / len(rows)
    times = sorted({o["t"] for o in rows})
    cut = times[len(times) // 2]
    h1 = [o[key] for o in rows if o["t"] < cut]
    h2 = [o[key] for o in rows if o["t"] >= cut]
    a, b = st.mean(h1), st.mean(h2)
    stable = (a > 0) == (b > 0)
    p = boot(rows, key)
    ok = stable and len(rows) >= MIN_N and p < ALPHA and ev > 0
    print(f"  {label:<34} n={len(rows):>5}  EV {ev:+.3f}%  win {win:>3.0f}%  "
          f"p {p:.4f}  halves {a:+.2f}/{b:+.2f}  {'PASS' if ok else 'fail'}")
    return {"ev": ev, "n": len(rows), "p": p, "stable": stable, "ok": ok}


def main() -> int:
    obs = build()
    if not obs:
        print("  not enough panel history")
        return 1
    print("C5 - is there a LONG edge, or only a short one?\n")
    print(f"  {len(obs)} entries, {HOLD_H:.0f}h hold, net of {SLIP_PCT}% slippage "
          f"and funding carry")
    print(f"  two pre-declared hypotheses, Bonferroni alpha = {ALPHA}, "
          f"min n = {MIN_N}\n")

    awake = [o for o in obs if o["awake"] >= AWAKE_MIN]
    bottom = [o for o in awake if o["mom_pct"] <= 10]
    top = [o for o in awake if o["mom_pct"] >= 90]

    print("THE LONG CANDIDATES")
    l1 = score(bottom, "long_ret", "L1 long bottom decile (mirror)")
    l2 = score(top, "long_ret", "L2 long top decile (momentum)")

    # L3 exists because L1/L2 are CROSS-SECTIONAL: ranking within each snapshot
    # subtracts whatever the whole universe did, so both are blind to plain
    # market drift. "Is there long money here at all" is a different question
    # from "is there a long SIGNAL", and answering only the second one would
    # leave the operator's question half-answered.
    print("\nL3 - unconditional long, no signal (does the tape simply drift up?)")
    l3 = score(awake, "long_ret", "   long anything liquid")

    print("\nTHE SHORT SIDE, same sample, for reference")
    s1 = score(top, "short_ret", "   short top decile (xs_reversal)")

    print("\nTHE READ")
    winners = [n for n, d in (("L1", l1), ("L2", l2), ("L3", l3)) if d.get("ok")]
    if not winners:
        print("  NO long edge clears the bar on this panel.")
        if l1:
            print(f"  L1, the mirror of the live short book, comes in at "
                  f"{l1['ev']:+.3f}% (p={l1['p']:.3f}).")
        print("  That asymmetry is itself the finding: if the effect were plain")
        print("  mean reversion it would pay on both tails, and it does not. The")
        print("  short side is being paid for something a long cannot collect -")
        print("  most likely the funding carry, which the short RECEIVES and the")
        print("  long PAYS, so the same price move nets out differently.")
        if l3:
            print(f"\n  And the tape itself is not drifting up: an unconditional long")
            print(f"  pays {l3['ev']:+.3f}%, so there is no beta to harvest either. The")
            print("  long side is not being missed for want of a signal - it is not")
            print("  there in this universe over this window.")
        print("\n  Shipping a long book here would be shipping a hedge, not an edge.")
        if s1.get("ok"):
            print(f"  The short book remains the measured one ({s1['ev']:+.3f}%).")
    else:
        print(f"  {', '.join(winners)} clears the bar. Wire it live per the")
        print("  operator's standing instruction that any EV+ method goes live.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
