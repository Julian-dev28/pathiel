from __future__ import annotations

import json
import threading
from datetime import datetime, timezone

from services.research_mcp import usage


def test_record_call_creates_the_file_under_the_state_dir(tmp_path):
    path = str(tmp_path / "usage.json")
    usage.record_call("levels", path=path)
    assert (tmp_path / "usage.json").exists()
    data = json.loads((tmp_path / "usage.json").read_text())
    assert data["levels"]["calls"] == 1


def test_record_call_increments_and_keeps_first_called_at(tmp_path):
    path = str(tmp_path / "usage.json")
    t0 = datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 11, 20, 10, 5, tzinfo=timezone.utc)
    row1 = usage.record_call("headlines", path=path, now=t0)
    row2 = usage.record_call("headlines", path=path, now=t1)
    assert row1["calls"] == 1 and row2["calls"] == 2
    assert row2["first_called_at"] == t0.isoformat()
    assert row2["last_called_at"] == t1.isoformat()


def test_record_call_tracks_each_tool_separately(tmp_path):
    path = str(tmp_path / "usage.json")
    usage.record_call("levels", path=path)
    usage.record_call("levels", path=path)
    usage.record_call("events", path=path)
    data = usage.read_usage(path)
    assert data["levels"]["calls"] == 2
    assert data["events"]["calls"] == 1


def test_read_usage_on_a_missing_file_is_empty_not_an_error(tmp_path):
    assert usage.read_usage(str(tmp_path / "nope.json")) == {}


def test_record_call_contains_no_arguments_or_identity_fields(tmp_path):
    """Privacy-preserving by construction: the row this writes has exactly
    three keys, none of which could carry a coin, a query, a result or a
    caller identity."""
    path = str(tmp_path / "usage.json")
    row = usage.record_call("research_brief", path=path)
    assert set(row.keys()) == {"calls", "first_called_at", "last_called_at"}


def test_record_call_is_thread_safe_under_concurrent_calls(tmp_path):
    path = str(tmp_path / "usage.json")
    n_threads, calls_each = 8, 10

    def hammer():
        for _ in range(calls_each):
            usage.record_call("levels", path=path)

    threads = [threading.Thread(target=hammer) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert usage.read_usage(path)["levels"]["calls"] == n_threads * calls_each


def test_usage_path_defaults_to_the_state_dir(monkeypatch, tmp_path):
    from services.research_mcp import state
    monkeypatch.setenv(state.ENV_STATE_DIR, str(tmp_path))
    assert usage.usage_path() == str(tmp_path / usage.USAGE_FILE_NAME)
