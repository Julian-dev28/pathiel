"""The review-trades skill hands a new LEAK to the operator as a yes/no the
same day (operator, 2026-11-04: "aren't we grading and growing"). Before,
every finding waited on a backtest and nothing changed for days."""
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent / ".claude" / "skills" / "review-trades" / "SKILL.md"


def test_a_new_leak_is_pushed_as_one_yes_no():
    text = SKILL.read_text()
    assert "PushNotification" in text
    assert "Turn on <lever>? yes/no" in text
    assert "Ask once per new leak" in text


def test_a_leak_skips_the_backtest_and_the_review_still_only_reads():
    text = SKILL.read_text()
    assert "A LEAK (10+\n  trades, labeled LEAK) skips the backtest" in text
    assert "The review itself still changes nothing" in text
    assert "Never edit live config" in text
