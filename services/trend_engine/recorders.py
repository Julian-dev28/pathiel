"""Lane RECORDERS — what every zero-capital recorder has actually earned.

The other three lanes forecast. This one grades: each shadow book's forward EV
per signal, each Polymarket paper lane's realised PnL per dollar, and the TREND
in both (first half vs second half), because a recorder whose edge is decaying
looks identical to a healthy one in a single average.

Two graders, two sources of truth:

  shadow books   `hermes_trader.agents.shadow_ledger.grade_records` — the same
                 path `scripts/shadow_status.py` uses, forward candles net of
                 funding, so the numbers here and in the survey agree.
  scout ledger   `services/polymarket_scout/ledger.grade` with an injected
                 resolver. The updown_5m lane is resolved offline from the 1m
                 klines lane 2 already caches (free, no Gamma calls); the
                 judgment / trending / sports lanes need Gamma.

Both graders are network-heavy, which is exactly why this lane lives behind the
same cache + background-refresh contract as the others.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional, Sequence

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

    def fetch_fwd(coin: str, signal_bar_t: int, n_bars: int, interval: str = "1d"):
        ms = bar_ms.get(interval, 86_400_000)
        age_bars = max(0, int((time.time() * 1000 - int(signal_bar_t)) // ms))
        bars = fetch_hl_candles(coin, interval, n_bars + age_bars + 3)
        return [b for b in bars if int(getattr(b, "t", 0)) > int(signal_bar_t)]

    def fetch_funding(coin: str, start_ms: int, end_ms: int):
        return fetch_funding_history(coin, int(start_ms), int(end_ms))

    return fetch_fwd, fetch_funding


# ── polymarket paper ledger ──────────────────────────────────────────────────


def updown_resolver(windows: Sequence[Dict[str, Any]],
                    tolerance_ms: int = 60_000) -> Callable[[str], Optional[bool]]:
    """Resolve an updown_5m paper trade OFFLINE, from cached 1m klines.

    The ledger row carries the window's end timestamp, so the window is
    identifiable without a single Gamma call — 900 rows graded for free. The
    resolver is keyed on end-time rather than market id, so it is built as a
    closure over a lookup and handed the row's `end_ms` by `grade_scout`.

    Caveat kept visible: Polymarket settles these on Chainlink, this grades on
    Binance. Sub-tick disagreements will misgrade a handful of near-flat
    windows, which is why the lane reports its own n next to the number.
    """
    by_end: Dict[int, bool] = {}
    for w in windows:
        by_end[int(w["t"]) + 5 * 60_000] = bool(w["up"])

    def resolve(end_ms: Optional[int]) -> Optional[bool]:
        if end_ms is None:
            return None
        # Snap to the nearest 5-minute boundary rather than probing fixed
        # offsets: a recorded end time can be seconds off the grid, and an
        # offset probe only matches the exact deltas it was given.
        window = 5 * 60_000
        snapped = round(int(end_ms) / window) * window
        if abs(snapped - int(end_ms)) > tolerance_ms:
            return None
        return by_end.get(snapped)

    return resolve


def _end_ms(row: Dict[str, Any]) -> Optional[int]:
    from datetime import datetime
    iso = str(row.get("end_date") or "")
    if not iso:
        return None
    try:
        return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)
    except Exception:
        return None


def grade_scout(rows: Optional[List[Dict[str, Any]]] = None,
                windows: Optional[Sequence[Dict[str, Any]]] = None,
                gamma_resolver: Optional[Callable[[str], Optional[bool]]] = None,
                ) -> Dict[str, Any]:
    """Realised paper PnL per lane for the Polymarket scout ledger.

    Lanes are separate hypotheses and are never pooled — the updown lane is
    thousands of latency coin flips, the judgment lane is a few dozen
    considered forecasts, and averaging them would hide both.
    """
    from services.polymarket_scout import ledger as L

    rows = L.load() if rows is None else rows
    by_lane: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        by_lane.setdefault(L.row_lane(r), []).append(r)

    ud_resolve = updown_resolver(windows or [])
    out: Dict[str, Any] = {"lanes": {}, "total_rows": len(rows)}
    for lane, lrows in sorted(by_lane.items()):
        if lane == "updown_5m":
            resolver_by_row = {str(r.get("market_id")): ud_resolve(_end_ms(r)) for r in lrows}
            source = "binance klines (market settles on chainlink)"
        elif gamma_resolver is not None:
            resolver_by_row = {str(r.get("market_id")): gamma_resolver(str(r.get("market_id")))
                               for r in lrows}
            source = "gamma resolution"
        else:
            out["lanes"][lane] = {"n": 0, "pending": len(lrows), "rows": len(lrows),
                                  "source": "not graded (no resolver)"}
            continue
        graded = L.grade(lambda mid: resolver_by_row.get(str(mid)), lrows)
        graded.pop("detail", None)
        graded["rows"] = len(lrows)
        graded["source"] = source
        if graded.get("n"):
            wins = round(float(graded.get("win_rate") or 0.0) * graded["n"])
            lo, hi = wilson(wins, graded["n"])
            graded["win_ci"] = [round(lo, 3), round(hi, 3)]
            half = graded["n"] // 2
            graded["halves_note"] = f"{half}/{graded['n'] - half} split"
        out["lanes"][lane] = graded
    return out


# ── assembly ─────────────────────────────────────────────────────────────────


def observations(books: Sequence[Dict[str, Any]], scout: Dict[str, Any]) -> List[str]:
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
        out.append(f"{len(stale)} recorder(s) have not written in over a week: "
                   + ", ".join(b["book"] for b in stale[:5])
                   + " — either the lane is off or its trigger has stopped firing.")

    for lane, g in (scout.get("lanes") or {}).items():
        if not g.get("n"):
            continue
        beat = g.get("llm_beats_market")
        out.append(
            f"Polymarket {lane}: {g['n']} resolved, {g.get('mean_pnl_per_$'):+.4f} per $ "
            f"per position, win {g.get('win_rate')}, Brier {g.get('brier_llm')} vs the "
            f"market's {g.get('brier_mkt')} — "
            + ("our forecast is better calibrated than the price."
               if beat else "the market is better calibrated than our forecast."))
    return out


def read(min_n: int = 8, books: Optional[Sequence[str]] = None,
         windows: Optional[Sequence[Dict[str, Any]]] = None,
         gamma_resolver: Optional[Callable[[str], Optional[bool]]] = None,
         with_network: bool = True, now_ms: Optional[int] = None) -> Dict[str, Any]:
    """Full recorders lane. `with_network=False` grades nothing and returns the
    inventory only — used by tests and by any caller that must not fetch."""
    t0 = time.time()
    if with_network and windows is None:
        try:
            from services.trend_engine import updown_trends as ud
            windows = ud.enrich(ud.build_windows(ud.load_1m(30_240)))
        except Exception:
            windows = []
    if with_network and gamma_resolver is None:
        try:
            from services.polymarket_scout.scout import PolymarketClient, make_gamma_resolver
            gamma_resolver = make_gamma_resolver(PolymarketClient())
        except Exception:
            gamma_resolver = None

    rows = grade_books(books=books, min_n=min_n, now_ms=now_ms) if with_network else []
    try:
        scout = grade_scout(windows=windows, gamma_resolver=gamma_resolver)
    except Exception as exc:
        scout = {"lanes": {}, "error": str(exc)[:200]}

    graded = [b for b in rows if (b.get("resolved") or 0) > 0]
    evs = [b["ev_pct"] for b in graded if b.get("ev_pct") is not None]
    counts: Dict[str, int] = {}
    for b in rows:
        counts[b["verdict"]] = counts.get(b["verdict"], 0) + 1
    return {
        "status": "ok" if rows or scout.get("lanes") else "no_data",
        "generated_at": int(time.time()),
        "elapsed_s": round(time.time() - t0, 2),
        "books": rows,
        "scout": scout,
        "summary": {
            "n_books": len(rows),
            "n_graded": len(graded),
            "verdicts": counts,
            "mean_ev_pct": round(mean(evs), 4) if evs else None,
            "positive_books": sum(1 for e in evs if e > 0),
            "total_signals": sum(int(b.get("signals") or 0) for b in rows),
            "total_resolved": sum(int(b.get("resolved") or 0) for b in rows),
        },
        "observations": observations(rows, scout),
    }
