"""Crash-safe state writes, in one place.

The repo had 192 sites writing JSON state and 17 of them doing it safely. The
rest used the obvious `open(path, "w")` + `json.dump`, which truncates the file
before writing a byte: a process killed at the wrong moment leaves a truncated
or empty file where state used to be.

That is not theoretical here. The claims registry is loaded with a
corrupt-file-starts-empty guard, which is the right failure mode for a torn
read but means a crash mid-write silently DROPS every cross-book claim — and a
dropped claim is two books opening the same coin. The shadow ledger is the
evidence every promote/demote decision is made from; a truncated line there is a
trade that never happened.

`write_json_atomic` does the full sequence, not the usual half of it:

    write temp in the SAME directory  (a cross-filesystem rename is a copy,
                                       which is not atomic)
    flush + fsync the file            (without this the rename can land before
                                       the bytes, and a power loss leaves a
                                       valid name pointing at nothing)
    os.replace                        (atomic on POSIX and Windows)
    fsync the directory               (so the rename itself is durable)

The directory fsync is skipped where the platform refuses it rather than
failing the write — the data is already safe at that point, and refusing to
save state because a filesystem will not sync a directory handle would trade a
small durability gap for a large availability one.

`append_line` is the same care for JSONL: flush + fsync so a ledger row is on
disk before the caller believes it, since the caller's next act is usually to
place an order against it.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _fsync_dir(path: str) -> None:
    """Make a rename durable. Best-effort: not every platform allows it."""
    try:
        fd = os.open(os.path.dirname(os.path.abspath(path)) or ".", os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass                      # some filesystems refuse; the data is already safe
    finally:
        os.close(fd)


def write_json_atomic(path: str, data: Any, *, indent: Optional[int] = None,
                      sort_keys: bool = False) -> None:
    """Replace `path` with `data` as JSON, atomically.

    A reader either sees the whole previous file or the whole new one. There is
    no window in which it sees a truncated one.
    """
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    # Same directory as the target: os.replace is only atomic within a
    # filesystem, and /tmp is frequently a different one.
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-",
                               suffix=os.path.basename(path))
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, indent=indent, sort_keys=sort_keys)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        _fsync_dir(path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json(path: str, default: Any = None) -> Any:
    """Read JSON, returning `default` for a missing or unreadable file.

    Torn files are survivable by construction once every writer goes through
    write_json_atomic, but this stays forgiving for files written before that
    was true.
    """
    if not os.path.exists(path):
        return default
    try:
        with open(path) as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        logger.warning(f"[atomic-io] unreadable state at {path}: {exc}")
        return default


def append_line(path: str, line: str, *, fsync: bool = True) -> None:
    """Append one line durably.

    The caller's next act is usually to trade against what it just recorded, so
    'written' has to mean on disk, not in a buffer the OS may drop.
    """
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    with open(path, "a") as fh:
        fh.write(line if line.endswith("\n") else line + "\n")
        fh.flush()
        if fsync:
            os.fsync(fh.fileno())


def append_json_line(path: str, obj: Any, *, fsync: bool = True) -> None:
    """Append one JSON object as a JSONL row, durably."""
    append_line(path, json.dumps(obj, separators=(",", ":")), fsync=fsync)
