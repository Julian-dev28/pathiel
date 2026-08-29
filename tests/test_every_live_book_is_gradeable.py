"""Every book that can spend money must be reachable by the evidence loop.

news_ta_aligned placed real bounded orders for weeks with no entry in
autonomous_cycle._SWITCHES, so the automated grader could neither promote nor
demote it. A live money path the evidence loop cannot switch off is the one
shape that script exists to rule out, and nothing was checking for it.

This is the check. It compares the dashboard's live-book registry — the list the
operator reads as "what can trade" — against the switch table the grader acts
through, and fails on any book in the first that is missing from the second.
"""
from __future__ import annotations

import os
import importlib.util
from pathlib import Path

import hermes_trader.dashboard as db

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "autonomous_cycle", ROOT / "scripts" / "autonomous_cycle.py")
AC = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(AC)


def test_every_live_book_has_a_switch_the_grader_can_flip():
    missing = sorted(set(db._KNOWN_BOOK_NAMES)
                     - set(AC._SWITCHES)
                     - set(AC._NEVER_PROMOTE))
    assert not missing, (
        f"{missing} can place orders but the evidence loop has no switch for "
        f"them — they can never be auto-demoted when their ledger refutes them")


def test_the_switch_table_does_not_reference_deleted_books():
    """A switch pointing at a book that no longer exists is dead weight that
    makes the table lie about what the loop controls."""
    live = set(db._KNOWN_BOOK_NAMES) | {"main_engine"}
    stale = sorted(b for b in AC._SWITCHES if b not in live)
    assert not stale, f"switches for books that no longer exist: {stale}"


def test_demotion_is_reachable_for_every_live_book():
    """Not just present in the table — actually flippable. A switch shape the
    apply function does not understand is the same as no switch at all."""
    for book, path in AC._SWITCHES.items():
        if path[0] == "top":
            cfg = {path[1]: {"enabled": True, "shadow_only": False,
                             "notional_usd": 20.0, "leverage": 3,
                             "stop_pct": 15.0}}
        elif path[0] == "nested":
            cfg = {path[1]: {path[2]: {"enabled": True, "shadow_only": False,
                                       "notional_usd": 20.0, "leverage": 3,
                                       "stop_pct": 15.0}}}
        else:                                   # "entries" — flag, no sizing knobs
            cfg = {path[1]: {"entries_enabled": True}}
        assert AC.apply_action(cfg, book, "demote") is True, (
            f"{book} has a switch the loop cannot actually flip")


def test_the_promotion_bar_is_stricter_than_the_demotion_bar():
    """The core safety asymmetry, pinned so a future edit cannot quietly relax
    it: stopping a bleed is cheap, promoting wrongly costs real money."""
    src = (ROOT / "scripts" / "autonomous_cycle.py").read_text()
    assert "PROMOTE_MAX_P" in src and AC.PROMOTE_MAX_P <= 0.05
    assert AC.STRICT_FEE_TIER != AC.REAL_FEE_TIER, (
        "promotions must be tested at a more conservative cost tier than the "
        "one demotions use")
    assert AC.MIN_N >= 8


def test_the_abort_diagnostic_reports_state_rather_than_guessing():
    """The old message asserted 'likely HL rate-budget contention with the live
    loop' without checking whether the loop was running. On 2026-08-29 it
    printed that while the loop had been stopped for weeks and the real cause
    was the exchange returning bulk 500s. A diagnostic that guesses sends the
    reader to the wrong place, which is worse than one that says it does not
    know."""
    msg = AC._diagnose_slowness()
    assert msg and "likely" not in msg.lower()
    assert ("loop IS running" in msg or "loop is NOT running" in msg
            or "could not determine" in msg)


# ── the ledger-path trap ─────────────────────────────────────────────────────

def test_an_ambiguous_ledger_path_warns_instead_of_reading_empty(caplog, monkeypatch):
    """HERMES_STATE_DIR lives in .env.local. A tool that forgets to load it
    resolves ledgers to the repo root instead, and if a stale directory exists
    there every book silently reads as '0 signals' rather than erroring.

    That burned a real investigation on 2026-08-29: three books reported zero
    while 95 records sat in the other directory. Same shape as a data outage
    reading as a quiet market.

    Exercised against the real repo layout rather than a faked one — the guard's
    whole job is to notice a condition of THIS tree.
    """
    import logging
    import pathlib as _pl

    from hermes_trader.agents import shadow_ledger as SL

    root = _pl.Path(__file__).resolve().parents[1]
    monkeypatch.delenv("HERMES_STATE_DIR", raising=False)
    monkeypatch.setattr(SL, "_AMBIGUITY_WARNED", False)
    monkeypatch.setattr(SL, "state_file", lambda name: str(root / name))

    state_ledger = root / ".state" / SL._DIR
    with caplog.at_level(logging.WARNING):
        SL._ledger_dir()
    warned = any("HERMES_STATE_DIR is unset" in r.message for r in caplog.records)
    # The warning fires exactly when the ambiguity actually exists.
    assert warned is state_ledger.is_dir(), (
        "the guard did not match the real state of the tree")


def test_the_ambiguity_warning_fires_at_most_once(caplog, monkeypatch):
    """A per-call warning on a hot path becomes noise, and noise gets muted."""
    import logging

    from hermes_trader.agents import shadow_ledger as SL
    monkeypatch.delenv("HERMES_STATE_DIR", raising=False)
    monkeypatch.setattr(SL, "_AMBIGUITY_WARNED", False)
    with caplog.at_level(logging.WARNING):
        for _ in range(5):
            SL._ledger_dir()
    assert sum("HERMES_STATE_DIR is unset" in r.message
               for r in caplog.records) <= 1


def test_the_ledger_ambiguity_guard_never_raises(monkeypatch):
    from hermes_trader.agents import shadow_ledger as SL
    monkeypatch.delenv("HERMES_STATE_DIR", raising=False)
    monkeypatch.setattr(SL, "_AMBIGUITY_WARNED", False)
    assert isinstance(SL._ledger_dir(), str)


def test_book_status_loads_env_before_resolving_state():
    """The script exists because the ad-hoc version of it read the wrong
    directory and reported every book as empty. If it stops loading .env.local
    first, it silently lies again."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "scripts" / "book_status.py").read_text()
    env_load = src.index(".env.local")
    first_agent_import = src.index("from hermes_trader.agents")
    assert env_load < first_agent_import, (
        "book_status.py imports state-resolving modules before loading "
        ".env.local — it would read the wrong ledger directory")
