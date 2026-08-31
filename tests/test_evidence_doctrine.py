"""The doctrine in research/EVIDENCE_DOCTRINE.md, enforced.

"No shadows. No recorders. Proven backtests." A doctrine nothing checks is a
wish, and this repo has already had two of those: CLAUDE.md claimed a
pre-commit hook that did not exist, and sizing.py claimed the executor used it
when the executor had zero references.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pathia.dashboard as db

ROOT = Path(__file__).resolve().parents[1]
DOCTRINE = ROOT / "research" / "EVIDENCE_DOCTRINE.md"

_spec = importlib.util.spec_from_file_location(
    "autonomous_cycle", ROOT / "scripts" / "autonomous_cycle.py")
AC = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(AC)


def test_the_doctrine_exists_and_names_its_own_enforcement():
    assert DOCTRINE.exists()
    body = DOCTRINE.read_text()
    for clause in ("No shadows", "No recorders", "matched null", "Bonferroni",
                   "both OOS halves"):
        assert clause.lower() in body.lower(), f"the doctrine does not state: {clause}"


def test_two_states_only_no_book_is_shadow():
    """A book trades or it does not exist."""
    cfg = json.loads((ROOT / ".agent-config.json").read_text())
    shadowed = [k for k, v in cfg.items()
                if isinstance(v, dict) and v.get("shadow_only") is True]
    assert not shadowed, f"{shadowed} occupy a shadow tier the doctrine removed"


def test_no_book_is_exempt_from_promotion():
    assert AC._NEVER_PROMOTE == frozenset()


def test_every_live_book_can_be_demoted_by_evidence():
    """The other half: a book that evidence cannot switch off is as bad as one
    evidence cannot switch on."""
    assert set(db._KNOWN_BOOK_NAMES) <= set(AC._SWITCHES)


def test_every_live_book_has_a_written_verdict():
    """Live capital requires a finding on disk, not a remembered conversation.
    Each live book must be named somewhere in research/alpha_swarm/findings/."""
    findings = " ".join(p.read_text() for p in
                        (ROOT / "research" / "alpha_swarm" / "findings").glob("*.md"))
    missing = [b for b in db._KNOWN_BOOK_NAMES if b not in findings]
    assert not missing, (
        f"{missing} trade real money with no written verdict in "
        f"research/alpha_swarm/findings/")


def test_the_refuted_stay_refuted():
    """A refutation nobody can find gets rediscovered. Each must be on disk."""
    fdir = ROOT / "research" / "alpha_swarm" / "findings"
    for name, book in (("W-ME1", "main_engine"), ("W-SESS1", "session")):
        hits = list(fdir.glob(f"{name}*.md"))
        assert hits, f"{name} ({book}) has no finding on disk"


def test_the_doctrine_admits_what_it_cannot_do():
    """Three of the four live books CANNOT be backtested — their signals have no
    retrievable history. A doctrine that hid that would be quietly false, and
    the next person would waste a week trying."""
    body = DOCTRINE.read_text()
    assert "cannot be backtested" in body
    for book in ("news_surge_short", "news_surge_multi", "social_trending"):
        assert book in body, f"{book}'s backtest limitation is not documented"
