"""Append-only JSONL activity log — the trading system's visible heartbeat.

The trading loop and the FastAPI server append events here; `status.py` and the
hourly cron report read them back. One line per event, each tagged with a `ts`
(epoch ms). Path is overridable via the `SESSION_LOG_PATH` env var.
"""
from __future__ import annotations

import json
import os
import time
from collections import deque
from typing import Any, Dict, List

SESSION_LOG_FILE = os.environ.get(
    "SESSION_LOG_PATH",
    os.path.expanduser("~/.pathia-session-log.jsonl"),
)


def append(event: Dict[str, Any]) -> None:
    """Append one event as a JSONL line. A `ts` field is added automatically.

    Best-effort: a logging failure must never interrupt trading, so disk errors
    are swallowed.
    """
    record = {"ts": int(time.time() * 1000), **event}
    try:
        directory = os.path.dirname(SESSION_LOG_FILE)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(SESSION_LOG_FILE, "a") as f:
            f.write(json.dumps(record, default=str, separators=(",", ":")) + "\n")
    except Exception:
        pass


def tail(n: int = 10) -> List[Dict[str, Any]]:
    """Return the last `n` parseable events, oldest first.

    Reads a bounded block from the END of the file (audit 2026-07-10: the old
    line-iterator streamed the whole 101MB log per call — every SSE connect
    paid it)."""
    if n <= 0:
        return []
    try:
        with open(SESSION_LOG_FILE, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            block = min(size, max(65_536, n * 1_024))
            f.seek(size - block)
            data = f.read()
        if block < size:
            data = data.split(b"\n", 1)[-1]      # drop the partial first line
        lines = deque((ln.strip().decode("utf-8", "replace")
                       for ln in data.split(b"\n") if ln.strip()), maxlen=n)
    except FileNotFoundError:
        return []
    except OSError:
        return []
    out: List[Dict[str, Any]] = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            pass
    return out
