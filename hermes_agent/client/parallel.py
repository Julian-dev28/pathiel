"""parallel — concurrency-bounded fan-out for independent API calls.

Adapted from senpi_runtime_helpers.parallel for hermes-trader's scanning
pipeline. Backed by concurrent.futures.ThreadPoolExecutor so scanning
100+ markets doesn't spawn 100+ threads — at most `max_workers` workers
are created and the executor's internal queue holds the rest.

Stdlib-only.
"""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, List, Optional, Tuple

logger = logging.getLogger(__name__)

_WARN_THROTTLE_SECONDS = 5.0


class _WarnGate:
    """Throttles parallel_queue_warn events across calls so a producer tick
    that fires several parallel() invocations doesn't flood logs."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_emitted = 0.0

    def should_emit(self) -> bool:
        now = time.time()
        with self._lock:
            if now - self._last_emitted < _WARN_THROTTLE_SECONDS:
                return False
            self._last_emitted = now
            return True


_WARN_GATE = _WarnGate()


def parallel(
    calls: List[Callable[[], Any]],
    max_workers: Optional[int] = None,
    warn_threshold: Optional[int] = None,
) -> List[Tuple[bool, Any]]:
    """Run independent calls in parallel, concurrency-bounded.

    Args:
        calls: list of zero-arg callables. Each typically wraps an API call
            (fetch_candles, fetch_mids, etc.).
        max_workers: cap on worker threads. Defaults to min(32, len(calls)).
        warn_threshold: if queue depth exceeds this, emit a warning.

    Returns:
        List of (success: bool, result: Any) tuples, in the same order as
        the input calls (not completion order).
    """
    n = len(calls)
    if n == 0:
        return []
    if n == 1:
        try:
            result = calls[0]()
            return [(True, result)]
        except Exception as e:
            return [(False, e)]

    if max_workers is None:
        max_workers = min(32, n)

    results: List[Tuple[bool, Any]] = [None] * n  # type: ignore[list-item]

    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="hermes-par") as pool:
        future_to_idx = {
            pool.submit(fn): idx for idx, fn in enumerate(calls)
        }

        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                results[idx] = (True, future.result(timeout=30))
            except Exception as e:
                results[idx] = (False, e)
                logger.warning(f"[par] Call {idx} failed: {e}")

    return results
