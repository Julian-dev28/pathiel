"""End to end, offline: `python3 -m services.review run --offline BUNDLE` on
the saved 2026-11-02 bundle. Ledger, report, summary line, idempotency,
"new" detection, and the report's words."""
from __future__ import annotations

import json
import re

from services.review import env
from services.review import ledger as LG
from services.review.__main__ import main
from services.review.tests.conftest import BUNDLE

BANNED = ("robust", "crucial", "comprehensive", "significant", "delve", "landscape", "furthermore",
          "moreover", "pivotal", "tapestry", "underscore", "foster", "showcase", "intricate", "vibrant",
          "fundamental", "nuanced", "multifaceted", "interplay")


def _run(tmp_path, capsys, *extra):
    rc = main(["run", "--offline", BUNDLE, "--report-dir", str(tmp_path / "rep"), *extra])
    assert rc == 0
    return capsys.readouterr().out.strip().splitlines()[-1]


def test_offline_run_writes_ledger_report_and_one_summary_line(tmp_path, capsys, state_dir):
    line = _run(tmp_path, capsys)
    assert line.startswith("REVIEW 2026-11-02T09:40Z trades=10 net=+31.49 ")
    assert "edge=0 leak=0" in line and "new_leaks=none" in line
    assert re.search(r"proposals=\d+ new_proposals=\d+", line)
    ledger = state_dir / "review" / "ledger.jsonl"
    assert ledger.exists() and env.ledger_path() == str(ledger)
    assert len(LG.ledger_records(str(ledger))) == 10
    report = (tmp_path / "rep" / "REVIEW_2026-11-02.md").read_text()
    assert report.startswith("# Trade review, 2026-11-02\n\n**No edge and no leak yet.**")
    assert "n is small" in report and "never edits live config" in report


def test_second_run_appends_nothing_and_nothing_is_new(tmp_path, capsys, state_dir):
    _run(tmp_path, capsys)
    ledger = state_dir / "review" / "ledger.jsonl"
    before = ledger.read_text()
    line = _run(tmp_path, capsys)
    assert ledger.read_text() == before
    assert "new_proposals=0" in line and "new_leaks=none" in line


def test_since_narrows_the_window(tmp_path, capsys):
    line = _run(tmp_path, capsys, "--since", "2026-10-31")
    assert " trades=4 " in line


def test_a_new_leak_is_named_once(tmp_path, capsys, state_dir, monkeypatch):
    """A slice that turns LEAK is named on the run it turns, not after; its
    proposal is new once too. (The real fixture has no LEAK at n<=10, so
    one is forced here.)"""
    from services.review import review

    real = review.A.slice_table

    def forced(recs, seed):
        rows = real(recs, seed)
        for r in rows:
            if r["key"] == "cause=size":
                r["label"] = "LEAK"
        return rows

    monkeypatch.setattr(review.A, "slice_table", forced)
    first = _run(tmp_path, capsys)
    assert "new_leaks=cause=size" in first and "new_proposals=0" not in first
    body = json.loads((state_dir / "review" / "last_run.json").read_text())
    assert body["labels"]["cause=size"] == "LEAK" and "risk_cap_frac@cause=size" in body["proposals"]
    second = _run(tmp_path, capsys)
    assert "new_leaks=none" in second and "new_proposals=0" in second


def test_the_report_reads_plain(tmp_path, capsys):
    _run(tmp_path, capsys)
    report = (tmp_path / "rep" / "REVIEW_2026-11-02.md").read_text()
    assert "—" not in report and "–" not in report          # no em or en dashes
    low = report.lower()
    assert not [w for w in BANNED if re.search(rf"\b{w}", low)]
    assert "| cause=wrong_direction | 1 |" in report and "| NOISE |" in report
