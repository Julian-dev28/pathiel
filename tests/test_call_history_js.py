"""pathiel/static/call_history.js under node: the Result column's words and
the folding of restated calls, run against the real file so the page and
the test cannot drift apart.

Regression, 2026-11-13: every call since 11-06 on the Desk page read
"pending", and one call written four times in a night took four rows. The
grader now writes Grade.result the hour a call resolves; this file is what
turns that into the table. Skipped (not failed) where node is not
installed; the gate machine has it (/opt/homebrew/bin/node).
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "pathiel" / "static" / "call_history.js"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")


def _run(fn: str, *args) -> object:
    script = (
        f"const m = require({json.dumps(str(JS))});"
        f"process.stdout.write(JSON.stringify(m.{fn}(...{json.dumps(list(args))})));"
    )
    out = subprocess.run([NODE, "-e", script], check=True, capture_output=True, text=True, timeout=20)
    return json.loads(out.stdout) if out.stdout else None


def _row(thesis_id, created, *, author="machine", side="long", entry=89.97, stop=87.58, targets=(97.667,),
         one_liner="Still long Brent on a dip to 89.97", grade=None):
    return {"id": thesis_id, "author": author, "created_at": created, "grade": grade,
            "call": {"side": side, "instrument": "xyz:BRENTOIL", "entry_px": entry, "stop_px": stop,
                     "targets": list(targets), "one_liner": one_liner, "confidence": 0.6}}


# ── the Result column ────────────────────────────────────────────────────────

@pytest.mark.parametrize("grade, word, cls, shows_ret", [
    (None, "pending", "grade-pending", False),
    ({"result": "win", "direction_hit": True, "ret_pct": 0.02}, "win", "grade-hit", True),
    ({"result": "loss", "direction_hit": False, "ret_pct": -0.016}, "loss", "grade-miss", True),
    ({"result": "expired", "direction_hit": True, "ret_pct": 0.004}, "expired", "grade-expired", True),
    ({"result": "untriggered", "direction_hit": None, "ret_pct": 0.0}, "untriggered", "grade-untriggered", False),
    # grades written before Grade.result existed keep the old direction read
    ({"direction_hit": True, "ret_pct": 0.05}, "hit", "grade-hit", True),
    ({"direction_hit": False, "ret_pct": -0.05}, "miss", "grade-miss", True),
    ({"direction_hit": None, "ret_pct": 0.0}, "graded", "", True),
])
def test_grade_result_word_class_and_return(grade, word, cls, shows_ret):
    assert _run("gradeResult", grade) == word
    assert _run("gradeClass", grade) == cls
    assert _run("gradeShowsReturn", grade) is shows_ret


# ── folding restated calls ───────────────────────────────────────────────────

def test_consecutive_restatements_fold_into_the_first_call():
    """The real 11-11 run: 01:35, 02:16, 02:47, 03:20, 03:50 all "Still long
    Brent on a dip to 89.97, stop 87.58, target 97.667". One row, the
    earliest, restated four times, last at 03:50."""
    history = [  # newest first, as DeskBrief.history arrives
        _row("oil-20261111T0350-m", "2026-11-11T03:50:00Z"),
        _row("oil-20261111T0320-m", "2026-11-11T03:20:00Z"),
        _row("oil-20261111T0247-m", "2026-11-11T02:47:00Z"),
        _row("oil-20261111T0216-m", "2026-11-11T02:16:00Z"),
        _row("oil-20261111T0135-m", "2026-11-11T01:35:00Z", one_liner="Long Brent on a dip to 89.97"),
    ]
    groups = _run("collapseCallHistory", history)
    assert len(groups) == 1
    g = groups[0]
    assert g["thesis"]["id"] == "oil-20261111T0135-m"
    assert g["thesis"]["call"]["one_liner"] == "Long Brent on a dip to 89.97"
    assert g["repeats"] == 5
    assert g["last_restated_at"] == "2026-11-11T03:50:00Z"
    assert g["ids"] == [r["id"] for r in history]


def test_a_changed_entry_stop_or_target_starts_a_new_row():
    history = [
        _row("d", "2026-11-12T03:50:00Z", entry=90.0, stop=89.29, targets=(91.714, 92.978)),
        _row("c", "2026-11-12T03:07:00Z", entry=90.0, stop=89.29, targets=(91.714, 92.978)),
        _row("b", "2026-11-12T02:37:00Z", entry=90.2, stop=89.29, targets=(91.754, 92.978)),
        _row("a", "2026-11-12T02:04:00Z", entry=90.2, stop=89.29, targets=(91.754, 92.978)),
    ]
    groups = _run("collapseCallHistory", history)
    assert [(g["thesis"]["id"], g["repeats"]) for g in groups] == [("c", 2), ("a", 2)]


def test_a_user_call_never_folds_into_the_machines_identical_call():
    history = [
        _row("u1", "2026-11-11T02:00:00Z", author="user"),
        _row("m1", "2026-11-11T01:35:00Z"),
    ]
    groups = _run("collapseCallHistory", history)
    assert [g["thesis"]["id"] for g in groups] == ["u1", "m1"]
    assert all(g["repeats"] == 1 and g["last_restated_at"] is None for g in groups)


def test_non_consecutive_repeats_stay_apart():
    """Same call, a different call in between: two rows. The table reads in
    time order; folding across a different call would hide the flip."""
    history = [
        _row("c", "2026-11-11T03:00:00Z"),
        _row("b", "2026-11-11T02:00:00Z", side="short", entry=92.0, stop=93.0, targets=(90.0,)),
        _row("a", "2026-11-11T01:00:00Z"),
    ]
    assert [g["thesis"]["id"] for g in _run("collapseCallHistory", history)] == ["c", "b", "a"]


def test_empty_and_missing_history_fold_to_nothing():
    assert _run("collapseCallHistory", []) == []
    assert _run("collapseCallHistory", None) == []


# ── wiring ───────────────────────────────────────────────────────────────────

def test_desks_js_uses_the_helpers_and_the_pages_load_them_first():
    js = (ROOT / "pathiel" / "static" / "desks.js").read_text()
    for name in ("collapseCallHistory(", "gradeResult(", "gradeClass(", "gradeShowsReturn("):
        assert name in js, name
    assert "grade-pending\">pending" not in js        # the word now comes from call_history.js
    for tpl in ("desk.html", "oil_desk.html"):
        html = (ROOT / "pathiel" / "templates" / tpl).read_text()
        assert html.index("/static/call_history.js") < html.index("/static/desks.js"), tpl
    css = (ROOT / "pathiel" / "static" / "desks.css").read_text()
    for cls in (".grade-expired", ".grade-untriggered", ".repeat-chip"):
        assert cls in css, cls
