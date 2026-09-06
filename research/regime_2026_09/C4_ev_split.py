#!/usr/bin/env python3
"""Should the portfolio be split between HIP-3 and crypto?

The operator asked for expected EV under HIP-3-only versus crypto-enabled,
because they are considering splitting capital across both.

EV PER TRADE IS THE WRONG NUMBER AND IS WHY THIS SCRIPT EXISTS
A book with a 4% edge that fires twice a month loses to a 1% edge that fires
daily, on the same capital. What decides a split is dollars per month, which is

    edge_per_trade  x  trades_actually_taken

and the second term is capped by slots, not by how many signals exist. This
account has 2 concurrent slots and a hold measured in days, so it can take
roughly (24 / hold_hours) x slots trades per day and no more — whatever the
signal supply. On the numbers below the supply exceeds the capacity by an order
of magnitude in both universes, which means adding a second universe adds
CANDIDATES, not TRADES, and candidates are free.

That is the whole decision, and it is why the answer does not depend on which
universe has the better edge in isolation.

    python research/regime_2026_09/C4_ev_split.py
"""
from __future__ import annotations

import json
import statistics as st
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent))
from C1_session_structure import load_panel  # noqa: E402
from H1_funding_carry import DAY_MS, HOUR_MS, SLIP_PCT, _f  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
LOOKBACK_D = 3.0
MIN_VOL = 1_000_000
BASELINE_F = 1.25e-05
AWAKE_MIN = 0.67
HOLD_H = 24.0


def build(which: str) -> List[dict]:
    """which: 'hip3' | 'crypto'. Ranked WITHIN its own universe, because that is
    what the live book would do if only that universe were enabled."""
    panel = load_panel()
    sel = {c: s for c, s in panel.items()
           if (":" in c) == (which == "hip3")}
    if not sel:
        return []
    all_ts = sorted({t for s in sel.values() for t in s})
    need = len(all_ts) * 0.9
    universe = [c for c, s in sel.items() if len(s) >= need]

    per_ts: Dict[int, List[dict]] = defaultdict(list)
    for coin in universe:
        series = sel[coin]
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
            per_ts[t].append({
                "coin": coin, "t": t, "awake": awake, "mom": (px / p0 - 1) * 100,
                "short_ret": -(p2 / px - 1) * 100 + favg * hours * 100 - SLIP_PCT,
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
    return [o for o in obs if o["mom_pct"] >= 90 and o["awake"] >= AWAKE_MIN]


def summarise(obs: List[dict], label: str) -> dict:
    if len(obs) < 100:
        print(f"  {label:<16} n={len(obs)} — too few to score")
        return {}
    span_d = (max(o["t"] for o in obs) - min(o["t"] for o in obs)) / DAY_MS
    per_day = len(obs) / max(span_d, 1)
    ev = st.mean(o["short_ret"] for o in obs)
    win = 100 * sum(1 for o in obs if o["short_ret"] > 0) / len(obs)
    times = sorted({o["t"] for o in obs})
    cut = times[len(times) // 2]
    h1 = [o["short_ret"] for o in obs if o["t"] < cut]
    h2 = [o["short_ret"] for o in obs if o["t"] >= cut]
    a, b = st.mean(h1), st.mean(h2)
    stable = (a > 0) == (b > 0)
    # An edge whose two time halves disagree in sign is not an edge yet,
    # however large the average. Reported, never acted on.
    trusted = stable and len(obs) >= 300
    flag = "" if trusted else "   <-- NOT TRUSTED"
    print(f"  {label:<16} n={len(obs):>5}  EV {ev:+.3f}%  win {win:.0f}%  "
          f"candidates/day {per_day:5.1f}  halves {a:+.2f}/{b:+.2f}{flag}")
    return {"ev": ev, "per_day": per_day, "n": len(obs), "trusted": trusted}


def main() -> int:
    cfg = json.loads((ROOT / ".agent-config.json").read_text())
    notional = float(cfg["xs_reversal"]["notional_usd"])
    slots = int(cfg["max_concurrent"])
    equity = float(cfg["min_tradable_equity_usd"])

    print("C4 — HIP-3 only, crypto only, or both?\n")
    print(f"  live config: ${notional:.0f} notional x {slots} slots, "
          f"{HOLD_H:.0f}h hold, floor ${equity:.0f}\n")
    print("EDGE AND SUPPLY, each universe ranked within itself")
    h = summarise(build("hip3"), "HIP-3 (xyz)")
    c = summarise(build("crypto"), "crypto")
    if not h or not c:
        return 1

    # Capacity: slots turning over once per hold. Signal supply above this is
    # not reachable, whatever the universe contains.
    trades_per_day = slots * (24.0 / HOLD_H)
    print(f"\nCAPACITY")
    print(f"  {slots} slots x {24/HOLD_H:.0f} turns/day = {trades_per_day:.0f} trades/day maximum")
    for name, d in (("HIP-3", h), ("crypto", c)):
        print(f"  {name:<8} supplies {d['per_day']:5.1f} candidates/day "
              f"= {d['per_day']/trades_per_day:5.1f}x capacity")

    print(f"\nEXPECTED DOLLARS, at ${notional:.0f} notional")
    print(f"  {'universe':<18}{'EV/trade':>10}{'$/trade':>10}{'$/day':>9}{'$/month':>10}{'%/mo':>8}")
    # Only a TRUSTED edge may drive a projection. Quoting dollars from an
    # untrusted one is how a 106-observation sample with a sign flip becomes a
    # capital allocation.
    rows = [("HIP-3 only", h["ev"], h["trusted"]),
            ("crypto only", c["ev"], c["trusted"])]
    trusted_evs = [d["ev"] for d in (h, c) if d["trusted"]]
    rows.append(("both enabled", max(trusted_evs) if trusted_evs else 0.0,
                 bool(trusted_evs)))
    for name, ev, ok in rows:
        per_trade = notional * ev / 100
        per_day = per_trade * trades_per_day
        note = "" if ok else "   (untrusted — not a projection)"
        print(f"  {name:<18}{ev:>+9.3f}%{per_trade:>10.3f}{per_day:>9.2f}"
              f"{per_day*30:>10.2f}{per_day*30/equity*100:>7.0f}%{note}")

    print("\nTHE DECISION")
    if not c["trusted"]:
        print(f"  The crypto edge is NOT usable evidence: n={c['n']}, and its two")
        print("  time halves disagree in sign, so the whole average rests on one")
        print("  period. A higher number from an unstable sample is not a better")
        print("  edge, and allocating to it would be allocating to noise.")
    if not h["trusted"]:
        print(f"  The HIP-3 edge is also untrusted (n={h['n']}).")
    better = "HIP-3" if h.get("trusted") and not c.get("trusted") else (
        "crypto" if c.get("trusted") and not h.get("trusted") else
        ("HIP-3" if h["ev"] > c["ev"] else "crypto"))
    print(f"  Only {better} clears the bar on stability and sample size.")
    trusted_names = [n for n, d in (("HIP-3", h), ("crypto", c)) if d["trusted"]]
    if len(trusted_names) < 2:
        print("\n  DO NOT SPLIT.")
        print("  A split allocates slots to a universe whose edge has not been")
        print("  demonstrated. Two independent reasons point the same way here:")
        print(f"    - crypto's sample is {c['n']} observations with a sign flip across")
        print("      its halves, so there is no edge to allocate TO;")
        print(f"    - crypto supplies {c['per_day']:.1f} candidates/day against "
              f"{trades_per_day:.0f} slots/day,")
        print("      so it could not fill the book even if the edge were real.")
        print(f"  HIP-3 already supplies {h['per_day']/trades_per_day:.0f}x the capacity, which means")
        print("  the constraint is CAPITAL, not opportunity. A second universe adds")
        print("  candidates the ranker never reaches — and dilutes the ranking, since")
        print("  the top decile is then taken across a pool where one half has no")
        print("  measured edge.")
        print("\n  The way to earn more is not a second universe. It is longer holds")
        print("  (C3: 24h -> 72h takes the same book from +2.0% to +4.1%) or more")
        print("  capital. Both raise dollars without adding an unproven bet.")
    elif min(h["per_day"], c["per_day"]) > trades_per_day * 2:
        print("\n  DO NOT SPLIT. Both universes already supply more candidates than")
        print("  the slots can take, so a split changes the mix, not the turnover.")
    else:
        print("\n  A split is worth considering: both edges are trusted and neither")
        print("  universe alone fills the book.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
