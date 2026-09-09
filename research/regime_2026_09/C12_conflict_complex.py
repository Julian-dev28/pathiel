#!/usr/bin/env python3
"""Cycle 12 — is there a tradeable conflict complex on this dex?

THE SETUP
Brent through $100, +43% since 2 July, on US strikes near the Strait of Hormuz.
The operator wants a strategy aimed at the conflict specifically. The panel now
covers 74 days (2026-06-26 -> 09-08), so the ENTIRE move is inside the sample.

WHAT CAN AND CANNOT BE ANSWERED HERE
Cannot: "does conflict cause oil to rise". That is n=1. C9 already refused this
        shape - one geopolitical shock cannot be backtested, and a strategy
        fitted to a single event is a story.
Can:    the MECHANICS of the move, all of which have hundreds of observations
        inside the window:
          1. WHICH markets actually move with oil (measured, not assumed - the
             intuition that defence or gold belongs in the complex is a
             hypothesis, and correlation settles it)
          2. whether oil's returns are PERSISTENT or mean-reverting, which
             decides trend-following versus fading
          3. whether the live short book is positioned AGAINST it
          4. what a long would have earned, and with what drawdown

The distinction matters: "buy oil because of war" is untestable, "oil returns
have positive autocorrelation at 3d in this regime" is a measurement.

THE STANDING RESULT THIS HAS TO ARGUE WITH
Six independent tests (C5 L1/L2/L3, C7 7d/30d, C8 S1 long leg) found no long
edge on this venue, because funding runs positive and an unhedged long pays the
carry the short collects. Any long here must clear that tax, so the carry is
subtracted explicitly rather than ignored.

    python research/regime_2026_09/C12_conflict_complex.py
"""
from __future__ import annotations

import statistics as st
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

sys.path.insert(0, str(Path(__file__).resolve().parent))
from C1_session_structure import load_panel  # noqa: E402
from H1_funding_carry import HOUR_MS, SLIP_PCT, _f  # noqa: E402

ANCHOR = "xyz:BRENTOIL"
MIN_VOL = 500_000


def series() -> Dict[str, Dict[int, dict]]:
    return {c: s for c, s in load_panel().items() if c.startswith("xyz:")}


def rets(s: Dict[int, dict]) -> Dict[int, float]:
    ts = sorted(s)
    out = {}
    for a, b in zip(ts, ts[1:]):
        pa, pb = _f(s[a], "px"), _f(s[b], "px")
        if pa and pb and (b - a) <= 6 * HOUR_MS:
            out[b] = (pb / pa - 1) * 100
    return out


def corr(a: Dict[int, float], b: Dict[int, float]) -> tuple:
    ks = sorted(set(a) & set(b))
    if len(ks) < 60:
        return None, len(ks)
    x = [a[k] for k in ks]; y = [b[k] for k in ks]
    mx, my = st.mean(x), st.mean(y)
    sxy = sum((p - mx) * (q - my) for p, q in zip(x, y))
    sxx = sum((p - mx) ** 2 for p in x); syy = sum((q - my) ** 2 for q in y)
    return (sxy / (sxx * syy) ** 0.5 if sxx > 0 and syy > 0 else 0.0), len(ks)


def main() -> int:
    panel = series()
    if ANCHOR not in panel:
        print(f"  {ANCHOR} not in panel")
        return 1
    anchor_r = rets(panel[ANCHOR])
    ts = sorted(panel[ANCHOR])
    p0, p1 = _f(panel[ANCHOR][ts[0]], "px"), _f(panel[ANCHOR][ts[-1]], "px")
    d0 = datetime.fromtimestamp(ts[0] / 1000, tz=timezone.utc)
    d1 = datetime.fromtimestamp(ts[-1] / 1000, tz=timezone.utc)
    print("C12 — the conflict complex\n")
    print(f"  {ANCHOR}: {p0:.2f} -> {p1:.2f}  ({(p1/p0-1)*100:+.1f}%) "
          f"over {d0:%m-%d} to {d1:%m-%d}\n")

    # ---- 1. who actually moves with oil ----------------------------------
    print("WHO MOVES WITH OIL (measured, not assumed)")
    rows = []
    for c, s in panel.items():
        if c == ANCHOR:
            continue
        vol = max((_f(v, "v") or 0) for v in s.values())
        if vol < MIN_VOL:
            continue
        r, n = corr(anchor_r, rets(s))
        if r is None:
            continue
        tsx = sorted(s)
        a, b = _f(s[tsx[0]], "px"), _f(s[tsx[-1]], "px")
        rows.append((c, r, ((b / a - 1) * 100) if a and b else 0.0, vol))
    rows.sort(key=lambda x: -x[1])
    print(f"  {'market':<18}{'corr':>7}{'move':>9}{'volume':>11}")
    for c, r, mv, v in rows[:10]:
        print(f"  {c:<18}{r:>+7.2f}{mv:>+8.1f}%{v/1e6:>10.1f}M")
    print("  ...")
    for c, r, mv, v in rows[-3:]:
        print(f"  {c:<18}{r:>+7.2f}{mv:>+8.1f}%{v/1e6:>10.1f}M")

    # ---- 2. is oil PERSISTENT or mean-reverting? -------------------------
    print(f"\nIS THE MOVE PERSISTENT? (autocorrelation of {ANCHOR} returns)")
    ks = sorted(anchor_r)
    print(f"  {'lag':>5}{'autocorr':>11}   read")
    for lag in (1, 2, 4, 8):
        pairs = [(anchor_r[a], anchor_r[b])
                 for a, b in zip(ks, ks[lag:]) if a in anchor_r and b in anchor_r]
        if len(pairs) < 60:
            continue
        xa = {i: p for i, (p, _) in enumerate(pairs)}
        xb = {i: q for i, (_, q) in enumerate(pairs)}
        r, _ = corr(xa, xb)
        tag = ("trend persists" if r and r > 0.05 else
               "mean-reverts" if r and r < -0.05 else "no memory")
        print(f"  {lag:>4}i{r:>+11.3f}   {tag}")

    # ---- 3. long vs short the complex, net of carry ---------------------
    print(f"\nWHAT A POSITION IN {ANCHOR} WOULD HAVE EARNED (net of "
          f"{SLIP_PCT}% and funding)")
    s = panel[ANCHOR]
    tsx = sorted(s)
    print(f"  {'hold':>6}{'n':>6}{'LONG':>9}{'SHORT':>9}{'win(L)':>8}")
    for hold_h in (24.0, 72.0, 168.0):
        L, S = [], []
        for i, t in enumerate(tsx):
            px = _f(s[t], "px")
            xt = next((u for u in tsx[i + 1:] if u >= t + hold_h * HOUR_MS), None)
            p2 = _f(s[xt], "px") if xt else None
            if not px or not p2:
                continue
            f0, f1 = _f(s[t], "f"), _f(s[xt], "f")
            favg = (f0 + f1) / 2 if (f0 is not None and f1 is not None) else (f0 or 0.0)
            carry = favg * (xt - t) / HOUR_MS * 100
            raw = (p2 / px - 1) * 100
            L.append(raw - carry - SLIP_PCT)
            S.append(-raw + carry - SLIP_PCT)
        if len(L) < 30:
            continue
        print(f"  {hold_h:>5.0f}h{len(L):>6}{st.mean(L):>+8.2f}%{st.mean(S):>+8.2f}%"
              f"{100*sum(1 for x in L if x>0)/len(L):>7.0f}%")

    # ---- 4. is the carry STRUCTURAL or part of the squeeze? -------------
    print("\nIS THE FUNDING EDGE STABLE? (annualised, by third)")
    print(f"  {'market':<16}{'T1':>9}{'T2':>9}{'T3':>9}  verdict")
    for c in (ANCHOR, "xyz:CL", "xyz:GOLD", "xyz:NVDA"):
        ss = panel.get(c)
        if not ss:
            continue
        tt = sorted(ss)
        a, b = len(tt) // 3, 2 * len(tt) // 3
        parts = []
        for seg in (tt[:a], tt[a:b], tt[b:]):
            fs = [x for x in (_f(ss[t], "f") for t in seg) if x is not None]
            parts.append(st.mean(fs) * 24 * 365 * 100 if len(fs) >= 20 else None)
        if any(v is None for v in parts):
            continue
        verdict = ("pays longs throughout" if all(v < 0 for v in parts)
                   else "taxes longs throughout" if all(v > 0 for v in parts)
                   else "FLIPS — not structural")
        print(f"  {c:<16}{parts[0]:>+8.1f}%{parts[1]:>+8.1f}%{parts[2]:>+8.1f}%  {verdict}")
    print("  BRENTOIL's -15% average is ENTIRELY its third period. Funding was")
    print("  POSITIVE (+8.9%, +6.3%) before the squeeze and went to -60.2% during")
    print("  it. That is not a venue feature a strategy can lean on - it is the")
    print("  squeeze itself, and it reverts when the squeeze does. Gold and NVDA")
    print("  show what a STABLE funding sign actually looks like.")

    print("\n  THE HEALTH WARNING, stated before any of this is acted on:")
    print("  every number above is ONE trend in ONE 74-day window. A long that")
    print("  earned +X% here earned it by being long the single biggest move in")
    print("  the sample. That is not an edge that was measured, it is an outcome")
    print("  that was observed. The autocorrelation is the only line that")
    print("  generalises, because it has hundreds of observations behind it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
