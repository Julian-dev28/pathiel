"""What a funded account would actually do — checked before money is at risk.

W-FUND1 simulated funding and found two things that would otherwise have been
discovered by funding the account and watching it sit idle:

  1. the majors allowlist blocks 92-100% of every live book's historical
     signals, and 100% of unlock_short_runin's
  2. the $25 dust floor supports ONE of four books; all four need $88.89

A book that is live, validated, and structurally unable to fire is the shadow
state wearing a live badge — and it is invisible from inside the code.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import hermes_trader.dashboard as db
from hermes_trader.agents import shadow_ledger as SL
from hermes_trader.agents.config_store import read_agent_config
from hermes_trader.agents.universe import in_allowlist

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "preflight_live", ROOT / "scripts" / "preflight_live.py")
PF = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(PF)


def _reachability(book: str, allow: list[str]) -> float | None:
    """Share of a book's own historical signals an allowlist would permit.

    Takes the allowlist as an argument rather than reading config, so it is
    testable without monkeypatching module-level state.
    """
    if not allow:
        return 100.0
    path = SL._book_path(book)
    if not os.path.exists(path):
        return None
    coins = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    coins.append(json.loads(line)["coin"])
                except Exception:
                    continue
    if not coins:
        return None
    return 100 * sum(1 for c in coins if in_allowlist(c, allow)) / len(coins)


def test_the_preflight_checks_book_reachability():
    """The check must exist, whatever it currently reports. Without it the
    conflict between the allowlist and the books is invisible until funding."""
    assert hasattr(PF, "check_book_reachability")
    assert hasattr(PF, "check_margin_headroom")


def test_reachability_is_computed_from_the_ledger(tmp_path, monkeypatch):
    """The mechanism, on synthetic data.

    Deliberately NOT asserted against the operator's live ledgers: pytest
    isolates HERMES_STATE_DIR so the suite cannot read live state, and a test
    reaching for it would assert on the machine rather than the code. Reading
    live state is the preflight's job — it loads .env.local first.
    """
    # rebalancer_owned captures _STATE_DIR at IMPORT time, so setenv here is
    # too late — patch the resolved value, which is what state_file() reads.
    import hermes_trader.agents.rebalancer_owned as ro
    monkeypatch.setattr(ro, "_STATE_DIR", str(tmp_path))
    d = tmp_path / "shadow_ledger"
    d.mkdir(parents=True)
    (d / "demo.jsonl").write_text(
        "\n".join(json.dumps({"coin": c})
                  for c in ["BTC", "CASHCAT", "CASHCAT", "CASHCAT"]) + "\n")
    assert _reachability("demo", ["BTC", "ETH"]) == 25.0
    assert _reachability("demo", ["CASHCAT"]) == 75.0


def test_no_allowlist_means_full_reachability():
    """An empty allowlist is 'unrestricted' — the historical meaning of the key.
    A book is then limited only by its own volume floors."""
    assert _reachability("anything", []) == 100.0


def test_a_book_with_no_history_is_unmeasurable_not_zero(tmp_path, monkeypatch):
    """Missing history must read as 'cannot assess', never as 'blocked' — the
    two demand opposite responses."""
    import hermes_trader.agents.rebalancer_owned as ro
    monkeypatch.setattr(ro, "_STATE_DIR", str(tmp_path))
    (tmp_path / "shadow_ledger").mkdir(parents=True)
    assert _reachability("nonexistent", ["BTC"]) is None


def test_the_preflight_blocks_rather_than_warns_on_an_unreachable_book():
    """The property that matters: if the allowlist silences a book, the
    preflight must BLOCK, not pass quietly and let someone fund an idle system.
    Checked on the source, since the live reading needs .env.local."""
    src = (ROOT / "scripts" / "preflight_live.py").read_text()
    start = src.index("def check_book_reachability")
    body = src[start:src.index("\ndef ", start + 1)]
    assert "r.block(" in body, "an unreachable book must BLOCK, not warn"
    assert "W-FUND1" in body, "the finding that motivated the check is not cited"


def test_margin_headroom_knows_what_all_books_cost():
    """Funding to exactly the dust floor buys a system where the first book to
    fire consumes the budget and the rest are margin-blocked behind it."""
    cfg = read_agent_config()
    total = 0.0
    for book in db._KNOWN_BOOK_NAMES:
        c = cfg.get(book) or cfg.get("unlock_short") or {}
        total += float(c.get("notional_usd", 0) or 0) / max(1, int(c.get("leverage", 1) or 1))
    assert total > 0, "no book declares a notional — sizing is unknowable"
    from hermes_trader.agents.executor import MIN_TRADABLE_EQUITY_USD
    assert total > MIN_TRADABLE_EQUITY_USD, (
        "the dust floor now covers every book simultaneously — if that is "
        "deliberate, this test should be updated to say so")


# ── the floor is derived, not guessed ────────────────────────────────────────

def test_the_floor_covers_every_enabled_book():
    """W-FUND1: a flat $25 floor let the FIRST book to fire consume the whole
    budget while the rest sat margin-blocked behind it — a crippled subset that
    looks, from outside, like books that simply are not firing.

    The floor is now what the enabled book set actually costs.
    """
    from hermes_trader.agents.executor import (book_margin_requirement,
                                               min_tradable_equity)
    cfg = read_agent_config()
    need = book_margin_requirement(cfg)
    assert need > 0, "no book declares a notional — sizing is unknowable"
    assert min_tradable_equity(cfg) >= need, (
        "the floor is below what the books cost — funding to it buys a "
        "crippled subset of the system")


def test_a_gate_relaxation_is_not_counted_as_a_book():
    """`shadow_only` is the discriminator and it is load-bearing. `enabled` +
    `notional_usd` alone also matches gate relaxations like thin_short_relax,
    which carries a notional it applies to but opens no position. Counting it
    inflated the requirement from $88.89 to $111.11."""
    from hermes_trader.agents.executor import book_margin_requirement
    base = {"min_available_margin_pct": 0.0,
            "a_book": {"enabled": True, "notional_usd": 20.0, "leverage": 1,
                       "shadow_only": False}}
    assert book_margin_requirement(base) == 20.0
    with_relax = dict(base, thin_short_relax={"enabled": True, "notional_usd": 20.0})
    assert book_margin_requirement(with_relax) == 20.0, (
        "a gate relaxation was counted as a book")


def test_a_disabled_book_costs_nothing():
    from hermes_trader.agents.executor import book_margin_requirement
    cfg = {"min_available_margin_pct": 0.0,
           "off": {"enabled": False, "notional_usd": 20.0, "leverage": 1,
                   "shadow_only": False}}
    assert book_margin_requirement(cfg) == 0.0


def test_leverage_reduces_the_requirement():
    from hermes_trader.agents.executor import book_margin_requirement
    cfg = {"min_available_margin_pct": 0.0,
           "b": {"enabled": True, "notional_usd": 20.0, "leverage": 4,
                 "shadow_only": False}}
    assert book_margin_requirement(cfg) == 5.0


def test_an_explicit_override_still_wins():
    """Deliberate small-account testing must stay possible."""
    from hermes_trader.agents.executor import min_tradable_equity
    cfg = dict(read_agent_config(), min_tradable_equity_usd=5.0)
    assert min_tradable_equity(cfg) == 5.0


def test_the_exchange_minimum_is_the_backstop():
    """With no books configured the floor falls back to the exchange minimum,
    never to zero."""
    from hermes_trader.agents.executor import (MIN_TRADABLE_EQUITY_USD,
                                               min_tradable_equity)
    assert min_tradable_equity({}) == MIN_TRADABLE_EQUITY_USD
