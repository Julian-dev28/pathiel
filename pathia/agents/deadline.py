"""A hard wall-clock deadline for calls that must never stall the trading loop.

WHY THIS EXISTS
2026-09-07: the loop wedged for 8 minutes with three positions open. The cause
was social_trending_recorder's CoinGecko fetch, which already passed
`timeout=15.0` to urllib. That timeout is per-SOCKET-OPERATION, not a total
deadline: every recv that returns a byte resets it, so a server dribbling a
response holds the call open indefinitely. The fetch neither returned nor
raised, its `except` never ran, and seven sockets piled up in CLOSE_WAIT.

The loop's own structure is what made that dangerous. Exits run early in the
cycle and the recorders run late, so a recorder that never returns does not
break the exit that already happened - it prevents the NEXT one, and every one
after. Stops become unreachable on live positions because a social API is slow.
That is an unacceptable coupling regardless of which library is at fault.

So the deadline is enforced at the CALLER, not delegated to a library's notion
of a timeout. The worker is a daemon thread: a blocked syscall cannot be
interrupted from outside, so the thread is abandoned rather than joined, and
dies with the process. Abandoning a thread leaks it, which is the correct trade
- one leaked thread per stall, against an unmonitored live position.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)
T = TypeVar("T")


def with_deadline(fn: Callable[[], T], seconds: float, default: T,
                  label: str = "call") -> T:
    """Run `fn` and give up hard after `seconds`, returning `default`.

    `default` is returned on timeout AND on exception, so a caller inside the
    trading loop never has to care which happened — either way the answer is
    "no data this pass", which every recorder already handles.
    """
    box: dict[str, Any] = {}

    def _run() -> None:
        try:
            box["ok"] = fn()
        except BaseException as exc:            # noqa: BLE001
            box["err"] = exc

    t = threading.Thread(target=_run, name=f"deadline:{label}", daemon=True)
    t.start()
    t.join(seconds)
    if t.is_alive():
        logger.warning(
            f"[deadline] {label} exceeded {seconds:.0f}s — abandoning the thread "
            f"and continuing. The loop must not wait on it.")
        return default
    if "err" in box:
        logger.warning(f"[deadline] {label} raised: {box['err']!r}")
        return default
    return box.get("ok", default)
