"""A cache written under a different contract version must read as a cache
miss, never a 500 (2026-10-31: the MCP server on contract 1.3.0 wrote
analytics the dashboard's 1.2.0 models forbade, and every Desk panel 500ed)."""
from __future__ import annotations

from services.strategies import pipeline


def test_analytics_cache_with_unknown_fields_reads_as_missing(monkeypatch):
    monkeypatch.setattr(pipeline, "read_json", lambda path: {"desk_id": "oil", "from_the_future": 1})
    assert pipeline.load_cached_analytics("oil") is None


def test_intel_cache_with_unknown_fields_reads_as_missing(monkeypatch):
    monkeypatch.setattr(pipeline, "read_json", lambda path: {"desk_id": "oil", "from_the_future": 1})
    assert pipeline.load_cached_intel("oil") is None


def test_an_empty_cache_is_still_none(monkeypatch):
    monkeypatch.setattr(pipeline, "read_json", lambda path: None)
    assert pipeline.load_cached_analytics("oil") is None and pipeline.load_cached_intel("oil") is None
