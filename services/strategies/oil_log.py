"""The oil-tick loop's plain-text progress log (.claude/skills/oil-tick/SKILL.md
step 5: one line per tick, `OIL-TICK <utc> mid=... pos=... action=... <reason>`).

Backs `GET /api/desks/oil/log` (the Oil Desk dashboard's "Loop log" section).
Read-only, deterministic: the last N non-empty lines, oldest first. A
missing file (the loop has never ticked, or runs on a different machine) is
not an error here - it returns an empty list; the dashboard route renders
"no ticks yet" for that case, per the task spec.
"""
from __future__ import annotations

import os
from typing import List, Optional

DEFAULT_LOG_PATH = "/tmp/oil-loop/progress.log"


def log_path() -> str:
    return os.environ.get("PATHIEL_OIL_LOG", DEFAULT_LOG_PATH)


# ----------------------------------------------------------------- pure part
def tail_lines(text: str, n: int) -> List[str]:
    """The last n non-empty, non-whitespace lines of `text`, oldest first."""
    if n <= 0:
        return []
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return lines[-n:]


def read_oil_log(n: int = 30, path: Optional[str] = None) -> List[str]:
    """tail_lines() over the real log file. [] when the file does not exist
    or cannot be read - never raises, so a missing loop never breaks the
    page it is reported on."""
    p = path if path is not None else log_path()
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return []
    return tail_lines(text, n)


__all__ = ["DEFAULT_LOG_PATH", "log_path", "tail_lines", "read_oil_log"]
