"""One small helper: run several zero-arg "fetcher" callables concurrently
and never raise.

Every services/intel/sources/*.py fetcher already returns the uniform
`(items, error | None)` shape (services/intel/http_client.py's "timeout, one
retry, never raises" contract, carried through every ATOMIC_FETCHERS entry).
services/intel/api.py's build_packet fans these out across a thread pool for
exactly this reason -- this module is that same pattern, trimmed to what a
single research_mcp tool call needs: no desk_dir, no TTL file cache, no
env.py (build_packet's _submit resolves env.intel_dir(spec.id), which this
server must never touch -- see coins.py's and server.py's module docstrings
on why env.state_dir() is off limits here). A tool that wants caching gets it
for free from the OS/network layer's own behavior at today's call volumes;
v1 ships without one on purpose, see README.md's Limits section.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait
from typing import Any, Callable, List, Optional, Sequence, Tuple

FetchResult = Tuple[List[Any], Optional[str]]


def _name(fn: Callable[[], FetchResult]) -> str:
    return getattr(fn, "__name__", None) or getattr(fn, "func", fn).__class__.__name__


def fetch_many(fns: Sequence[Callable[[], FetchResult]], *, deadline_s: float = 10.0
               ) -> Tuple[List[Any], List[str]]:
    """Run every zero-arg callable in `fns` on its own thread; wait up to
    `deadline_s` total. Returns (items, errors): every finished call's items
    concatenated, and one error string per call that raised, returned an
    error, or did not finish in time. Never raises -- a tool handler can
    always build a response from what this returns, same contract as
    build_packet's own worker loop."""
    if not fns:
        return [], []
    items: List[Any] = []
    errors: List[str] = []
    # Not a `with` block on purpose: ThreadPoolExecutor.__exit__ calls
    # shutdown(wait=True), which blocks until every submitted thread
    # finishes -- exactly the stall `deadline_s` exists to prevent. A
    # straggler (a hung connection, a slow host) must let this function
    # return on time; shutdown(wait=False) abandons it instead, the same
    # choice services/intel/api.py's build_packet makes for the identical
    # reason (see that module's docstring).
    ex = ThreadPoolExecutor(max_workers=min(16, len(fns)))
    try:
        futs = {ex.submit(fn): fn for fn in fns}
        done, not_done = wait(futs, timeout=deadline_s)
        for fut in not_done:
            errors.append(f"{_name(futs[fut])}:timeout_budget_exceeded")
        for fut in done:
            name = _name(futs[fut])
            try:
                rows, err = fut.result()
            except Exception as exc:  # noqa: BLE001 - never raise to the tool handler
                rows, err = None, f"{name}:unexpected:{type(exc).__name__}"
            if err:
                errors.append(err)
            if rows:
                items.extend(rows)
    finally:
        ex.shutdown(wait=False)
    return items, errors
