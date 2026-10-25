"""Self-contained state-dir + .env.local resolution for services/intel.

Deliberately a leaf module with zero imports from other `services/*` or
`pathiel/*` packages (see docs/overhaul/SWARM_RULES.md section 6 ownership —
this service owns `services/intel/**` only and must not reach into another
service's internals). `services/accounts/env.py` solves the identical
problem the identical way; the duplication is intentional, not an oversight,
so the two services can be edited by two different agents in parallel
without a shared-file collision.

Call `state_dir()` to get the directory every cache file in this service is
written under. `PATHIEL_STATE_DIR` always wins when set (every worktree in
the swarm exports a throwaway one per SWARM_RULES section 1); otherwise we
fall back to `.env.local` at the repo root, and finally to the repo root
itself so an ad-hoc `python -c` still works.
"""
from __future__ import annotations

import os

_LOADED = False


def _load_dotenv() -> None:
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    path = os.path.join(root, ".env.local")
    if not os.path.exists(path):
        return
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip())
    except OSError:
        pass


def state_dir() -> str:
    """The directory this process writes intel cache files under."""
    _load_dotenv()
    configured = os.environ.get("PATHIEL_STATE_DIR")
    if configured:
        return configured
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def intel_dir(desk_id: str) -> str:
    """`$PATHIEL_STATE_DIR/intel/<desk>/` — created on first write by cache.py."""
    return os.path.join(state_dir(), "intel", desk_id)
