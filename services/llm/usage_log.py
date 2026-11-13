"""Append-only usage ledger: one JSON line per services.llm.complete() call.

Lives under ``$PATHIEL_STATE_DIR/llm/usage.jsonl``. This is the only place a
per-call cost/token spend becomes inspectable after the fact - complete()
itself returns the single result to its caller and moves on, so without this
file there is no way to answer "how much did the oil desk spend on theses
today" without re-deriving it from application logs.

A write failure here (disk full, read-only state dir) is logged and
swallowed, never raised: the usage ledger is an observability side channel,
not a correctness dependency, and a desk refresh must not fail because its
bookkeeping file could not be appended to.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any, Mapping

from services.llm import config

logger = logging.getLogger(__name__)
_write_lock = threading.Lock()


def append_usage(entry: Mapping[str, Any]) -> None:
    record: dict[str, Any] = {"ts": datetime.now(timezone.utc).isoformat()}
    record.update(entry)
    path = config.usage_log_path()
    line = json.dumps(record, default=str) + "\n"
    try:
        with _write_lock:
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line)
    except OSError as exc:
        logger.warning(f"[llm] could not append usage log at {path}: {exc}")
