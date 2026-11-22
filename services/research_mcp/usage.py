"""Local, privacy-preserving usage counter: how many times each tool was
called, ever, on this machine, and when it was last called.

No arguments, no results, no caller identity, no IP -- counting calls is the
whole job. Telemetry (sending this, or anything else, off this machine) is
off by default and not implemented at all in v1: see
docs/design/RESEARCH_MCP_PAID_TIER.md for the opt-in, off-box plan for
later. The file lives under state.state_dir() (never ~/.pathiel).
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from pathiel.agents.atomic_io import write_json_atomic
from services.research_mcp import state

USAGE_FILE_NAME = "usage.json"
_LOCK = threading.Lock()


def usage_path() -> str:
    return os.path.join(state.state_dir(), USAGE_FILE_NAME)


def _read(path: str) -> Dict[str, Dict[str, Any]]:
    try:
        with open(path) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def record_call(tool_name: str, *, path: Optional[str] = None,
                 now: Optional[datetime] = None) -> Dict[str, Any]:
    """Increment `tool_name`'s call count by 1 and bump its last_called_at.
    Thread-safe (one process-wide lock -- call volume here never justifies
    anything fancier) and never raises: a counter write that fails is a
    feature nobody lost, where a tool call failing because ITS OWN counter
    write failed would be a much worse trade. Returns the row just written,
    or a null row on a write failure."""
    path = path or usage_path()
    now = now or datetime.now(timezone.utc)
    with _LOCK:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            data = _read(path)
            row = dict(data.get(tool_name) or {"calls": 0, "first_called_at": None,
                                                "last_called_at": None})
            row["calls"] = int(row.get("calls") or 0) + 1
            if not row.get("first_called_at"):
                row["first_called_at"] = now.isoformat()
            row["last_called_at"] = now.isoformat()
            data[tool_name] = row
            write_json_atomic(path, data)
            return dict(row)
        except OSError:
            return {"calls": None, "first_called_at": None, "last_called_at": None}


def read_usage(path: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """The counter file as it stands. {} if nothing has been recorded yet."""
    return _read(path or usage_path())
