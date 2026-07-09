#!/usr/bin/env python3
"""W-E4 — xyz mark-vs-oracle premium dislocation fade.

HL fundingHistory rows expose hourly (fundingRate, premium) per market. On xyz
tokenized equities the oracle can go STALE while the 24/7 perp keeps moving
(nights/weekends), so premium spikes are structural, not just crowding. Do
premium extremes mean-revert TRADEABLY in price space (fade the dislocated
side), and does the answer differ when the underlying is open vs shut?

Rule:
  signal at funding row time t: premium p. Episode = threshold CROSSING
  (|p| >= thr, re-arm when |p| < thr/2, plus >=12h per-coin cooldown).
  side = -sign(p) (short the rich perp / long the cheap one).
  fill = open of the next 1h candle after t. exits: hold {4,8,24}h.
  Funding accrual NETTED into the trade (fading positive premium usually
  COLLECTS funding). Costs via alpha_lib tiers. Stop sweep on the fade.
  Split: signal during RTH vs closed hours. Nulls: random-sign + same-side pool.
Thresholds set from the printed premium distribution (p90/p97.5/p99 of |p|),
not hand-picked.
"""
from __future__ import annotations
import random, statistics, sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import importlib
we = importlib.import_module("W-E_lib")
import alpha_lib
import mc_null

T, O, C = we.T, we.O, we.C
HOUR = we.HOUR


def is_rth(t_ms: int) -> bool:
    day = date.fromtimestamp(t_ms / 1000)
    if not we.is_trading_day(day):
        return False
    o_, c_ = we.rth_utc(day)
    return o_ <= t_ms < c_


def episodes_for(coin, rows, idx, thr, hold_h, mode="fade"):
    """Threshold-crossing episodes with funding netted. mode: fade|momo."""
    eps = []
    armed = True
    last_entry = -10**18
    rate_by_t = {int(r[0]): float(r[1]) for r in rows}
    for r in rows:
        t, rate, prem = int(r[0]), float(r[1]), float(r[2])
        if abs(prem) < thr / 2:
            armed = True
            continue
        if abs(prem) < thr or not armed or t - last_entry < 12 * HOUR:
            continue
        armed = False
        last_entry = t
        fill_t = ((t // HOUR) + 1) * HOUR
        entry_b = idx.get(fill_t)
        exit_b = idx.get(fill_t + (hold_h - 1) * HOUR)
        if not entry_b or not exit_b:
            continue
        if mode == "fade":
            side = "short" if prem > 0 else "long"
        else:
            side = "long" if prem > 0 else "short"
        sign = -1.0 if side == "short" else 1.0
        entry = float(entry_b[O]); exitp = float(exit_b[C])
        ret_px = sign * (exitp - entry) / entry
        # funding accrual over held hours: positive rate -> longs pay shorts
        fund = 0.0
        for h in range(hold_h):
            rt = rate_by_t.get(fill_t + h * HOUR)
            if rt is not None:
                fund += (rt if side == "short" else -rt)
        eps.append({"t": fill_t, "ep": f"{coin}|{fill_t}", "coin": coin,
                    "prem": prem, "side": side, "ret": ret_px + fund,
                    "ret_px": ret_px, "fund": fund, "rth": is_rth(t),
                    "idx": idx, "fill_t": fill_t, "hold_h": hold_h})
    return eps


def random_sign_null(rets, n_iter=5000, seed=0):
    rng = random.Random(seed)
    obs_m = statistics.mean(rets)
    return sum(1 for _ in range(n_iter)
               if statistics.mean(x * (1 if rng.random() < 0.5 else -1) for x in rets) >= obs_m) / n_iter


def main():
    d = we.load()
    fund = {c: v for c, v in d["funding"].items() if len(v) >= 500}
    print(f"funding coverage: {[(c, len(v)) for c, v in fund.items()]}")

    # premium distribution -> thresholds
    allp = [abs(float(r[2])) for v in fund.values() for r in v]
    allp.sort()
    q = lambda p: allp[int(p * (len(allp) - 1))]
    p90, p975, p99 = q(0.90), q(0.975), q(0.99)
    print(f"|premium| bps: p50 {q(0.5)*1e4:.1f}  p90 {p90*1e4:.1f}  "
          f"p97.5 {p975*1e4:.1f}  p99 {p99*1e4:.1f}  max {allp[-1]*1e4:.1f}")

    idxs = {c: we.by_t(d["candles_1h"][c]) for c in fund}
    for mode in ("fade", "momo"):
        for thr_lbl, thr in (("p90", p90), ("p97.5", p975), ("p99", p99)):
            for hold_h in (4, 8, 24):
                eps = []
                for c, rows in fund.items():
                    eps += episodes_for(c, rows, idxs[c], thr, hold_h, mode)
                if len(eps) < 15:
                    print(f"\n{mode} thr={thr_lbl} hold={hold_h}h: n={len(eps)} — below 15, no verdict")
                    continue
                s = alpha_lib.summarize(eps)
                p_sign = random_sign_null([e["ret"] for e in eps])
                n_rth = sum(1 for e in eps if e["rth"])
                fund_part = statistics.mean(e["fund"] for e in eps)
                print(f"\n{mode.upper()} premium thr={thr_lbl}({thr*1e4:.0f}bps) hold={hold_h}h  "
                      f"n={s['n']} ({n_rth} RTH / {s['n']-n_rth} closed)  "
                      f"p_sign={p_sign:.4f}  avg funding leg {fund_part*1e4:+.1f}bps")
                print(we.fmt_summary(s))
                # closed-hours only split (the structural story)
                closed = [e for e in eps if not e["rth"]]
                if len(closed) >= 15:
                    sc = alpha_lib.summarize(closed)
                    pc = random_sign_null([e["ret"] for e in closed])
                    print(f"  closed-only: n={sc['n']} p_sign={pc:.4f} "
                          f"mean@12bps {sc['slip12']['mean_ret_pct']:+.3f}% "
                          f"@25bps {sc['slip25']['mean_ret_pct']:+.3f}% "
                          f"OOS {sc['oos_12bps']['first_half_mean_pct']}/{sc['oos_12bps']['second_half_mean_pct']}")
                # stop sweep at the mid threshold / 8h
                if thr_lbl == "p97.5" and hold_h == 8:
                    for sp in (0.08, 0.15, 0.20, 0.25, 0.40):
                        rs = []
                        for e in eps:
                            r = we.hold_return(e["idx"], e["fill_t"],
                                               e["fill_t"] + e["hold_h"] * HOUR,
                                               e["side"], stop_pct=sp)
                            if r is not None:
                                rs.append(r + e["fund"])
                        print(f"  stop {int(sp*100)}%: mean {100*statistics.mean(rs):+.3f}% (n={len(rs)})")


if __name__ == "__main__":
    main()
