"""parallel — concurrency-bounded fan-out for independent API calls.

Backed by concurrent.futures.ThreadPoolExecutor so scanning 100+ markets
doesn't spawn 100+ threads — at most `max_workers` workers run and the
executor's internal queue holds the rest.
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, List, Optional, Tuple

logger = logging.getLogger(__name__)


def parallel(
    calls: List[Callable[[], Any]],
    max_workers: Optional[int] = None,
) -> List[Tuple[bool, Any]]:
    """Run independent calls in parallel, concurrency-bounded.

    Args:
        calls: list of zero-arg callables, each typically wrapping an API call.
        max_workers: cap on worker threads. Defaults to min(32, len(calls)).

    Returns:
        List of (success, result) tuples in the same order as the input calls.
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

    results: List[Tuple[bool, Any]] = [(False, None)] * n

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
