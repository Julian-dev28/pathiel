#!/usr/bin/env python3
"""Does anything we already record predict which signal is worth a slot?

THE QUESTION, AND WHY IT IS THIS ONE
------------------------------------
The books write ~32 signals a day between them. `max_concurrent` is 10 and the
holds are one day, so the system can act on about ten. It currently takes them
in the order they arrive, which means roughly two thirds of every day's signals
are discarded by a rule with no opinion: the clock.

That makes selection, not discovery, the cheapest place to find P&L. A new book
has to be researched, validated and funded. A better ranking over signals we
already generate costs one comparison and ships behind the existing gates.

So: for each live book, join every graded signal's forward return to the
features recorded alongside it, and ask whether any feature separates the good
signals from the bad. If one does, taking the top ten by that feature beats
taking the first ten by arrival, and the difference is free.

METHOD
------
Grades through the real machinery (`shadow_ledger.grade_records`), one record at
a time so each return stays joined to its own metadata. Deduplication is applied
first, exactly as the aggregate grader does it, so this study sees the same
episode set the live verdicts do.

Returns are net of funding and of 25bps slippage, matching how every book in
`dashboard._BOOKS` is quoted. Comparing a raw price return against a net-quoted
verdict is how a study talks itself into an edge that does not survive contact.

Reported per feature:
  spread      mean return of the top tercile minus the bottom tercile
  ic          Spearman rank correlation between feature and return
  p           two-sided p-value on that correlation

A spread is only interesting when the IC agrees with it. Terciles on a few
hundred points will happily produce a spread from noise, and the rank
correlation is the check that the ordering is monotone rather than one lucky
bucket.

    python research/signal_ranking/rank_study.py --book news_surge_multi
    python research/signal_ranking/rank_study.py --all --cache

Network: read-only Hyperliquid candle and funding history. No orders.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pathia.agents import shadow_ledger as SL          # noqa: E402

CACHE_DIR = Path(__file__).parent / "_cache"
SLIP_BPS = 25          # matches how every _BOOKS verdict is quoted
_BAR_MS = {"1d": 86_400_000, "1h": 3_600_000, "15m": 900_000, "5m": 300_000}

# Features worth testing per book, and what each one is. Anything not listed is
# either an identifier (name, source), a flag with no ordering (shadow), or a
# duplicate of the entry price.
FEATURES = {
    "news_surge_multi": ["surge_x", "n_recent", "n_feeds", "breaking"],
    "news_surge_short": ["surge_x", "n_recent", "breaking"],
    "social_trending": ["trending_score", "cg_rank"],
    "unlock_short_runin": ["unlock_pct_circ", "hours_to_unlock"],
}


# ── candle cache ────────────────────────────────────────────────────────────
# One deep pull per (coin, interval), reused for every signal on that coin.
# Without it this is one rate-limited weight-20 call per signal: the same
# mistake that once made a grading pass take 2h13m of pure waiting for 4.8s of
# CPU (measured 2026-08-04, recorded in the trend-engine tests).

_MEM: Dict[Tuple[str, str], List[Any]] = {}


def _cached_candles(coin: str, interval: str, want: int, use_disk: bool) -> List[Any]:
    key = (coin, interval)
    if key in _MEM and len(_MEM[key]) >= want:
        return _MEM[key]
    disk = CACHE_DIR / f"{coin.replace('/', '_')}_{interval}.json"
    if use_disk and disk.exists():
        try:
            rows = json.loads(disk.read_text())
            if len(rows) >= want:
                bars = [type("B", (), r) for r in rows]
                _MEM[key] = bars
                return bars
        except Exception:
            pass
    from pathia.client.hl_client import fetch_hl_candles
    bars = fetch_hl_candles(coin, interval, want)
    _MEM[key] = bars
    if use_disk:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        try:
            disk.write_text(json.dumps(
                [{k: getattr(b, k) for k in ("t", "o", "h", "l", "c", "v")
                  if hasattr(b, k)} for b in bars]))
        except Exception:
            pass
    return bars


def make_fetch_fwd(use_disk: bool):
    def fetch_fwd(coin: str, signal_bar_t: int, n_bars: int,
                  interval: str = "1d") -> List[Any]:
        bar_ms = _BAR_MS.get(interval, 86_400_000)
        age = max(0, int((time.time() * 1000 - int(signal_bar_t)) // bar_ms))
        bars = _cached_candles(coin, interval, n_bars + age + 3, use_disk)
        return [b for b in bars if int(getattr(b, "t", 0)) > int(signal_bar_t)]
    return fetch_fwd


# ── statistics, written out rather than pulled in ───────────────────────────
# scipy is not a dependency of this project and adding one for two functions
# that are twenty lines each is not a trade worth making.

def _rank(xs: List[float]) -> List[float]:
    """Average ranks, so ties do not manufacture an ordering that is not there.
    Ties are the common case here: surge_x is quantised and n_recent is an
    integer that is very often 0."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman(xs: List[float], ys: List[float]) -> Tuple[float, float]:
    """Rank correlation and a two-sided p-value via the t approximation."""
    n = len(xs)
    if n < 8:
        return 0.0, 1.0
    rx, ry = _rank(xs), _rank(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sxx = sum((a - mx) ** 2 for a in rx)
    syy = sum((b - my) ** 2 for b in ry)
    if sxx <= 0 or syy <= 0:
        return 0.0, 1.0
    r = sxy / math.sqrt(sxx * syy)
    r = max(-0.999999, min(0.999999, r))
    t = r * math.sqrt((n - 2) / (1 - r * r))
    # Normal approximation to the t tail. Fine at these n (hundreds), and the
    # decision threshold is not close enough to the boundary for the difference
    # between t and z to change any conclusion below.
    p = math.erfc(abs(t) / math.sqrt(2))
    return r, p


# ── the study ───────────────────────────────────────────────────────────────

def grade_book(book: str, use_disk: bool, limit: Optional[int] = None
               ) -> List[Dict[str, Any]]:
    """Every resolved signal, its net return, and the metadata it carried."""
    records = SL.load(book)
    records, _ = SL.dedup_episodes(records)      # same episode set as the verdicts
    if limit:
        records = records[-limit:]
    fetch_fwd = make_fetch_fwd(use_disk)
    from pathia.client.hl_client import fetch_funding_history

    out: List[Dict[str, Any]] = []
    for i, r in enumerate(records, 1):
        if i % 200 == 0:
            print(f"    graded {i}/{len(records)}", flush=True)
        try:
            g = SL.grade_records([r], fetch_fwd, fetch_funding=fetch_funding_history,
                                 dedup=False)
        except Exception:
            continue
        if g.get("n") != 1 or not g.get("detail"):
            continue                              # pending, or could not price
        d = g["detail"][0]
        gross = float(d["ret_pct"])
        out.append({
            "coin": r.get("coin"),
            "ts": int(r.get("ts") or 0),
            "ret_net": gross - SLIP_BPS / 100.0,   # one round trip at 25bps
            "meta": r.get("meta") or {},
        })
    return out


def analyse(book: str, rows: List[Dict[str, Any]]) -> None:
    if len(rows) < 24:
        print(f"  {book}: {len(rows)} graded signals, too few to rank on\n")
        return
    rets = [r["ret_net"] for r in rows]
    base = sum(rets) / len(rets)
    print(f"\n  {book}  n={len(rows)}  mean net EV {base:+.3f}%/signal")
    print(f"    {'feature':<18}{'n':>6}{'bottom⅓':>10}{'top⅓':>10}{'spread':>10}"
          f"{'IC':>8}{'p':>9}")

    for feat in FEATURES.get(book, []):
        pairs = [(float(r["meta"][feat]), r["ret_net"]) for r in rows
                 if isinstance(r["meta"].get(feat), (int, float, bool))]
        if len(pairs) < 24 or len({p[0] for p in pairs}) < 3:
            print(f"    {feat:<18}{len(pairs):>6}  no usable variation")
            continue
        pairs.sort(key=lambda p: p[0])
        k = len(pairs) // 3
        lo = sum(p[1] for p in pairs[:k]) / k
        hi = sum(p[1] for p in pairs[-k:]) / k
        ic, p = spearman([a for a, _ in pairs], [b for _, b in pairs])
        flag = "  <-- " + ("keep" if p < 0.05 else "noise") if abs(ic) > 0.05 else ""
        print(f"    {feat:<18}{len(pairs):>6}{lo:>+10.3f}{hi:>+10.3f}"
              f"{hi - lo:>+10.3f}{ic:>+8.3f}{p:>9.4f}{flag}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="rank_study")
    ap.add_argument("--book", action="append", default=[])
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--cache", action="store_true",
                    help="persist candles under _cache/ so reruns are free")
    ap.add_argument("--limit", type=int, default=None,
                    help="grade only the most recent N signals per book")
    a = ap.parse_args(argv)

    books = a.book or (list(FEATURES) if a.all else ["news_surge_multi"])
    os.environ.setdefault("PATHIA_STATE_DIR", str(ROOT / ".state"))

    print("Signal ranking study — net of funding and 25bps slippage")
    print("Question: with 32 signals a day and 10 slots, does anything we already"
          " record say which ten to take?")
    for book in books:
        print(f"\n  grading {book} …", flush=True)
        rows = grade_book(book, use_disk=a.cache, limit=a.limit)
        analyse(book, rows)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
