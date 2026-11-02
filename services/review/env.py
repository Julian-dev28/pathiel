"""Where the review writes: `$PATHIEL_STATE_DIR/review/`.

Resolved the way services/intel/env.py does it (PATHIEL_STATE_DIR, then the
repo's .env.local, then the repo root), with one difference on purpose:
.env.local is read only when PATHIEL_STATE_DIR is unset, so a test or a
throwaway run that sets the variable never opens that file. Resolved at
call time, never frozen at import, so a test's monkeypatch.setenv is all
the isolation it needs.
"""
from __future__ import annotations

import os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _from_dotenv(key: str) -> str:
    path = os.path.join(REPO_ROOT, ".env.local")
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    if k.strip() == key:
                        return v.strip()
    except OSError:
        pass
    return ""


def state_dir() -> str:
    return os.environ.get("PATHIEL_STATE_DIR") or _from_dotenv("PATHIEL_STATE_DIR") or REPO_ROOT


def review_dir() -> str:
    return os.path.join(state_dir(), "review")


def ledger_path() -> str:
    return os.path.join(review_dir(), "ledger.jsonl")


def last_run_path() -> str:
    return os.path.join(review_dir(), "last_run.json")


def theses_dir(desk: str) -> str:
    """The live journal layout (services/strategies/journal.py). READ ONLY."""
    return os.path.join(state_dir(), "strategies", desk, "theses")


def report_dir() -> str:
    return os.path.join(REPO_ROOT, "docs", "strategy", "learning")
