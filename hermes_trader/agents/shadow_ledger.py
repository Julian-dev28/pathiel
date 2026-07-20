"""Unified shadow-mode ledger.

Every shadow book (default-off / `shadow_only:true` strategy) records its
candidate signals HERE instead of inventing its own jsonl. That gives one
organized, checkable home for shadow data:

    <state_dir>/shadow_ledger/<book>.jsonl   (one record per candidate signal)

A record is a normalized, side-aware, forward-gradeable snapshot:

    {v, ts, book, coin, side, signal_bar_t, entry_ref_px, horizon_days, stop_pct, meta}

`scripts/shadow_status.py` is the single handler that inventories every book
and forward-grades the resolved signals (fetch realized bars, simulate the exact
side/stop/horizon exit, report EV + OOS halves). No book grades itself anymore.

This module does ZERO trading. It only appends/reads jsonl + runs a pure-Python
exit simulation with the candle fetch injected (so it is unit-testable offline).
"""
from __future__ import annotations

import json
import math
import os
import statistics
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from hermes_trader.agents.rebalancer_owned import state_file

SCHEMA_VERSION = 1
_DIR = "shadow_ledger"
_DAY_MS = 86_400_000
_HOUR_MS = 3_600_000
SLIP_TIERS_BPS = [0, 6, 12, 25, 50]


def grade_interval(horizon_days: float) -> Tuple[str, int, int]:
    """(interval, bar_ms, n_bars) used to grade a horizon.

    Sub-day horizons grade on HOURLY bars. The old grader did int(horizon_days),
    so an 8h-horizon book (horizon_days=0.333) truncated to 0 and every record
    stayed permanently ungradeable — a live book traded 10 days with zero
    measurement (audit 2026-07-09)."""
    if horizon_days >= 1.0:
        return "1d", _DAY_MS, int(math.ceil(horizon_days))
    return "1h", _HOUR_MS, max(1, int(math.ceil(horizon_days * 24.0)))


def resolve_after_ms(horizon_days: float) -> int:
    """ms after signal_bar_t when a record becomes gradeable: the horizon's worth
    of forward bars must have CLOSED (+1 signal bar, +1 fetch-latency buffer)."""
    _, bar_ms, n_bars = grade_interval(horizon_days)
    return (n_bars + 2) * bar_ms


def _ledger_dir() -> str:
    d = state_file(_DIR)
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def _book_path(book: str) -> str:
    return os.path.join(_ledger_dir(), f"{book}.jsonl")


def _f(bar: Any, key: str) -> float:
    try:
        return float(bar.get(key) if isinstance(bar, dict) else getattr(bar, key))
    except Exception:
        return 0.0


def record(book: str, *, coin: str, side: str, signal_bar_t: Optional[int] = None,
           entry_ref_px: float = 0.0, horizon_days: float = 0.0, stop_pct: float = 0.0,
           ts: Optional[int] = None, meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Append one shadow signal record. Best-effort (never raises into the loop)."""
    rec = {
        "v": SCHEMA_VERSION,
        "ts": int(ts if ts is not None else time.time() * 1000),
        "book": book,
        "coin": coin,
        "side": side,
        "signal_bar_t": int(signal_bar_t or 0),
        "entry_ref_px": float(entry_ref_px or 0.0),
        "horizon_days": float(horizon_days or 0.0),
        "stop_pct": float(stop_pct or 0.0),
        "meta": meta or {},
    }
    try:
        with open(_book_path(book), "a") as fh:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
    except Exception:
        pass
    return rec


def record_many(book: str, rows: List[Dict[str, Any]]) -> int:
    """Record a list of candidate kwargs-dicts. Returns count written."""
    n = 0
    for r in rows or []:
        record(book, **r)
        n += 1
    return n


def list_books() -> List[str]:
    d = _ledger_dir()
    try:
        return sorted(f[:-6] for f in os.listdir(d) if f.endswith(".jsonl"))
    except Exception:
        return []


def load(book: str) -> List[Dict[str, Any]]:
    path = _book_path(book)
    if not os.path.isfile(path):
        return []
    out: List[Dict[str, Any]] = []
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        return out
    return out


def parse_meta_filters(pairs: List[str]) -> Dict[str, Any]:
    """Parse CLI 'key=value' pairs into typed meta filters. Values go through
    json.loads so `armed=true` matches the boolean the recorder wrote, not the
    string 'true'; unparseable values stay strings."""
    out: Dict[str, Any] = {}
    for p in pairs:
        k, sep, v = p.partition("=")
        if not sep or not k:
            raise ValueError(f"bad meta filter {p!r} (want key=value)")
        try:
            out[k] = json.loads(v)
        except ValueError:
            out[k] = v
    return out


def filter_by_meta(records: List[Dict[str, Any]],
                   filters: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Keep records whose meta matches every filter; rows missing a filtered
    key are dropped (a signal recorded before the flag existed is not evidence
    about either arm)."""
    if not filters:
        return list(records)
    kept: List[Dict[str, Any]] = []
    for r in records:
        meta = r.get("meta") or {}
        if all(k in meta and meta.get(k) == v for k, v in filters.items()):
            kept.append(r)
    return kept


def summary(now_ms: Optional[int] = None) -> List[Dict[str, Any]]:
    """Per-book inventory: counts, distinct coins, last-signal age, gradeable/resolved."""
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    rows: List[Dict[str, Any]] = []
    for book in list_books():
        recs = load(book)
        if not recs:
            rows.append({"book": book, "n": 0})
            continue
        ts_list = [int(r.get("ts") or 0) for r in recs if r.get("ts")]
        gradeable = [r for r in recs if int(r.get("signal_bar_t") or 0) and float(r.get("entry_ref_px") or 0) > 0]
        resolved = [r for r in gradeable
                    if now >= int(r["signal_bar_t"]) + resolve_after_ms(float(r.get("horizon_days") or 0))]
        last_ts = max(ts_list) if ts_list else 0
        rows.append({
            "book": book,
            "n": len(recs),
            "coins": len({r.get("coin") for r in recs}),
            "first_ts": min(ts_list) if ts_list else 0,
            "last_ts": last_ts,
            "last_age_h": round((now - last_ts) / 3_600_000, 1) if last_ts else None,
            "gradeable": len(gradeable),
            "resolved": len(resolved),
            "pending": len(gradeable) - len(resolved),
            "ungradeable": len(recs) - len(gradeable),
        })
    return rows


def simulate_exit(side: str, entry_px: float, fwd: List[Any], stop_pct: float,
                  horizon: int) -> Optional[Tuple[float, int]]:
    """Lookahead-safe signed PRICE return. `fwd` = bars STRICTLY AFTER the signal bar.
    long: stop at -stop_pct (low touches); short: stop at +stop_pct (high touches);
    else exit at the horizon close. Returns (signed fractional return, bars_held)
    so the caller can accrue funding over the actual holding time.

    Short return is (entry-last)/entry — a short's PnL as a fraction of notional.
    The old entry/last-1 was convexity-inverted (optimistic for every winning
    short, e.g. entry 100 -> 90 graded +11.1% instead of +10%)."""
    if entry_px <= 0 or not fwd or horizon <= 0:
        return None
    n_held = min(horizon, len(fwd))
    if side == "long":
        stop_px = entry_px * (1 - stop_pct / 100.0)
        for i, bar in enumerate(fwd[:horizon]):
            if _f(bar, "l") <= stop_px:
                return -stop_pct / 100.0, i + 1
        last = _f(fwd[n_held - 1], "c")
        return (last / entry_px - 1.0, n_held) if last else None
    else:
        stop_px = entry_px * (1 + stop_pct / 100.0)
        for i, bar in enumerate(fwd[:horizon]):
            if _f(bar, "h") >= stop_px:
                return -stop_pct / 100.0, i + 1
        last = _f(fwd[n_held - 1], "c")
        return ((entry_px - last) / entry_px, n_held) if last else None


def funding_return(side: str, rows: List[Dict[str, Any]], start_ms: int, end_ms: int) -> float:
    """Signed funding PnL fraction over (start_ms, end_ms]. HL fundingHistory rows
    are hourly {time, fundingRate}; positive rate = longs pay shorts, so a short
    RECEIVES +rate and a long pays -rate. A deep-negative-funding short (the
    neg_funding_fade book) PAYS every hour held — omitting this term graded that
    book +2.43%/sig when the true net was ~+1.50%/sig (audit 2026-07-09)."""
    tot = 0.0
    for r in rows or []:
        try:
            t = int(r.get("time") or 0)
            if not (start_ms < t <= end_ms):
                continue
            tot += float(r.get("fundingRate", r.get("funding", 0.0)) or 0.0)
        except Exception:
            continue
    return tot if side == "short" else -tot


def dedup_episodes(records: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    """Collapse per-coin signal CLUSTERS into independent episodes: after a kept
    record, further records for the same coin inside its horizon window are the
    same trade re-signalled on a later scan cycle, not new evidence (a single
    MANTA cluster contributed 5 of neg_funding_fade's 13 records). Returns
    (kept_records_time_ordered, n_dropped)."""
    ordered = sorted(records, key=lambda r: int(r.get("signal_bar_t") or r.get("ts") or 0))
    kept: List[Dict[str, Any]] = []
    last_kept: Dict[str, int] = {}
    dropped = 0
    for r in ordered:
        coin = str(r.get("coin") or "")
        sig_t = int(r.get("signal_bar_t") or r.get("ts") or 0)
        horizon_ms = int(float(r.get("horizon_days") or 0.0) * _DAY_MS)
        if not sig_t or float(r.get("entry_ref_px") or 0.0) <= 0 or horizon_ms <= 0:
            kept.append(r)          # broken record: pass through (graded ungradeable),
            continue                # but never let it suppress a valid one
        prev = last_kept.get(coin)
        if prev is not None and sig_t < prev + horizon_ms:
            dropped += 1
            continue
        last_kept[coin] = sig_t
        kept.append(r)
    return kept, dropped


def grade_records(records: List[Dict[str, Any]],
                  fetch_fwd: Callable[[str, int, int, str], List[Any]],
                  now_ms: Optional[int] = None,
                  fetch_funding: Optional[Callable[[str, int, int], List[Dict[str, Any]]]] = None,
                  dedup: bool = True) -> Dict[str, Any]:
    """Forward-grade resolved records. `fetch_fwd(coin, signal_bar_t, n_bars, interval)`
    must return bars of `interval` AFTER signal_bar_t (caller injects real or fake fetch).
    `fetch_funding(coin, start_ms, end_ms)` (optional) returns HL fundingHistory rows;
    when provided, graded returns are NET of funding over the simulated holding time."""
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    if dedup:
        records, deduped = dedup_episodes(records)
    else:
        deduped = 0
    rets: List[float] = []
    price_rets: List[float] = []
    detail: List[Dict[str, Any]] = []
    pending = ungradeable = errors = 0
    for r in records:
        sig_t = int(r.get("signal_bar_t") or 0)
        entry_px = float(r.get("entry_ref_px") or 0.0)
        horizon_days = float(r.get("horizon_days") or 0.0)
        stop_pct = float(r.get("stop_pct") or 0.0)
        side = str(r.get("side") or "long")
        if not sig_t or entry_px <= 0 or horizon_days <= 0:
            ungradeable += 1
            continue
        if now < sig_t + resolve_after_ms(horizon_days):
            pending += 1
            continue
        interval, bar_ms, n_bars = grade_interval(horizon_days)
        try:
            fwd = fetch_fwd(r.get("coin"), sig_t, n_bars + 5, interval)
        except Exception:
            errors += 1
            continue
        sim = simulate_exit(side, entry_px, fwd, stop_pct, n_bars)
        if sim is None:
            errors += 1
            continue
        price_ret, bars_held = sim
        fund_ret = 0.0
        if fetch_funding is not None:
            try:
                rows = fetch_funding(r.get("coin"), sig_t, sig_t + bars_held * bar_ms)
                fund_ret = funding_return(side, rows, sig_t, sig_t + bars_held * bar_ms)
            except Exception:
                fund_ret = 0.0
        ret = price_ret + fund_ret
        rets.append(ret)
        price_rets.append(price_ret)
        detail.append({"coin": r.get("coin"), "side": side,
                       "ret_pct": round(100 * ret, 2),
                       "price_pct": round(100 * price_ret, 2),
                       "funding_pct": round(100 * fund_ret, 3),
                       "bars_held": bars_held, "interval": interval,
                       "stop_pct": stop_pct, "n_bars": n_bars})

    n = len(rets)
    out: Dict[str, Any] = {"n": n, "pending": pending, "ungradeable": ungradeable,
                           "errors": errors, "deduped": deduped,
                           "funding_included": fetch_funding is not None}
    if n == 0:
        return out
    for bps in SLIP_TIERS_BPS:
        cost = bps / 10000.0
        net = [x - cost for x in rets]
        wins = sum(1 for x in net if x > 0)
        out[f"slip{bps}"] = {
            "mean_pct": round(100 * statistics.mean(net), 4),
            "total_pct": round(100 * sum(net), 2),
            "win": round(wins / n, 3),
        }
    out["mean_price_only_12bps_pct"] = round(100 * statistics.mean([x - 0.0012 for x in price_rets]), 4)
    half = n // 2
    def _ev(xs: List[float]) -> Optional[float]:
        return round(100 * statistics.mean([x - 0.0012 for x in xs]), 4) if xs else None
    out["oos_12bps"] = {"first": _ev(rets[:half]), "second": _ev(rets[half:]),
                        "n_first": half, "n_second": n - half}
    out["detail"] = detail
    out["verdict"] = classify(out)
    return out


def classify(grade: Dict[str, Any], min_n: int = 8) -> Dict[str, Any]:
    """Turn a forward grade into a survey VERDICT for the strategy. This is the
    PIT forward read (no survivorship upper-bound bias), so it is the authoritative
    refute/validate signal — stronger evidence than any backtest.

    - PENDING   : fewer than `min_n` resolved signals — not enough to decide yet.
    - VALIDATED : mean@12bps > 0 AND both OOS halves > 0 AND still > 0 at 25bps.
    - MARGINAL  : positive @12bps but dies by 25bps or one OOS half is weak/negative.
    - REFUTED   : non-positive @12bps (the edge is not there forward).
    """
    n = int(grade.get("n", 0))
    if n < min_n:
        return {"label": "PENDING", "why": f"only {n} resolved (need {min_n})"}
    m12 = grade.get("slip12", {}).get("mean_pct")
    m25 = grade.get("slip25", {}).get("mean_pct")
    oos = grade.get("oos_12bps", {})
    h1, h2 = oos.get("first"), oos.get("second")
    win12 = grade.get("slip12", {}).get("win")
    if m12 is None:
        return {"label": "PENDING", "why": "no graded returns"}
    both_halves_pos = (h1 is not None and h2 is not None and h1 > 0 and h2 > 0)
    if m12 > 0 and both_halves_pos and (m25 is not None and m25 > 0):
        return {"label": "VALIDATED",
                "why": f"+{m12:.3f}%/sig @12bps, both OOS halves + ({h1}/{h2}), survives 25bps, win {win12}"}
    if m12 > 0:
        reason = "dies by 25bps" if (m25 is not None and m25 <= 0) else "OOS half weak/flipped"
        return {"label": "MARGINAL",
                "why": f"+{m12:.3f}%/sig @12bps but {reason} (halves {h1}/{h2}, m25 {m25})"}
    return {"label": "REFUTED", "why": f"{m12:.3f}%/sig @12bps — no forward edge (halves {h1}/{h2})"}
