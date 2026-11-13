"""TTL file cache for every intel source, under `$PATHIEL_STATE_DIR/intel/<desk>/`.

Why a cache at all: the measurable target is "packet built in under 15s cold
and under 100ms cached." Fifteen-plus network calls (news x4, policy x3,
predictions x2, positioning x1-3, a couple live catalyst checks) cannot hit
15s reliably every time from a residential-grade rate limit (GDELT in
particular — see sources/news.py), so a short-TTL cache turns "slow or
flaky source" into "serves last-known-good instantly" for the common case
of refreshing a desk that was just refreshed.

`get_or_fetch` is the one function sources/*.py should call. It:
  1. Serves a fresh (within `ttl_s`) cache hit with zero network I/O.
  2. If the LAST attempt failed within `_FAILURE_BACKOFF_S`, skips the
     network call entirely and returns that cached failure immediately
     (falling back to stale success data if any exists). This is the
     piece that makes the "under 100ms cached" target real rather than
     theoretical: a persistently-failing source (GDELT's rate limit was
     observed to trigger on most calls from this sandbox — see
     sources/news.py) would otherwise re-pay its full timeout+retry cost
     on EVERY call, which a real measurement during development caught —
     the very first cached-path timing run still took ~12s wall clock
     because GDELT had never succeeded even once, so it had no success
     cache to short-circuit on and retried live every time. Caching the
     failure itself, not just the success, is what fixes that.
  3. On a real miss (no fresh success, no recent failure), calls
     `fetch_fn()` -> (data, error).
  4. On success, writes the cache, clears any failure marker, and
     returns (data, None).
  5. On failure, records the failure marker (so point 2 applies next
     time), then falls back to ANY existing success cache regardless of
     age (stale beats empty) but still returns the error, so the caller
     still records the source as failed in `IntelPacket.sources_failed`
     even though the packet is not actually empty for it.
  6. On failure with no success cache at all, returns (None, error).

Atomic write (tempfile + os.replace) so a reader can never observe a
half-written cache file — the same pattern `pathiel/agents/atomic_io.py`
uses for the same reason.

This module takes a resolved directory (`desk_dir`), never a desk id plus
a live lookup of `$PATHIEL_STATE_DIR` — on purpose. `services.intel.api`'s
build_packet fans every source out to its own thread via
`ThreadPoolExecutor`, and deliberately does NOT wait for a straggler past
its deadline (`executor.shutdown(wait=False)`); that straggler thread
keeps running and eventually calls back in here. If this module re-read
`env.state_dir()` / `os.environ["PATHIEL_STATE_DIR"]` lazily at write
time instead, a straggler that outlives its caller's context (a test's
`monkeypatch.setenv`, in particular) would resolve a DIFFERENT directory
than the one that was correct when the job was submitted — which is
exactly what a real test run caught: a slow-fetch test using a tiny
deadline left an orphaned thread that woke up after pytest had already
torn the test's isolated `PATHIEL_STATE_DIR` back down, and it wrote its
result straight into the repo root (`intel/oil/...`). The fix is
structural, not a timing patch: `api.py` resolves the directory ONCE, in
the synchronous main thread, before submitting any job, and every worker
closure closes over that already-resolved string — so nothing in a
background thread ever reads an environment variable that could change
or disappear out from under it.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from typing import Any, Callable, Optional, Tuple

_FilenameSafe = str

# How long a failure is remembered before the next call is willing to pay
# for another live attempt. Short on purpose — long enough that a run of
# calls a few seconds apart (a page of desks refreshing back to back)
# doesn't re-hammer a dead source, short enough that a source recovering
# mid-session is retried well within one TTL window.
_FAILURE_BACKOFF_S = 90.0


def _key_to_filename(key: str, *, suffix: str = "") -> _FilenameSafe:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in key)
    return f"{safe}{suffix}.json"


def path_for(desk_dir: str, key: str) -> str:
    return os.path.join(desk_dir, _key_to_filename(key))


def _failure_path_for(desk_dir: str, key: str) -> str:
    return os.path.join(desk_dir, _key_to_filename(key, suffix=".err"))


def _read_wrapper(path: str) -> Optional[Tuple[Any, float]]:
    """(payload, age_s) or None if no cache file / unreadable."""
    try:
        with open(path) as fh:
            wrapper = json.load(fh)
        cached_at = float(wrapper.get("cached_at", 0))
        return wrapper.get("payload"), max(0.0, time.time() - cached_at)
    except (OSError, ValueError, json.JSONDecodeError, TypeError):
        return None


def _read(desk_dir: str, key: str) -> Optional[Tuple[Any, float]]:
    return _read_wrapper(path_for(desk_dir, key))


def _read_failure(desk_dir: str, key: str) -> Optional[Tuple[str, float]]:
    return _read_wrapper(_failure_path_for(desk_dir, key))


def _atomic_write(path: str, directory: str, payload: Any) -> None:
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump({"cached_at": time.time(), "payload": payload}, fh, default=str)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def save(desk_dir: str, key: str, payload: Any) -> None:
    _atomic_write(path_for(desk_dir, key), desk_dir, payload)


def _save_failure(desk_dir: str, key: str, error: str) -> None:
    _atomic_write(_failure_path_for(desk_dir, key), desk_dir, error)


def _clear_failure(desk_dir: str, key: str) -> None:
    try:
        os.unlink(_failure_path_for(desk_dir, key))
    except OSError:
        pass


def load_fresh(desk_dir: str, key: str, ttl_s: float) -> Optional[Any]:
    """Cached payload if present and within `ttl_s`, else None."""
    hit = _read(desk_dir, key)
    if hit is None:
        return None
    payload, age_s = hit
    return payload if age_s <= ttl_s else None


def read_history(desk_dir: str, key: str) -> list:
    """The rolling sample list written by `append_history`, or [] if none
    exists yet. Each sample is whatever dict `append_history` was given,
    always including a 'ts' epoch-seconds field.

    Exists for sources/venue.py's open-interest 24h change: Hyperliquid's
    public /info API has no historical-OI endpoint (confirmed live
    2026-10-23 - openInterestHistory/assetHistory/candleSnapshot all 400 on
    a bare {"type": ..., "coin": ...} body), so this service grows its own
    short history by appending one sample per REAL (non-cached) fetch and
    reading back whatever is closest to 24h old. Until ~20h of samples
    exist, the 24h change is None with a note saying so - never a fabricated
    number."""
    hit = _read(desk_dir, key)
    if hit is None:
        return []
    payload, _age_s = hit
    return payload if isinstance(payload, list) else []


def append_history(desk_dir: str, key: str, sample: dict, *, keep_s: float,
                    now: Optional[float] = None) -> list:
    """Append `sample` (must be JSON-safe and include a numeric 'ts') to the
    rolling list cached under `key`, drop entries older than `keep_s`, write
    it back atomically, and return the resulting list. `now` is injectable
    (epoch seconds) so tests never depend on the real wall clock."""
    now = time.time() if now is None else now
    history = read_history(desk_dir, key)
    history.append(sample)
    cutoff = now - keep_s
    history = [s for s in history if float(s.get("ts", 0)) >= cutoff]
    save(desk_dir, key, history)
    return history


def get_or_fetch(desk_dir: str, key: str, ttl_s: float,
                  fetch_fn: Callable[[], Tuple[Optional[Any], Optional[str]]]
                  ) -> Tuple[Optional[Any], Optional[str]]:
    fresh = load_fresh(desk_dir, key, ttl_s)
    if fresh is not None:
        return fresh, None

    recent_failure = _read_failure(desk_dir, key)
    if recent_failure is not None:
        failure_msg, age_s = recent_failure
        if age_s <= _FAILURE_BACKOFF_S:
            stale = _read(desk_dir, key)
            if stale is not None:
                payload, _stale_age_s = stale
                return payload, f"stale_fallback_recent_failure:{failure_msg}"
            return None, f"recent_failure:{failure_msg}"

    data, error = fetch_fn()
    if error is None:
        save(desk_dir, key, data)
        _clear_failure(desk_dir, key)
        return data, None
    _save_failure(desk_dir, key, error)
    stale = _read(desk_dir, key)
    if stale is not None:
        payload, age_s = stale
        return payload, f"stale_fallback_{int(age_s)}s:{error}"
    return None, error
