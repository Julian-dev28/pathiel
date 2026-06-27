"""extreme_fade_live.maybe_run — the execution path that was MISSING (the loop computed + logged
fade signals and never traded), plus the entry-timing fixes (completed-bar signal, freshness window,
per-crash-bar dedup) that align live entry with the backtest. Pure mocks — no network, no live state
(conftest redirects HERMES_STATE_DIR to a temp dir, so the dedup state file is disposable)."""
import time

from hermes_trader.agents import extreme_fade_live as efl

DAY = 86_400_000
NOW = int(time.time() * 1000)


def _mk_bars(crashed=True, fresh=True, forming=True):
    """Bars whose last COMPLETED daily bar crashed (or not), closing `fresh`=1h ago / stale=20h ago.
    Optionally append a still-forming current bar (start == the completed bar's close)."""
    close_ms = NOW - (1 * 3_600_000 if fresh else 20 * 3_600_000)
    crash_start = close_ms - DAY
    prior_start = crash_start - DAY
    prior_c = 100.0
    crash_c = prior_c * (1 + (-0.15 if crashed else 0.01))
    bars = [
        {"t": prior_start, "o": prior_c, "h": prior_c, "l": prior_c, "c": prior_c, "v": 1e7},
        {"t": crash_start, "o": crash_c, "h": crash_c, "l": crash_c, "c": crash_c, "v": 1e7},
    ]
    if forming:
        fc = crash_c * 1.05   # already bounced +5% — the forming bar must NOT drive the signal
        bars.append({"t": close_ms, "o": fc, "h": fc, "l": fc, "c": fc, "v": 1e7})
    return bars


def _universe(coins):
    return [{"coin": c, "dayNtlVlm": 1e8} for c in coins]


def _cfg(**over):
    ef = {"enabled": True, "crash_pct": -0.12}
    ef.update(over)
    return {"extreme_fade": ef}


def _fetch(fresh=frozenset(), stale=frozenset()):
    def f(coin, interval, n):
        if coin in fresh:
            return _mk_bars(crashed=True, fresh=True)
        if coin in stale:
            return _mk_bars(crashed=True, fresh=False)
        return _mk_bars(crashed=False, fresh=True)
    return f


def test_disabled_is_noop():
    calls = []
    out = efl.maybe_run({"extreme_fade": {"enabled": False}}, _universe(["D1"]), [],
                        _fetch(fresh={"D1"}), lambda a: calls.append(a))
    assert out is None and calls == []


def test_live_opens_fresh_crash_with_strategy_book_tag():
    calls = []
    efl.maybe_run(_cfg(), _universe(["FRESH", "CALM"]), [],
                  _fetch(fresh={"FRESH"}), lambda a: calls.append(a))
    assert len(calls) == 1
    a = calls[0]
    assert a["coin"] == "FRESH" and a["side"] == "long" and a["verdict"] == "LONG"
    assert a["strategy_book"] == "extreme_fade"


def test_skips_stale_crash_no_chase(caplog):
    """A crash whose bar closed 20h ago is past the entry window → must NOT open (don't chase the
    already-bounced move; this is the mid-day-restart case that surfaced the bug)."""
    calls = []
    caplog.set_level("INFO", logger="hermes_trader.agents.extreme_fade_live")
    efl.maybe_run(_cfg(), _universe(["STALE"]), [],
                  _fetch(stale={"STALE"}), lambda a: calls.append(a))
    assert calls == []
    assert "skip STALE: stale entry window" in caplog.text


def test_forming_bar_does_not_drive_signal():
    """The forming bar bounced +5%; the signal must come from the completed crash bar, so a fade
    still fires (proving we read completed bars, not today-so-far)."""
    calls = []
    efl.maybe_run(_cfg(), _universe(["FORM"]), [],
                  _fetch(fresh={"FORM"}), lambda a: calls.append(a))
    assert len(calls) == 1 and calls[0]["coin"] == "FORM"


def test_skips_held_coin_no_stacking():
    calls = []
    positions = [{"position": {"coin": "HELD", "szi": "1.0"}}]
    efl.maybe_run(_cfg(), _universe(["HELD", "OPEN"]), positions,
                  _fetch(fresh={"HELD", "OPEN"}), lambda a: calls.append(a))
    opened = {c["coin"] for c in calls}
    assert "HELD" not in opened and "OPEN" in opened


def test_dedup_same_crash_bar_opens_once():
    calls = []
    fetch = _fetch(fresh={"DEDUP"})
    efl.maybe_run(_cfg(), _universe(["DEDUP"]), [], fetch, lambda a: calls.append(a))
    efl.maybe_run(_cfg(), _universe(["DEDUP"]), [], fetch, lambda a: calls.append(a))
    assert len(calls) == 1            # second cycle deduped on the same crash bar


def test_blocked_execute_does_not_record_open_or_dedup():
    calls = []
    fetch = _fetch(fresh={"BLOCKED"})

    def blocked(a):
        calls.append(a)
        return {"executed": False, "reason": "gate"}

    first = efl.maybe_run(_cfg(), _universe(["BLOCKED"]), [], fetch, blocked)
    second = efl.maybe_run(_cfg(), _universe(["BLOCKED"]), [], fetch, blocked)

    assert first["opened"] == 0
    assert second["opened"] == 0
    assert len(calls) == 2            # not deduped because no exchange risk opened


def test_per_cycle_cap():
    calls = []
    efl.maybe_run(_cfg(max_new_per_cycle=1), _universe(["C1", "C2", "C3"]), [],
                  _fetch(fresh={"C1", "C2", "C3"}), lambda a: calls.append(a))
    assert len(calls) == 1


def test_non_crash_does_not_fire():
    calls = []
    efl.maybe_run(_cfg(), _universe(["CALM"]), [],
                  _fetch(), lambda a: calls.append(a))
    assert calls == []
