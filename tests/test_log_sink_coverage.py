"""Every log sink is bounded, and the two rotation paths never overlap.

Two mechanisms exist and they are mutually exclusive by construction:

  external copytruncate (scripts/log_rotate.py) — the only thing that can bound
  a file a process appends to through a shell `nohup >>` redirection, because
  the process never reopens that fd.

  in-process RotatingFileHandler (hermes_trader.log_setup.configure_logging) —
  correct only for a process whose stdout/stderr are NOT already being appended
  to the same path externally.

Using both on one file would double-log and let the handler rename a file the
shell fd still points at. This pins that no entrypoint does that, and that the
rotator's glob actually covers every sink, so a new log file added later is
bounded automatically rather than growing forever unnoticed.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _restart_sh() -> str:
    return (ROOT / "scripts" / "restart.sh").read_text()


def _scheduler_py() -> str:
    return (ROOT / "scripts" / "scheduler.py").read_text()


def test_the_rotator_globs_the_whole_log_directory():
    """A glob, not an enumerated list: a log file added next month must be
    bounded without anyone remembering to register it."""
    body = (ROOT / "scripts" / "log_rotate.py").read_text()
    assert "*.log" in body, (
        "the rotator enumerates specific files — a new sink would grow unbounded")


def test_every_shell_redirected_sink_lands_in_the_log_dir():
    """The rotator only sweeps its own directory. A process redirected somewhere
    else would be outside every bound."""
    for line in _restart_sh().splitlines():
        if ">>" not in line or line.lstrip().startswith("#"):
            continue
        for target in re.findall(r'>>\s*"?(\$[A-Z_]+|[^\s"]+)"?', line):
            assert ("LOG" in target or "logs/" in target), (
                f"restart.sh appends to {target}, which is not a logs/ sink the "
                f"rotator sweeps")


def test_scheduler_job_logs_are_under_the_log_dir():
    """scheduler.py opens each job's log itself, so those are sinks too."""
    body = _scheduler_py()
    for log in re.findall(r'"log":\s*"([^"]+)"', body):
        assert log.startswith("logs/"), f"scheduler job log {log} escapes logs/"


def test_no_entrypoint_mixes_both_rotation_mechanisms():
    """An in-process handler on a file the shell is also appending to would
    double-log and rename a path the shell fd still holds."""
    redirected = {"trading_loop.py", "scheduler.py", "log_rotate.py"}
    for name in redirected:
        src = (ROOT / "scripts" / name).read_text()
        assert "configure_logging(" not in src, (
            f"{name} is shell-redirected by restart.sh but also attaches an "
            f"in-process rotating handler — the two would fight over one file")
    server = (ROOT / "hermes_trader" / "server.py").read_text()
    assert "configure_logging(" not in server


def test_configure_logging_documents_why_it_is_unwired():
    """It is deliberately uncalled, not forgotten. If that reason ever stops
    being written down, the next reader wires it into a nohup entrypoint and
    breaks rotation."""
    doc = (ROOT / "hermes_trader" / "log_setup.py").read_text()
    assert "not called anywhere yet" in doc
    assert "nohup" in doc
