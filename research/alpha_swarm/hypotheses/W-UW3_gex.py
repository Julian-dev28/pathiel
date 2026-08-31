#!/usr/bin/env python3
"""W-UW3 — dealer GREEK EXPOSURE (GEX) on xyz equities: a different UW mechanism.

Net-flow (W-UW1/2) is directional smart-money positioning. GEX is where the DEALERS are
pinned: net_delta = dealer directional inventory; net_gamma > 0 = dealers long gamma
(suppress vol -> mean-reversion), < 0 = short gamma (amplify -> momentum). Independent of
flow, and greek-exposure returns ~250 daily rows in ONE call per ticker, so this tests on a
FULL YEAR (vs net-flow's 42 days) — a much stronger read if it holds.

Signals tested (cross-sectional, LONG top-k / SHORT bottom-k; and directional own-sign):
  net_delta  (dealer directional exposure, normalised)   -> does it lead spot?
  net_gamma  (regime: high = mean-revert, low = momentum) -> directional read
  neg_gamma  (-net_gamma: momentum-regime tilt)
Horizons +1d/+5d, 25bps, matched same-day random-book null (2000), OOS halves.
"""
from __future__ import annotations

import os
import random
import statistics as st
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
SCRATCH = Path("/private/tmp/claude-501/-Users-julian-dev-Documents-code-pathia/"
               "63d7d57b-6290-4704-91fe-5931e8fda8f7/scratchpad")
os.environ["UW_CACHE_DIR"] = str(SCRATCH / "uw_cache")
from pathia.client import uw_client as uw          # noqa: E402
from pathia.client.hl_client import fetch_hl_candles  # noqa: E402

SEED = 20260723
FEE = 0.0025
K = 3
TICKERS = ["AAPL", "NVDA", "TSLA", "MSFT", "GOOGL", "AMD", "MU", "INTC",
           "COIN", "MSTR", "PLTR", "META", "AMZN", "AVGO"]
SIGNALS = ["net_delta", "net_gamma", "neg_gamma"]


def hl_daily(t):
    cs = fetch_hl_candles(f"xyz:{t}", "1d", 400)
    out = {}
    for b in (cs or [])[:-1]:
        out[datetime.fromtimestamp(b.t / 1000, timezone.utc).date().isoformat()] = (float(b.o), float(b.c))
    return out


def panel():
    rows = []
    for t in TICKERS:
        px = hl_daily(t)
        time.sleep(0.15)
        gex = uw.greek_daily(t)
        if not px or not gex:
            continue
        pdays = sorted(px)
        for d, g in gex.items():
            if d not in px:
                continue
            idx = pdays.index(d)
            def fwd(h):
                return None if idx + h >= len(pdays) else px[pdays[idx + h]][1] / px[d][0] - 1.0
            # normalise greeks to comparable scale (sign + magnitude within reason)
            scale = abs(g["net_gamma"]) + abs(g["net_delta"]) + 1.0
            rows.append({"date": d, "ticker": t, "fwd1": fwd(1), "fwd5": fwd(5),
                         "net_delta": g["net_delta"] / scale,
                         "net_gamma": g["net_gamma"] / scale,
                         "neg_gamma": -g["net_gamma"] / scale})
    return rows


def xs(rows, sig, hk, k=K):
    byd = {}
    for r in rows:
        if r[hk] is not None:
            byd.setdefault(r["date"], []).append(r)
    evs, groups = [], []
    for d, rs in byd.items():
        if len(rs) < 2 * k:
            continue
        rs.sort(key=lambda x: -x[sig])
        evs.append((st.fmean(r[hk] for r in rs[:k]) - st.fmean(r[hk] for r in rs[-k:])) / 2 - FEE)
        groups.append(rs)
    return evs, groups


def null_xs(groups, obs, hk, k=K):
    rnd = random.Random(SEED); ge = 0
    for _ in range(2000):
        tot = [(st.fmean(r[hk] for r in rnd.sample(g, 2 * k)[:k])
                - st.fmean(r[hk] for r in rnd.sample(g, 2 * k)[k:])) / 2 - FEE for g in groups]
        if tot and st.fmean(tot) >= obs:
            ge += 1
    return (1 + ge) / 2001


def directional(rows, sig, hk):
    xs_ = [r for r in rows if r[hk] is not None]
    if len(xs_) < 30:
        return None
    return st.fmean((1 if r[sig] > 0 else -1) * r[hk] - FEE for r in xs_), len(xs_)


def main():
    if not uw.has_key():
        print("NO UW_API_KEY"); return
    rows = panel()
    print(f"panel: {len(rows)} ticker-day rows, {len({r['date'] for r in rows})} dates, "
          f"{len({r['ticker'] for r in rows})} tickers (full-year GEX)\n")
    print(f"{'signal':<11}{'style':<6}{'H':<4}{'n':<6}{'EV/leg net25':<15}{'halves':<16}{'p':<8}verdict")
    print("-" * 76)
    surv = []
    for sig in SIGNALS:
        for hk in ("fwd1", "fwd5"):
            evs, groups = xs(rows, sig, hk)
            if len(evs) >= 10:
                ev = st.fmean(evs); h = len(evs) // 2
                h1, h2 = st.fmean(evs[:h]), st.fmean(evs[h:])
                p = null_xs(groups, ev, hk)
                v = ("ROBUST" if ev > 0 and p < 0.05 and h1 > 0 and h2 > 0
                     else "MARGINAL" if ev > 0 and p < 0.10 else "REFUTED")
                print(f"{sig:<11}{'xs':<6}{hk[-1]:<4}{len(evs):<6}{ev*100:+.3f}%{'':<8}"
                      f"{h1*100:+.2f}/{h2*100:<9.2f}{p:<8.4f}{v}")
                if v != "REFUTED":
                    surv.append((sig, hk, ev, p))
            dr = directional(rows, sig, hk)
            if dr:
                ev, n = dr
                print(f"{sig:<11}{'dir':<6}{hk[-1]:<4}{n:<6}{ev*100:+.3f}%{'':<8}{'—':<16}{'—':<8}"
                      f"{'(+)' if ev > 0 else '(-)'}")
    print()
    print("SURVIVORS:" if surv else "No survivor — GEX does not lead spot cross-sectionally on this set.")
    for s, hk, ev, p in sorted(surv, key=lambda x: -x[2]):
        print(f"  {s} xs {hk}: +{ev*100:.3f}%  p={p:.4f}")


if __name__ == "__main__":
    main()
