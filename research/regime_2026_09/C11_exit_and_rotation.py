#!/usr/bin/env python3
"""Cycle 11 — the gaps: exits, take-profit, rotation, hold length.

WHAT THE BOOK DOES TODAY
Enter the top 2% by 3d return, short it, hold 72 hours, hard stop at 15%. That
is the WHOLE exit policy. bounded_exit_override sets protect_pct to 9999 (i.e.
unreachable), no trailing stop, no breakeven ratchet, no ATR stop, no partial
scale-out. Once open, the position is blind until its timer expires.

Two gaps follow directly, and neither is visible in a per-trade EV number:

  1. NO PROFIT IS EVER TAKEN. A short that is +12% at hour 6 rides to hour 72
     and can hand it all back.
  2. NO ROTATION. With 3 slots and a 72h hold, a slot is committed for three
     days. If the signal that filled it decays on day one and a far stronger
     candidate appears on day two, the book cannot act — the capital is locked
     in a position the ranking no longer likes.

Gap 2 is the structural one. A cross-sectional book's edge is "be short the
strongest names"; a fixed 72h timer means it is short whatever WAS strongest up
to three days ago. Those are different strategies and only one of them is the
one that was measured.

WHY A PORTFOLIO SIMULATOR AND NOT MORE EV-PER-TRADE
Rotation changes WHICH trades are taken, so per-trade EV cannot score it — a
rule that takes different trades has a different denominator. The only fair
comparison is running each policy over the same panel with the same 3 slots and
the same costs, and reading dollars and drawdown off the equity curve. C4 and C6
both established this; a rule that raises EV per trade while cutting turnover
can and does lose money.

COSTS ARE CHARGED HONESTLY
Every entry and every exit pays SLIP_PCT/2, so a rotation that churns is
penalised exactly as much as it churns. Funding accrues to the short across the
hold. A policy that looks good only because its costs were not counted is the
main thing this script exists to rule out.

    python research/regime_2026_09/C11_exit_and_rotation.py
"""
from __future__ import annotations

import statistics as st
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from C1_session_structure import load_panel  # noqa: E402
from H1_funding_carry import DAY_MS, HOUR_MS, SLIP_PCT, _f  # noqa: E402

LOOKBACK_D = 3.0
MIN_VOL = 1_000_000
SLOTS = 3
STOP_PCT = 15.0
HALF_COST = SLIP_PCT / 2.0          # charged on entry AND on exit


def panel_frames():
    """Ordered snapshots: {ts: {coin: {px, mom_pct, funding}}}."""
    panel = {c: s for c, s in load_panel().items() if ":" in c}
    all_ts = sorted({t for s in panel.values() for t in s})
    need = len(all_ts) * 0.9
    universe = [c for c, s in panel.items() if len(s) >= need]

    per_ts: Dict[int, Dict[str, dict]] = defaultdict(dict)
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
            per_ts[t][coin] = {"px": px, "mom": (px / p0 - 1) * 100,
                               "f": _f(series[t], "f") or 0.0}
    frames = []
    for t in sorted(per_ts):
        rows = per_ts[t]
        if len(rows) < 20:
            continue
        order = sorted(rows, key=lambda c: rows[c]["mom"])
        n = len(order)
        for rank, c in enumerate(order):
            rows[c]["pct"] = rank / (n - 1) * 100
        frames.append((t, rows))
    return frames


class Position:
    __slots__ = ("coin", "entry_px", "entry_t", "peak_gain")

    def __init__(self, coin, px, t):
        self.coin, self.entry_px, self.entry_t = coin, px, t
        self.peak_gain = 0.0

    def gain(self, px: float) -> float:
        """Short P&L in percent, price only."""
        return (self.entry_px - px) / self.entry_px * 100


def simulate(frames, entry_pct: float, hold_h: Optional[float],
             tp: Optional[float] = None, trail: Optional[float] = None,
             exit_below_pct: Optional[float] = None,
             rotate: bool = False) -> dict:
    """One policy over the whole panel. Returns dollars-shaped stats."""
    held: Dict[str, Position] = {}
    rets: List[float] = []
    equity, curve = 100.0, [100.0]
    n_stop = n_tp = n_trail = n_time = n_rot = n_sig = 0

    for t, rows in frames:
        # ---- exits ---------------------------------------------------
        for coin in list(held):
            p = held[coin]
            row = rows.get(coin)
            if row is None:
                continue
            g = p.gain(row["px"])
            p.peak_gain = max(p.peak_gain, g)
            hrs = (t - p.entry_t) / HOUR_MS
            why = None
            if g <= -STOP_PCT:
                why, _ = "stop", n_stop
                n_stop += 1
            elif tp is not None and g >= tp:
                why = "tp"; n_tp += 1
            elif (trail is not None and p.peak_gain >= trail
                  and g <= p.peak_gain - trail):
                why = "trail"; n_trail += 1
            elif exit_below_pct is not None and row["pct"] < exit_below_pct:
                why = "signal"; n_sig += 1
            elif hold_h is not None and hrs >= hold_h:
                why = "time"; n_time += 1
            if why:
                carry = row["f"] * hrs * 100
                r = g + carry - 2 * HALF_COST
                rets.append(r)
                equity *= (1 + r / 100 * (1.0 / SLOTS))
                curve.append(equity)
                del held[coin]

        # ---- rotation: drop anything no longer top-ranked -------------
        if rotate and len(rows) >= 20:
            keep = {c for c in rows if rows[c]["pct"] >= entry_pct}
            for coin in list(held):
                if coin in rows and coin not in keep:
                    p = held[coin]
                    hrs = (t - p.entry_t) / HOUR_MS
                    g = p.gain(rows[coin]["px"])
                    r = g + rows[coin]["f"] * hrs * 100 - 2 * HALF_COST
                    rets.append(r); n_rot += 1
                    equity *= (1 + r / 100 * (1.0 / SLOTS))
                    curve.append(equity)
                    del held[coin]

        # ---- entries -------------------------------------------------
        if len(held) < SLOTS:
            cands = sorted((c for c in rows if rows[c]["pct"] >= entry_pct
                            and c not in held),
                           key=lambda c: -rows[c]["mom"])
            for c in cands[: SLOTS - len(held)]:
                held[c] = Position(c, rows[c]["px"], t)

    if not rets:
        return {}
    span_d = (frames[-1][0] - frames[0][0]) / DAY_MS
    peak, mdd = curve[0], 0.0
    for v in curve:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    return {"n": len(rets), "ev": st.mean(rets),
            "total": equity - 100.0,
            "per_month": (equity - 100.0) / span_d * 30,
            "mdd": mdd * 100, "win": 100 * sum(1 for r in rets if r > 0) / len(rets),
            "exits": f"stop{n_stop} tp{n_tp} trail{n_trail} sig{n_sig} "
                     f"time{n_time} rot{n_rot}"}


def main() -> int:
    frames = panel_frames()
    span = (frames[-1][0] - frames[0][0]) / DAY_MS
    print(f"C11 - exits, take-profit, rotation\n")
    print(f"  {len(frames)} snapshots over {span:.0f} days, {SLOTS} slots, "
          f"{STOP_PCT:.0f}% stop, costs {SLIP_PCT}% round trip\n")

    policies = [
        ("LIVE: top2%, hold 72h",            dict(entry_pct=98, hold_h=72)),
        ("top2%, hold 24h",                  dict(entry_pct=98, hold_h=24)),
        ("top2%, hold 72h + TP 8%",          dict(entry_pct=98, hold_h=72, tp=8)),
        ("top2%, hold 72h + TP 5%",          dict(entry_pct=98, hold_h=72, tp=5)),
        ("top2%, hold 72h + trail 5%",       dict(entry_pct=98, hold_h=72, trail=5)),
        ("top2%, hold 72h + trail 3%",       dict(entry_pct=98, hold_h=72, trail=3)),
        ("top2%, exit when pct<90",          dict(entry_pct=98, hold_h=None,
                                                  exit_below_pct=90)),
        ("top2%, exit when pct<95",          dict(entry_pct=98, hold_h=None,
                                                  exit_below_pct=95)),
        ("ROTATE: always short top2%",       dict(entry_pct=98, hold_h=None,
                                                  rotate=True)),
        ("ROTATE top5%",                     dict(entry_pct=95, hold_h=None,
                                                  rotate=True)),
        ("ROTATE top2% + TP 8%",             dict(entry_pct=98, hold_h=None,
                                                  rotate=True, tp=8)),
        ("ROTATE top2% + trail 5%",          dict(entry_pct=98, hold_h=None,
                                                  rotate=True, trail=5)),
    ]
    out = []
    print(f"  {'policy':<30}{'trades':>7}{'EV%':>8}{'total%':>9}{'%/mo':>8}"
          f"{'maxDD':>8}{'win':>6}")
    for name, kw in policies:
        r = simulate(frames, **kw)
        if not r:
            continue
        out.append((name, r))
        print(f"  {name:<30}{r['n']:>7}{r['ev']:>+7.2f}{r['total']:>+8.1f}"
              f"{r['per_month']:>+8.1f}{r['mdd']:>7.1f}{r['win']:>5.0f}%")

    base = dict(out)["LIVE: top2%, hold 72h"]
    best = max(out, key=lambda kv: kv[1]["per_month"])
    print(f"\n  LIVE policy: {base['per_month']:+.1f}%/mo at {base['mdd']:.1f}% max drawdown")
    print(f"  BEST policy: {best[0]} at {best[1]['per_month']:+.1f}%/mo, "
          f"{best[1]['mdd']:.1f}% max drawdown")
    print(f"  exit mix of the best: {best[1]['exits']}")
    if best[1]["per_month"] > base["per_month"]:
        lift = best[1]["per_month"] - base["per_month"]
        print(f"  lift over live: {lift:+.1f} points/month")
    print("\n  Read the DRAWDOWN column beside the return. A policy that earns")
    print("  more by holding losers longer is not better, it is levered.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
