#!/usr/bin/env python3
"""H2 — cross-sectional momentum vs reversal, and the OI cut on it.

PRE-REGISTERED BEFORE THE FIRST RUN, and it exists because H1 failed in an
informative way rather than a boring one.

WHAT H1 TAUGHT
--------------
H1 shorted the most crowded funding and lost 0.285%/trade. The decile table
said why: on D10 the carry leg paid +0.245% exactly as theory predicts, and the
price leg went -0.323% against it. Coins paying the most funding kept going up.
So in this tape high funding is a MOMENTUM marker, not a crowding-to-revert
marker, and any book built on "fade the crowd" is fighting the direction that
actually pays.

THE CLAIM
---------
The regime read says the index is going nowhere (median +0.63%) while
cross-sectional dispersion is 7.08% over four days. That is the definition of a
market where relative performance carries the information and direction does
not.

H2a: a coin's trailing 3-day return predicts its next 24 hours cross-sectionally
     — the top decile of trailing return beats the bottom decile.
H2b: open interest conditions it. Price up on RISING OI is new money taking a
     side; price up on FALLING OI is short covering, which has nothing behind
     it once the shorts are done. So momentum should be stronger where OI
     confirms and weaker or absent where it does not.

H2b is the part worth having. H2a alone is a factor anyone can compute from
public candles. The OI cut uses the panel this project has been quietly
collecting for 70 days and almost nobody outside a venue has per-coin.

SPECIFICATION (frozen, same discipline as H1)
---------------------------------------------
universe    >= 90% panel coverage, >= $1M 24h volume at entry.
feature     trailing 3d price return, ranked cross-sectionally AT EACH SNAPSHOT
            (not pooled), because a pooled rank compares a coin today against
            the whole market's history and quietly becomes a time-series signal.
condition   OI change over the same trailing 3d, split at zero.
outcome     next-24h return, long side, minus 25bps round trip. Funding is
            included for the long (paying positive funding is a real cost and
            H1 showed the carry leg is not small).
stats       decile ladder, IC, and a bootstrap clustered on entry timestamp —
            300 coins in one snapshot share one market and are not 300
            independent draws.
threshold   D10-D1 spread > 0.5%/trade net, IC of the right sign, p < 0.05, and
            the sign holding in both time halves.

    python research/regime_2026_09/H2_dispersion.py
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
from H1_funding_carry import (DAY_MS, HOUR_MS, MIN_VOL_USD, SLIP_PCT, _f,  # noqa: E402
                              load_panel, spearman)

LOOKBACK_D = 3.0


def build(horizon_h: float) -> List[dict]:
    stamps, panel = load_panel()
    need = len(stamps) * 0.9
    universe = [c for c, s in panel.items() if len(s) >= need]
    horizon_ms = int(horizon_h * HOUR_MS)
    look_ms = int(LOOKBACK_D * DAY_MS)

    # Ranked WITHIN each snapshot, so the comparison is always coin-vs-market
    # at one instant. Pooling every observation and ranking once would let a
    # calm week and a volatile one share a scale, turning a cross-sectional
    # question into a time-series one without anyone deciding to.
    per_ts: Dict[int, List[dict]] = defaultdict(list)
    for coin in universe:
        series = panel[coin]
        ts_sorted = sorted(series)
        for i, t in enumerate(ts_sorted):
            px_now = _f(series[t], "px")
            vol = _f(series[t], "v") or 0.0
            if not px_now or vol < MIN_VOL_USD:
                continue
            past = [u for u in ts_sorted[:i] if u <= t - look_ms]
            if not past:
                continue
            t_past = past[-1]
            px_past = _f(series[t_past], "px")
            oi_past, oi_now = _f(series[t_past], "oi"), _f(series[t], "oi")
            if not px_past:
                continue
            exit_t = next((u for u in ts_sorted[i + 1:] if u >= t + horizon_ms), None)
            if exit_t is None:
                continue
            px_exit = _f(series[exit_t], "px")
            if not px_exit:
                continue
            f_now, f_exit = _f(series[t], "f"), _f(series[exit_t], "f")
            f_avg = (f_now + f_exit) / 2.0 if (f_now is not None and f_exit is not None) else (f_now or 0.0)
            hours = (exit_t - t) / HOUR_MS
            per_ts[t].append({
                "coin": coin, "t": t,
                "mom": (px_now / px_past - 1.0) * 100.0,
                "oi_chg": ((oi_now / oi_past - 1.0) * 100.0
                           if oi_past and oi_now and oi_past > 0 else None),
                # Long side: price move, minus funding paid, minus slippage.
                "ret": (px_exit / px_now - 1.0) * 100.0 - f_avg * hours * 100.0 - SLIP_PCT,
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


def bootstrap_p(obs: List[dict], lo_hi, n_iter: int = 2000) -> float:
    by_t: Dict[int, List[float]] = defaultdict(list)
    for o in obs:
        if o["mom_pct"] >= 90:
            by_t[o["t"]].append(o["ret"])
        elif o["mom_pct"] <= 10:
            by_t[o["t"]].append(-o["ret"])       # the short leg of the spread
    keys = [t for t, v in by_t.items() if v]
    if len(keys) < 10:
        return 1.0
    rng = random.Random(11)
    worse = 0
    for _ in range(n_iter):
        pool: List[float] = []
        for _ in range(len(keys)):
            pool.extend(by_t[rng.choice(keys)])
        if pool and st.mean(pool) <= 0:
            worse += 1
    return worse / n_iter


def ladder(obs: List[dict], label: str) -> Optional[float]:
    if len(obs) < 200:
        print(f"\n  {label}: only {len(obs)} observations, not scored")
        return None
    obs = sorted(obs, key=lambda o: o["mom_pct"])
    k = max(1, len(obs) // 10)
    print(f"\n  {label}   n={len(obs)}")
    print(f"    {'decile':<9}{'n':>7}{'3d mom%':>10}{'next 24h%':>12}")
    for d in range(10):
        chunk = obs[d * k:(d + 1) * k] if d < 9 else obs[9 * k:]
        if chunk:
            print(f"    D{d+1:<8}{len(chunk):>7}{st.mean(o['mom'] for o in chunk):>+10.2f}"
                  f"{st.mean(o['ret'] for o in chunk):>+12.3f}")
    top = [o["ret"] for o in obs if o["mom_pct"] >= 90]
    bot = [o["ret"] for o in obs if o["mom_pct"] <= 10]
    spread = st.mean(top) - st.mean(bot)
    ic = spearman([o["mom_pct"] for o in obs], [o["ret"] for o in obs])
    print(f"    top {st.mean(top):+.3f}%   bottom {st.mean(bot):+.3f}%   "
          f"spread {spread:+.3f}%   IC {ic:+.4f}")
    return spread


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="H2_dispersion")
    ap.add_argument("--horizon-h", type=float, default=24.0)
    a = ap.parse_args(argv)
    print(f"H2 — cross-sectional 3d momentum, {a.horizon_h:.0f}h hold, long side,"
          f" net of funding and {SLIP_PCT}% round trip")

    obs = build(a.horizon_h)
    if not obs:
        print("  not enough panel history")
        return 1
    print(f"\n  {len(obs)} observations, {len({o['coin'] for o in obs})} coins, "
          f"{len({o['t'] for o in obs})} snapshots")

    ladder(obs, "H2a  all coins")

    # H2b: does open interest separate real momentum from a short squeeze?
    conf = [o for o in obs if o["oi_chg"] is not None and o["oi_chg"] > 0]
    unconf = [o for o in obs if o["oi_chg"] is not None and o["oi_chg"] <= 0]
    s_conf = ladder(conf, "H2b  OI RISING (new money)")
    s_unconf = ladder(unconf, "H2b  OI FALLING (squeeze / unwind)")

    p = bootstrap_p(obs, None)
    print(f"\n  bootstrap p (all coins, clustered on snapshot): {p:.4f}")
    cut = sorted({o["t"] for o in obs})[len({o["t"] for o in obs}) // 2]
    for name, sub in (("first half", [o for o in obs if o["t"] < cut]),
                      ("second half", [o for o in obs if o["t"] >= cut])):
        top = [o["ret"] for o in sub if o["mom_pct"] >= 90]
        bot = [o["ret"] for o in sub if o["mom_pct"] <= 10]
        if top and bot:
            print(f"  {name:<12} spread {st.mean(top) - st.mean(bot):+.3f}%")
    if s_conf is not None and s_unconf is not None:
        print(f"\n  OI CUT: confirmed {s_conf:+.3f}%  vs  unconfirmed {s_unconf:+.3f}%"
              f"   (edge from OI: {s_conf - s_unconf:+.3f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
