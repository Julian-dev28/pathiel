"""Lane RECORDERS — what every zero-capital recorder has actually earned.

The other three lanes forecast. This one grades: each shadow book's forward EV
per signal, and the TREND
in both (first half vs second half), because a recorder whose edge is decaying
looks identical to a healthy one in a single average.

One grader, one source of truth: `hermes_trader.agents.shadow_ledger.grade_records`
— the same path `scripts/shadow_status.py` uses, forward candles net of funding,
so the numbers here and in the survey agree.

Grading is network-heavy, which is exactly why this lane lives behind the same
cache + background-refresh contract as the others.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from services.trend_engine.metrics import mean, wilson

# Books that were ripped out after being refuted — grading them again would
# put dead strategies back on a dashboard as if they were live candidates.
REMOVED_BOOKS = {"vol_breakout_long", "premium_fade_short", "hail_mary",
                 "capital_rotation", "neg_funding_fade"}
VERDICT_ORDER = ("VALIDATED", "MARGINAL", "PENDING", "REFUTED")


# ── shadow books ─────────────────────────────────────────────────────────────


def grade_books(books: Optional[Sequence[str]] = None, min_n: int = 8,
                fetch_fwd: Optional[Callable] = None,
                fetch_funding: Optional[Callable] = None,
                now_ms: Optional[int] = None,
                sl: Optional[Any] = None) -> List[Dict[str, Any]]:
    """Forward-grade every shadow book. One row per book, richest first.

    `ev_pct` is mean return per signal at 12bps slippage — the number the
    promote/demote rule is written against. `ev_first` / `ev_second` are the
    same figure on each half of the book's history: that pair is the trend,
    and a positive average built on a negative second half is a book on the
    way out, not a candidate.

    `sl` injects the ledger module (tests). It is a parameter rather than a
    monkeypatched import because `from pkg import mod` binds through the
    package ATTRIBUTE once the real module has been imported by anything else
    in the process — patching `sys.modules` alone passes in isolation and
    fails in a full suite run.
    """
    if sl is None:
        from hermes_trader.agents import shadow_ledger as sl

    SL = sl
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    inv = {r["book"]: r for r in SL.summary(now)}
    names = list(books) if books else SL.list_books()
    if fetch_fwd is None:
        fetch_fwd, fetch_funding = _live_fetchers()

    rows: List[Dict[str, Any]] = []
    for book in names:
        if book in REMOVED_BOOKS:
            continue
        iv = inv.get(book, {"n": 0})
        recs = SL.load(book)
        # A book with nothing gradeable yet costs a full pass of candle
        # fetches to learn nothing. The inventory already knows, and on a
        # flaky HL (6 retries with backoff per miss) that difference is the
        # gap between a 2-minute job and a 20-minute one.
        gradeable = iv.get("gradeable")
        if recs and gradeable == 0:
            grade = {"n": 0, "pending": iv.get("pending", 0), "skipped": "nothing gradeable yet"}
        else:
            grade = (SL.grade_records(recs, fetch_fwd, now_ms=now,
                                      fetch_funding=fetch_funding)
                     if recs else {"n": 0})
        grade.pop("detail", None)                     # never ship per-signal rows to a tab
        verdict = grade.get("verdict") or SL.classify(grade, min_n=min_n)
        s12 = grade.get("slip12") or {}
        s25 = grade.get("slip25") or {}
        oos = grade.get("oos_12bps") or {}
        n = int(grade.get("n") or 0)
        wins = round(float(s12.get("win") or 0.0) * n)
        lo, hi = wilson(wins, n) if n else (0.0, 1.0)
        rows.append({
            "book": book,
            "signals": iv.get("n", 0),
            "coins": iv.get("coins", 0),
            "resolved": n,
            "pending": grade.get("pending", iv.get("pending", 0)),
            # Signals the grader could not price this run — an empty forward
            # fetch is skipped, never scored as a flat trade, so a venue outage
            # shrinks the SAMPLE rather than dragging EV toward zero. Surfaced
            # so a short sample is distinguishable from a quiet book.
            "ungraded_errors": int(grade.get("errors") or 0),
            "last_age_h": iv.get("last_age_h"),
            "ev_pct": s12.get("mean_pct"),
            "ev25_pct": s25.get("mean_pct"),
            "total_pct": s12.get("total_pct"),
            "win_rate": s12.get("win"),
            "win_ci": [round(lo, 3), round(hi, 3)],
            "ev_first": oos.get("first"),
            "ev_second": oos.get("second"),
            "n_first": oos.get("n_first"),
            "n_second": oos.get("n_second"),
            "decaying": bool(oos.get("first") is not None and oos.get("second") is not None
                             and oos["second"] < 0 <= oos["first"]),
            "funding_included": grade.get("funding_included", False),
            "verdict": verdict.get("label", "PENDING"),
            "why": verdict.get("why", ""),
        })
    # verdict first (validated books lead the table), then EV inside a verdict
    rows.sort(key=lambda r: (VERDICT_ORDER.index(r["verdict"])
                             if r["verdict"] in VERDICT_ORDER else 9,
                             -(r["ev_pct"] if r["ev_pct"] is not None else -999)))
    return rows


def _live_fetchers():
    """Real forward-candle + funding fetchers, matching shadow_status.py.

    Lookback is sized from the signal's AGE (a fixed pad silently mis-windows
    anything older than the pad — the grader window-rot bug from the 2026-07-09
    audit).
    """
    from hermes_trader.client.hl_client import fetch_funding_history, fetch_hl_candles

    bar_ms = {"1d": 86_400_000, "1h": 3_600_000, "4h": 14_400_000}
    # One fetch per (coin, interval) per run, not one per SIGNAL. Every signal
    # on a book asks for forward bars on the same coin, and each miss is a
    # rate-limited HL info call at weight 20 — measured 2026-08-04, the lane
    # took over two hours of almost pure waiting (4.8s of CPU in 2h13m) and the
    # /trends P&L lane was chronically stale because of it. Bars come back
    # newest-window-anchored, so a cached pull of N bars answers every ask for
    # <= N bars exactly: the slice below is the only thing that ever varied.
    candles: Dict[Tuple[str, str], Tuple[int, List[Any]]] = {}
    funding: Dict[str, Tuple[int, int, List[Any]]] = {}

    def fetch_fwd(coin: str, signal_bar_t: int, n_bars: int, interval: str = "1d"):
        ms = bar_ms.get(interval, 86_400_000)
        age_bars = max(0, int((time.time() * 1000 - int(signal_bar_t)) // ms))
        need = n_bars + age_bars + 3
        key = (coin, interval)
        hit = candles.get(key)
        if hit is None or hit[0] < need:
            candles[key] = (need, fetch_hl_candles(coin, interval, need))
        bars = candles[key][1]
        return [b for b in bars if int(getattr(b, "t", 0)) > int(signal_bar_t)]

    def fetch_funding(coin: str, start_ms: int, end_ms: int):
        """Funding for one signal's window, cached per coin.

        Cached on the UNION of the windows asked for, never widened to now:
        measured 2026-08-04, pulling each coin's funding through to now turned
        85s of funding into 449s on one book, because the API cost scales with
        the range and an old signal drags the start back months.
        """
        start_ms, end_ms = int(start_ms), int(end_ms)
        hit = funding.get(coin)
        if hit is not None and hit[0] <= start_ms and hit[1] >= end_ms:
            return [f for f in hit[2]
                    if start_ms <= int(f.get("time", f.get("t", 0))) <= end_ms]
        lo = min(start_ms, hit[0]) if hit else start_ms
        hi = max(end_ms, hit[1]) if hit else end_ms
        rows = fetch_funding_history(coin, lo, hi)
        funding[coin] = (lo, hi, rows)
        return [f for f in rows
                if start_ms <= int(f.get("time", f.get("t", 0))) <= end_ms]

    return fetch_fwd, fetch_funding


# ── assembly ─────────────────────────────────────────────────────────────────


def observations(books: Sequence[Dict[str, Any]]) -> List[str]:
    """Plain-English reads generated from the same rows shown beside them."""
    out: List[str] = []
    graded = [b for b in books if (b.get("resolved") or 0) > 0]
    if not graded:
        out.append("No recorder has resolved a signal yet — every book is still accruing.")
        return out

    counts: Dict[str, int] = {}
    for b in books:
        counts[b["verdict"]] = counts.get(b["verdict"], 0) + 1
    out.append(
        f"{len(books)} recorders: {counts.get('VALIDATED', 0)} validated, "
        f"{counts.get('MARGINAL', 0)} marginal, {counts.get('REFUTED', 0)} refuted, "
        f"{counts.get('PENDING', 0)} still under the evidence floor.")

    best = max(graded, key=lambda b: (b.get("ev_pct") or -999))
    worst = min(graded, key=lambda b: (b.get("ev_pct") or 999))
    out.append(f"Best forward EV: {best['book']} {best.get('ev_pct'):+.3f}%/signal "
               f"@12bps over {best['resolved']} resolved (win {best.get('win_rate')}).")
    out.append(f"Worst: {worst['book']} {worst.get('ev_pct'):+.3f}%/signal over "
               f"{worst['resolved']} resolved.")

    decaying = [b for b in graded if b.get("decaying")]
    if decaying:
        out.append("Decaying (first half positive, second half negative — the average "
                   "is hiding it): "
                   + ", ".join(f"{b['book']} {b['ev_first']:+.2f}→{b['ev_second']:+.2f}%"
                               for b in decaying[:4]) + ".")

    stale = [b for b in books if isinstance(b.get("last_age_h"), (int, float))
             and b["last_age_h"] > 168]
    if stale:
        names = ", ".join(b["book"] for b in stale[:3])
        more = f" +{len(stale) - 3} more" if len(stale) > 3 else ""
        out.append(f"{len(stale)} lanes idle over a week ({names}{more}) — "
                   f"switched off, or the trigger stopped firing.")

    return out


def read(min_n: int = 8, books: Optional[Sequence[str]] = None,
         with_network: bool = True, now_ms: Optional[int] = None) -> Dict[str, Any]:
    """Full recorders lane. `with_network=False` grades nothing and returns the
    inventory only — used by tests and by any caller that must not fetch."""
    t0 = time.time()
    rows = grade_books(books=books, min_n=min_n, now_ms=now_ms) if with_network else []

    graded = [b for b in rows if (b.get("resolved") or 0) > 0]
    evs = [b["ev_pct"] for b in graded if b.get("ev_pct") is not None]
    counts: Dict[str, int] = {}
    for b in rows:
        counts[b["verdict"]] = counts.get(b["verdict"], 0) + 1
    return {
        "status": "ok" if rows else "no_data",
        "generated_at": int(time.time()),
        "elapsed_s": round(time.time() - t0, 2),
        "books": rows,
        "summary": {
            "n_books": len(rows),
            "n_graded": len(graded),
            "verdicts": counts,
            "mean_ev_pct": round(mean(evs), 4) if evs else None,
            "positive_books": sum(1 for e in evs if e > 0),
            "total_signals": sum(int(b.get("signals") or 0) for b in rows),
            "total_resolved": sum(int(b.get("resolved") or 0) for b in rows),
        },
        "observations": observations(rows),
    }
