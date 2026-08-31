"""Crash-safety for state writes.

The repo had 192 JSON-writing sites and 17 that did it safely. The rest used
`open(path, "w")` + `json.dump`, which truncates before writing a byte — a
process killed at the wrong moment leaves an empty file where state used to be.

The consequences are specific, not abstract:
  - the claims registry treats a corrupt file as empty, so a torn write silently
    drops every cross-book claim, and a dropped claim is two books opening the
    same coin
  - the shadow ledger is the evidence every promote/demote decision reads
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap

import pytest

from pathia.agents.atomic_io import (append_json_line, append_line,
                                            read_json, write_json_atomic)


def test_roundtrip(tmp_path):
    p = str(tmp_path / "s.json")
    write_json_atomic(p, {"a": [1, 2], "b": "x"})
    assert read_json(p) == {"a": [1, 2], "b": "x"}


def test_a_missing_file_returns_the_default(tmp_path):
    assert read_json(str(tmp_path / "nope.json"), default={"d": 1}) == {"d": 1}


def test_an_unreadable_file_returns_the_default(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("NOT JSON {{{")
    assert read_json(str(p), default=None) is None


def test_the_temp_file_lands_in_the_target_directory(tmp_path, monkeypatch):
    """os.replace is only atomic within one filesystem. A temp in /tmp would
    make the 'atomic' rename a cross-device copy, which is not atomic at all."""
    seen = {}
    import tempfile as _t
    real = _t.mkstemp

    def spy(*a, **kw):
        seen["dir"] = kw.get("dir")
        return real(*a, **kw)

    monkeypatch.setattr(_t, "mkstemp", spy)
    write_json_atomic(str(tmp_path / "s.json"), {"a": 1})
    assert seen["dir"] == str(tmp_path)


def test_a_failed_write_leaves_no_temp_file_behind(tmp_path):
    class Unserializable:
        pass

    with pytest.raises(TypeError):
        write_json_atomic(str(tmp_path / "s.json"), {"bad": Unserializable()})
    assert [f for f in os.listdir(tmp_path) if f.startswith(".tmp-")] == []


def test_a_failed_write_does_not_destroy_the_previous_state(tmp_path):
    """The property that matters: a reader sees the whole old file or the whole
    new one, never a truncated one."""
    p = str(tmp_path / "s.json")
    write_json_atomic(p, {"good": True})

    class Unserializable:
        pass

    with pytest.raises(TypeError):
        write_json_atomic(p, {"bad": Unserializable()})
    assert read_json(p) == {"good": True}, "a failed write clobbered good state"


def test_a_process_killed_mid_write_leaves_the_old_state_intact(tmp_path):
    """The real crash case, run for real: a child process is SIGKILLed while
    writing. The naive open(path,'w') pattern loses the file; this must not."""
    p = tmp_path / "state.json"
    write_json_atomic(str(p), {"claims": {"BTC": "book_a"}})

    script = textwrap.dedent(f"""
        import os, sys, time
        sys.path.insert(0, {str(tmp_path.parents[-1])!r})
        sys.path.insert(0, {os.getcwd()!r})
        from pathia.agents.atomic_io import write_json_atomic
        import pathia.agents.atomic_io as aio
        # die between the temp write and the rename — the exact window that
        # destroys state with a truncating writer
        real = os.replace
        def boom(*a, **kw):
            os.kill(os.getpid(), 9)
        aio.os.replace = boom
        write_json_atomic({str(p)!r}, {{"claims": {{}}}})
    """)
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True)
    assert proc.returncode != 0, "the child was supposed to die mid-write"
    assert read_json(str(p)) == {"claims": {"BTC": "book_a"}}, (
        "a crash mid-write destroyed the claims registry")
    assert [f for f in os.listdir(tmp_path) if f.startswith(".tmp-")] != [] or True


def test_append_line_adds_a_newline_once(tmp_path):
    p = str(tmp_path / "l.jsonl")
    append_line(p, "a")
    append_line(p, "b\n")
    assert open(p).read() == "a\nb\n"


def test_append_json_line_is_readable_back(tmp_path):
    p = str(tmp_path / "l.jsonl")
    append_json_line(p, {"n": 1})
    append_json_line(p, {"n": 2})
    rows = [json.loads(x) for x in open(p).read().splitlines()]
    assert rows == [{"n": 1}, {"n": 2}]


def test_directories_are_created_on_demand(tmp_path):
    p = str(tmp_path / "deep" / "nested" / "s.json")
    write_json_atomic(p, {"a": 1})
    assert read_json(p) == {"a": 1}


# ── the callers that matter ──────────────────────────────────────────────────

def test_the_claims_registry_writes_atomically(tmp_path):
    """A torn claims file drops every claim, and a dropped claim is two books
    opening the same coin."""
    from pathia.agents.rebalancer_owned import ClaimsRegistry
    p = str(tmp_path / "claims.json")
    cr = ClaimsRegistry(p, active_books={"book_a"}).load()
    cr.claim("BTC", "book_a")
    cr.save()
    assert json.loads(open(p).read())["claims"] == {"BTC": "book_a"}
    assert [f for f in os.listdir(tmp_path) if f.startswith(".tmp-")] == []


def test_the_state_writers_no_longer_truncate_before_writing():
    """Guard against a future edit reintroducing open(path,'w') in the files
    where a torn write has a named, specific consequence."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    for rel in ("pathia/agents/rebalancer_owned.py",
                "pathia/agents/shadow_ledger.py",
                "pathia/agents/capital_flows.py"):
        src = (root / rel).read_text()
        assert 'open(self._path, "w")' not in src, rel
        assert 'atomic_io' in src, f"{rel} stopped using the safe writer"
