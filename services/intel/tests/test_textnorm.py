"""Unit tests for title normalization, dedup, and stable id hashing."""
from __future__ import annotations

from services.intel.textnorm import (
    dedup_by_title, is_near_duplicate, normalize_title, stable_id, title_tokens,
)


def test_normalize_strips_source_suffix_and_punct():
    assert normalize_title("WTI gains 6.5% as inventories drop - Reuters") == \
        "wti gains 6 5 as inventories drop"


def test_normalize_handles_pipe_suffix():
    assert normalize_title("Oil at $100 | CNBC") == "oil at 100"


def test_normalize_is_idempotent():
    once = normalize_title("Brent Oil Touches $110 - CNBC")
    assert normalize_title(once) == once


def test_title_tokens_lowercase_set():
    assert title_tokens("WTI Gains 6.5%") == frozenset({"wti", "gains", "6", "5"})


def test_stable_id_deterministic():
    a = stable_id("headline", "https://x.com/1", "Oil surges")
    b = stable_id("headline", "https://x.com/1", "Oil surges")
    assert a == b
    assert len(a) == 16


def test_stable_id_distinguishes_inputs():
    a = stable_id("headline", "url-a", "title")
    b = stable_id("headline", "url-b", "title")
    assert a != b


def test_is_near_duplicate_identical():
    assert is_near_duplicate("Oil surges to $100", "Oil surges to $100")


def test_is_near_duplicate_source_suffix_variant():
    a = "WTI gains 6.5% as US crude inventories drop - Reuters"
    b = "WTI gains 6.5% as US crude inventories drop - Coinpaper"
    assert is_near_duplicate(a, b)


def test_is_near_duplicate_rejects_different_stories():
    a = "Brent oil touches $110 as Iran escalates Hormuz attacks"
    b = "Shell Rotella revs up its heavy-duty oil lineup"
    assert not is_near_duplicate(a, b)


def test_is_near_duplicate_empty_titles():
    assert is_near_duplicate("", "")
    assert not is_near_duplicate("", "Oil surges")


def test_dedup_by_title_keeps_first_of_each_cluster():
    items = [
        {"title": "WTI gains 6.5% as US crude inventories drop - Reuters", "id": 1},
        {"title": "WTI gains 6.5% as US crude inventories drop - Coinpaper", "id": 2},
        {"title": "Brent oil touches $110 as Iran escalates Hormuz attacks", "id": 3},
    ]
    out = dedup_by_title(items, lambda r: r["title"])
    assert [r["id"] for r in out] == [1, 3]


def test_dedup_by_title_empty_input():
    assert dedup_by_title([], lambda r: r["title"]) == []


def test_dedup_preserves_order_of_survivors():
    titles = [
        "Brent oil touches $110 as Iran escalates Hormuz attacks",
        "WTI gains 6.5% as US crude inventories drop 8.6 million barrels",
        "IEA says members to accelerate release of 100 million barrels",
        "G7 agrees 100M bbl emergency stock release over four months",
        "OPEC+ eight-country monthly meeting ends without a quota change",
    ]
    items = [{"title": t} for t in titles]
    out = dedup_by_title(items, lambda r: r["title"])
    assert len(out) == 5  # five genuinely distinct stories
    assert out == items


def test_dedup_by_title_near_duplicate_with_shared_topic_words():
    """Two titles sharing only generic topic words ("oil", "markets")
    but differing in their specific subject must NOT be treated as
    duplicates — near-duplicate detection is about the SAME story
    reworded, not "mentions the same commodity." """
    a = {"title": "Oil markets rattled by new OPEC+ supply signals"}
    b = {"title": "Oil markets shrug off latest US jobs report"}
    out = dedup_by_title([a, b], lambda r: r["title"])
    assert len(out) == 2
