"""Shared logging/disk policy for hermes-trader — one source of truth for
rotation thresholds and disk-guard limits, used by both the external rotator
(``scripts/log_rotate.py``) and ``scripts/restart.sh``.

WHY ROTATION HAS TO LIVE OUTSIDE THE PROCESS
---------------------------------------------
Every long-running hermes-trader process (``scripts/trading_loop.py``,
``python -m hermes_trader.server``, ``scripts/scheduler.py``, the trend_engine
sample-daemon) is started by ``scripts/restart.sh`` with shell append
redirection::

    nohup "$PY" scripts/trading_loop.py >> logs/trading_loop.log 2>&1 &

That ``>>`` opens the log file exactly once, in ``O_APPEND`` mode, and hands
the resulting file descriptor to the child as fd 1 and fd 2. Everything the
process ever emits — through the ``logging`` module, through a bare
``print()`` (the trend_engine sample-daemon uses only ``print()``, no
``logging`` at all), or an uncaught traceback the interpreter dumps straight
to stderr on crash — goes out through *that* fd for the entire life of the
process.

A ``logging.handlers.RotatingFileHandler`` configured *inside* the process
does not control this. It would open a second, independent handle to the log
path and rotate that handle's view of the file, while the shell's fd — the
thing actually catching stdout/stderr — keeps writing into whatever inode its
fd points at. Rename the file out from under it and the process's real
output silently keeps flowing into the renamed (now "old") file forever; the
"new" file at the original path stays empty. That's the trap: in-process
rotation looks like it fixes the file at ``logs/trading_loop.log``, but the
shell-owned fd never reopens, so the growth just moves to a differently-named
file. This is exactly the scenario ``logrotate``'s ``copytruncate`` option
exists for — an appender that cannot be told (via signal or otherwise) to
reopen its output.

So the rotator (``scripts/log_rotate.py``) truncates the log file **in
place**: same path, same inode. ``os.truncate(path, 0)`` operates on the
inode, not on any particular fd, so a writer's next ``O_APPEND`` write lands
right after the new (zero) EOF in the very same file — no signal, no
cooperation from the writer required. See that script's module docstring for
the copytruncate implementation and the race window it accepts.

This module supplies:

* the size / retention / disk policy, overridable by env var, shared by the
  rotator and the disk guard so they can never drift out of sync with each
  other;
* ``total_log_bytes()`` / ``check_disk_guard()`` — used by
  ``scripts/log_rotate.py --guard``, which ``scripts/restart.sh`` shells out
  to before starting any process;
* ``configure_logging()`` — a real ``RotatingFileHandler`` helper, for the
  one case where in-process rotation *does* work correctly: a process that
  owns its fd outright (invoked without shell ``>>`` wrapping, e.g. under a
  process supervisor that captures stdout itself, or a future entrypoint
  written to call this directly instead of relying on nohup redirection).
  Kept here, using the same policy constants, so if/when
  ``scripts/trading_loop.py`` or ``hermes_trader/server.py`` are changed to
  call it, the two rotation paths share one policy instead of drifting.
"""

from __future__ import annotations

import gzip
import logging
import logging.handlers
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

# ── policy defaults (all overridable via env) ───────────────────────────────
# Per-file size that triggers rotation.
DEFAULT_MAX_BYTES = 20 * 1024 * 1024          # 20 MB
# How many gzip'd backups to keep per log file (name.log.1.gz .. name.log.N.gz).
DEFAULT_BACKUP_COUNT = 5
# Hard cap on the *sum* of everything under logs/ (live files + backups).
# Enforced by pruning oldest backups first; see scripts/log_rotate.py.
DEFAULT_LOG_DIR_MAX_BYTES = 750 * 1024 * 1024  # 750 MB
# Disk-free thresholds (bytes) for the startup guard.
DEFAULT_DISK_FREE_WARN_BYTES = 2 * 1024 ** 3     # 2 GB — alarm loudly, still start
DEFAULT_DISK_FREE_CRITICAL_BYTES = 500 * 1024 ** 2  # 500 MB — refuse to start
# How often the background rotator daemon (scripts/log_rotate.py --daemon)
# sweeps the log directory.
DEFAULT_ROTATE_INTERVAL_SEC = 300  # 5 minutes


def _root_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def max_bytes() -> int:
    return _env_int("HERMES_LOG_MAX_BYTES", DEFAULT_MAX_BYTES)


def backup_count() -> int:
    return _env_int("HERMES_LOG_BACKUP_COUNT", DEFAULT_BACKUP_COUNT)


def log_dir_max_bytes() -> int:
    return _env_int("HERMES_LOG_DIR_MAX_BYTES", DEFAULT_LOG_DIR_MAX_BYTES)


def disk_free_warn_bytes() -> int:
    mb = os.environ.get("HERMES_DISK_FREE_WARN_MB")
    if mb is not None and mb.strip() != "":
        try:
            return int(mb) * 1024 * 1024
        except ValueError:
            pass
    return DEFAULT_DISK_FREE_WARN_BYTES


def disk_free_critical_bytes() -> int:
    mb = os.environ.get("HERMES_DISK_FREE_CRITICAL_MB")
    if mb is not None and mb.strip() != "":
        try:
            return int(mb) * 1024 * 1024
        except ValueError:
            pass
    return DEFAULT_DISK_FREE_CRITICAL_BYTES


def rotate_interval_sec() -> int:
    return _env_int("HERMES_LOG_ROTATE_INTERVAL_SEC", DEFAULT_ROTATE_INTERVAL_SEC)


def resolve_log_dir(root: Optional[str] = None) -> Path:
    """Resolve the logs directory. ``HERMES_LOG_DIR`` wins (tests / manual
    override); otherwise ``<repo root>/logs``."""
    override = os.environ.get("HERMES_LOG_DIR")
    if override:
        return Path(override)
    base = Path(root) if root else _root_dir()
    return base / "logs"


def total_log_bytes(log_dir: Path) -> int:
    """Sum of every regular file's size directly under ``log_dir`` (live logs
    and rotated ``*.gz`` backups alike). Non-recursive — the logs directory is
    flat by convention; a future subdirectory would need this extended."""
    total = 0
    try:
        for entry in log_dir.iterdir():
            if entry.is_file():
                try:
                    total += entry.stat().st_size
                except OSError:
                    continue
    except FileNotFoundError:
        return 0
    return total


@dataclass(frozen=True)
class DiskGuardResult:
    free_bytes: int
    log_dir_bytes: int
    warn_threshold: int
    critical_threshold: int
    dir_cap: int
    critical: bool
    warn: bool

    @property
    def ok(self) -> bool:
        return not self.critical

    @property
    def message(self) -> str:
        free_mb = self.free_bytes / (1024 * 1024)
        log_mb = self.log_dir_bytes / (1024 * 1024)
        if self.critical:
            return (
                f"CRITICAL: {free_mb:.0f} MB free disk (< "
                f"{self.critical_threshold / (1024 * 1024):.0f} MB threshold) — "
                f"logs/ is {log_mb:.0f} MB"
            )
        if self.warn:
            reasons = []
            if self.free_bytes < self.warn_threshold:
                reasons.append(
                    f"{free_mb:.0f} MB free disk < "
                    f"{self.warn_threshold / (1024 * 1024):.0f} MB warn threshold"
                )
            if self.log_dir_bytes > self.dir_cap:
                reasons.append(
                    f"logs/ is {log_mb:.0f} MB > "
                    f"{self.dir_cap / (1024 * 1024):.0f} MB cap"
                )
            return "WARN: " + "; ".join(reasons)
        return f"ok: {free_mb:.0f} MB free disk, logs/ is {log_mb:.0f} MB"


def check_disk_guard(
    root: Optional[str] = None,
    *,
    disk_usage_fn: Callable[[str], "os.statvfs_result"] = None,  # type: ignore[assignment]
    log_dir: Optional[Path] = None,
) -> DiskGuardResult:
    """Evaluate the disk guard. Pure function of injectable inputs so tests
    never have to actually fill a disk: pass ``disk_usage_fn`` (anything
    matching ``shutil.disk_usage``'s signature/return, i.e. an object with a
    ``.free`` attribute) and/or ``log_dir`` to fake both sides deterministically.
    """
    base = Path(root) if root else _root_dir()
    ld = log_dir if log_dir is not None else resolve_log_dir(root)
    usage_fn = disk_usage_fn or shutil.disk_usage
    usage = usage_fn(str(base))
    free = usage.free
    log_bytes = total_log_bytes(ld)
    warn_t = disk_free_warn_bytes()
    crit_t = disk_free_critical_bytes()
    cap = log_dir_max_bytes()
    critical = free < crit_t
    warn = (not critical) and (free < warn_t or log_bytes > cap)
    return DiskGuardResult(
        free_bytes=free,
        log_dir_bytes=log_bytes,
        warn_threshold=warn_t,
        critical_threshold=crit_t,
        dir_cap=cap,
        critical=critical,
        warn=warn,
    )


# ── in-process rotation helper (currently unwired — see module docstring) ──

def _gzip_and_remove_rotator(source: str, dest: str) -> None:
    """``RotatingFileHandler.rotator`` callable: gzip ``source`` (the handler's
    freshly-renamed ``.N`` file) into ``dest`` and drop the uncompressed copy,
    matching the ``*.N.gz`` naming ``scripts/log_rotate.py`` uses."""
    with open(source, "rb") as f_in, gzip.open(dest, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    os.remove(source)


def _gzip_namer(name: str) -> str:
    return name if name.endswith(".gz") else name + ".gz"


def configure_logging(
    name: Optional[str] = None,
    *,
    filename: str,
    level: int = logging.INFO,
    fmt: str = "%(asctime)s %(levelname)s:%(name)s:%(message)s",
    max_bytes_override: Optional[int] = None,
    backup_count_override: Optional[int] = None,
    also_console: bool = False,
) -> logging.Logger:
    """Attach a gzip'ing ``RotatingFileHandler`` to the given logger (root
    logger if ``name`` is None). Only correct for a process whose fd 1/2 are
    NOT already being appended to the same path by an external shell
    redirection — see the module docstring. Every current hermes-trader
    entrypoint IS shell-redirected, so this is not called anywhere yet; it
    exists so a future entrypoint (or one of those two, if rewritten to stop
    relying on nohup) has a ready, policy-consistent rotation path instead of
    reinventing one.
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)
    handler = logging.handlers.RotatingFileHandler(
        filename,
        maxBytes=max_bytes_override if max_bytes_override is not None else max_bytes(),
        backupCount=backup_count_override
        if backup_count_override is not None
        else backup_count(),
        encoding="utf-8",
    )
    handler.rotator = _gzip_and_remove_rotator
    handler.namer = _gzip_namer
    handler.setFormatter(logging.Formatter(fmt))
    logger.addHandler(handler)
    if also_console:
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter(fmt))
        logger.addHandler(console)
    return logger
