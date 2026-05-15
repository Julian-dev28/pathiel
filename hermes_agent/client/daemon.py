"""producer_daemon — long-lived scheduler for hermes-trader scanning.

Adapted from senpi_runtime_helpers.daemon. Replaces ad-hoc while True
loops with a robust scheduler that:
- Fires the scan function on a fixed interval
- Wraps each tick in scanner_lock so overlapping ticks are skipped
- Enforces per-tick wall-clock timeout via SIGALRM
- Handles SIGTERM/SIGINT with graceful drain
- Writes self-describing state files (pid, heartbeat)

Stdlib-only.
"""

import logging
import os
import signal
import threading
import time
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from .lock import scanner_lock

logger = logging.getLogger(__name__)

_DEFAULT_TICK_TIMEOUT = 180.0  # 3 minutes per scan tick
_DEFAULT_STATE_DIR = os.path.expanduser("~/.hermes")


class _TickTimeout(BaseException):
    """Raised internally when a single tick exceeds its wall-clock budget."""


def _install_shutdown_handlers(stop_event: threading.Event) -> None:
    def handler(signum: int, _frame: Any) -> None:
        if not stop_event.is_set():
            logger.info(f"[daemon] Signal {signum} received — draining...")
            stop_event.set()

    with suppress(ValueError):
        signal.signal(signal.SIGTERM, handler)
        signal.signal(signal.SIGINT, handler)


def _arm_tick_alarm(seconds: float) -> bool:
    """Install SIGALRM to enforce per-tick wall-clock budget. Returns True if armed."""
    if not hasattr(signal, 'SIGALRM'):
        return False
    def on_alarm(signum: int, frame: Any) -> None:
        raise _TickTimeout(f"Tick exceeded {seconds}s timeout")
    signal.signal(signal.SIGALRM, on_alarm)
    signal.alarm(int(seconds) + 1)  # +1s safety margin
    return True


def _disarm_tick_alarm() -> None:
    if hasattr(signal, 'SIGALRM'):
        try:
            signal.alarm(0)
        except Exception:
            pass


def _interruptible_sleep(seconds: float, stop_event: threading.Event) -> bool:
    """Sleep for up to `seconds`, but return early if stop_event is set."""
    end = time.time() + seconds
    while time.time() < end and not stop_event.is_set():
        remaining = end - time.time()
        if remaining > 0.5:
            stop_event.wait(timeout=0.5)
        else:
            time.sleep(remaining)
    return stop_event.is_set()


def _atomic_write(path: Path, data: dict) -> None:
    """Write JSON atomically (temp file + os.replace)."""
    tmp = path.with_suffix('.tmp')
    try:
        tmp.write_text(json.dumps(data, default=str))
        tmp.replace(path)
    except Exception as e:
        logger.warning(f"[daemon] State write failed: {e}")


def producer_daemon(
    scan_fn: Callable[[], dict],
    interval_seconds: int = 180,
    name: str = "hermes-scanner",
    state_dir: Optional[str] = None,
    tick_timeout: float = _DEFAULT_TICK_TIMEOUT,
) -> None:
    """Run a long-lived scanning daemon.

    Args:
        scan_fn: callable that returns a dict of scan results.
        interval_seconds: seconds between scan starts.
        name: lock name (affects lock file path).
        state_dir: directory for state files.
        tick_timeout: max seconds per scan tick.

    Usage:
        from hermes_agent.client.daemon import producer_daemon
        from hermes_agent.agents.perception import scan_once

        producer_daemon(
            scan_fn=lambda: scan_once(
                universe=get_universe(),
                min_score=75,
                throttle=100
            ),
            interval_seconds=180,
            name="hermes-scanner",
        )
    """
    import json  # For atomic_write

    state_dir = state_dir or _DEFAULT_STATE_DIR
    state_path = Path(state_dir)
    state_path.mkdir(parents=True, exist_ok=True)

    # PID file
    pid_file = state_path / f"{name}.pid"
    pid_file.write_text(str(os.getpid()))

    # Heartbeat file
    heartbeat_file = state_path / f"{name}.heartbeat"

    # Stop event for graceful shutdown
    stop_event = threading.Event()

    # Install signal handlers
    _install_shutdown_handlers(stop_event)

    # Install tick timeout alarm (only main thread can do this)
    has_alarm = _arm_tick_alarm(tick_timeout)

    tick_count = 0
    error_count = 0
    start_time = time.time()

    logger.info(f"[daemon] Starting {name} — interval={interval_seconds}s, timeout={tick_timeout}s")
    logger.info(f"[daemon] State dir: {state_dir}")

    try:
        while not stop_event.is_set():
            tick_count += 1
            tick_start = time.time()

            # Heartbeat: start of tick
            _atomic_write(heartbeat_file, {
                "tick": tick_count,
                "status": "running",
                "last_tick_iso": datetime.now(timezone.utc).isoformat(),
                "error_count": error_count,
            })

            # Acquire scan lock — if previous tick is still running, skip
            lock_acquired = False
            try:
                with scanner_lock(name, timeout=10.0):
                    # Run the scan with timeout
                    try:
                        if has_alarm:
                            _disarm_tick_alarm()  # reset before starting

                        result = scan_fn()
                        status = "ok"
                        tick_error = None
                    except _TickTimeout as e:
                        status = "timeout"
                        tick_error = str(e)
                        error_count += 1
                        result = {"error": "tick_timeout", "message": str(e)}
                    except Exception as e:
                        status = "error"
                        tick_error = str(e)
                        error_count += 1
                        result = {"error": "scan_error", "message": str(e)}

            except TimeoutError:
                status = "locked"
                tick_error = "previous scan still running"
                error_count += 0  # not our error
                result = {"status": "skipped", "reason": "scan_lock"}
                logger.warning(f"[daemon] Tick {tick_count} skipped (scan lock held)")

            tick_elapsed = time.time() - tick_start
            elapsed_total = time.time() - start_time

            # Heartbeat: end of tick
            _atomic_write(heartbeat_file, {
                "tick": tick_count,
                "status": status,
                "error": tick_error,
                "tick_elapsed_seconds": round(tick_elapsed, 2),
                "uptime_seconds": round(elapsed_total, 1),
                "last_tick_iso": datetime.now(timezone.utc).isoformat(),
                "error_count": error_count,
            })

            if status == "ok":
                logger.info(f"[daemon] Tick {tick_count} complete — {tick_elapsed:.1f}s elapsed")
            else:
                logger.warning(f"[daemon] Tick {tick_count} — status={status} ({tick_elapsed:.1f}s)")

            # Sleep until next interval
            sleep_time = max(0, interval_seconds - tick_elapsed)
            if sleep_time > 0:
                logger.debug(f"[daemon] Sleeping {sleep_time:.1f}s until next tick...")
                interrupted = _interruptible_sleep(sleep_time, stop_event)
                if interrupted:
                    logger.info("[daemon] Stop event received — exiting loop")
                    break

    finally:
        # Cleanup
        _disarm_tick_alarm()
        with suppress(FileNotFoundError):
            pid_file.unlink()
        logger.info(f"[daemon] {name} stopped — {tick_count} ticks, {error_count} errors, {time.time()-start_time:.0f}s uptime")


def check_daemon_state(name: str, state_dir: Optional[str] = None) -> dict:
    """Check the state of a running daemon."""
    state_dir = state_dir or _DEFAULT_STATE_DIR
    state_path = Path(state_dir)

    pid_file = state_path / f"{name}.pid"
    heartbeat_file = state_path / f"{name}.heartbeat"

    pid = None
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
        except (ValueError, FileNotFoundError):
            pass

    from .lock import check_lock_status
    lock_status = check_lock_status(name, state_dir)

    heartbeat = None
    if heartbeat_file.exists():
        try:
            heartbeat = json.loads(heartbeat_file.read_text())
        except (json.JSONDecodeError, FileNotFoundError):
            pass

    return {
        "name": name,
        "pid": pid,
        "pid_alive": _is_pid_alive(pid),
        "lock": lock_status,
        "heartbeat": heartbeat,
    }


def _is_pid_alive(pid: Optional[int]) -> bool:
    """Check if a PID is still running."""
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False
