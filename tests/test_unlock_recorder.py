"""Gate tests for the zero-capital unlock recorder (W-U lane)."""
import json
import time

import pytest

from hermes_trader.agents import unlock_recorder as ur

_NOW = int(time.time() * 1000)
_H = ur._HOUR_MS
_D = ur._DAY_MS


@pytest.fixture(autouse=True)
def _iso_state(tmp_path, monkeypatch):
    monkeypatch.setattr(ur, "_STATE_FILE", str(tmp_path / "state.json"))
    # never hit the network from tests
    monkeypatch.setattr(ur, "_refresh_calendar",
                        lambda state, min_pct, refresh_hours, now_ms: state)


def _captured(monkeypatch):
    out = []
    monkeypatch.setattr(ur.shadow_ledger, "record",
                        lambda book, **kw: out.append((book, kw)) or {})
    return out


def _state_with(events, seen=None):
    return {"upcoming": events, "seen": seen or {}, "fetched_at_ms": _NOW}


def _uni(coin="ARB", px=1.0):
    return [{"coin": coin, "midPx": px, "dayNtlVlm": 1e7}]


def test_outside_windows_records_nothing(monkeypatch):
    out = _captured(monkeypatch)
    ur._save_state(_state_with([
        {"coin": "GAP", "t_ms": _NOW + 30 * _H, "pct": 5.0},     # 24-48h dead zone
        {"coin": "FAR", "t_ms": _NOW + 80 * _H, "pct": 5.0},     # beyond run-in window
        {"coin": "PAST", "t_ms": _NOW - 1 * _H, "pct": 5.0},     # already unlocked
    ]))
    assert ur.maybe_record(_uni("GAP") + _uni("FAR") + _uni("PAST"), {}) == 0
    assert out == []


def test_runin_arm_records_at_three_days_out(monkeypatch):
    out = _captured(monkeypatch)
    ur._save_state(_state_with([{"coin": "ARB", "t_ms": _NOW + 60 * _H, "pct": 4.0}]))
    assert ur.maybe_record(_uni("ARB", 1.1), {}) == 1
    book, kw = out[0]
    assert book == "unlock_short_runin" and kw["side"] == "short"
    assert kw["horizon_days"] == 3.0
    assert 59.9 <= kw["meta"]["hours_to_unlock"] <= 60.1


def test_event_dedup_and_seen_persistence(monkeypatch):
    """60h out — inside the surviving 48-72h run-in arm. The 0-24h
    `unlock_short` arm was removed 2026-08-30: it graded VALIDATED and had no
    capital path, which is the shape "nothing should be a recorder" rules out."""
    out = _captured(monkeypatch)
    ur._save_state(_state_with([{"coin": "ARB", "t_ms": _NOW + 60 * _H, "pct": 2.5}]))
    assert ur.maybe_record(_uni("ARB"), {}) == 1
    assert ur.maybe_record(_uni("ARB"), {}) == 0  # same event, second scan
    assert len(out) == 1
    seen = ur._load_state()["seen"]
    assert list(seen) == [f"unlock_short_runin:ARB:{(_NOW + 60 * _H) // _D}"]


def test_no_mid_skips_without_marking_seen(monkeypatch):
    out = _captured(monkeypatch)
    ur._save_state(_state_with([{"coin": "GONE", "t_ms": _NOW + 60 * _H, "pct": 2.0}]))
    assert ur.maybe_record(_uni("OTHER"), {}) == 0
    assert out == []
    # not marked seen -> a later scan with a mid can still record
    assert ur.maybe_record(_uni("GONE", 3.0), {}) == 1


def test_hot_kill(monkeypatch):
    out = _captured(monkeypatch)
    ur._save_state(_state_with([{"coin": "ARB", "t_ms": _NOW + 6 * _H, "pct": 2.5}]))
    assert ur.maybe_record(_uni("ARB"), {"unlock_recorder": {"enabled": False}}) == 0
    assert out == []


def test_extract_upcoming_merges_same_day_and_filters():
    # Day-anchored so the +1h sibling can never cross a UTC day boundary
    # (a wall-clock-dependent flake caught live on 2026-07-11 at 23:47 UTC).
    ev_base = (_NOW // _D + 5) * _D + 6 * _H
    idx = {"data": [
        {"name": "Arbitrum", "circSupply": 1000.0, "events": [
            {"timestamp": ev_base / 1000, "noOfTokens": [6.0]},
            {"timestamp": (ev_base + _H) / 1000, "noOfTokens": [7.0]},        # same day -> merged
            {"timestamp": (_NOW - 2 * _D) / 1000, "noOfTokens": [50.0]},      # past -> dropped
            {"timestamp": (_NOW + 90 * _D) / 1000, "noOfTokens": [90.0]},     # beyond 45d -> dropped
        ]},
        {"name": "Unmapped Proto", "circSupply": 100.0, "events": [
            {"timestamp": (_NOW + 3 * _D) / 1000, "noOfTokens": [50.0]},
        ]},
    ]}
    ev = ur._extract_upcoming(idx, {"Arbitrum": "ARB"}, min_pct=1.0, now_ms=_NOW)
    assert len(ev) == 1
    assert ev[0]["coin"] == "ARB" and round(ev[0]["pct"], 1) == 1.3


def test_refresh_failure_keeps_stale_calendar(monkeypatch, tmp_path):
    monkeypatch.undo()  # need the real _refresh_calendar
    monkeypatch.setattr(ur, "_STATE_FILE", str(tmp_path / "s.json"))

    def boom(url, timeout):
        raise OSError("bucket down")

    monkeypatch.setattr(ur.urllib.request, "urlopen", boom)
    stale = {"upcoming": [{"coin": "ARB", "t_ms": _NOW + 6 * _H, "pct": 2.0}],
             "fetched_at_ms": _NOW - 100 * _H}
    state = ur._refresh_calendar(stale, min_pct=1.0, refresh_hours=12.0, now_ms=_NOW)
    assert state["upcoming"][0]["coin"] == "ARB"      # kept stale events
    assert state["fetched_at_ms"] == _NOW             # backed off


def test_shipped_map_is_valid_and_nonempty():
    m = ur._load_map()
    assert len(m) >= 50
    assert m.get("Celestia") == "TIA"


def test_the_capital_less_arm_is_gone():
    """`unlock_short` (0-24h) recorded forever and could never trade — the
    grader's own note was "validated but has no bounded capital path". Its
    signal is covered by unlock_short_runin, which trades it and graded
    VALIDATED at n=14, +3.75% net25, mc_p=0.0375."""
    assert [a[0] for a in ur._ARMS] == ["unlock_short_runin"]
