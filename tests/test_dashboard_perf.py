"""Dashboard audit fixes (2026-07-10): incremental log reader, bounded tail,
equity-curve sustained-drop acceptance, state-write gating in the server process."""
import importlib
import json
import os

import pytest


def _write_lines(path, events):
    with open(path, "a") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


def test_incremental_reader_parses_only_new_bytes(tmp_path, monkeypatch):
    from hermes_trader import dashboard as db
    log = tmp_path / "sess.jsonl"
    monkeypatch.setattr(db, "_LOG_PATH", log)
    monkeypatch.setitem(db._LOG_STATE, "offset", 0)
    monkeypatch.setitem(db._LOG_STATE, "ino", None)
    db._LOG_STATE["events"].clear()

    _write_lines(log, [{"ts": i, "event": "loop_heartbeat", "equity": 50 + i} for i in range(3)])
    out = db._read_log_lines()
    assert len(out) == 3 and out[-1]["equity"] == 52
    off1 = db._LOG_STATE["offset"]
    # append two more: only the delta is parsed (offset advances, events grow by 2)
    _write_lines(log, [{"ts": 3, "event": "scan"}, {"ts": 4, "event": "scan"}])
    out = db._read_log_lines()
    assert len(out) == 5 and db._LOG_STATE["offset"] > off1
    # no change -> no reparse, same snapshot
    assert len(db._read_log_lines()) == 5


def test_incremental_reader_handles_truncation(tmp_path, monkeypatch):
    from hermes_trader import dashboard as db
    log = tmp_path / "sess.jsonl"
    monkeypatch.setattr(db, "_LOG_PATH", log)
    db._LOG_STATE.update({"offset": 0, "ino": None})
    db._LOG_STATE["events"].clear()
    _write_lines(log, [{"ts": i, "event": "e"} for i in range(5)])
    assert len(db._read_log_lines()) == 5
    log.write_text('{"ts": 99, "event": "fresh"}\n')     # truncated/rotated
    out = db._read_log_lines()
    assert len(out) == 1 and out[0]["ts"] == 99


def test_tail_reads_bounded_block(tmp_path, monkeypatch):
    from hermes_trader import session_log as sl
    log = tmp_path / "sess.jsonl"
    monkeypatch.setattr(sl, "SESSION_LOG_FILE", str(log))
    _write_lines(log, [{"i": i} for i in range(500)])
    out = sl.tail(5)
    assert [e["i"] for e in out] == [495, 496, 497, 498, 499]
    assert sl.tail(0) == []


def test_equity_curve_accepts_sustained_drop(monkeypatch):
    from hermes_trader import dashboard as db
    import time as _t
    now = int(_t.time() * 1000)
    events = ([{"ts": now - 10_000 + i, "event": "loop_heartbeat", "equity": 150.0} for i in range(5)]
              + [{"ts": now - 5_000 + i, "event": "loop_heartbeat", "equity": 60.0} for i in range(4)])
    monkeypatch.setattr(db, "_read_log_lines", lambda: events)
    series = db._equity_curve_payload(86_400)
    eqs = [p["equity"] for p in series]
    # one-tick spikes are still filtered (first 2 sub-70% drops), but the
    # SUSTAINED drop is accepted from the 3rd reading on — no permanent freeze
    assert 60.0 in eqs
    assert eqs[:5] == [150.0] * 5


def test_state_readonly_gates_memory_flush(tmp_path, monkeypatch):
    from hermes_trader.agents.memory import memory
    monkeypatch.setenv("HERMES_STATE_READONLY", "1")
    # flush must be a no-op: point the file somewhere and verify nothing is written
    target = tmp_path / "mem.json"
    monkeypatch.setattr(memory, "_path", str(target), raising=False)
    memory.flush()
    assert not target.exists() or target.read_text() == ""
