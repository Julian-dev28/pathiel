"""Where the pool reads and writes. Resolved at call time, never at import,
so a test's monkeypatch.setenv is all the isolation it needs.

  repo side (tracked, public)
    <repo>/pool/members/<address>.json     signed joins
    <repo>/pool/leaves/<address>.json      signed leaves
    <repo>/pool/house.json                 the maintainer's row
    <repo>/docs/scoreboard/                the published page and JSON

  state side (local, never committed), under services.review.env.state_dir()
    <state>/pool/outbox/{members,leaves}/<address>.json   files signed here, not yet in the repo
    <state>/pool/ledger/<address>.jsonl                   graded trades, frozen once settled
    <state>/pool/work/<stamp>/                            one build's raw venue replies

PATHIEL_POOL_REPO_ROOT moves the repo side (tests, a second checkout).
PATHIEL_STATE_DIR moves the state side, as everywhere else in pathiel.
"""
from __future__ import annotations

import os
from typing import Optional

import contracts.pool as pool
from services.review import env as review_env

ENV_REPO_ROOT = "PATHIEL_POOL_REPO_ROOT"


def repo_root() -> str:
    return os.environ.get(ENV_REPO_ROOT) or review_env.REPO_ROOT


def repo_pool_root(root: Optional[str] = None) -> str:
    return pool.repo_pool_root(root or repo_root())


def scoreboard_dir(root: Optional[str] = None) -> str:
    return os.path.join(root or repo_root(), *pool.SCOREBOARD_DIR.split("/"))


def scoreboard_json(root: Optional[str] = None) -> str:
    return os.path.join(scoreboard_dir(root), pool.SCOREBOARD_JSON)


def state_root() -> str:
    return os.path.join(review_env.state_dir(), pool.STATE_SUBDIR)


def outbox_root() -> str:
    """A pool root of its own (members/ and leaves/ inside), so
    contracts.pool.membership reads it exactly like the repo's pool/."""
    return os.path.join(state_root(), pool.OUTBOX_SUBDIR)


def ledger_dir() -> str:
    return os.path.join(state_root(), pool.LEDGER_SUBDIR)


def ledger_path(address: str) -> str:
    return os.path.join(ledger_dir(), pool.normalize_address(address) + ".jsonl")


def work_dir(stamp: str) -> str:
    return os.path.join(state_root(), "work", stamp)
