"""A desk's loop progress log (.claude/skills/oil-tick/SKILL.md step 5, for
the oil desk specifically: one line per tick, `OIL-TICK <utc> mid=... pos=...
action=... <reason>`). Generalized per-desk: `PATHIEL_<DESK>_LOG` (default
`/tmp/<desk>-loop/progress.log`) for any desk id, so `PATHIEL_OIL_LOG`
(default `/tmp/oil-loop/progress.log`) keeps working unchanged for the one
desk with a loop today.

Operator, 2026-10-31: silver's loop ticks into the SAME file as oil's
(`/tmp/oil-loop/progress.log` -- metals' own default,
`/tmp/metals-loop/progress.log`, is only used if nothing points it at the
shared file), lines prefixed `DESK-TICK <desk_id> ...`
(.claude/agents/desk-tick.md). Two desks sharing one file must not leak
into each other's log card, so `read_desk_log` filters to the lines that
belong to `desk_id` BEFORE tailing to `n` -- otherwise metals ticking every
15 minutes would crowd oil's own last 30 lines out of its own card. A line
with no `DESK-TICK <desk>` prefix at all (the old `OIL-TICK ...` format, or
any desk whose file is never shared) is kept unfiltered: there is nothing
to tell it apart from, so it is assumed to already belong wholly to this
desk's own file.

Backs `GET /api/desks/{desk_id}/log` (the Desk dashboard's "Loop log"
section). Read-only, deterministic: the last N non-empty lines, oldest
first. A missing file (that desk's loop has never ticked, runs on a
different machine, or does not exist at all yet) is not an error here - it
returns an empty list; the dashboard route renders "no ticks yet" for that
case, per the task spec.
"""
from __future__ import annotations

import os
import re
from typing import List, Optional

DEFAULT_LOG_PATH = "/tmp/oil-loop/progress.log"   # desk_id == "oil" default

_TICK_PREFIX_RE = re.compile(r"^DESK-TICK\s+(\S+)\b")


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


def filter_desk_lines(lines: List[str], desk_id: str) -> List[str]:
    """Drop any `DESK-TICK <other-desk> ...` line when the file is shared
    by more than one desk's loop. A line that doesn't match the
    `DESK-TICK <desk>` shape at all (an older format, or a desk whose log
    file is never shared) passes through unfiltered."""
    desk_id_lower = desk_id.lower()
    out = []
    for ln in lines:
        m = _TICK_PREFIX_RE.match(ln)
        if m and m.group(1).lower() != desk_id_lower:
            continue
        out.append(ln)
    return out


def read_desk_log(desk_id: str = "oil", n: int = 30, path: Optional[str] = None) -> List[str]:
    """The last n lines belonging to `desk_id`, oldest first, from the real
    log file. Filters to this desk's own `DESK-TICK` lines BEFORE tailing
    (filter_desk_lines), so a shared log file's other desk never crowds
    this one's n lines out. [] when the file does not exist or cannot be
    read - never raises, so a missing loop never breaks the page it is
    reported on."""
    p = path if path is not None else log_path(desk_id)
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return []
    all_lines = [ln for ln in text.splitlines() if ln.strip()]
    return filter_desk_lines(all_lines, desk_id)[-n:] if n > 0 else []


__all__ = ["DEFAULT_LOG_PATH", "log_path", "tail_lines", "filter_desk_lines", "read_desk_log"]
