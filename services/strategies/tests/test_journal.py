"""journal.py: save/list/get, newest-first ordering, author filtering,
atomic writes (reuses pathiel.agents.atomic_io, never a naive open/write)."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from services.strategies import journal
from services.strategies.tests.conftest import make_thesis


def test_list_theses_empty_desk_returns_empty_list():
    assert journal.list_theses("no-such-desk") == []
    assert journal.latest_thesis("no-such-desk") is None


def test_save_and_get_round_trips():
    t = make_thesis(thesis_id="t1")
    journal.save_thesis(t)
    got = journal.get_thesis("oil", "t1")
    assert got == t


def test_get_missing_thesis_returns_none():
    assert journal.get_thesis("oil", "does-not-exist") is None


def test_list_theses_newest_first():
    t_old = make_thesis(thesis_id="old", created_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    t_mid = make_thesis(thesis_id="mid", created_at=datetime(2026, 1, 5, tzinfo=timezone.utc))
    t_new = make_thesis(thesis_id="new", created_at=datetime(2026, 1, 10, tzinfo=timezone.utc))
    for t in (t_mid, t_old, t_new):  # save out of order on purpose
        journal.save_thesis(t)
    rows = journal.list_theses("oil")
    assert [t.id for t in rows] == ["new", "mid", "old"]


def test_list_theses_limit():
    for i in range(5):
        journal.save_thesis(make_thesis(thesis_id=f"t{i}",
                                        created_at=datetime(2026, 1, 1 + i, tzinfo=timezone.utc)))
    rows = journal.list_theses("oil", limit=2)
    assert len(rows) == 2
    assert rows[0].id == "t4"


def test_list_theses_filters_by_author():
    journal.save_thesis(make_thesis(thesis_id="m1", author="machine"))
    journal.save_thesis(make_thesis(thesis_id="u1", author="user",
                                    created_at=datetime(2026, 1, 2, tzinfo=timezone.utc)))
    assert [t.id for t in journal.list_theses("oil", author="machine")] == ["m1"]
    assert [t.id for t in journal.list_theses("oil", author="user")] == ["u1"]
    assert {t.id for t in journal.list_theses("oil")} == {"m1", "u1"}


def test_latest_thesis_respects_author_filter():
    journal.save_thesis(make_thesis(thesis_id="m1", author="machine",
                                    created_at=datetime(2026, 1, 1, tzinfo=timezone.utc)))
    journal.save_thesis(make_thesis(thesis_id="u1", author="user",
                                    created_at=datetime(2026, 1, 5, tzinfo=timezone.utc)))
    assert journal.latest_thesis("oil").id == "u1"              # overall newest
    assert journal.latest_thesis("oil", author="machine").id == "m1"


def test_save_thesis_is_an_upsert():
    t1 = make_thesis(thesis_id="same-id", side="long")
    journal.save_thesis(t1)
    t2 = make_thesis(thesis_id="same-id", side="short")
    journal.save_thesis(t2)
    rows = journal.list_theses("oil")
    assert len(rows) == 1
    assert rows[0].call.side == "short"


def test_different_desks_are_isolated():
    journal.save_thesis(make_thesis(desk_id="oil", thesis_id="a"))
    journal.save_thesis(make_thesis(desk_id="btc", thesis_id="b"))
    assert [t.id for t in journal.list_theses("oil")] == ["a"]
    assert [t.id for t in journal.list_theses("btc")] == ["b"]


def test_all_desk_ids_reflects_disk_state():
    assert journal.all_desk_ids() == []
    journal.save_thesis(make_thesis(desk_id="oil", thesis_id="a"))
    journal.save_thesis(make_thesis(desk_id="metals", thesis_id="b"))
    assert journal.all_desk_ids() == ["metals", "oil"]  # sorted


def test_save_thesis_uses_the_shared_atomic_writer(monkeypatch):
    """Structural proof this goes through pathiel.agents.atomic_io, not a
    naive open()/json.dump() — the thing SWARM_RULES/README explicitly ask
    for ('reuse pathiel/agents/atomic_io.py')."""
    calls = []
    import services.strategies.journal as journal_mod
    real_write = journal_mod.write_json_atomic

    def spy(path, data, **kwargs):
        calls.append(path)
        return real_write(path, data, **kwargs)

    monkeypatch.setattr(journal_mod, "write_json_atomic", spy)
    journal.save_thesis(make_thesis(thesis_id="spy-me"))
    assert len(calls) == 1
    assert calls[0] == journal.thesis_path("oil", "spy-me")


def test_save_thesis_fully_replaces_a_corrupt_existing_file():
    """Atomicity in practice: pre-seed a half-written/corrupt file at the
    destination path, then save over it. The reader must see a fully valid
    thesis, never a merge of old garbage and new content."""
    path = journal.thesis_path("oil", "was-corrupt")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write('{"not": "valid json for a Thesis, and not even closed')

    journal.save_thesis(make_thesis(thesis_id="was-corrupt"))

    with open(path) as fh:
        raw = json.load(fh)  # would raise if the write wasn't a clean full replace
    assert raw["id"] == "was-corrupt"


def test_state_dir_resolved_at_call_time(monkeypatch, tmp_path):
    """journal.py must never freeze PATHIEL_STATE_DIR at import time — a
    second temp dir set mid-test has to take effect immediately."""
    other = tmp_path / "other_state"
    other.mkdir()
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(other))
    journal.save_thesis(make_thesis(thesis_id="in-other-dir"))
    assert os.path.exists(os.path.join(str(other), "strategies", "oil", "theses", "in-other-dir.json"))
