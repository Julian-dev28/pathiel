"""The cadence: /desk-loops schedules the daily review and runs it after a
close; /review-trades runs the CLI, flags only what is new, and never
touches live config."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LOOPS = ROOT / ".claude" / "skills" / "desk-loops" / "SKILL.md"
REVIEW = ROOT / ".claude" / "skills" / "review-trades" / "SKILL.md"


def test_desk_loops_schedules_the_daily_review_and_keeps_the_tick_crons():
    text = LOOPS.read_text()
    assert "`7 0 * * *`, prompt `/review-trades`" in text
    assert "`3,18,33,48 * * * *`, prompt `/desk-tick oil`" in text        # tests/test_plugin.py's strings
    assert "`10,25,40,55 * * * *`, prompt `/desk-tick metals`" in text
    assert "run `/review-trades` once" in text and "action=CLOSED" in text


def test_review_skill_runs_the_cli_and_flags_only_new_things():
    text = REVIEW.read_text()
    assert text.startswith("---\nname: review-trades\n")
    assert "python3 -m services.review run" in text
    assert "/tmp/oil-loop/progress.log" in text and "`REVIEW <utc>" in text
    assert "new_leaks" in text and "new_proposals" in text


def test_review_skill_never_changes_live_config():
    text = REVIEW.read_text()
    assert "backtested on the operator's OK" in text
    assert "Never edit live config" in text
    assert "Never feed the report into a thesis prompt" in text
