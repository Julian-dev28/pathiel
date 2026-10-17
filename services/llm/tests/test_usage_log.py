"""Unit tests for services/llm/usage_log.py."""
from __future__ import annotations

import json
import os

from services.llm import config, usage_log


def test_append_usage_writes_one_jsonl_line_with_timestamp():
    usage_log.append_usage({"model": "claude-fable-5-1", "ok": True, "input_tokens": 10})
    path = config.usage_log_path()
    lines = open(path).read().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["model"] == "claude-fable-5-1"
    assert record["ok"] is True
    assert record["input_tokens"] == 10
    assert "ts" in record and "T" in record["ts"]  # ISO 8601


def test_append_usage_appends_across_multiple_calls_in_order():
    usage_log.append_usage({"tag": "first"})
    usage_log.append_usage({"tag": "second"})
    usage_log.append_usage({"tag": "third"})
    lines = open(config.usage_log_path()).read().splitlines()
    assert [json.loads(line)["tag"] for line in lines] == ["first", "second", "third"]


def test_append_usage_creates_parent_directory():
    path = config.usage_log_path()
    assert not os.path.exists(os.path.dirname(path))
    usage_log.append_usage({"ok": True})
    assert os.path.isfile(path)


def test_append_usage_swallows_write_failure(monkeypatch, caplog):
    def _boom(path, exist_ok=True):
        raise OSError("disk full")

    monkeypatch.setattr(usage_log.os, "makedirs", _boom)
    import logging
    with caplog.at_level(logging.WARNING):
        usage_log.append_usage({"ok": True})  # must not raise
    assert "could not append usage log" in caplog.text
