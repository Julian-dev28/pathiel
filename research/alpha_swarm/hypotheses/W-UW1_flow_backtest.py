#!/usr/bin/env python3
"""W-UW1 — does Unusual Whales options flow predict xyz tokenized-equity moves?

Thesis: net options premium (net_call_premium - net_put_premium) on a US equity is
smart-money directional positioning that LEADS spot. Our best book trades the SAME names
as xyz tokens (xyz:AAPL, xyz:NVDA, ...). If flow leads, a cross-sectional book — long the
most-bullish-flow names, short the most-bearish — should beat a matched random null.

Method: for a liquid xyz-equity universe, pull UW daily net-premium (last ~45 weekdays,
cached) and the xyz:TICKER daily forward return from HL. Each US-trading day t: rank names
by net_premium, LONG top-k / SHORT bottom-k, hold to t+1d and t+5d (the book's horizon).
Cross-sectional per-leg signed EV, fees 25bps, matched random-book null (2000), OOS halves.
Zero capital — validates the data before any subscription bet, same discipline as every book.
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
SCRATCH = Path("/private/tmp/claude-501/-Users-julian-dev-Documents-code-hermes-trader/"
               "63d7d57b-6290-4704-91fe-5931e8fda8f7/scratchpad")
os.environ["UW_CACHE_DIR"] = str(SCRATCH / "uw_cache")
from hermes_trader.client import uw_client as uw          # noqa: E402
from hermes_trader.client.hl_client import fetch_hl_candles  # noqa: E402

SEED = 20260723
FEE = 0.0025
K = 3                    # legs per side
N_DAYS = 45             # trailing weekdays of flow to pull
TICKERS = ["AAPL", "NVDA", "TSLA", "MSFT", "GOOGL", "AMD", "MU", "INTC",
           "COIN", "MSTR", "PLTR", "META", "AMZN", "AVGO", "SMCI"]
T, O, C = 0, 1, 4


def weekdays(n):
    out, d = [], datetime.now(timezone.utc).date() - timedelta(days=1)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d -= timedelta(days=1)
    return sorted(out)


def hl_daily(ticker):
    """xyz:TICKER daily closes keyed by UTC date -> {date: (open,close)}."""
    cs = fetch_hl_candles(f"xyz:{ticker}", "1d", 400)
    if not cs:
        return {}
    out = {}
    for b in cs[:-1]:
        dt = datetime.fromtimestamp(b.t / 1000, timezone.utc).date().isoformat()
        out[dt] = (float(b.o), float(b.c))
    return out


def build_panel():
    dates = weekdays(N_DAYS)
    prices = {}
    for tk in TICKERS:
        prices[tk] = hl_daily(tk)
        time.sleep(0.2)
    # rows: per (date, ticker) with signal + forward returns
    rows = []
    for i, tk in enumerate(TICKERS):
        px = prices[tk]
        if not px:
            print(f"  {tk}: no xyz price on HL — skip", file=sys.stderr)
            continue
        pdays = sorted(px)
        for d in dates:
            np = uw.net_prem_daily(tk, d)
            time.sleep(0.15)
            if not np or d not in px:
                continue
            # forward return over 1 and 5 HL-days after date d
            try:
                idx = pdays.index(d)
            except ValueError:
                continue
            def fwd(h):
                if idx + h >= len(pdays):
                    return None
                return px[pdays[idx + h]][1] / px[d][0] - 1.0
            # normalise flow by gross premium so mega-caps don't dominate the rank
            gross = abs(np["net_call_premium"]) + abs(np["net_put_premium"]) + 1.0
            rows.append({"date": d, "ticker": tk,
                         "signal": np["net_premium"] / gross,
                         "raw_net": np["net_premium"],
                         "fwd1": fwd(1), "fwd5": fwd(5)})
        print(f"  {tk}: {sum(1 for r in rows if r['ticker']==tk)} day-rows", file=sys.stderr)
    return rows


def xs_book(rows, hkey, k=K):
    """Per date, long top-k / short bottom-k by signal; per-leg signed EV net of fees."""
    by_date = {}
    for r in rows:
        if r[hkey] is not None:
            by_date.setdefault(r["date"], []).append(r)
    evs, per_leg = [], []
    for d, rs in by_date.items():
        if len(rs) < 2 * k:
            continue
        rs.sort(key=lambda x: -x["signal"])
        longs = rs[:k]
        shorts = rs[-k:]
        ev = (st.fmean(r[hkey] for r in longs) - st.fmean(r[hkey] for r in shorts)) / 2 - FEE
        evs.append(ev)
        per_leg.append((d, [r[hkey] for r in longs], [r[hkey] for r in shorts], rs))
    return evs, per_leg


def null_p(per_leg, obs, hkey, k=K):
    rnd = random.Random(SEED)
    ge = 0
    for _ in range(2000):
        tot = []
        for _d, _l, _s, rs in per_leg:
            pick = rnd.sample(rs, 2 * k)
            tot.append((st.fmean(r[hkey] for r in pick[:k])
                        - st.fmean(r[hkey] for r in pick[k:])) / 2 - FEE)
        if tot and st.fmean(tot) >= obs:
            ge += 1
    return (1 + ge) / 2001


def main():
    if not uw.has_key():
        print("NO UW_API_KEY — aborting"); return
    print(f"pulling UW flow x {len(TICKERS)} tickers x {N_DAYS} weekdays (cached)...", file=sys.stderr)
    rows = build_panel()
    print(f"\npanel: {len(rows)} ticker-day rows, {len({r['date'] for r in rows})} dates, "
          f"{len({r['ticker'] for r in rows})} tickers")
    for hkey in ("fwd1", "fwd5"):
        evs, per_leg = xs_book(rows, hkey)
        if len(evs) < 8:
            print(f"{hkey}: only {len(evs)} rebalances — too few"); continue
        ev = st.fmean(evs)
        h = len(evs) // 2
        h1, h2 = st.fmean(evs[:h]), st.fmean(evs[h:])
        p = null_p(per_leg, ev, hkey)
        verdict = ("ROBUST" if ev > 0 and p < 0.05 and h1 > 0 and h2 > 0
                   else "MARGINAL" if ev > 0 and p < 0.10 else "REFUTED")
        print(f"{hkey}: n_rebal={len(evs)}  EV/leg net25 {ev*100:+.3f}%  "
              f"halves {h1*100:+.2f}/{h2*100:+.2f}  null p={p:.4f}  -> {verdict}")
    print("\nsignal = net_call_premium - net_put_premium (normalised); LONG bullish-flow / SHORT bearish.")


if __name__ == "__main__":
    main()
