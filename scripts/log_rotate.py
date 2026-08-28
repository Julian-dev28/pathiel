#!/usr/bin/env python3
"""External copytruncate log rotator for hermes-trader.

WHY EXTERNAL, WHY COPYTRUNCATE
-------------------------------
Every hermes-trader process is started by ``scripts/restart.sh`` with shell
append redirection (``nohup ... >> logs/foo.log 2>&1 &``). The shell opens
that file once, in ``O_APPEND`` mode, and hands the fd to the child for its
entire lifetime — including for a bare ``print()`` (the trend_engine
sample-daemon has no ``logging`` calls at all) and for an uncaught traceback
dumped straight to stderr. A ``RotatingFileHandler`` running *inside* one of
these processes cannot touch that fd: it would rotate a second, independent
handle while the shell's fd keeps appending to whatever inode it was opened
against. See ``hermes_trader/log_setup.py`` for the full writeup.

The fix that works with an fd that never reopens is the same one
``logrotate --copytruncate`` uses for exactly this situation:

    1. snapshot the file's current size S
    2. read exactly S bytes (never "until EOF" — a concurrent writer only
       ever appends past S, so capping the read at S can't pick up a partial
       write or corrupt anything)
    3. os.truncate(path, 0) — same path, same inode, so any process already
       holding this file open keeps writing correctly: O_APPEND writes
       always target current EOF, and EOF is now 0
    4. (off the hot path) gzip the captured bytes out to `path.N.gz`,
       shifting older backups down and dropping anything past the retention
       count

THE RACE, NAMED AND BOUNDED
----------------------------
Between step 2 finishing and step 3 running, a concurrent writer's append is
neither in the captured bytes (already read) nor left in the file (truncate
zeroes it regardless of what landed there since). That write is lost. This is
the well-known copytruncate race and it is NOT eliminated here — POSIX has no
atomic "read-and-truncate". It IS bounded:

  * nothing except the read (step 2) and the truncate (step 3) happens in
    between — no gzip, no renames, no I/O to another file — so the window is
    just the time to read up to ``HERMES_LOG_MAX_BYTES`` (default 20 MB) off
    local disk, typically low single-digit milliseconds;
  * a file only gets copytruncated when it is already over the size
    threshold, and the daemon sweeps on a fixed interval (default 300s), so
    the window opens rarely, not on every write;
  * worst case: one writer's write() call — at most a few KB for a log
    line — lands in the gap once, on a file that is already megabytes past
    its rotation threshold. Operationally negligible against the alternative
    this replaces (unbounded growth to a full disk).

If a future entrypoint stops depending on shell redirection (own its own fd,
no ``nohup ... >>``), it should call ``hermes_trader.log_setup.configure_logging``
instead — a real in-process ``RotatingFileHandler`` has no race at all.

USAGE
-----
    scripts/log_rotate.py                    # one rotation pass (default: --once)
    scripts/log_rotate.py --daemon           # loop forever, sweep every --interval-sec
    scripts/log_rotate.py --guard            # disk-guard check only; exit 1 if critical
    scripts/log_rotate.py --file logs/x.log --force   # force-rotate one file now
    scripts/log_rotate.py --dir /tmp/somelogs --once  # point at a different directory (tests)
"""
from __future__ import annotations

import argparse
import gzip
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from hermes_trader import log_setup  # noqa: E402


def _log(msg: str, *, quiet: bool = False) -> None:
    if not quiet:
        print(f"[log_rotate] {time.strftime('%Y-%m-%d %H:%M:%S')} {msg}", flush=True)


def rotate_file(
    path: Path,
    *,
    max_bytes: int,
    backup_count: int,
    force: bool = False,
) -> Optional[Dict[str, object]]:
    """Copytruncate-rotate ``path`` if it is >= ``max_bytes`` (or always, if
    ``force``). Returns a result dict, or None if rotation was not needed.

    Safe to call on a path with no writer, one writer, or several concurrent
    O_APPEND writers (hermes-trader's scheduler runs multiple jobs that share
    a log file). Safe to call on a path nothing currently has open — the next
    process to `open(path, "a")` after this just gets a fresh empty file.
    """
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return None
    if not force and size < max_bytes:
        return None
    if size == 0:
        return None

    # Steps 2+3 of the module docstring: read exactly `size` bytes, then
    # truncate immediately. Nothing else happens between these two lines —
    # that gap IS the entire copytruncate race window.
    with open(path, "rb") as f:
        data = f.read(size)
    os.truncate(path, 0)

    backup_path = None
    if backup_count > 0:
        _shift_backups(path, backup_count)
        backup_path = path.with_name(path.name + ".1.gz")
        with gzip.open(backup_path, "wb") as gz:
            gz.write(data)

    return {
        "path": str(path),
        "rotated_bytes": size,
        "backup": str(backup_path) if backup_path else None,
    }


def _shift_backups(path: Path, backup_count: int) -> None:
    """Drop the oldest backup beyond retention, then shift .N -> .N+1 down to
    .1, freeing up `name.1.gz` for the file we're about to rotate. Walking
    from the highest index down avoids clobbering a lower one before it's
    moved."""
    oldest = path.with_name(path.name + f".{backup_count}.gz")
    if oldest.exists():
        oldest.unlink()
    for i in range(backup_count - 1, 0, -1):
        src = path.with_name(path.name + f".{i}.gz")
        if not src.exists():
            continue
        dst = path.with_name(path.name + f".{i + 1}.gz")
        os.replace(src, dst)


def enforce_dir_cap(log_dir: Path, max_total_bytes: int, *, quiet: bool = False) -> List[Path]:
    """Delete oldest rotated backups (``*.gz`` — never a live, non-backup log
    file) until the directory's total size is back under ``max_total_bytes``.
    If live files alone already exceed the cap (no backups left to prune),
    force-rotates the single largest live file to reclaim space rather than
    ever deleting log content that hasn't been captured anywhere."""
    removed: List[Path] = []
    try:
        entries = [p for p in log_dir.iterdir() if p.is_file()]
    except FileNotFoundError:
        return removed
    total = sum(p.stat().st_size for p in entries)
    if total <= max_total_bytes:
        return removed

    backups = sorted(
        (p for p in entries if p.name.endswith(".gz")),
        key=lambda p: p.stat().st_mtime,
    )
    for p in backups:
        if total <= max_total_bytes:
            break
        sz = p.stat().st_size
        p.unlink()
        total -= sz
        removed.append(p)
        _log(f"pruned {p.name} ({sz} bytes) — logs/ over {max_total_bytes} byte cap", quiet=quiet)

    if total > max_total_bytes:
        live = sorted(
            (p for p in entries if p.name.endswith(".log")),
            key=lambda p: p.stat().st_size,
            reverse=True,
        )
        if live:
            biggest = live[0]
            _log(
                f"logs/ still over cap after pruning backups — force-rotating "
                f"{biggest.name} ({biggest.stat().st_size} bytes)",
                quiet=quiet,
            )
            rotate_file(
                biggest,
                max_bytes=log_setup.max_bytes(),
                backup_count=log_setup.backup_count(),
                force=True,
            )
    return removed


def rotate_all(
    log_dir: Path,
    *,
    max_bytes: int,
    backup_count: int,
    dir_cap: int,
    quiet: bool = False,
) -> List[Dict[str, object]]:
    results: List[Dict[str, object]] = []
    if not log_dir.exists():
        return results
    for path in sorted(log_dir.glob("*.log")):
        res = rotate_file(path, max_bytes=max_bytes, backup_count=backup_count)
        if res:
            results.append(res)
            _log(
                f"rotated {path.name}: {res['rotated_bytes']} bytes -> {res['backup']}",
                quiet=quiet,
            )
    enforce_dir_cap(log_dir, dir_cap, quiet=quiet)
    return results


def run_guard(*, log_dir: Optional[Path] = None, quiet: bool = False) -> int:
    # Disk-free is always measured against the real repo root's filesystem —
    # `log_dir` (from --dir) only overrides which directory's *contents* count
    # toward the logs/ size cap, so tests can point it at a tmp dir without
    # faking the machine's actual free space.
    result = log_setup.check_disk_guard(log_dir=log_dir)
    if result.critical:
        _log(result.message, quiet=quiet)
        return 1
    if result.warn:
        _log(result.message, quiet=quiet)
        return 0
    _log(result.message, quiet=quiet)
    return 0


def _run_once(args: argparse.Namespace) -> int:
    log_dir = Path(args.dir) if args.dir else log_setup.resolve_log_dir()
    max_b = args.max_bytes or log_setup.max_bytes()
    backup_n = args.backup_count if args.backup_count is not None else log_setup.backup_count()
    dir_cap = args.dir_max_bytes or log_setup.log_dir_max_bytes()

    if args.file:
        target = Path(args.file)
        res = rotate_file(target, max_bytes=max_b, backup_count=backup_n, force=args.force)
        if res:
            _log(f"rotated {target.name}: {res['rotated_bytes']} bytes -> {res['backup']}", quiet=args.quiet)
        else:
            _log(f"{target.name}: below threshold, nothing to do (use --force to override)", quiet=args.quiet)
        enforce_dir_cap(log_dir, dir_cap, quiet=args.quiet)
        return 0

    results = rotate_all(log_dir, max_bytes=max_b, backup_count=backup_n, dir_cap=dir_cap, quiet=args.quiet)
    if not results:
        _log(f"swept {log_dir} — nothing over {max_b} bytes", quiet=args.quiet)
    return 0


def _run_daemon(args: argparse.Namespace) -> int:
    interval = args.interval_sec or log_setup.rotate_interval_sec()
    _log(f"daemon starting — sweeping {args.dir or log_setup.resolve_log_dir()} every {interval}s", quiet=args.quiet)
    while True:
        try:
            _run_once(args)
        except Exception as exc:  # noqa: BLE001 — never let one bad sweep kill the daemon
            _log(f"sweep error (continuing): {exc!r}", quiet=args.quiet)
        time.sleep(interval)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="run a single rotation pass (default)")
    mode.add_argument("--daemon", action="store_true", help="loop forever, sweeping every --interval-sec")
    mode.add_argument("--guard", action="store_true", help="disk-guard check only; exit 1 if critical")
    parser.add_argument("--dir", default=None, help="logs directory to operate on (default: <repo>/logs)")
    parser.add_argument("--file", default=None, help="rotate only this one file")
    parser.add_argument("--force", action="store_true", help="rotate --file even if under the size threshold")
    parser.add_argument("--max-bytes", type=int, default=None, dest="max_bytes")
    parser.add_argument("--backup-count", type=int, default=None, dest="backup_count")
    parser.add_argument("--dir-max-bytes", type=int, default=None, dest="dir_max_bytes")
    parser.add_argument("--interval-sec", type=int, default=None, dest="interval_sec")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    if args.guard:
        return run_guard(log_dir=Path(args.dir) if args.dir else None, quiet=args.quiet)
    if args.daemon:
        return _run_daemon(args)
    return _run_once(args)


if __name__ == "__main__":
    raise SystemExit(main())
