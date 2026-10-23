"""A desk's loop progress log (.claude/skills/oil-tick/SKILL.md step 5, for
the oil desk specifically: one line per tick, `OIL-TICK <utc> mid=... pos=...
action=... <reason>`). Generalized per-desk: `PATHIEL_<DESK>_LOG` (default
`/tmp/<desk>-loop/progress.log`) for any desk id, so `PATHIEL_OIL_LOG`
(default `/tmp/oil-loop/progress.log`) keeps working unchanged for the one
desk with a loop today.

Backs `GET /api/desks/{desk_id}/log` (the Desk dashboard's "Loop log"
section). Read-only, deterministic: the last N non-empty lines, oldest
first. A missing file (that desk's loop has never ticked, runs on a
different machine, or does not exist at all yet) is not an error here - it
returns an empty list; the dashboard route renders "no ticks yet" for that
case, per the task spec.
"""
from __future__ import annotations

import os
from typing import List, Optional

DEFAULT_LOG_PATH = "/tmp/oil-loop/progress.log"   # desk_id == "oil" default


def log_path(desk_id: str = "oil") -> str:
    """`PATHIEL_<DESK>_LOG` if set, else `/tmp/<desk>-loop/progress.log` -
    except for `desk_id == "oil"`, whose default stays the original
    `/tmp/oil-loop/progress.log` (the only loop live before this was
    generalized to other desks)."""
    env_name = f"PATHIEL_{desk_id.upper()}_LOG"
    default = DEFAULT_LOG_PATH if desk_id == "oil" else f"/tmp/{desk_id}-loop/progress.log"
    return os.environ.get(env_name, default)


# ----------------------------------------------------------------- pure part
def tail_lines(text: str, n: int) -> List[str]:
    """The last n non-empty, non-whitespace lines of `text`, oldest first."""
    if n <= 0:
        return []
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return lines[-n:]


def read_desk_log(desk_id: str = "oil", n: int = 30, path: Optional[str] = None) -> List[str]:
    """tail_lines() over the real log file for `desk_id`. [] when the file
    does not exist or cannot be read - never raises, so a missing loop
    never breaks the page it is reported on."""
    p = path if path is not None else log_path(desk_id)
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return []
    return tail_lines(text, n)


__all__ = ["DEFAULT_LOG_PATH", "log_path", "tail_lines", "read_desk_log"]
