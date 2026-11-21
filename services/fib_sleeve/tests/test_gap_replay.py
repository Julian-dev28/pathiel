"""Gate tests for the gap-replay eval's own machinery: the log parser, the
gap/day bucketing, the tag detector, the replay of `rule.plan_entry`
against injected candles, and the pass rule - all pure, all fed fakes, no
network, no real `/tmp/oil-loop/progress.log`. `evals/gap_replay.py` run
as `__main__` is what reads THAT file and REAL Hyperliquid candles; this
file proves the machinery it is built from is correct."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List

from pathiel.models.types import Candle
from services.fib_sleeve.config import SleeveConfig
from services.fib_sleeve.evals import gap_replay as G
from services.fib_sleeve.tests import _scenario as S

LEVEL, STOP, TARGET = 1018.658, 1015.3223, 1038.932   # test_tick.py's own constants, S.hours()'s up-swing


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def _write_log(tmp_path, lines: List[str]) -> str:
    p = tmp_path / "progress.log"
    p.write_text("\n".join(lines) + "\n")
    return str(p)


# ── parse_log ────────────────────────────────────────────────────────────

def test_parse_log_reads_tagged_and_untagged_lines_and_stops_at_STOPPED(tmp_path):
    lines = [
        "something unrelated that is not a FIB-TICK line",
        f"FIB-TICK {_iso(S.T0)} no_order level_spent",
        f"FIB-TICK {_iso(S.T0 + 60_000)} range24 cancel_entry swing_changed",
        f"FIB-TICK {_iso(S.T0 + 120_000)} STOPPED operator asked (funds moving back to main)",
        f"FIB-TICK {_iso(S.T0 + 180_000)} range24 resting the entry rests at the level the rule names",
    ]
    ticks = G.parse_log(_write_log(tmp_path, lines))
    assert [t.action for t in ticks] == ["no_order", "cancel_entry"]        # stops at STOPPED, before it's read
    assert ticks[0].why == "level_spent" and ticks[1].why == "swing_changed"
    assert ticks == sorted(ticks, key=lambda t: t.ts_ms)


def test_parse_log_is_fine_with_no_STOPPED_line_at_all(tmp_path):
    lines = [f"FIB-TICK {_iso(S.T0)} range24 resting the entry rests at the level the rule names"]
    ticks = G.parse_log(_write_log(tmp_path, lines))
    assert [t.action for t in ticks] == ["resting"]


# ── gaps ─────────────────────────────────────────────────────────────────

def _mk(ms: int, action: str, why: str = "") -> G.Tick:
    return G.Tick(ts_ms=ms, utc=_iso(ms), action=action, why=why)


def test_gaps_spans_from_the_gap_action_to_the_next_tick_and_carries_error_forward():
    ticks = [_mk(0, "resting"), _mk(900_000, "cancel_entry", "swing_changed"),
             _mk(1_800_000, "error", "connection reset"), _mk(2_700_000, "resting")]
    out = G.gaps(ticks)
    assert len(out) == 2                                      # the cancel tick's gap AND the error tick's
    assert (out[0].start_ms, out[0].end_ms, out[0].action) == (900_000, 1_800_000, "cancel_entry")
    assert (out[1].start_ms, out[1].end_ms, out[1].action) == (1_800_000, 2_700_000, "error")


def test_a_resting_tick_opens_no_gap_and_a_trailing_tick_opens_none_either():
    assert G.gaps([_mk(0, "resting"), _mk(900_000, "resting")]) == []
    assert G.gaps([_mk(0, "cancel_entry")]) == []               # no "next" tick to bound it


# ── minutes_by_day ───────────────────────────────────────────────────────

def test_minutes_by_day_splits_a_window_across_utc_midnight():
    midnight = int(datetime(2026, 11, 10, tzinfo=timezone.utc).timestamp() * 1000)
    out = G.minutes_by_day([(midnight - 30 * 60_000, midnight + 45 * 60_000)])
    assert out == {"2026-11-09": 30.0, "2026-11-10": 45.0}


def test_minutes_by_day_sums_several_windows_on_the_same_day():
    assert G.minutes_by_day([(0, 600_000), (900_000, 1_500_000)]) == {"1970-01-01": 20.0}   # two 10-min windows


# ── tagged ───────────────────────────────────────────────────────────────

def _bar(t: int, h: float, l: float) -> Candle:
    return Candle(t=t, o=(h + l) / 2, h=h, l=l, c=(h + l) / 2, v=1)


def test_tagged_checks_the_low_for_a_long_and_the_high_for_a_short_inside_the_window():
    bars = [_bar(0, 105, 103), _bar(900_000, 101, 99), _bar(1_800_000, 104, 102)]   # the middle bar dips to 99
    assert G.tagged(bars, 0, 1_800_000, entry_px=100.0, side="long") is True
    assert G.tagged(bars, 0, 1_800_000, entry_px=98.0, side="long") is False        # never that low
    assert G.tagged(bars, 1_800_000, 2_700_000, entry_px=100.0, side="long") is False   # outside the window
    assert G.tagged(bars, 0, 1_800_000, entry_px=104.5, side="short") is True       # the first bar's high (105)


# ── simulate_replacement ─────────────────────────────────────────────────

def test_simulate_replacement_names_the_same_entry_the_rest_of_the_suite_trusts():
    cfg = SleeveConfig()
    hours_all, bars_all = S.hours(), S.quarters(S.hours())
    plan = G.simulate_replacement(cfg, hours_all, bars_all, S.NOW)
    assert plan["ok"] is True and plan["side"] == "long"
    assert plan["entry"] == LEVEL and round(plan["stop"], 4) == STOP and plan["target"] == TARGET


def test_simulate_replacement_reports_insufficient_history_before_enough_hours_close():
    cfg = SleeveConfig()
    hours_all, bars_all = S.hours(), S.quarters(S.hours())
    early = S.T0 + 5 * S.HOUR + 60_000              # 5 closed hours: under the 28-hour floor
    assert G.simulate_replacement(cfg, hours_all, bars_all, early) == {
        "ok": False, "reason": "insufficient_candle_history_for_replay"}


# ── check (the eval's pass rule) ─────────────────────────────────────────

def test_check_passes_when_after_never_exceeds_before():
    assert G.check({"by_day": [{"day": "x", "before_minutes": 10.0, "after_minutes": 10.0}]}) == []
    assert G.check({"by_day": [{"day": "x", "before_minutes": 10.0, "after_minutes": 0.0}]}) == []


def test_check_fails_if_after_ever_exceeds_before_or_the_log_could_not_be_read():
    assert G.check({"by_day": [{"day": "x", "before_minutes": 10.0, "after_minutes": 11.0}]})
    assert G.check({"error": "fewer than 2 FIB-TICK lines in /tmp/x; nothing to replay"})


# ── run(), end to end, on a synthetic log and fake candles ──────────────

def test_run_end_to_end_reports_the_gap_the_fix_closes_and_catches_a_tag(tmp_path):
    hours_all = list(S.hours())
    bars_all = list(S.quarters(S.hours()))
    # A 15m bar INSIDE the swing_changed gap that trades straight through
    # LEVEL - the tag this bug makes concrete: a touch with nothing
    # resting. `rule.closed()` excludes it from the swing/plan calculation
    # itself (it has not closed as of S.NOW), exactly like a real forming
    # bar during a real gap.
    bars_all.append(Candle(t=S.NOW, o=1024.0, h=1024.0, l=1010.0, c=1015.0, v=1))

    early_cancel = S.T0 + 5 * S.HOUR + 60_000            # too little history: simulate_replacement says no
    early_next = early_cancel + 73 * 60_000
    swing_cancel = S.NOW
    swing_next = S.NOW + 15 * 60_000

    lines = [
        f"FIB-TICK {_iso(S.T0 + 4 * S.HOUR)} range24 resting the entry rests at the level the rule names",
        f"FIB-TICK {_iso(early_cancel)} range24 cancel_entry level_spent",
        f"FIB-TICK {_iso(early_next)} range24 resting the entry rests at the level the rule names",
        f"FIB-TICK {_iso(swing_cancel)} range24 cancel_entry swing_changed",
        f"FIB-TICK {_iso(swing_next)} range24 resting the entry rests at the level the rule names",
    ]
    log_path = _write_log(tmp_path, lines)

    def fetch(coin: str, interval: str, count: int):
        return hours_all if interval == "1h" else bars_all

    result = G.run(log_path, "xyz:BRENTOIL", fetch)
    assert G.check(result) == []

    assert result["cancel_entry_events"] == 2 and result["would_replace_count"] == 1
    early_event, swing_event = result["cancel_entry_events_detail"]
    assert early_event["would_replace_same_tick"] is False
    assert early_event["gap_minutes_after"] == early_event["gap_minutes_before"] == 73.0
    assert early_event["tagged"] is None

    assert swing_event["would_replace_same_tick"] is True
    assert swing_event["gap_minutes_before"] == 15.0 and swing_event["gap_minutes_after"] == 0.0
    assert swing_event["replacement"] == {"side": "long", "entry": LEVEL, "stop": swing_event["replacement"]["stop"],
                                          "target": TARGET}
    assert swing_event["tagged"] is True

    assert len(result["tagged_gaps"]) == 1
    total_before = sum(r["before_minutes"] for r in result["by_day"])
    total_after = sum(r["after_minutes"] for r in result["by_day"])
    assert round(total_before - total_after, 4) == 15.0          # exactly the swing_changed gap collapsed to 0


def test_run_reports_an_error_on_too_short_a_log(tmp_path):
    log_path = _write_log(tmp_path, [f"FIB-TICK {_iso(S.T0)} range24 resting the entry rests"])
    result = G.run(log_path, "xyz:BRENTOIL", lambda coin, interval, count: [])
    assert "error" in result and G.check(result)


def test_run_reports_an_error_when_candles_fail_to_load(tmp_path):
    lines = [f"FIB-TICK {_iso(S.T0)} range24 resting the entry rests",
             f"FIB-TICK {_iso(S.T0 + 900_000)} range24 resting the entry rests"]
    log_path = _write_log(tmp_path, lines)
    result = G.run(log_path, "xyz:BRENTOIL", lambda coin, interval, count: [])
    assert "error" in result and G.check(result)
