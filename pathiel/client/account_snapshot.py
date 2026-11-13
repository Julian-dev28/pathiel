"""One shared, stale-while-revalidate read of an account's Hyperliquid state,
for code that SHOWS the account: the dashboard's summary and positions, the
trade banner, "Your size" on a desk, desk analytics. Never for code that
trades: the executor and the trading loop call hl_client.fetch_account_state
directly, fresh, every time, and nothing here changes what they see.

Why (2026-10-19). Every page poll did its own fresh read, and one read is a
clearinghouseState per perp dex (main + 10 HIP-3) plus spot: 12 requests, 24
weight, on the dashboard server's 2 weight/s budget, so 12 s of budget per
read. The landing page asked every 6 s (summary) and 10 s (positions); reads
took 6-12 s, polls piled up behind each other, and the browser's six
connections per site filled with them, so clicking Desks waited.

Now:
  - one snapshot per (address, include_hip3), shared by every caller;
  - younger than FRESH_S: served as is, no request;
  - older: served at once, and ONE background re-read starts (single flight);
  - older than MAX_STALE_S, or none yet: the caller waits for a read, still
    single flight (ten callers, one read);
  - a re-read asks only the HIP-3 dexes that held money or a position at the
    last full sweep (main + spot + xyz: 6 weight, not 24); a full sweep runs
    every SWEEP_S, so money moved to a new dex shows within that window;
  - a read that failed, or lost a dex that held money last time, is thrown
    away and the previous snapshot stays: a failed read never reads as $0.

Every returned state is fetch_account_state's dict plus `snapshot_age_s`.
Knobs (seconds): PATHIEL_ACCOUNT_FRESH_S (15), PATHIEL_ACCOUNT_MAX_STALE_S
(300), PATHIEL_ACCOUNT_SWEEP_S (300), PATHIEL_ACCOUNT_WAIT_S (45).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from pathiel.client.rate_limit import background_priority

logger = logging.getLogger(__name__)


class AccountReadError(RuntimeError):
    """No snapshot young enough to show, and the read for a new one failed."""


def _knob(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def fresh_s() -> float:
    return _knob("PATHIEL_ACCOUNT_FRESH_S", 15.0)


def max_stale_s() -> float:
    return _knob("PATHIEL_ACCOUNT_MAX_STALE_S", 300.0)


def sweep_s() -> float:
    return _knob("PATHIEL_ACCOUNT_SWEEP_S", 300.0)


def wait_s() -> float:
    return _knob("PATHIEL_ACCOUNT_WAIT_S", 45.0)


def _default_fetch(user: str, include_hip3: bool, **kwargs: Any) -> Dict[str, Any]:
    from pathiel.client.hl_client import fetch_account_state
    return fetch_account_state(user, include_hip3=include_hip3, **kwargs)


def _default_list_dexes() -> List[str]:
    from pathiel.client.universe import list_hip3_dexes
    return list_hip3_dexes()


# Swappable for tests; production never touches these.
FETCH: Callable[..., Dict[str, Any]] = _default_fetch
LIST_DEXES: Callable[[], List[str]] = _default_list_dexes


@dataclass
class _Entry:
    state: Optional[Dict[str, Any]] = None
    at: float = 0.0                          # monotonic time of the accepted read
    swept_at: float = 0.0                    # monotonic time of the last accepted full sweep
    funded: Optional[Set[str]] = None        # HIP-3 dexes to re-read between sweeps
    inflight: Optional[threading.Event] = None
    error: Optional[str] = None


_LOCK = threading.Lock()
_ENTRIES: Dict[Tuple[str, bool], _Entry] = {}


def reset() -> None:
    """Forget every snapshot (tests, and an account switch that wants a clean read)."""
    with _LOCK:
        _ENTRIES.clear()


def account_state(user: str, include_hip3: bool = True, *,
                  max_age_s: Optional[float] = None) -> Dict[str, Any]:
    """The account's state for display. See the module docstring for the
    freshness rules. `max_age_s` (a caller that must not act on an old read,
    like an MCP tool): serve a snapshot only this young, else wait for a new
    read; never served stale. Raises AccountReadError when there is nothing
    young enough to show and a read could not get one."""
    if not user:
        raise AccountReadError("no account address")
    key = (user.lower(), bool(include_hip3))
    called = time.monotonic()
    with _LOCK:
        entry = _ENTRIES.setdefault(key, _Entry())
        age = called - entry.at if entry.state is not None else None
        if age is not None and (age <= fresh_s() if max_age_s is None else age < max_age_s):
            return _with_age(entry.state, age)
        if max_age_s is None and age is not None and age <= max_stale_s():
            if entry.inflight is None:
                entry.inflight = threading.Event()
                threading.Thread(target=_revalidate, args=(key, user, entry.inflight),
                                 name="account-snapshot", daemon=True).start()
            return _with_age(entry.state, age)
        event = entry.inflight
        leader = event is None
        if leader:
            event = entry.inflight = threading.Event()
    if leader:
        _read(key, user, event)              # at the caller's own (foreground) priority
    else:
        event.wait(wait_s())
    with _LOCK:
        entry = _ENTRIES[key]
        if entry.state is not None:
            now = time.monotonic()
            limit = max_stale_s() if max_age_s is None else max_age_s
            if entry.at >= called or now - entry.at <= limit:
                return _with_age(entry.state, now - entry.at)
        raise AccountReadError(entry.error or "the account read did not finish in time")


def _with_age(state: Dict[str, Any], age: float) -> Dict[str, Any]:
    out = dict(state)
    out["snapshot_age_s"] = round(max(0.0, age), 1)
    return out


def _revalidate(key: Tuple[str, bool], user: str, event: threading.Event) -> None:
    with background_priority():              # nobody is waiting on this one
        _read(key, user, event)


def _funded_dexes(state: Dict[str, Any]) -> Set[str]:
    out = {d for d, v in (state.get("dex_equity") or {}).items() if d and float(v or 0) > 0}
    for p in state.get("asset_positions") or []:
        coin = ((p or {}).get("position") or {}).get("coin") or ""
        if ":" in coin:
            out.add(coin.split(":", 1)[0])
    return out


def _read(key: Tuple[str, bool], user: str, event: threading.Event) -> None:
    include_hip3 = key[1]
    with _LOCK:
        entry = _ENTRIES[key]
        prev = entry.state
        full = include_hip3 and (entry.funded is None
                                 or time.monotonic() - entry.swept_at >= sweep_s())
        funded = set(entry.funded or ())
    t0 = time.monotonic()
    try:
        dexes: Optional[List[str]] = None
        if include_hip3:
            dexes = list(LIST_DEXES()) if full else sorted(funded)
        state = FETCH(user, include_hip3, hip3_dexes=dexes, strict=True)
        if not isinstance(state, dict):
            raise AccountReadError(f"account read returned {type(state).__name__}")
        problem, failed = _check(state, prev, dexes)
        with _LOCK:
            if problem:
                entry.error = problem
            else:
                entry.state, entry.at, entry.error = state, time.monotonic(), None
                if full:
                    now_funded = _funded_dexes(state) | failed   # unknown dexes stay on the list
                    if entry.funded is not None and now_funded != entry.funded:
                        logger.info(f"[account_snapshot] {user[:10]}: dexes re-read between "
                                    f"sweeps now {sorted(now_funded)}")
                    entry.funded, entry.swept_at = now_funded, entry.at
        if problem:
            logger.warning(f"[account_snapshot] {user[:10]}: kept the previous read: {problem}")
        else:
            logger.debug(f"[account_snapshot] {user[:10]}: {'full' if full else 'funded-dex'} read "
                         f"in {time.monotonic() - t0:.1f}s")
    except Exception as exc:  # noqa: BLE001 - a display read must never take the caller down
        with _LOCK:
            entry.error = f"{type(exc).__name__}: {exc}"
        logger.warning(f"[account_snapshot] {user[:10]}: read failed after "
                       f"{time.monotonic() - t0:.1f}s, kept the previous read: {exc}")
    finally:
        with _LOCK:
            if entry.inflight is event:
                entry.inflight = None
        event.set()


def _check(state: Dict[str, Any], prev: Optional[Dict[str, Any]],
           dexes: Optional[List[str]]) -> Tuple[Optional[str], Set[str]]:
    """(why this read must not replace the previous one, or None; the HIP-3
    dexes that were asked but did not answer)."""
    answered = set(state.get("queried_dexes") or ())
    failed = set(dexes or ()) - answered
    if prev is not None:
        from pathiel.client.hl_client import missing_material_dexes
        lost = missing_material_dexes(prev.get("dex_equity"), answered)
        if lost:
            return f"dex(es) {sorted(lost)} held money last read and did not answer", failed
    if failed:
        logger.info(f"[account_snapshot] dex(es) {sorted(failed)} did not answer; "
                    f"they are asked again next read")
    return None, failed


def wait_idle(user: str, include_hip3: bool = True, timeout: float = 5.0) -> bool:
    """Block until no read is in flight for this account (tests)."""
    with _LOCK:
        entry = _ENTRIES.get((user.lower(), bool(include_hip3)))
        event = entry.inflight if entry else None
    return True if event is None else event.wait(timeout)
