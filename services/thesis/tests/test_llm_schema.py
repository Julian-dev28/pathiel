"""Unit tests for services/thesis/llm_schema.py: the draft model and the
--json-schema the CLI is asked to honor, including the prose-length caps
added after the real eval run showed single calls running 2x over budget
with no caps in place.
"""
from __future__ import annotations

from services.thesis.llm_schema import (
    _LEVERAGE_REASON_MAX_LENGTH, _PROSE_CAPS, _WHAT_CHANGES_MY_MIND_ITEM_MAX_LENGTH,
    thesis_draft_json_schema,
)


def test_leverage_reasons_pinned_to_the_requested_rung_count():
    schema = thesis_draft_json_schema(5)
    assert schema["properties"]["leverage_reasons"]["minItems"] == 5
    assert schema["properties"]["leverage_reasons"]["maxItems"] == 5


def test_leverage_reasons_items_have_a_length_cap():
    schema = thesis_draft_json_schema(3)
    assert schema["properties"]["leverage_reasons"]["items"]["maxLength"] == _LEVERAGE_REASON_MAX_LENGTH


def test_what_changes_my_mind_items_have_a_length_cap():
    schema = thesis_draft_json_schema(3)
    cap = schema["properties"]["what_changes_my_mind"]["items"]["maxLength"]
    assert cap == _WHAT_CHANGES_MY_MIND_ITEM_MAX_LENGTH


def test_every_prose_cap_is_injected_into_the_matching_def():
    schema = thesis_draft_json_schema(3)
    found = set()
    for def_schema in schema["$defs"].values():
        for field_name, cap in _PROSE_CAPS.items():
            prop = def_schema.get("properties", {}).get(field_name)
            if prop is not None:
                assert prop["maxLength"] == cap
                found.add(field_name)
    assert found == set(_PROSE_CAPS)  # every capped field actually exists somewhere in the schema


def test_rung_count_floor_is_at_least_one():
    schema = thesis_draft_json_schema(0)
    assert schema["properties"]["leverage_reasons"]["minItems"] == 1
    assert schema["properties"]["leverage_reasons"]["maxItems"] == 1
