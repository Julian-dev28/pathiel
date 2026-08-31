#!/usr/bin/env python3
"""W-UW2 — UW options-flow SIGNAL BATTERY on xyz equities (reuses W-UW1's cache).

One net-prem-ticks pull carries several signals. Test each against a matched null so we
keep only the EV+ ones (same discipline that killed numerology). Signals per ticker-day:
  net_premium  = net_call_premium - net_put_premium      (directional smart-money $)
  net_volume   = net_call_volume  - net_put_volume        (directional contracts)
  aggression   = (call_ask-call_bid) - (put_ask-put_bid)  (who's lifting the offer)
  neg_pcr      = -(put_vol/call_vol)                        (greed; contrarian if extreme)
Two book styles per signal: CROSS-SECTIONAL (long top-k / short bottom-k by signal each
day) and DIRECTIONAL (long if own signal>0 else short). Horizons +1d/+5d, 25bps, matched
null (2000), OOS halves. Cache-warm after W-UW1; zero extra pull if the cache exists.
"""
from __future__ import annotations

import os
import random
import statistics as st
import sys
import time
from datetime import datetime, timedelta, timezone
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
N_DAYS = 45
TICKERS = ["AAPL", "NVDA", "TSLA", "MSFT", "GOOGL", "AMD", "MU", "INTC",
           "COIN", "MSTR", "PLTR", "META", "AMZN", "AVGO", "SMCI"]
SIGNALS = ["net_premium", "net_volume", "aggression", "neg_pcr"]


def weekdays(n):
    out, d = [], datetime.now(timezone.utc).date() - timedelta(days=1)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d -= timedelta(days=1)
    return sorted(out)


def hl_daily(t):
    cs = fetch_hl_candles(f"xyz:{t}", "1d", 400)
    out = {}
    for b in (cs or [])[:-1]:
        out[datetime.fromtimestamp(b.t / 1000, timezone.utc).date().isoformat()] = (float(b.o), float(b.c))
    return out


def panel():
    dates = weekdays(N_DAYS)
    rows = []
    for t in TICKERS:
        px = hl_daily(t)
        time.sleep(0.15)
        if not px:
            continue
        pdays = sorted(px)
        for d in dates:
            np = uw.net_prem_daily(t, d)
            if not np or d not in px:
                continue
            idx = pdays.index(d)
            def fwd(h):
                return None if idx + h >= len(pdays) else px[pdays[idx + h]][1] / px[d][0] - 1.0
            gross = abs(np["net_call_premium"]) + abs(np["net_put_premium"]) + 1.0
            vol = np["call_volume"] + np["put_volume"] + 1.0
            rows.append({"date": d, "ticker": t, "fwd1": fwd(1), "fwd5": fwd(5),
                         "net_premium": np["net_premium"] / gross,
                         "net_volume": np["net_volume"] / vol,
                         "aggression": np["aggression"] / vol,
                         "neg_pcr": -np["pcr"]})
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


def directional(rows, sig, hk):
    xs_ = [r for r in rows if r[hk] is not None]
    if len(xs_) < 20:
        return None
    ev = st.fmean((1 if r[sig] > 0 else -1) * r[hk] - FEE for r in xs_)
    return ev, len(xs_)


def null_xs(groups, obs, hk, k=K):
    rnd = random.Random(SEED); ge = 0
    for _ in range(2000):
        tot = [(st.fmean(r[hk] for r in rnd.sample(g, 2 * k)[:k])
                - st.fmean(r[hk] for r in rnd.sample(g, 2 * k)[k:])) / 2 - FEE for g in groups]
        if tot and st.fmean(tot) >= obs:
            ge += 1
    return (1 + ge) / 2001


def main():
    if not uw.has_key():
        print("NO UW_API_KEY"); return
    rows = panel()
    print(f"panel: {len(rows)} ticker-day rows, {len({r['date'] for r in rows})} dates")
    print(f"\n{'signal':<12}{'style':<7}{'H':<4}{'n':<5}{'EV/leg net25':<14}{'halves':<16}{'p':<8}verdict")
    print("-" * 78)
    best = []
    for sig in SIGNALS:
        for hk in ("fwd1", "fwd5"):
            evs, groups = xs(rows, sig, hk)
            if len(evs) >= 8:
                ev = st.fmean(evs); h = len(evs) // 2
                h1, h2 = st.fmean(evs[:h]), st.fmean(evs[h:])
                p = null_xs(groups, ev, hk)
                v = ("ROBUST" if ev > 0 and p < 0.05 and h1 > 0 and h2 > 0
                     else "MARGINAL" if ev > 0 and p < 0.10 else "REFUTED")
                print(f"{sig:<12}{'xs':<7}{hk[-1]:<4}{len(evs):<5}{ev*100:+.3f}%{'':<7}"
                      f"{h1*100:+.2f}/{h2*100:<9.2f}{p:<8.4f}{v}")
                if v != "REFUTED":
                    best.append((sig, "xs", hk, ev, p))
            dr = directional(rows, sig, hk)
            if dr:
                ev, n = dr
                print(f"{sig:<12}{'dir':<7}{hk[-1]:<4}{n:<5}{ev*100:+.3f}%{'':<7}{'—':<16}{'—':<8}"
                      f"{'(+)' if ev>0 else '(-)'}")
    print()
    if best:
        print("SURVIVORS (not refuted):")
        for s, style, hk, ev, p in sorted(best, key=lambda x: -x[3]):
            print(f"  {s} {style} {hk}: EV/leg {ev*100:+.3f}%  p={p:.4f}")
    else:
        print("No cross-sectional survivor. (directional signs above are diagnostic.)")


if __name__ == "__main__":
    main()
