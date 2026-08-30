"""Shared scaffolding for the "books" — the per-thesis modules under
hermes_trader/agents/ that each poll a signal, record it to the shared shadow
ledger, and (once forward-validated) open a bounded live order through the
executor.

Split out 2026-08-30 (agent 1/8, dedup pass) after finding the exact same
handful of tiny functions copy-pasted byte-for-byte across `news_surge_multi.py`,
`news_surge_short_live.py`, `social_trending_recorder.py`, `unlock_short_live.py`,
`unlock_recorder.py`, and `main_engine_recorder.py`:

- `execute_opened` / `execute_block_detail` — parse whatever `_book_execute`
  (scripts/trading_loop.py) / `executor.maybe_execute` returned.
- `load_seen` / `save_seen` — a per-"coin:day"-key dedup timestamp file, so a
  book doesn't re-open the same signal twice in one day.
- `last_pass_ms` / `mark_pass` — a scan-interval throttle timestamp file.
- `load_state` / `save_state` — the same read-whole-dict/write-whole-dict
  pattern as the two above, but for an untyped state blob instead of a
  str->int map (CoinGecko trending's poll clock, the unlock calendar cache).
- `safe_float` — cast-or-zero, the same 4 lines that were showing up under
  three different private names (`_num`, `_num`, `_f`).
- `bounded_exit_override` — the exact `dsl_exit_override` dict every one of
  the reverse-refuted/validated live books sends: a hard %/ROE stop, no
  partial take-profit, no breakeven ratchet, no ATR stop, no noise band. Only
  the stop width, leverage, and timeout differ book-to-book, so those stay
  each book's own responsibility; this returns just the resulting shape.

Every function here is pure (or does its own best-effort file I/O with the
book's own failure semantics preserved exactly — see each docstring). Zero
dependency on anything else in hermes_trader/agents: a leaf module, the same
shape as atomic_io.py and client/http_session.py, so importing it from any
book cannot create an import cycle.
"""
from __future__ import annotations

import json
from typing import Any, Dict

from hermes_trader.models.types import DslExitOverride


def execute_opened(result: Any) -> bool:
    """True iff a book's `execute_fn(...)` call actually opened a position.

    Handles every shape `_book_execute`/`maybe_execute` can return: a nested
    `{'result': {...}}` wrapper, a flat `{'executed': ...}`, a flat
    `{'ok': ...}` (close_position_market's shape), or a bare `None` (a test
    double / an execute_fn that returns nothing)."""
    if isinstance(result, dict):
        nested = result.get("result")
        if isinstance(nested, dict):
            return bool(nested.get("executed"))
        if "executed" in result:
            return bool(result.get("executed"))
        if "ok" in result:
            return bool(result.get("ok"))
    return result is None


def execute_block_detail(result: Any) -> Any:
    """Human-readable reason a blocked `execute_fn(...)` call didn't open,
    for the book's own warning log. Checks every key `maybe_execute`'s return
    dict can carry, in the order they're actually set (verified against every
    `return {...}` site in executor.py: a risk-gate block sets `blocked_by`
    and `gate_results` together, never `gate_results` alone, so that fallback
    is defensive rather than reachable today)."""
    if not isinstance(result, dict):
        return result
    return (result.get("reason") or result.get("error")
            or result.get("blocked_by") or result.get("gate_results") or result)


def load_seen(path: str) -> Dict[str, int]:
    """Read a book's per-key-per-day dedup timestamp file. `{}` on any
    failure (missing file, torn write, wrong shape) — a book must never raise
    into the scan loop over its own dedup bookkeeping."""
    try:
        raw = json.load(open(path))
        return {str(k): int(v) for k, v in raw.items()} if isinstance(raw, dict) else {}
    except Exception:
        return {}


def save_seen(path: str, seen: Dict[str, int]) -> None:
    """Write a book's dedup timestamp file. Best-effort: a failed write is
    swallowed, same as the read side — losing this file just means the book
    may re-open a coin it already opened today, which the claims registry and
    the book's own `held` check still guard against."""
    try:
        with open(path, "w") as fh:
            json.dump(seen, fh, sort_keys=True)
    except Exception:
        pass


def last_pass_ms(path: str) -> int:
    """Read a book's scan-interval throttle timestamp. 0 on any failure,
    which is the safe default (the book's interval check then always fires)."""
    try:
        raw = json.load(open(path))
        return int(raw.get("ts", 0)) if isinstance(raw, dict) else 0
    except Exception:
        return 0


def mark_pass(path: str, now_ms: int) -> None:
    """Record 'we ran a pass at now_ms'. Best-effort, same failure semantics
    as `save_seen`."""
    try:
        with open(path, "w") as fh:
            json.dump({"ts": now_ms}, fh)
    except Exception:
        pass


def load_state(path: str) -> Dict[str, Any]:
    """Read a book's small untyped state blob (poll clock, dedup map, ...).
    `{}` on any failure."""
    try:
        raw = json.load(open(path))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def save_state(path: str, state: Dict[str, Any]) -> None:
    """Write a book's state blob. Best-effort, same failure semantics as
    `save_seen`."""
    try:
        with open(path, "w") as fh:
            json.dump(state, fh)
    except Exception:
        pass


def safe_float(x: Any) -> float:
    """Cast to float, 0.0 on anything that isn't one. Never raises."""
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def bounded_exit_override(stop_pct: float, leverage: float,
                          hard_timeout_minutes: float) -> DslExitOverride:
    """The fixed-stop/fixed-timeout/no-trail exit geometry shared by every
    live book descended from the reverse-refuted-direction audit and the
    W-SOC/W-U1 forward-validated lanes: a hard % stop (also expressed in ROE
    terms via `leverage`), no partial-profit scale-out (`protect_pct` set
    unreachably high), no breakeven ratchet, no ATR stop, no noise band.

    What differs book-to-book is only the 3 inputs: the stop width, the
    leverage, and how the timeout is computed — a fixed hold-days horizon for
    most books, but "ride to the scheduled event" for unlock_short_live's
    pre-unlock run-in. Those stay each book's own responsibility; this
    returns only the resulting fixed shape, byte-identical to what every
    caller built inline before this extraction."""
    return {
        "max_loss_pct": stop_pct,
        "max_loss_roe_pct": stop_pct * leverage,
        "protect_pct": 9999.0,
        "retrace_threshold": 0.5,
        "hard_timeout_minutes": hard_timeout_minutes,
        "breakeven_trigger_pct": 0.0,
        "breakeven_lock_pct": 0.0,
        "stale_flat_timeout_minutes": 0.0,
        "consecutive_breaches_required": 1,
        "atr_stop": {"enabled": False},
        "noise_band": {"enabled": False},
    }
