"""A desk's loop progress log (.claude/agents/desk-tick.md step 6: one line
per tick, `DESK-TICK <desk> <utc> coin=... mid=... pos=... action=...
<reason>`). Generalized per-desk: `PATHIEL_<DESK>_LOG` (default the shared
`/tmp/oil-loop/progress.log`) for any desk id, so `PATHIEL_OIL_LOG`
(default `/tmp/oil-loop/progress.log`) keeps working unchanged for the
desk the loop started on.

Operator, 2026-10-31: silver's loop ticks into the SAME file as oil's
(`/tmp/oil-loop/progress.log`, every desk's default), lines prefixed
`DESK-TICK <desk_id> ...`. 2026-11-20: scripts/trigger_watch.py (the price
trigger watcher) ALSO writes into this same shared file now, prefixed
`WATCH <desk_id> ...` (`_append_watch_line`), on the handful of fires that
name a desk (a new entry waking it, a weekend-flat close, a headline
alert) -- so a desk's Loop Log card shows not only its own ticks but when
the watcher woke it between them.

`read_desk_log` filters to the lines that belong to `desk_id` BEFORE
tailing to `n`, so one desk's own last N lines are never crowded out by
another desk ticking (or being watched) in between, or by something else
entirely writing into the same shared file. Only a recognized, desk-
addressed line (`DESK-TICK <desk>` or `WATCH <desk>`) is kept, and only
when `<desk>` matches (case-insensitively).

Until 2026-11-20 a line that matched NEITHER known prefix passed through
unfiltered for every desk, on the theory that nothing else ever wrote into
this file. That broke the day the oil-only paper-breakout sim
(scripts/paper_breakout.py) started logging its own `PAPER-A6 ...` lines
into the SAME file oil's real loop uses: every desk's card (sp500's,
hype's, not just oil's) picked up PAPER-A6 lines as if they were its own,
and "Last tick" on a desk that has never ticked could show one. Default
filtering now drops any line with no desk-addressed prefix at all, same as
it already dropped another desk's; `GET /api/desks/{id}/log?all=1`
(`read_desk_log(..., include_all=True)`) serves the complete, unprefixed-
included tail for whoever wants to see literally everything in the file.

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

# Group 1: which desk-addressed kind of line this is (unused by the filter
# itself, kept for callers/tests that want to tell a tick apart from a
# watcher wake); group 2: the desk id it names.
_TICK_PREFIX_RE = re.compile(r"^(DESK-TICK|WATCH)\s+(\S+)\b")


def log_path(desk_id: str = "oil") -> str:
    """`PATHIEL_<DESK>_LOG` if set, else the shared
    `/tmp/oil-loop/progress.log` every desk's tick appends to."""
    env_name = f"PATHIEL_{desk_id.upper()}_LOG"
    # Every desk's tick writes the SAME file (.claude/agents/desk-tick.md
    # step 6), so that file is every desk's default; read_desk_log filters it
    # to this desk's lines. (2026-10-31: a per-desk /tmp/<desk>-loop default
    # left the silver card on "no ticks yet" while its ticks were logged.)
    return os.environ.get(env_name, DEFAULT_LOG_PATH)


# ----------------------------------------------------------------- pure part
def tail_lines(text: str, n: int) -> List[str]:
    """The last n non-empty, non-whitespace lines of `text`, oldest first."""
    if n <= 0:
        return []
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return lines[-n:]


def filter_desk_lines(lines: List[str], desk_id: str) -> List[str]:
    """Keep only the lines that are THIS desk's own: a `DESK-TICK <desk>`
    tick line or a `WATCH <desk>` watcher-wake line (see the module
    docstring) whose `<desk>` matches `desk_id`, case-insensitively.
    Everything else -- another desk's DESK-TICK/WATCH line, and any line
    with neither prefix at all (a sim's own log lines, an older format, a
    one-off debug print) -- is dropped. `read_desk_log(..., include_all=True)`
    is the way to see the dropped lines too."""
    desk_id_lower = desk_id.lower()
    out = []
    for ln in lines:
        m = _TICK_PREFIX_RE.match(ln)
        if m and m.group(2).lower() == desk_id_lower:
            out.append(ln)
    return out


def read_desk_log(desk_id: str = "oil", n: int = 30, path: Optional[str] = None,
                  include_all: bool = False) -> List[str]:
    """The last n lines belonging to `desk_id`, oldest first, from the real
    log file. Filters to this desk's own `DESK-TICK`/`WATCH` lines BEFORE
    tailing (filter_desk_lines), so a shared log file's other desk -- or
    something else entirely writing into it -- never crowds this one's n
    lines out. `include_all=True` skips that filter and returns the last n
    lines of the file exactly as written (the Desk page's "show all lines"
    toggle). [] when the file does not exist or cannot be read - never
    raises, so a missing loop never breaks the page it is reported on."""
    p = path if path is not None else log_path(desk_id)
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return []
    all_lines = [ln for ln in text.splitlines() if ln.strip()]
    if n <= 0:
        return []
    return all_lines[-n:] if include_all else filter_desk_lines(all_lines, desk_id)[-n:]


__all__ = ["DEFAULT_LOG_PATH", "log_path", "tail_lines", "filter_desk_lines", "read_desk_log"]
