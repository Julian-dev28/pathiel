"""Unit tests for services/intel/scoring.py: keyword match, recency decay,
source tier, the multiplicative relevance combination, and the per-desk
stance lexicon."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services.intel.scoring import (
    match_score, recency_weight, relevance, source_tier_weight, stance,
)

NOW = datetime(2026, 10, 17, 3, 0, tzinfo=timezone.utc)


# ── match_score ──────────────────────────────────────────────────────

def test_match_exact_multiword_phrase_substring():
    assert match_score("Ships face attacks in the Strait of Hormuz", ["Strait of Hormuz"]) == 1.0


def test_match_partial_multiword_phrase_bag_of_words():
    # "crude oil price" shares only "oil" with this title -> 1/3
    score = match_score("Crews respond to a truck crash with oil leakage", ["crude oil price"])
    assert score == pytest.approx(1 / 3)


def test_match_single_token_ticker_requires_exact_token():
    assert match_score("Brent oil touches $110 as Iran escalates", ["Brent"]) == 1.0


def test_match_single_token_ticker_rejects_substring_false_positive():
    """A 2-letter ticker like "CL" must not match merely because it is a
    substring of an unrelated word ("circle", "include", ...) — this is
    exactly the false-positive mode a naive `in` check would hit."""
    assert match_score("Fed officials circle back on rate path", ["CL"]) == 0.0
    assert match_score("WTI (CL) settles higher on the day", ["CL"]) == 1.0


def test_match_best_across_multiple_phrases():
    title = "OPEC+ holds output steady as WTI climbs"
    assert match_score(title, ["Strait of Hormuz", "WTI", "EIA crude inventories"]) == 1.0


def test_match_no_phrases_is_zero():
    assert match_score("Some headline", []) == 0.0


def test_match_empty_phrase_is_skipped_not_crashing():
    assert match_score("Oil rises", ["", "oil"]) == 1.0


# ── recency_weight ───────────────────────────────────────────────────

def test_recency_weight_fresh_is_near_one():
    assert recency_weight(NOW, NOW, half_life_hours=36) == 1.0


def test_recency_weight_at_half_life_is_three_quarters():
    half_life_ago = NOW - timedelta(hours=36)
    assert recency_weight(half_life_ago, NOW, half_life_hours=36) == pytest.approx(0.75, abs=1e-6)


def test_recency_weight_floor_for_very_old():
    very_old = NOW - timedelta(days=365)
    assert recency_weight(very_old, NOW, half_life_hours=36) == pytest.approx(0.5, abs=1e-3)


def test_recency_weight_none_gets_floor():
    assert recency_weight(None, NOW) == 0.5


def test_recency_weight_naive_datetime_treated_as_utc():
    naive = datetime(2026, 10, 17, 2, 0)  # 1h before NOW, no tzinfo
    aware = recency_weight(naive, NOW, half_life_hours=36)
    assert aware == pytest.approx(recency_weight(NOW - timedelta(hours=1), NOW, 36), abs=1e-9)


# ── source_tier_weight ───────────────────────────────────────────────

def test_source_tier_wire_service_is_top():
    assert source_tier_weight("Reuters") == 1.0
    assert source_tier_weight("reuters") == 1.0  # case-insensitive


def test_source_tier_known_secondary():
    assert source_tier_weight("Yahoo Finance") == 0.85


def test_source_tier_unknown_default():
    assert source_tier_weight("WSAZ") == 0.65


def test_source_tier_none_defaults_like_unknown():
    assert source_tier_weight(None) == 0.65


def test_source_tier_named_junk_sources_demoted():
    """Robinhood's templated "BTC price on <date> — Crypto Prediction
    Market" posts and Moomoo's auto-translated feed items both keyword-
    match a bitcoin query perfectly while carrying zero news content —
    see scoring.py's module docstring for the real captured examples.
    Source tier is what suppresses them; confirm it actually does."""
    assert source_tier_weight("Robinhood") == 0.2
    assert source_tier_weight("Moomoo") == 0.2


# ── relevance (the full combination) ────────────────────────────────

def test_relevance_zero_match_is_zero_regardless_of_tier_or_recency():
    r = relevance("Long-Term Treasuries Fall as Dealer Survey Offers Little Signal",
                   queries=["crude oil price", "WTI", "Brent"], source="Bloomberg",
                   published_at=NOW, now=NOW)
    assert r == 0.0


def test_relevance_strong_match_fresh_top_tier_is_high():
    r = relevance("Brent oil touches $110 as Iran escalates Hormuz attacks",
                   queries=["crude oil price", "Strait of Hormuz", "WTI", "Brent"],
                   source="CNBC", published_at=NOW - timedelta(hours=2), now=NOW)
    assert r > 0.9


def test_relevance_perfect_keyword_match_but_junk_source_is_suppressed():
    """The adversarial case the multiplicative design exists for: a
    templated title that keyword-matches perfectly but from a named
    noise source must land well below real signal, not merely slightly
    below it. See scoring.py's module docstring."""
    r_junk = relevance("BTC price on Oct 16, 2026 at 11pm EDT — Crypto Prediction Market",
                        queries=["bitcoin price", "BTC"], source="Robinhood",
                        published_at=NOW - timedelta(hours=1), now=NOW)
    r_real = relevance("MSCI makes it official, and bitcoin (BTC) treasury firms on notice",
                        queries=["bitcoin price", "BTC"], source="CoinDesk",
                        published_at=NOW - timedelta(hours=5), now=NOW)
    assert r_junk < 0.3
    assert r_real > 0.7
    assert r_real > r_junk


def test_relevance_weak_match_ranks_below_strong_match():
    weak = relevance("3 Oil And Gas Stocks Retail Investors Are Watching",
                      queries=["crude oil price", "WTI", "Brent"], source="Yahoo Finance",
                      published_at=NOW - timedelta(hours=24), now=NOW)
    strong = relevance("Brent oil touches $110 as Iran escalates Hormuz attacks",
                        queries=["crude oil price", "WTI", "Brent"], source="CNBC",
                        published_at=NOW - timedelta(hours=2), now=NOW)
    assert 0.0 < weak < strong


def test_relevance_bounded_zero_to_one():
    r = relevance("WTI Brent crude oil price", queries=["WTI", "Brent", "crude oil price"],
                   source="Reuters", published_at=NOW, now=NOW)
    assert 0.0 <= r <= 1.0


# ── stance ───────────────────────────────────────────────────────────

def test_stance_oil_bull_words():
    assert stance("Iran escalates Hormuz attacks on tankers", "oil") == "bull"


def test_stance_oil_bear_words():
    assert stance("G7 agrees emergency reserve release as truce talks continue", "oil") == "bear"


def test_stance_no_lexicon_hit_is_unknown():
    assert stance("Quarterly earnings beat expectations for tech giant", "oil") == "unknown"


def test_stance_tie_is_neutral():
    # exactly one bull word ("attack") and one bear word ("deal"), nothing else
    assert stance("Attack reported amid new deal talks", "oil") == "neutral"


def test_stance_btc_lexicon():
    assert stance("ETF inflows surge as bitcoin rallies past resistance", "btc") == "bull"
    assert stance("Exchange hacked, bitcoin liquidations cascade", "btc") == "bear"


def test_stance_unknown_desk_uses_default_lexicon():
    assert stance("Stock soars after earnings beat", "some_new_desk") == "bull"
    assert stance("Stock plunges after earnings miss", "some_new_desk") == "bear"
