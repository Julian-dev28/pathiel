"""Shared helpers for read-only scripts that inspect live agent memory."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict


def load_memory(path: str | Path, *, retries: int = 5, sleep_s: float = 0.05) -> Dict[str, Any]:
    """Load `.agent-memory.json`, retrying transient partial-write reads.

    The trading loop can flush memory while read-only analysis scripts start.
    Production writes are now atomic, but the retry keeps older files and
    operator-side manual rewrites from crashing every backtest on a single
    malformed read.
    """
    p = Path(path)
    last: Exception | None = None
    for attempt in range(max(1, retries)):
        try:
            with p.open() as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError) as e:
            last = e
            if attempt < retries - 1:
                time.sleep(sleep_s)
    raise RuntimeError(f"failed to read memory file {p}: {last}")
