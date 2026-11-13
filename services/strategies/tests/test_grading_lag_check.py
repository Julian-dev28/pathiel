"""services/strategies/evals/grading_lag_check.py: the check that fails when a
call older than its horizon is still pending, when the grader's heartbeat
says the sweep stopped, or when the sweep skipped a pending call. Pure
functions on raw thesis dicts, one regression on the real 11-13 journal
against the real Brent tape, and one end-to-end run on a tmp state dir."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

from pathiel.models.types import Candle
from services.strategies import grader as G
from services.strategies import journal
from services.strategies.evals import grading_lag_check as C
from services.strategies.tests.conftest import make_thesis

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)


def _t(thesis_id, created, *, horizon_days=7, grade=None):
    return {"id": thesis_id, "created_at": created.isoformat(), "grade": grade,
            "expires_at": (created + timedelta(days=horizon_days)).isoformat()}


def _g(at):
    return {"graded_at": at.isoformat(), "result": "loss"}


def _sweep(at, open_rows=()):
    return {"swept_at": at.isoformat(), "graded": 0, "regrade": False, "open": list(open_rows)}


# ── stale: horizon passed, no grade ────────────────────────────────────────

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


# ── sweep: the heartbeat says whether the grader is alive ─────────────────

def test_a_fresh_heartbeat_is_a_live_sweep():
    s = C.sweep_status(_sweep(NOW - timedelta(minutes=50)), now=NOW, pending=6)
    assert s["ok"] is True and s["age_hours"] == 0.8 and s["reason"] is None


def test_an_old_heartbeat_with_calls_pending_is_a_stopped_sweep():
    s = C.sweep_status(_sweep(NOW - timedelta(hours=5)), now=NOW, pending=1)
    assert s["ok"] is False and s["age_hours"] == 5.0 and "stopped" in s["reason"]
    # exactly at the grace edge still counts as alive; one second past does not
    assert C.sweep_status(_sweep(NOW - timedelta(hours=2)), now=NOW, pending=1)["ok"] is True
    assert C.sweep_status(_sweep(NOW - timedelta(hours=2, seconds=1)), now=NOW, pending=1)["ok"] is False


def test_no_heartbeat_with_calls_pending_fails_and_names_the_restart():
    s = C.sweep_status(None, now=NOW, pending=3)
    assert s["ok"] is False and s["swept_at"] is None and "pre-heartbeat" in s["reason"]


def test_the_heartbeat_is_not_required_when_nothing_is_pending():
    assert C.sweep_status(None, now=NOW, pending=0)["ok"] is True
    assert C.sweep_status(_sweep(NOW - timedelta(days=3)), now=NOW, pending=0)["ok"] is True


# ── missed: a pending call the sweep never read ───────────────────────────

def test_a_pending_call_absent_from_the_sweep_s_open_list_is_missed():
    swept = NOW - timedelta(minutes=30)
    rows = {"oil": [_t("seen", NOW - timedelta(days=1)),
                    _t("skipped", NOW - timedelta(days=2)),
                    _t("brand-new", swept - timedelta(minutes=5))],       # inside grace of the sweep: fine
            "metals": [_t("graded", NOW - timedelta(days=3), grade=_g(swept))]}
    sweep = _sweep(swept, [{"desk": "oil", "id": "seen", "note": "open"}])
    assert [r["id"] for r in C.find_missed(rows, sweep)] == ["skipped"]


def test_missed_needs_a_heartbeat_to_compare_against():
    rows = {"oil": [_t("a", NOW - timedelta(days=2))]}
    assert C.find_missed(rows, None) == []


# ── regression: a quiet journal with a live sweep is OK ───────────────────
#
# 2026-11-13: the check reported "lagging=6" for the six open Brent longs
# below while the dashboard server's hourly sweep logged "0 theses graded"
# each hour. The sweep was right: against the real 1h tape the T2150 limit
# at 89.40 never filled (lowest low after it 89.484) and the five 89.97
# limits filled 11-12 01:00 but Brent ran 89.5 to 94.6, touching neither
# the 87.58 stop nor the 97.667 target. The old rule ("no grade written in
# 2h while a newer call is graded" = stopped grader) cannot tell that from
# a stopped sweep. The heartbeat can. The fixture is the real tape; the
# calls are the real oil-desk rows (entry / stop / targets as journaled).

_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "brent_1h_ohlc_2026-11-10_to_11-13.json")
with open(_FIXTURE) as _fh:
    BRENT_1H = [Candle(t=r[0], o=r[1], h=r[2], l=r[3], c=r[4], v=r[5]) for r in json.load(_fh)["rows"]]


def _brent(coin, interval, count):
    assert coin == "xyz:BRENTOIL" and interval == "1h"
    return BRENT_1H


def _real_call(thesis_id, created, entry, stop, targets):
    return make_thesis(desk_id="oil", thesis_id=thesis_id, created_at=datetime(*created, tzinfo=timezone.utc),
                       horizon_days=7, side="long", instrument="xyz:BRENTOIL",
                       entry_px=entry, stop_px=stop, targets=targets, scenarios=[])


REAL_JOURNAL_11_13 = [
    _real_call("oil-20261110T1555-m", (2026, 11, 10, 15, 55), 89.4, 87.58, [94.278, 98.43]),   # win 11-13 06:00
    _real_call("oil-20261110T2150-m", (2026, 11, 10, 21, 50), 89.4, 87.58, [94.278]),          # never filled
    _real_call("oil-20261111T0056-m", (2026, 11, 11, 0, 56), 89.9, 88.88, [91.857, 97.667]),   # win 11-12 13:00
    _real_call("oil-20261111T0135-m", (2026, 11, 11, 1, 35), 89.97, 87.58, [97.667]),          # open
    _real_call("oil-20261111T0216-m", (2026, 11, 11, 2, 16), 89.97, 87.58, [97.667]),          # open
    _real_call("oil-20261111T0247-m", (2026, 11, 11, 2, 47), 89.97, 87.58, [97.667]),          # open
    _real_call("oil-20261111T0320-m", (2026, 11, 11, 3, 20), 89.97, 87.58, [97.667]),          # open
    _real_call("oil-20261111T0350-m", (2026, 11, 11, 3, 50), 89.97, 87.58, [97.667]),          # open
    _real_call("oil-20261112T1020-m", (2026, 11, 12, 10, 20), 90.025, 88.98, [91.852, 92.978]),  # win 11-12 20:00
]
OPEN_SIX = ["oil-20261110T2150-m", "oil-20261111T0135-m", "oil-20261111T0216-m",
            "oil-20261111T0247-m", "oil-20261111T0320-m", "oil-20261111T0350-m"]
SWEEP_1 = datetime(2026, 11, 13, 6, 53, tzinfo=timezone.utc)    # the sweep that graded T1555's win
SWEEP_2 = SWEEP_1 + timedelta(hours=3)                           # three quiet hours later
CHECK_AT = SWEEP_2 + timedelta(minutes=10)


def test_the_tape_proves_the_six_calls_are_open():
    """The grader's view of the six, hour by hour, on the real bars."""
    after_2150 = [b for b in BRENT_1H if b.t >= int(datetime(2026, 11, 10, 22, 0, tzinfo=timezone.utc).timestamp() * 1000)]
    assert min(b.l for b in after_2150) == 89.484          # the 89.40 limit never fills
    assert min(b.l for b in BRENT_1H) > 87.58               # the stop is never touched
    assert max(b.h for b in BRENT_1H) == 94.569             # the 97.667 target is never touched
    for thesis in REAL_JOURNAL_11_13:
        if thesis.id not in OPEN_SIX:
            continue
        g = G.grade_thesis(thesis, hourly_fetcher=_brent, now=CHECK_AT)
        assert g.result is None and thesis.expires_at > CHECK_AT, thesis.id
        if thesis.id == "oil-20261110T2150-m":
            assert g.notes == ["entry not filled yet"] and g.triggered_at is None
        else:
            assert g.notes == ["open: neither stop nor target touched yet"]
            assert g.triggered_at == datetime(2026, 11, 12, 1, 0, tzinfo=timezone.utc)


def test_six_open_calls_and_a_live_sweep_is_grading_ok_regression(state_dir):
    """Before the fix this reported lagging=6 / FAIL on exactly this
    journal: the last grade was three hours old and a newer call than the
    six was graded. Now the sweep's heartbeat is the evidence, and a quiet
    journal behind a live sweep is OK."""
    for thesis in REAL_JOURNAL_11_13:
        journal.save_thesis(thesis)

    assert G.grade_all_due(hourly_fetcher=_brent, now=SWEEP_1) == 3
    results = {t.id: (t.grade.result if t.grade else None) for t in journal.list_theses("oil")}
    assert results["oil-20261110T1555-m"] == "win" and results["oil-20261111T0056-m"] == "win"
    assert results["oil-20261112T1020-m"] == "win"
    assert [i for i in OPEN_SIX if results[i] is None] == OPEN_SIX

    assert G.grade_all_due(hourly_fetcher=_brent, now=SWEEP_2) == 0        # the quiet hour: nothing resolved
    heartbeat = journal.load_sweep()
    assert heartbeat["swept_at"] == SWEEP_2.isoformat() and heartbeat["graded"] == 0
    assert sorted(o["id"] for o in heartbeat["open"]) == OPEN_SIX

    report = C.run(state_dir, now=CHECK_AT)
    assert report["ok"] is True, report
    assert report["theses"] == 9 and report["pending"] == 6
    assert report["stale"] == [] and report["missed"] == []
    assert report["sweep"] == {"ok": True, "swept_at": SWEEP_2.isoformat(), "age_hours": 0.2, "reason": None}
    assert C.main(["--state-dir", state_dir, "--as-of", CHECK_AT.isoformat()]) == 0

    # the same journal with the sweep dead since SWEEP_2: FAIL, and the reason says so
    late = C.run(state_dir, now=SWEEP_2 + timedelta(hours=4))
    assert late["ok"] is False and late["sweep"]["ok"] is False and late["sweep"]["age_hours"] == 4.0
    assert late["stale"] == [] and late["missed"] == []
    assert C.main(["--state-dir", state_dir, "--as-of", (SWEEP_2 + timedelta(hours=4)).isoformat()]) == 1


# ── end to end ─────────────────────────────────────────────────────────────

def test_run_end_to_end_on_a_state_dir(tmp_path, capsys):
    d = tmp_path / "strategies" / "oil" / "theses"
    d.mkdir(parents=True)
    (d / "old.json").write_text(json.dumps(_t("old", NOW - timedelta(days=10))))
    (d / "ok.json").write_text(json.dumps(_t("ok", NOW - timedelta(hours=3))))
    (d / "junk.json").write_text("{not json")
    report = C.run(str(tmp_path), now=NOW)
    assert report["ok"] is False
    assert report["theses"] == 2 and report["pending"] == 2
    assert [r["id"] for r in report["stale"]] == ["old"]
    assert report["sweep"]["ok"] is False and report["sweep"]["swept_at"] is None     # no heartbeat yet
    assert C.main(["--state-dir", str(tmp_path), "--as-of", NOW.isoformat()]) == 1
    out = capsys.readouterr().out
    assert out.startswith("GRADING FAIL theses=2 pending=2 stale=1 missed=0 sweep=none STOPPED")
    assert "  stale   oil old" in out and "  sweep   no heartbeat" in out

    (d / "old.json").write_text(json.dumps(_t("old", NOW - timedelta(days=10), grade=_g(NOW - timedelta(days=3)))))
    (tmp_path / "strategies" / "grade_sweep.json").write_text(json.dumps(
        _sweep(NOW - timedelta(minutes=20), [{"desk": "oil", "id": "ok", "note": "open"}])))
    assert C.run(str(tmp_path), now=NOW)["ok"] is True
    assert C.main(["--state-dir", str(tmp_path), "--json", "--as-of", NOW.isoformat()]) == 0
    assert json.loads(capsys.readouterr().out)["sweep"]["age_hours"] == 0.3


def test_a_sweep_that_skipped_a_pending_call_fails_as_missed(tmp_path):
    d = tmp_path / "strategies" / "oil" / "theses"
    d.mkdir(parents=True)
    (d / "a.json").write_text(json.dumps(_t("a", NOW - timedelta(days=2))))
    (tmp_path / "strategies" / "grade_sweep.json").write_text(json.dumps(_sweep(NOW - timedelta(minutes=20))))
    report = C.run(str(tmp_path), now=NOW)
    assert report["ok"] is False and report["sweep"]["ok"] is True
    assert [r["id"] for r in report["missed"]] == ["a"]


def test_a_corrupt_heartbeat_reads_as_no_heartbeat(tmp_path):
    (tmp_path / "strategies").mkdir()
    (tmp_path / "strategies" / "grade_sweep.json").write_text("{not json")
    assert C.load_sweep(str(tmp_path)) is None
    (tmp_path / "strategies" / "grade_sweep.json").write_text("[1, 2]")
    assert C.load_sweep(str(tmp_path)) is None


def test_missing_state_dir_is_ok_and_empty(tmp_path):
    report = C.run(str(tmp_path / "nowhere"), now=NOW)
    assert report == {"ok": True, "theses": 0, "pending": 0, "stale": [],
                      "sweep": {"ok": True, "swept_at": None, "age_hours": None, "reason": None},
                      "missed": [], "as_of": NOW.isoformat()}


def test_main_needs_a_state_dir(monkeypatch):
    monkeypatch.delenv("PATHIEL_STATE_DIR", raising=False)
    assert C.main([]) == 2
