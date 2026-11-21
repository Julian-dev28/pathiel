from __future__ import annotations

import time

from services.research_mcp.concurrent import fetch_many


def test_empty_list_returns_empty():
    assert fetch_many([]) == ([], [])


def test_items_from_every_call_are_concatenated():
    items, errors = fetch_many([lambda: ([1, 2], None), lambda: ([3], None)])
    assert sorted(items) == [1, 2, 3]
    assert errors == []


def test_an_error_string_is_collected_and_its_items_are_not_lost():
    items, errors = fetch_many([lambda: ([1], None), lambda: ([], "source_b:timeout")])
    assert items == [1]
    assert errors == ["source_b:timeout"]


def test_a_raising_call_is_reported_not_propagated():
    def boom():
        raise RuntimeError("kaboom")
    items, errors = fetch_many([lambda: ([1], None), boom])
    assert items == [1]
    assert len(errors) == 1 and "RuntimeError" in errors[0]


def test_a_slow_call_past_the_deadline_is_reported_as_timed_out():
    def slow():
        time.sleep(1.0)
        return ([99], None)
    items, errors = fetch_many([lambda: ([1], None), slow], deadline_s=0.05)
    assert items == [1]
    assert len(errors) == 1 and "timeout_budget_exceeded" in errors[0]


def test_a_call_returning_no_items_contributes_nothing_but_no_error():
    items, errors = fetch_many([lambda: ([], None)])
    assert items == [] and errors == []
