"""Shared state-directory resolver.

Moved out of `pathiel/agents/rebalancer_owned.py` 2026-10-23 (wave 2): that
module's `OwnedPositions`/`ClaimsRegistry` cross-book coordination machinery
was already cut down to this one function in wave 1 (headless desk) — with
the autonomous scan/research/execute engine retired in wave 2, nothing in
`pathiel.agents` is left to import it from, so the module moved here, to the
top-level package, rather than staying in `pathiel.agents` as a one-function
shell.

Every process-local state file (the funding/OI logger, backup receipts,
supervisor/alert-evaluator timestamps, ...) routes through `state_file()` so
`PATHIEL_STATE_DIR` redirects them all together — tests/conftest.py points
it at a fresh temp dir so the suite never touches live state.
"""
from __future__ import annotations

import os

# PATHIEL_STATE_DIR (tests) else the project root.
_STATE_DIR = os.environ.get("PATHIEL_STATE_DIR") or os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))


def state_file(name: str) -> str:
    """Absolute path for a state file, under PATHIEL_STATE_DIR (tests) or the project root."""
    return os.path.join(_STATE_DIR, name)
