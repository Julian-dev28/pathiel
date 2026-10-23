"""Wiring-integrity checks that run on every commit, not just at review time
(operator instruction 2026-07-20: check every integration for collisions and
leaks, before/during/after).

Static scan across pathiel/agents/*.py: no two live modules may reuse a
`_BOOK_NAME` or a `state_file(...)` path. A collision here is silent —
nothing raises, two modules just corrupt each other's persisted state or
fight over the same ledger rows.

2026-10-23 (wave 2): everything else this file used to cover — the seven
strategy books' cross-coordination (retired wave 1), and the backup-SL
clamp / `executor.stop_honoring_leverage` (retired wave 2, along with the
rest of the autonomous executor: `place_bracket` takes an exact stop Claude
already chose and never narrows it, so there is nothing left to clamp) — is
gone. The book-name/state-file collision scan is general hygiene, unrelated
to either retirement, and the bug class it catches (two modules silently
sharing a ledger or a state path) is still real for whatever lives in
pathiel/agents/ going forward.
"""
from __future__ import annotations

import re
from pathlib import Path

_AGENTS_DIR = Path(__file__).resolve().parents[1] / "pathiel" / "agents"


def _scan(pattern: str) -> dict[str, list[str]]:
    hits: dict[str, list[str]] = {}
    for path in sorted(_AGENTS_DIR.glob("*.py")):
        src = path.read_text()
        for m in re.finditer(pattern, src):
            hits.setdefault(m.group(1), []).append(path.name)
    return hits


def _all_book_name_sites() -> dict[str, list[str]]:
    """Union of both ways a module names its ledger book: a `_BOOK_NAME`
    constant, or an inline shadow_ledger.record(...) / record_many(...)
    string literal."""
    sites = _scan(r'_BOOK_NAME\s*=\s*"([^"]+)"')
    for k, v in _scan(r'shadow_ledger\.record(?:_many)?\(\s*"([^"]+)"').items():
        sites.setdefault(k, []).extend(v)
    return sites


def test_no_two_live_modules_share_a_book_name():
    dupes = {k: v for k, v in _all_book_name_sites().items() if len(set(v)) > 1}
    assert dupes == {}, f"duplicate ledger book name across modules: {dupes}"


def test_no_two_modules_share_a_state_file_path():
    dupes = {k: v for k, v in _scan(r'state_file\(\s*"([^"]+)"').items() if len(v) > 1}
    assert dupes == {}, f"duplicate state_file(...) path across modules: {dupes}"
