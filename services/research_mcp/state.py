"""Where the research MCP writes its own (tiny, non-secret) local state: the
usage counter (usage.py).

Deliberately its own directory, never `~/.pathiel` (CLAUDE.md's hard rule
for this server: "never open .env.local or ~/.pathiel") and never the
trading server's `PATHIEL_STATE_DIR` -- this process has no business reading
or writing anything under either, keys included, and keeping the directory
separate is what makes that true by construction rather than by care at
every call site.

`PATHIEL_RESEARCH_STATE_DIR` wins when set (worktrees, tests, a container).
Otherwise `~/.pathiel-research/state` -- a sibling of `~/.pathiel`, not a
subdirectory of it, so a trading install and a research install on the same
machine never collide and neither can read the other's directory by a
relative-path mistake.
"""
from __future__ import annotations

import os

ENV_STATE_DIR = "PATHIEL_RESEARCH_STATE_DIR"
_DEFAULT_HOME = os.path.join(os.path.expanduser("~"), ".pathiel-research")


def state_dir() -> str:
    """The directory this process may read/write its own state under.
    Created on first use by whoever writes into it (usage.py); this
    function itself never touches the filesystem."""
    configured = os.environ.get(ENV_STATE_DIR)
    if configured:
        return configured
    return os.path.join(_DEFAULT_HOME, "state")
