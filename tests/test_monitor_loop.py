"""A monitor that is silent through a crash is worse than no monitor.

Silence and health must never look the same. These tests pin the failure-mode
coverage, because the natural way to write a watcher is to grep for the thing
you hope to see - and that watcher says nothing at all while the process is
dead, the log is wedged, or the kill switch has fired.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / "scripts" / "monitor_loop.py").read_text()

spec = importlib.util.spec_from_file_location(
    "monitor_loop", ROOT / "scripts" / "monitor_loop.py")
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)


@pytest.mark.parametrize("signal", [
    "Traceback", "CRITICAL", "killswitch", "Daily loss", "flattening",
    "[dsl] Closing", "ERROR", "watchdog", "REFUSED",
])
def test_every_failure_signature_is_covered(signal):
    """The alert set must match how the loop actually reports trouble."""
    assert any(pat == signal for _, pat in M.ALERT_PATTERNS), (
        f"{signal!r} is not in ALERT_PATTERNS — the monitor would stay silent "
        f"while the loop reported it")


def test_process_death_and_log_staleness_are_both_alerts():
    """Two distinct wedge modes. A loop can die (no pid) or hang (pid alive,
    log frozen); greping only for the first misses the second entirely, and
    the second is the one that looks healthy in `ps`."""
    assert 'if not pids:' in SRC and '"DEAD' in SRC
    assert "STALE" in SRC and "log silent" in SRC


def test_routine_ticks_do_not_alert():
    """The healthy path writes to progress.log and NOT to stdout, or every
    quiet check becomes a notification and the real ones get lost."""
    i = SRC.index('f"ok — ')
    tail = SRC[i:i + 400]
    assert "False)" in tail, "the routine tick must pass alert=False"


def test_emit_writes_progress_even_when_not_alerting(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "JOB", tmp_path)
    monkeypatch.setattr(M, "PROGRESS", tmp_path / "progress.log")
    M.emit("quiet tick", False)
    assert "quiet tick" in (tmp_path / "progress.log").read_text(), (
        "a non-alerting tick must still be auditable afterwards")


def test_monitor_survives_a_bad_tick():
    """A malformed state file must not kill the watch - that would turn a
    recoverable blip into unmonitored trading."""
    assert "except Exception as e:" in SRC and "MONITOR ERROR" in SRC


def test_log_rotation_does_not_replay_the_whole_file():
    """After rotation the file shrinks; without the reset the reader seeks past
    the end and goes permanently silent."""
    assert "if size < offset:" in SRC
