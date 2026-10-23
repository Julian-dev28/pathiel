"""What a funded account would actually do — checked before money is at risk.

W-FUND1 (2026-08-30, when the four books were the only trader) simulated
funding and found the $25 dust floor supported ONE of four books while all
four together needed $88.89. The per-book reachability check this file used
to pin (an allowlist silently blocking a book's signals) went with the books
themselves — headless desk, 2026-10-23: Claude trades through the MCP
server, and there is no book roster left for an allowlist to silence.
`book_margin_requirement`'s derived-floor mechanism stays load-bearing
(`pathiel.agents.bracket.min_tradable_equity` still calls it, and the main
engine / MCP path is exactly the kind of "enabled position-opening block"
the function sums) — the tests below that exercise it on synthetic config
still apply unmodified. Both functions moved from `pathiel.agents.executor`
to `pathiel.agents.bracket` 2026-10-23 (wave 2): the rest of executor.py
(the autonomous scan/research/execute engine) has no caller left and is
deleted; `close_position`/`book_margin_requirement`/`min_tradable_equity`
are the surviving pieces `pathiel/dashboard.py` and the MCP server's
`close_position` tool still call.
"""
from __future__ import annotations

from pathlib import Path

from pathiel.agents.config_store import read_agent_config

ROOT = Path(__file__).resolve().parents[1]


def test_a_gate_relaxation_is_not_counted_as_a_book():
    """`shadow_only` is the discriminator and it is load-bearing. `enabled` +
    `notional_usd` alone also matches gate relaxations like thin_short_relax,
    which carries a notional it applies to but opens no position. Counting it
    inflated the requirement from $88.89 to $111.11."""
    from pathiel.agents.bracket import book_margin_requirement
    base = {"min_available_margin_pct": 0.0,
            "a_book": {"enabled": True, "notional_usd": 20.0, "leverage": 1,
                       "shadow_only": False}}
    assert book_margin_requirement(base) == 20.0
    with_relax = dict(base, thin_short_relax={"enabled": True, "notional_usd": 20.0})
    assert book_margin_requirement(with_relax) == 20.0, (
        "a gate relaxation was counted as a book")


def test_a_disabled_book_costs_nothing():
    from pathiel.agents.bracket import book_margin_requirement
    cfg = {"min_available_margin_pct": 0.0,
           "off": {"enabled": False, "notional_usd": 20.0, "leverage": 1,
                   "shadow_only": False}}
    assert book_margin_requirement(cfg) == 0.0


def test_leverage_reduces_the_requirement():
    from pathiel.agents.bracket import book_margin_requirement
    cfg = {"min_available_margin_pct": 0.0,
           "b": {"enabled": True, "notional_usd": 20.0, "leverage": 4,
                 "shadow_only": False}}
    assert book_margin_requirement(cfg) == 5.0


def test_an_explicit_override_still_wins():
    """Deliberate small-account testing must stay possible."""
    from pathiel.agents.bracket import min_tradable_equity
    cfg = dict(read_agent_config(), min_tradable_equity_usd=5.0)
    assert min_tradable_equity(cfg) == 5.0


def test_the_exchange_minimum_is_the_backstop():
    """With no books configured the floor falls back to the exchange minimum,
    never to zero."""
    from pathiel.agents.bracket import (MIN_TRADABLE_EQUITY_USD,
                                        min_tradable_equity)
    assert min_tradable_equity({}) == MIN_TRADABLE_EQUITY_USD


def test_the_preflight_uses_the_per_trade_rule_not_the_retired_floor():
    """2026-10-18: the preflight still blocked on min_tradable_equity ($26) and
    read equity from the loop's stale heartbeat ($1.37 vs a live $15.21)."""
    src = (ROOT / "scripts" / "preflight_live.py").read_text()
    assert "derive_limits" in src and "get_active_summary" in src
    assert "min_tradable_equity(" not in src
