"""The four live books, exercised together through the real machinery.

These four place real bounded orders. Three of them were rewired on 2026-08-30
and one (`social_trending`) had its live arm written that day, so none had ever
produced an order in its current wiring. Two of them —  news_surge_short and
news_surge_multi — trade THE SAME underlying signal (a breaking coverage surge),
differing only in how many firehoses measure it, so they can fire on the same
coin from the same event.

Per the standing rule that integration beats mocks for anything touching shared
state: everything here uses a real tmp_path-backed ClaimsRegistry, never a
double.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import pathia.dashboard as db
from pathia.agents.rebalancer_owned import ClaimsRegistry, active_claim_books

ROOT = Path(__file__).resolve().parents[1]

LIVE_BOOKS = ("news_surge_short", "news_surge_multi", "social_trending",
              "unlock_short_runin")


# ── every live book can actually hold a claim ────────────────────────────────

def test_every_live_book_may_persist_a_claim():
    """A book absent from _ACTIVE_CLAIM_BOOKS has its claims scrubbed on load,
    so it can never block another book from the coin it is holding."""
    missing = sorted(set(db._KNOWN_BOOK_NAMES) - active_claim_books())
    assert not missing, f"{missing} trade but cannot hold a cross-book claim"


def test_the_claim_set_matches_the_live_set_exactly():
    """A stale name in the claim set is a book that no longer exists reserving
    coins; a missing one is a live book that cannot reserve."""
    assert active_claim_books() == set(db._KNOWN_BOOK_NAMES)


# ── the two books that share a trigger ───────────────────────────────────────

@pytest.fixture
def registry(tmp_path):
    """A real registry, not a mock. Shared-state bugs do not reproduce against
    doubles."""
    return ClaimsRegistry(str(tmp_path / "claims.json"),
                          active_books=active_claim_books()).load()


def test_the_two_news_books_cannot_both_hold_one_coin(registry):
    """news_surge_multi is the same breaking-coverage signal as
    news_surge_short measured across more sources, so a single news event can
    surface the same coin to both. Without exclusion that is two shorts on one
    name from one signal — double the intended size, correlated."""
    assert registry.claim("xyz:NVDA", "news_surge_short") is True
    assert registry.claim("xyz:NVDA", "news_surge_multi") is False
    assert "xyz:NVDA" in registry.claimed_by_others("news_surge_multi")


def test_the_loser_of_a_race_can_claim_after_the_winner_releases(registry):
    registry.claim("xyz:NVDA", "news_surge_short")
    registry.release("xyz:NVDA", "news_surge_short")
    assert registry.claim("xyz:NVDA", "news_surge_multi") is True


@pytest.mark.parametrize("first,second",
                         [(a, b) for a in LIVE_BOOKS for b in LIVE_BOOKS if a != b])
def test_no_two_live_books_can_hold_the_same_coin(registry, first, second):
    """Exhaustive over every ordered pair, not just the two obvious ones."""
    assert registry.claim("BTC", first) is True
    assert registry.claim("BTC", second) is False


def test_a_claim_survives_a_reload(registry, tmp_path):
    """Claims are the only thing stopping a restart from re-opening a coin an
    in-flight book already owns."""
    registry.claim("ETH", "social_trending")
    registry.save()
    reloaded = ClaimsRegistry(str(tmp_path / "claims.json"),
                              active_books=active_claim_books()).load()
    assert reloaded.owner_of("ETH") == "social_trending"


# ── each book's order geometry is placeable ──────────────────────────────────

def _analysis_for(book: str):
    """Build an order the way the book itself does, via its own builder."""
    cfg = {"notional_usd": 20.0, "leverage": 1, "stop_pct": 15.0,
           "hold_days": 1.0, "horizon_days": 1.0}
    if book == "social_trending":
        from pathia.agents.social_trending_recorder import _analysis
        return _analysis("BTC", {"rank": 1, "score": 0}, cfg)
    if book == "unlock_short_runin":
        from pathia.agents import unlock_short_live as m
        return m._analysis("ARB", {"pct": 2.0, "t_ms": 0}, 1.0, cfg) \
            if hasattr(m, "_analysis") else None
    return None


@pytest.mark.parametrize("book", ["social_trending"])
def test_the_order_geometry_is_internally_consistent(book):
    """The stop must be reachable: the executor clamps the backup SL to
    entry*(60/leverage) percent, so a wider stop can never fire and every
    adverse move liquidates before the thesis plays out."""
    a = _analysis_for(book)
    assert a is not None
    lev = a["leverage_override"]
    stop = a["backup_sl_pct_override"]
    assert stop <= 60.0 / lev, f"{book}'s stop is clamped away and can never fire"
    assert stop < 100.0 / lev, f"{book} liquidates before its stop"
    assert a["strategy_book"] == book
    assert a["strategy_book_notional"] > 0


def test_the_graded_and_live_geometry_agree_for_social_trending():
    """social_trending's ledger recorded side=long over a 1-day horizon, and
    that is what graded VALIDATED. A live order on different geometry would be
    an ungraded book wearing a validated book's verdict."""
    a = _analysis_for("social_trending")
    assert a["side"] == "long" and a["verdict"] == "LONG"
    assert a["dsl_exit_override"]["hard_timeout_minutes"] == pytest.approx(1440.0)


# ── the configured universe actually admits what the books trade ─────────────

def test_the_books_can_reach_coins_the_majors_allowlist_permits():
    """A live book restricted to an allowlist that excludes everything it
    trades is a book that silently never fires."""
    from pathia.agents import universe as U
    cfg = json.loads((ROOT / ".agent-config.json").read_text())
    allow = cfg.get("coin_allowlist") or []
    tradable = [c for c in ("BTC", "ETH", "xyz:NVDA", "xyz:GOLD") if U.in_allowlist(c, allow)]
    assert tradable, "the allowlist admits none of the coins the books target"


def test_no_live_book_is_shadow_in_the_running_config():
    cfg = json.loads((ROOT / ".agent-config.json").read_text())
    shadow = [b for b in LIVE_BOOKS
              if isinstance(cfg.get(b) or cfg.get("unlock_short"), dict)
              and (cfg.get(b) or cfg.get("unlock_short", {})).get("shadow_only")]
    assert not shadow, f"{shadow} are live in the registry but shadow in config"
