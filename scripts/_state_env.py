"""Where state lives, and how a standalone script finds it. One definition.

Three scripts got this wrong in three different ways in a single session:

  - The supervisor, the alert evaluator and restart.sh hardcoded <root>/.state,
    which equals the live PATHIA_STATE_DIR and nothing else. The metrics reader
    used state_file(). Under the test env they pointed at different trees and
    each side looked correct alone.
  - backup_state.py resolved PATHIA_STATE_DIR without loading .env.local, so a
    hand-run wrote its receipt to the repo root while preflight_live.py — which
    does load it — reported "backup never run" thirty seconds after a
    successful backup.

The rule is the same everywhere and it is worth exactly one implementation:
load .env.local, then PATHIA_STATE_DIR, else the project root. It matches
pathia.agents.rebalancer_owned.state_file(), which is what the running
system uses, and tests/test_supervisor.py pins the two together.

Deliberately dependency-free: these scripts run as scheduler subprocesses and
must not need the agents package imported to find a file.
"""
from __future__ import annotations

import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_env_local(root: str = ROOT) -> None:
    """Populate os.environ from .env.local without overriding a real env var.

    Must run BEFORE anything resolves a state path, or a script reads an empty
    directory and reports it as an empty system.
    """
    path = os.path.join(root, ".env.local")
    if not os.path.exists(path):
        return
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())
    except OSError:
        pass


def state_dir(root: str = ROOT) -> str:
    return os.environ.get("PATHIA_STATE_DIR") or root


def state_file(name: str, root: str = ROOT) -> str:
    return os.path.join(state_dir(root), name)
