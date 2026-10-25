"""Where state lives, where the env file is, and how a standalone script
finds them. One definition.

Three scripts got this wrong in three different ways in a single session:

  - The supervisor, the alert evaluator and restart.sh hardcoded <root>/.state,
    which equals the live PATHIEL_STATE_DIR and nothing else. The metrics reader
    used state_file(). Under the test env they pointed at different trees and
    each side looked correct alone.
  - backup_state.py resolved PATHIEL_STATE_DIR without loading .env.local, so a
    hand-run wrote its receipt to the repo root while preflight_live.py — which
    does load it — reported "backup never run" thirty seconds after a
    successful backup.

The rule is the same everywhere and it is worth exactly one implementation:
load the env file, then PATHIEL_STATE_DIR, else the project root. It matches
pathiel.state.state_file(), which is what the running system uses.

The env file (2026-10-25, the Claude Code plugin): the first of
  1. $PATHIEL_ENV_FILE, when set,
  2. <root>/.env.local (a checkout the operator works in),
  3. ~/.pathiel/.env.local (a plugin install: the plugin root is a cache
     directory Claude Code replaces on update, so keys cannot live there).
`scripts/pathiel-mcp` and `scripts/pathiel-py` set PATHIEL_ENV_FILE explicitly.

Deliberately dependency-free: these scripts run as subprocesses and must not
need the agents package imported to find a file.
"""
from __future__ import annotations

import os
from typing import List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME_ENV_FILE = os.path.join("~", ".pathiel", ".env.local")


def env_file_candidates(root: str = ROOT) -> List[str]:
    """The env files a pathiel process looks for, in precedence order."""
    out: List[str] = []
    explicit = os.environ.get("PATHIEL_ENV_FILE")
    if explicit:
        out.append(os.path.expanduser(explicit))
    out.append(os.path.join(root, ".env.local"))
    out.append(os.path.expanduser(HOME_ENV_FILE))
    return out


def load_env_local(root: str = ROOT) -> Optional[str]:
    """Populate os.environ from the first env file that exists, without
    overriding a real env var. Returns the path loaded, or None.

    Must run BEFORE anything resolves a state path, or a script reads an empty
    directory and reports it as an empty system.
    """
    for path in env_file_candidates(root):
        if not os.path.isfile(path):
            continue
        try:
            with open(path) as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    os.environ.setdefault(key.strip(), value.strip())
        except OSError:
            return None
        return path
    return None


def state_dir(root: str = ROOT) -> str:
    return os.environ.get("PATHIEL_STATE_DIR") or root


def state_file(name: str, root: str = ROOT) -> str:
    return os.path.join(state_dir(root), name)
