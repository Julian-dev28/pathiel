"""services/strategies/evals/grading_lag_check.py: the check that fails when a
call older than its horizon is still pending, or when the sweep stopped
mid-journal. Pure functions on raw thesis dicts plus one end-to-end run on
a tmp state dir."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from services.strategies.evals import grading_lag_check as C

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)


def _t(thesis_id, created, *, horizon_days=7, grade=None):
    return {"id": thesis_id, "created_at": created.isoformat(), "grade": grade,
            "expires_at": (created + timedelta(days=horizon_days)).isoformat()}


def _g(at):
    return {"graded_at": at.isoformat(), "result": "loss"}


def test_a_call_past_its_horizon_without_a_grade_is_stale():
    rows = {"oil": [_t("old", NOW - timedelta(days=8)),                 # horizon passed a day ago
                    _t("open", NOW - timedelta(days=1))]}              # inside its horizon: fine
    stale = C.find_stale(rows, now=NOW)
    assert [r["id"] for r in stale] == ["old"]
    assert stale[0]["overdue_hours"] == 24.0


def test_grace_covers_the_sweep_interval():
    """Expired 30 minutes ago, the hourly sweep has not run yet: not stale."""
    rows = {"oil": [_t("just", NOW - timedelta(days=7, minutes=30))]}
    assert C.find_stale(rows, now=NOW) == []
    assert [r["id"] for r in C.find_stale(rows, now=NOW, grace=timedelta(0))] == ["just"]


def test_a_graded_call_is_never_stale():
    rows = {"oil": [_t("done", NOW - timedelta(days=9), grade=_g(NOW - timedelta(days=2)))]}
    assert C.find_stale(rows, now=NOW) == []


def test_lagging_means_the_grader_stopped_while_older_calls_wait():
    """A newer call is graded, an older one is not, and nothing has been
    written for hours: the sweep stopped. One graded hour ago is fine."""
    rows = {"oil": [_t("older", NOW - timedelta(days=3)),
                    _t("newer", NOW - timedelta(days=2), grade=_g(NOW - timedelta(hours=5)))]}
    assert [r["id"] for r in C.find_lagging(rows, now=NOW)] == ["older"]
    fresh = {"oil": [_t("older", NOW - timedelta(days=3)),
                     _t("newer", NOW - timedelta(days=2), grade=_g(NOW - timedelta(hours=1)))]}
    assert C.find_lagging(fresh, now=NOW) == []


def test_an_unresolved_call_newer_than_every_grade_is_not_lagging():
    """The normal picture: the latest call is open, everything before it graded."""
    rows = {"oil": [_t("a", NOW - timedelta(days=3), grade=_g(NOW - timedelta(days=1))),
                    _t("b", NOW - timedelta(hours=6))]}
    assert C.find_lagging(rows, now=NOW) == []


def test_run_end_to_end_on_a_state_dir(tmp_path):
    d = tmp_path / "strategies" / "oil" / "theses"
    d.mkdir(parents=True)
    (d / "old.json").write_text(json.dumps(_t("old", NOW - timedelta(days=10))))
    (d / "ok.json").write_text(json.dumps(_t("ok", NOW - timedelta(hours=3))))
    (d / "junk.json").write_text("{not json")
    report = C.run(str(tmp_path), now=NOW)
    assert report["ok"] is False
    assert report["theses"] == 2 and report["pending"] == 2
    assert [r["id"] for r in report["stale"]] == ["old"]
    assert C.main(["--state-dir", str(tmp_path)]) == 1

    (d / "old.json").write_text(json.dumps(_t("old", NOW - timedelta(days=10), grade=_g(NOW - timedelta(days=3)))))
    assert C.run(str(tmp_path), now=NOW)["ok"] is True
    assert C.main(["--state-dir", str(tmp_path), "--json"]) == 0


def test_missing_state_dir_is_ok_and_empty(tmp_path):
    report = C.run(str(tmp_path / "nowhere"), now=NOW)
    assert report == {"ok": True, "theses": 0, "pending": 0, "stale": [], "lagging": [], "as_of": NOW.isoformat()}


def test_main_needs_a_state_dir(monkeypatch):
    monkeypatch.delenv("PATHIEL_STATE_DIR", raising=False)
    assert C.main([]) == 2
