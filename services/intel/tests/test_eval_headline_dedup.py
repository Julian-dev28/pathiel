"""The pure parts of services/intel/evals/eval_headline_dedup.py: the frozen
pre-fix legacy filter, the real-alert-row replay, and the two named
regressions. No network; the live-log path is exercised through a fixture
file under tmp_path, never /tmp/trigger-watch/."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from services.intel import headline_watch as HW
from services.intel.evals import eval_headline_dedup as EV

NOW = datetime(2026, 11, 17, 0, 0, tzinfo=timezone.utc)
NOW_MS = int(NOW.timestamp() * 1000)
MIN = 60_000


def _row(title, at_ms, published_ms=None, group="oil", label="iran/hormuz", source="Reuters", kind="news"):
    return {"group": group, "kind": kind, "label": label, "title": title, "source": source,
            "url": "https://x/" + str(abs(hash(title))), "at_ms": at_ms, "published_ms": published_ms}


# ── _legacy_news_alerts: the frozen pre-fix behavior ────────────────────

def test_legacy_takes_an_undated_item_as_fresh():
    """The actual root cause this eval exists to prove is fixed: the old
    code's age check only ever ran when published_at was present."""
    seen: set = set()
    item = {"title": "Iran seizes tanker in Hormuz", "published_at": None}
    assert _fires(EV._legacy_news_alerts, "oil", item, seen, NOW_MS)


def test_legacy_still_filters_a_dated_stale_item():
    seen: set = set()
    old = {"title": "Iran seizes tanker in Hormuz", "published_at": NOW - timedelta(minutes=HW.MAX_AGE_MIN + 1)}
    assert not _fires(EV._legacy_news_alerts, "oil", old, seen, NOW_MS)


def test_legacy_never_dedupes_a_retitled_duplicate():
    seen: set = set()
    a = {"title": EV.DIRTY_BOMB_TITLE, "published_at": NOW - timedelta(minutes=3)}
    b = {"title": EV.DIRTY_BOMB_RETITLED, "published_at": NOW + timedelta(minutes=37)}
    assert _fires(EV._legacy_news_alerts, "oil", a, seen, NOW_MS)
    assert _fires(EV._legacy_news_alerts, "oil", b, seen, NOW_MS + 40 * MIN)   # no near-dup check at all


def _fires(fn, group, item, seen, now_ms):
    return bool(fn(group, [item], seen, now_ms))


# ── load_real_news_alerts / _as_item ────────────────────────────────────

def test_load_real_news_alerts_filters_kind_and_sorts(tmp_path):
    path = tmp_path / "alerts.json"
    rows = [_row("b", 200, kind="news"), _row("a", 100, kind="news"), _row("odds-one", 50, kind="odds")]
    path.write_text(json.dumps(rows))
    out = EV.load_real_news_alerts(str(path))
    assert [r["title"] for r in out] == ["a", "b"]           # odds dropped, sorted by at_ms


def test_load_real_news_alerts_tolerates_missing_or_broken_file(tmp_path):
    assert EV.load_real_news_alerts(str(tmp_path / "nope.json")) == []
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert EV.load_real_news_alerts(str(bad)) == []
    bad.write_text("[1, 2]")
    assert EV.load_real_news_alerts(str(bad)) == []


def test_as_item_reconstructs_published_at_from_published_ms():
    row = _row("Iran deal", NOW_MS, published_ms=NOW_MS - 5 * MIN)
    item = EV._as_item(row)
    assert item["title"] == "Iran deal" and item["published_at"] == NOW - timedelta(minutes=5)
    undated = EV._as_item(_row("Iran deal", NOW_MS, published_ms=None))
    assert undated["published_at"] is None


# ── replay_before / replay_after on a small synthetic log ───────────────

def test_replay_matches_legacy_vs_current_on_the_real_duplicate_pair(tmp_path):
    """A minimal synthetic log reproducing the shape of the real 2026-11-13
    Trump/Vance duplicate: BEFORE fires both, AFTER fires once."""
    rows = [
        _row("Trump directs Vance to shift focus from Iran war to U.S. corruption and crime",
             NOW_MS, published_ms=NOW_MS - 4 * MIN, label="trump/oil"),
        _row("President Donald Trump Directs Vice President JD Vance to Shift Focus From Iran War to Crime",
             NOW_MS + 76 * MIN, published_ms=NOW_MS + 64 * MIN, label="trump/oil"),
    ]
    before = EV.replay_before(rows)
    after = EV.replay_after(rows)
    assert len(before) == 2 and len(after) == 1
    assert after[0]["_src_title"] == rows[0]["title"]        # the first telling survives


def test_replay_never_drops_two_genuinely_different_same_day_stories():
    rows = [
        _row("OPEC, Allies Keep Output Steady While Gulf Shipping Remains Under Threat",
             NOW_MS, published_ms=NOW_MS - 8 * MIN, label="opec"),
        _row("U.S. military disables cargo vessel attempting to breach Iran blockade",
             NOW_MS + 57 * MIN, published_ms=NOW_MS + 55 * MIN, label="iran/hormuz"),
    ]
    assert len(EV.replay_before(rows)) == 2 and len(EV.replay_after(rows)) == 2


def test_eval_real_log_reports_pass_on_a_tmp_fixture(tmp_path, capsys):
    path = tmp_path / "headline_alerts.json"
    rows = [
        _row("Trump directs Vance to shift focus from Iran war to U.S. corruption and crime",
             NOW_MS, published_ms=NOW_MS - 4 * MIN, label="trump/oil"),
        _row("President Donald Trump Directs Vice President JD Vance to Shift Focus From Iran War to Crime",
             NOW_MS + 76 * MIN, published_ms=NOW_MS + 64 * MIN, label="trump/oil"),
    ]
    path.write_text(json.dumps(rows))
    assert EV.eval_real_log(str(path)) is True
    out = capsys.readouterr().out
    assert "2 real" in out and "1 fired AFTER" in out


def test_eval_real_log_skips_cleanly_on_an_empty_or_missing_log(tmp_path):
    assert EV.eval_real_log(str(tmp_path / "nope.json")) is True


def test_eval_real_log_fails_on_an_unexplained_drop(tmp_path, monkeypatch):
    """A drop that is NOT a near-duplicate of anything AFTER kept must fail
    the eval -- simulated by feeding replay_after a stub that silently
    drops a genuinely distinct story, the exact failure mode this eval
    exists to catch."""
    rows = [_row("Iran, US reach deal to reopen Strait of Hormuz", NOW_MS, published_ms=NOW_MS - 4 * MIN)]
    path = tmp_path / "alerts.json"
    path.write_text(json.dumps(rows))
    monkeypatch.setattr(EV, "replay_after", lambda rows: [])
    assert EV.eval_real_log(str(path)) is False


# ── part 2: the two named regressions, end to end ───────────────────────

def test_eval_named_regressions_passes():
    assert EV.eval_named_regressions() is True


def test_eval_named_regressions_catches_a_broken_age_fix(monkeypatch):
    """If the age fix regressed (an undated item taken as fresh again),
    this must fail, not silently pass."""
    monkeypatch.setattr(HW, "news_alerts", EV._legacy_news_alerts)
    assert EV.eval_named_regressions() is False


def test_main_exits_zero_against_a_clean_fixture(tmp_path, capsys):
    path = tmp_path / "alerts.json"
    path.write_text(json.dumps([_row("Iran, US reach deal to reopen Strait of Hormuz",
                                     NOW_MS, published_ms=NOW_MS - 4 * MIN)]))
    rc = EV.main(["--alerts", str(path)])
    assert rc == 0
    assert "ALL PASS" in capsys.readouterr().out
