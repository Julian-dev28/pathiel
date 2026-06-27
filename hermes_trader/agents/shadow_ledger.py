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
import os
import statistics
import time
from typing import Any, Callable, Dict, List, Optional

from hermes_trader.agents.rebalancer_owned import state_file

SCHEMA_VERSION = 1
_DIR = "shadow_ledger"
_DAY_MS = 86_400_000
SLIP_TIERS_BPS = [0, 6, 12, 25, 50]


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
                    if now >= int(r["signal_bar_t"]) + int((float(r.get("horizon_days") or 0) + 2) * _DAY_MS)]
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


def simulate_exit(side: str, entry_px: float, fwd: List[Any], stop_pct: float, horizon: int) -> Optional[float]:
    """Lookahead-safe signed return. `fwd` = daily bars STRICTLY AFTER the signal bar.
    long: stop at -stop_pct (low touches); short: stop at +stop_pct (high touches);
    else exit at the horizon close. Returns the trade's signed fractional return."""
    if entry_px <= 0 or not fwd or horizon <= 0:
        return None
    if side == "long":
        stop_px = entry_px * (1 - stop_pct / 100.0)
        for bar in fwd[:horizon]:
            if _f(bar, "l") <= stop_px:
                return -stop_pct / 100.0
        last = _f(fwd[min(horizon, len(fwd)) - 1], "c")
        return last / entry_px - 1.0 if entry_px else None
    else:
        stop_px = entry_px * (1 + stop_pct / 100.0)
        for bar in fwd[:horizon]:
            if _f(bar, "h") >= stop_px:
                return -stop_pct / 100.0
        last = _f(fwd[min(horizon, len(fwd)) - 1], "c")
        return entry_px / last - 1.0 if last else None


def grade_records(records: List[Dict[str, Any]],
                  fetch_fwd: Callable[[str, int, int], List[Any]],
                  now_ms: Optional[int] = None) -> Dict[str, Any]:
    """Forward-grade resolved records. `fetch_fwd(coin, signal_bar_t, n_bars)` must
    return daily bars AFTER signal_bar_t (caller injects real or fake fetch)."""
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    rets: List[float] = []
    detail: List[Dict[str, Any]] = []
    pending = ungradeable = errors = 0
    for r in records:
        sig_t = int(r.get("signal_bar_t") or 0)
        entry_px = float(r.get("entry_ref_px") or 0.0)
        horizon = int(float(r.get("horizon_days") or 0.0))
        stop_pct = float(r.get("stop_pct") or 0.0)
        side = str(r.get("side") or "long")
        if not sig_t or entry_px <= 0 or horizon <= 0:
            ungradeable += 1
            continue
        if now < sig_t + int((horizon + 2) * _DAY_MS):
            pending += 1
            continue
        try:
            fwd = fetch_fwd(r.get("coin"), sig_t, horizon + 5)
        except Exception:
            errors += 1
            continue
        ret = simulate_exit(side, entry_px, fwd, stop_pct, horizon)
        if ret is None:
            errors += 1
            continue
        rets.append(ret)
        detail.append({"coin": r.get("coin"), "side": side, "ret_pct": round(100 * ret, 2)})

    n = len(rets)
    out: Dict[str, Any] = {"n": n, "pending": pending, "ungradeable": ungradeable, "errors": errors}
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
