#!/usr/bin/env python3
"""Cycle 9 — does the short book bleed when it holds through earnings?

WHY THIS EVENT AND NOT THE INTERESTING ONES
The operator asked about policy catalysts: bills like the CLARITY Act, wartime
measures, oil and gold initiatives, funding rounds. Nearly all of it is
fetchable (services/events/fetch_events.py pulls the Fed calendar and the
Federal Register without a key). Almost none of it is TESTABLE here, and the
reason is arithmetic rather than effort:

    a named bill passing            n = 1, by definition
    executive orders per topic      n = 0-3 in 72 days
    FOMC decisions in the panel     n ~ 2

A strategy fitted to n=1 is a story with a backtest attached. Earnings is the
one scheduled event dense enough to measure: ~190 filings inside the same 72-day
window across 65 mapped tickers, arriving on a schedule nobody disputes.

THE CLAIM
xs_reversal shorts the top decile of 3d return and holds 24h. A name that just
ran 8% is disproportionately likely to be running INTO its earnings print. If
the hold spans that print, the position is no longer a reversal bet - it is a
coin flip on a binary event the book has no view on, with the fat tail pointing
against a short.

C9: entries whose hold spans an earnings date underperform those that do not.
If so, the fix is free: the same book, declining to open when the calendar says
a print lands inside the window. No new signal, no extra risk, no capital.

WHAT WOULD MAKE THIS NOISE
Splitting any sample two ways finds a difference. So the split is declared
before the run, both arms must clear the breadth guard (>=100 entry timestamps
AND >=25 distinct coins - C7 nearly shipped a book off 59 timestamps), the
bootstrap is clustered on entry timestamp, and the DIFFERENCE between arms is
tested, not just each arm's sign.

    python research/regime_2026_09/C9_earnings_filter.py
"""
from __future__ import annotations

import json
import random
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
CAL = ROOT / ".state" / "events_calendar.json"
LOOKBACK_D, HOLD_H = 3.0, 24.0
MIN_VOL, BASELINE_F, AWAKE_MIN = 1_000_000, 1.25e-05, 0.67
MIN_CLUSTERS, MIN_COINS = 100, 25


def earnings_index() -> Dict[str, set]:
    cal = json.loads(CAL.read_text())
    return {f"xyz:{tk}": set(dates) for tk, dates in cal["earnings"].items()}


def _day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def build(earn: Dict[str, set]) -> List[dict]:
    panel = {c: s for c, s in load_panel().items() if ":" in c}
    all_ts = sorted({t for s in panel.values() for t in s})
    need = len(all_ts) * 0.9
    universe = [c for c, s in panel.items() if len(s) >= need]

    per_ts: Dict[int, List[dict]] = defaultdict(list)
    for coin in universe:
        series, dates = panel[coin], earn.get(coin, set())
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
            # every calendar day the hold touches
            span = {_day(t + k * 12 * HOUR_MS)
                    for k in range(0, int(HOLD_H / 12) + 2)} | {_day(t), _day(xt)}
            per_ts[t].append({
                "coin": coin, "t": t, "awake": awake,
                "mom": (px / p0 - 1) * 100,
                "short_ret": -(p2 / px - 1) * 100 + favg * hours * 100 - SLIP_PCT,
                "earnings": bool(dates & span),
                "has_cal": coin in earn,
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


def boot_diff(a: List[dict], b: List[dict], n_iter: int = 2000) -> float:
    """P(mean(a) <= mean(b)) under resampling clustered on entry timestamp.
    Tests the DIFFERENCE, which is the actual claim."""
    ta = defaultdict(list); tb = defaultdict(list)
    for o in a: ta[o["t"]].append(o["short_ret"])
    for o in b: tb[o["t"]].append(o["short_ret"])
    ka, kb = list(ta), list(tb)
    if len(ka) < 10 or len(kb) < 10:
        return 1.0
    rng = random.Random(211)
    worse = 0
    for _ in range(n_iter):
        pa = [v for _ in ka for v in ta[rng.choice(ka)]]
        pb = [v for _ in kb for v in tb[rng.choice(kb)]]
        if pa and pb and st.mean(pa) <= st.mean(pb):
            worse += 1
    return worse / n_iter


def show(rows: List[dict], label: str) -> None:
    if len(rows) < 30:
        print(f"  {label:<30} n={len(rows):>5}   too few")
        return
    ev = st.mean(o["short_ret"] for o in rows)
    win = 100 * sum(1 for o in rows if o["short_ret"] > 0) / len(rows)
    ts = sorted({o["t"] for o in rows}); cut = ts[len(ts) // 2]
    h1 = [o["short_ret"] for o in rows if o["t"] < cut]
    h2 = [o["short_ret"] for o in rows if o["t"] >= cut]
    coins = len({o["coin"] for o in rows})
    thin = "" if (len(ts) >= MIN_CLUSTERS and coins >= MIN_COINS) else \
           f"  THIN({len(ts)}ts/{coins}coins)"
    print(f"  {label:<30} n={len(rows):>5}  EV {ev:+.3f}%  win {win:>3.0f}%  "
          f"halves {st.mean(h1):+.2f}/{st.mean(h2):+.2f}{thin}")


def main() -> int:
    if not CAL.exists():
        print("  no calendar — run services/events/fetch_events.py --refresh")
        return 1
    earn = earnings_index()
    obs = build(earn)
    covered = [o for o in obs if o["has_cal"]]
    print("C9 - does the short book bleed through earnings?\n")
    print(f"  {len(obs)} book-shaped entries; {len(covered)} on the "
          f"{len(earn)} tickers with an SEC calendar\n")

    spans = [o for o in covered if o["earnings"]]
    clean = [o for o in covered if not o["earnings"]]
    show(covered, "all calendar-covered")
    show(clean, "  hold is CLEAR of earnings")
    show(spans, "  hold SPANS an earnings date")

    if len(spans) >= 30 and len(clean) >= 30:
        d = st.mean(o["short_ret"] for o in clean) - st.mean(o["short_ret"] for o in spans)
        p = boot_diff(clean, spans)
        print(f"\n  difference (clean - spans): {d:+.3f}%   bootstrap p={p:.4f}")
        kept = 100 * len(clean) / len(covered)
        ts_c, co_c = len({o['t'] for o in spans}), len({o['coin'] for o in spans})
        breadth = ts_c >= MIN_CLUSTERS and co_c >= MIN_COINS
        print(f"  skipping them keeps {kept:.0f}% of trades")
        print("\nTHE READ")
        if not breadth:
            print(f"  The earnings arm is THIN: {ts_c} entry timestamps across "
                  f"{co_c} coins.")
            print("  n looks large because one print catches many entries on the same")
            print("  name; those are one event, not many. Not actionable at this size.")
        elif p < 0.05 and d > 0:
            print(f"  Earnings-spanning shorts underperform by {d:.3f}%/trade and the")
            print(f"  difference survives clustering (p={p:.4f}). The filter is free:")
            print(f"  same book, declining to open when a print lands in the window.")
        else:
            print(f"  No usable difference (p={p:.4f}). The book is not being hurt by")
            print("  earnings in a way this sample can detect, so a filter would cost")
            print(f"  {100-kept:.0f}% of trades and buy nothing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
