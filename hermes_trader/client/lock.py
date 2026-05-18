"""scanner_lock — fcntl lock with PID-aliveness stale recovery.

Ensures only one scan runs at a time, with automatic recovery if a previous
scan crashed: fcntl flock is released by the kernel on process death, so a
stale lock file is reclaimed on the next acquire.
"""

import fcntl
import json
import logging
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional, Dict, Any

logger = logging.getLogger(__name__)

_DEFAULT_LOCK_DIR = os.path.expanduser("~/.hermes")


def _lock_path(name: str, lock_dir: Optional[str] = None) -> Path:
    base = Path(lock_dir or _DEFAULT_LOCK_DIR)
    base.mkdir(parents=True, exist_ok=True)
    return base / f"hermes-{name}.lock"


def _read_lock_metadata(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with path.open("r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _is_pid_alive(pid: Optional[int]) -> bool:
    """Check if a PID is still running."""
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _write_metadata_inplace(fd: int, payload: Dict[str, Any]) -> None:
    """Write metadata onto the already-locked fd. Keeps inode + flock stable."""
    encoded = json.dumps(payload).encode("utf-8")
    os.ftruncate(fd, 0)
    os.lseek(fd, 0, os.SEEK_SET)
    while encoded:
        written = os.write(fd, encoded)
        encoded = encoded[written:]


@contextmanager
def scanner_lock(name: str, timeout: float = 300.0) -> Iterator[None]:
    """Acquire a file lock for a named scanner.

    If a previous scan is still running, waits up to `timeout` seconds.
    If the previous scan has died (crash), reclaims the lock immediately.

    Usage:
        with scanner_lock("scan", timeout=300):
            run_scan()  # only one scan at a time
    """
    lock_path = _lock_path(name)
    acquired = False
    deadline = time.time() + timeout
    start_time = time.time()
    metadata = {
        "pid": os.getpid(),
        "start_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(start_time)),
        "name": name,
        "hostname": os.uname().nodename if hasattr(os, 'uname') else "unknown",
    }

    fd = None
    try:
        while time.time() < deadline:
            try:
                fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                # Got the lock
                acquired = True
                # Write metadata in-place
                _write_metadata_inplace(fd, metadata)
                logger.debug(f"[lock] Acquired lock '{name}' (PID {os.getpid()})")
                break
            except (IOError, OSError):
                # Lock held by someone else
                if fd is not None:
                    os.close(fd)
                    fd = None

                # Check if previous holder is alive
                prev = _read_lock_metadata(lock_path)
                if prev and not _is_pid_alive(prev.get("pid")):
                    logger.info(f"[lock] Reclaiming stale lock '{name}' (prev PID {prev.get('pid')} is dead)")
                    continue  # try again, we should get it now

                # Previous holder is alive — wait a bit
                logger.debug(f"[lock] Waiting for lock '{name}' (held by PID {prev.get('pid') if prev else '?'})")
                time.sleep(0.5)

        if not acquired:
            raise TimeoutError(f"Could not acquire lock '{name}' within {timeout}s")

        # Yield control — caller's scan runs here
        yield

    finally:
        if fd is not None:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)
            except OSError:
                pass
        logger.debug(f"[lock] Released lock '{name}'")


def check_lock_status(name: str, lock_dir: Optional[str] = None) -> Dict[str, Any]:
    """Check if a lock is currently held and by whom."""
    lock_path = _lock_path(name, lock_dir)
    prev = _read_lock_metadata(lock_path)

    if prev is None:
        return {"held": False, "lock_file": False}

    pid = prev.get("pid")
    alive = _is_pid_alive(pid)
    return {
        "held": True,
        "lock_file": lock_path.exists(),
        "pid": pid,
        "pid_alive": alive,
        "start_iso": prev.get("start_iso"),
        "name": prev.get("name"),
        "age_seconds": round(time.time() - time.mktime(time.strptime(prev.get("start_iso", ""), "%Y-%m-%dT%H:%M:%SZ")), 1),
    }
