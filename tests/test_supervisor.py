"""The supervisor restarts dead processes — and must never fight the operator.

The failure this covers is on record: the loop process died (Mac asleep) and
nothing brought it back for a week. The loop's own watchdog only helps when the
process is alive and hung.

The failure this must not CREATE is worse: `restart.sh stoploop` is the kill
switch. A supervisor that restarts the loop two minutes after the operator
stopped it has silently deleted the one control that has to work.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load():
    spec = importlib.util.spec_from_file_location(
        "supervise_processes", os.path.join(ROOT, "scripts", "supervise_processes.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


S = _load()


def _case_block(src: str, action: str) -> str:
    """Body of one `case` arm in restart.sh. Arms may be alternations
    (`stoprotate|stoprotator)`), so match the action as one alternative."""
    m = re.search(rf"^\s+(?:[\w|]+\|)?{re.escape(action)}(?:\|[\w|]+)?\)$",
                  src, re.M)
    assert m, f"restart.sh has no case arm for {action!r}"
    return src[m.end():].split(";;", 1)[0]


# ── the operator always wins ─────────────────────────────────────────────────

def test_a_halted_component_is_never_restarted():
    """`stoploop` must stay stopped. This is the kill switch."""
    what, why = S.decide("loop", is_alive=False, is_halted=True,
                         history=[], now=time.time())
    assert what == "none"
    assert "operator" in why


def test_halt_is_read_from_the_marker_file(tmp_path, monkeypatch):
    f = tmp_path / "halt.json"
    f.write_text(json.dumps({"halted": ["loop", "rotator"]}))
    monkeypatch.setattr(S, "HALT_FILE", str(f))
    assert S.halted() == ["loop", "rotator"]


def test_a_missing_or_corrupt_marker_reads_as_nothing_halted(tmp_path, monkeypatch):
    """Absence of the file must not mean 'everything is halted' — that would
    turn a fresh install into a supervisor that never supervises."""
    monkeypatch.setattr(S, "HALT_FILE", str(tmp_path / "absent.json"))
    assert S.halted() == []
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    monkeypatch.setattr(S, "HALT_FILE", str(bad))
    assert S.halted() == []


# ── restart policy ───────────────────────────────────────────────────────────

def test_a_dead_unhalted_component_is_restarted():
    what, _ = S.decide("loop", is_alive=False, is_halted=False,
                       history=[], now=time.time())
    assert what == "restart"


def test_a_running_component_is_left_alone():
    what, _ = S.decide("loop", is_alive=True, is_halted=False,
                       history=[], now=time.time())
    assert what == "none"


def test_a_crash_loop_is_left_down_rather_than_hidden():
    """A process dying on startup will die again. Restarting it forever buries
    the traceback that explains why."""
    now = time.time()
    hist = [now - 300, now - 200, now - 100]
    what, why = S.decide("loop", is_alive=False, is_halted=False,
                         history=hist, now=now)
    assert what == "give_up"
    assert "crash loop" in why


def test_old_restarts_fall_out_of_the_window():
    """Three crashes last month is not a crash loop today."""
    now = time.time()
    old = [now - S.RESTART_WINDOW_S - 10] * 5
    what, _ = S.decide("loop", is_alive=False, is_halted=False,
                       history=old, now=now)
    assert what == "restart"


# ── wiring ───────────────────────────────────────────────────────────────────

def test_the_scheduler_actually_runs_the_supervisor():
    """A supervisor nobody calls is a file, not a supervisor."""
    spec = importlib.util.spec_from_file_location(
        "sched", os.path.join(ROOT, "scripts", "scheduler.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert "supervisor" in m.JOBS
    job = m.JOBS["supervisor"]
    assert job["args"][-1].endswith("supervise_processes.py")
    assert job["interval_min"] <= 5, "a dead loop should cost minutes, not hours"


def test_a_default_run_never_restarts_its_own_parent():
    """The supervisor runs as the scheduler's child. A default run restarting
    the scheduler would kill the process doing the restarting. (Supervising it
    explicitly, from the trading loop, is safe — see below.)"""
    assert "scheduler" not in S.DEFAULT_COMPONENTS
    assert not any("scheduler.py" in S.COMPONENTS[c]["target"]
                   for c in S.DEFAULT_COMPONENTS)


def test_every_component_maps_to_a_real_restart_action():
    """A typo'd action would make every restart a silent no-op."""
    usage = subprocess.run(["bash", os.path.join(ROOT, "scripts", "restart.sh"),
                            "--bogus-action"], capture_output=True, text=True,
                           cwd=ROOT).stderr
    for spec in S.COMPONENTS.values():
        assert spec["action"] in usage, (
            f"{spec['label']}: restart.sh has no '{spec['action']}' action")


# ── restart.sh's half of the contract ────────────────────────────────────────

@pytest.mark.parametrize("action,expect_halted", [
    ("stoploop", "loop"),
    ("stoprotate", "rotator"),
])
def test_stop_actions_mark_the_component_halted(action, expect_halted):
    """Read the script rather than run it — running it would stop the live
    loop. The pairing of action to halt_mark is what matters."""
    src = open(os.path.join(ROOT, "scripts", "restart.sh")).read()
    block = _case_block(src, action)
    assert f"halt_mark {expect_halted}" in block


@pytest.mark.parametrize("action,expect_cleared", [
    ("loop", "loop"),
    ("server", "server"),
])
def test_start_actions_clear_the_halt(action, expect_cleared):
    """Otherwise `stoploop` then `loop` leaves a marker that makes the
    supervisor refuse to ever revive the loop again."""
    src = open(os.path.join(ROOT, "scripts", "restart.sh")).read()
    block = _case_block(src, action)
    assert f"halt_clear {expect_cleared}" in block


def test_dry_run_changes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "STATE", str(tmp_path / "sup.json"))
    assert S.main(["--dry-run"]) == 0
    assert not os.path.exists(str(tmp_path / "sup.json"))


# ── process detection ───────────────────────────────────────────────────────

PY_BIN = ("/Library/Frameworks/Python.framework/Versions/3.13/Resources/"
          "Python.app/Contents/MacOS/Python")
# Deliberately not this machine's path: these are command-line SHAPES, and
# the absolute-path guard is right to reject a real-looking home directory.
ROOT_ABS = "/opt/hermes-trader"


@pytest.mark.parametrize("comp,cmd", [
    ("loop",      f"{PY_BIN} {ROOT_ABS}/scripts/trading_loop.py"),
    ("server",    f"{PY_BIN} -m hermes_trader.server"),
    ("rotator",   f"{PY_BIN} {ROOT_ABS}/scripts/log_rotate.py --daemon"),
    ("scheduler", f"{PY_BIN} {ROOT_ABS}/scripts/scheduler.py"),
])
def test_the_real_daemon_command_lines_are_recognised(comp, cmd):
    """Taken verbatim from `ps -Ao command=` on the live box. If restart.sh
    ever changes how it launches something, this fails instead of the
    supervisor quietly deciding a running process is dead."""
    assert S.is_the_process(cmd, S.COMPONENTS[comp]) is True


@pytest.mark.parametrize("cmd", [
    # a shell whose argv merely mentions the script
    'bash -c pgrep -f scripts/trading_loop.py',
    # the supervisor itself, which names the component
    f"{PY_BIN} {ROOT_ABS}/scripts/supervise_processes.py --components scheduler",
    # restart.sh, which starts the thing being checked
    f"bash {ROOT_ABS}/scripts/restart.sh sched",
    # an editor with the file open
    f"vim {ROOT_ABS}/scripts/trading_loop.py",
    # a different interpreter running an unrelated file
    f"node {ROOT_ABS}/scripts/trading_loop.py",
])
def test_merely_mentioning_a_daemon_is_not_running_it(cmd):
    """The substring detector reported the log rotator "running" one second
    after SIGKILL because the invoking shell's argv contained the pattern."""
    assert not any(S.is_the_process(cmd, spec) for spec in S.COMPONENTS.values())


def test_a_process_that_started_the_daemon_is_not_the_daemon():
    """The regression that cost a healthy trading loop.

    Excluding our own ancestors fixed the substring false-positive and created
    a worse false-NEGATIVE: when the loop starts the scheduler, the loop is
    transiently the new scheduler's ancestor, so the scheduler's first
    supervisor pass subtracted the loop's pid, concluded the loop was down, and
    restarted it mid-scan. Recorded in logs/supervisor.log at 08:22:56 on
    2026-08-31. Identity must not depend on who our parent is.
    """
    chain = [f"bash {ROOT_ABS}/scripts/restart.sh sched",
             f"{PY_BIN} {ROOT_ABS}/scripts/supervise_processes.py --components scheduler",
             f"{PY_BIN} {ROOT_ABS}/scripts/trading_loop.py"]
    assert S.alive(S.COMPONENTS["loop"], chain) is True
    assert S.alive(S.COMPONENTS["scheduler"], chain) is False


def test_the_rotator_needs_its_daemon_flag():
    """A one-shot `log_rotate.py --guard` is not the rotator daemon."""
    assert S.is_the_process(f"{PY_BIN} {ROOT_ABS}/scripts/log_rotate.py --guard",
                            S.COMPONENTS["rotator"]) is False


def test_a_module_daemon_is_not_matched_by_a_script_of_the_same_name():
    assert S.is_the_process(f"{PY_BIN} {ROOT_ABS}/hermes_trader/server.py",
                            S.COMPONENTS["server"]) is False


def test_an_unreadable_process_table_never_triggers_a_blind_restart():
    """If we cannot tell, restarting is the dangerous guess: it can start a
    SECOND trading loop against the same account."""
    assert S.alive(S.COMPONENTS["loop"], []) is True


# ── mutual supervision ───────────────────────────────────────────────────────

def test_the_scheduler_is_supervisable_but_not_by_default():
    """The supervisor runs as the scheduler's child, so a DEFAULT run must not
    restart it — that would kill the process doing the restarting. It still has
    to be supervisable by someone else."""
    assert "scheduler" in S.COMPONENTS
    assert "scheduler" not in S.DEFAULT_COMPONENTS
    assert S.selected([]) == list(S.DEFAULT_COMPONENTS)
    assert S.selected(["--components", "scheduler"]) == ["scheduler"]


def test_an_unknown_component_is_an_error_not_an_empty_run():
    """`--components schedular` supervising nothing would look exactly like a
    healthy pass."""
    with pytest.raises(SystemExit):
        S.selected(["--components", "schedular"])
    with pytest.raises(SystemExit):
        S.selected(["--components", ""])


def test_the_trading_loop_watches_the_scheduler():
    """Without this the whole watch rests on one process: the scheduler runs
    both the supervisor and the alert evaluator, so its death would end
    supervision and alerting at once, silently."""
    src = open(os.path.join(ROOT, "scripts", "trading_loop.py")).read()
    assert "supervise_processes.py" in src
    assert '"--components", "scheduler"' in src
    assert "hermes-supervise-sched" in src, "the thread must actually be started"


def test_the_loop_reuses_the_supervisor_rather_than_reimplementing_it():
    """A second implementation would not honour the halt marker, and would
    revive a scheduler the operator deliberately stopped."""
    src = open(os.path.join(ROOT, "scripts", "trading_loop.py")).read()
    block = src.split("_supervise_scheduler", 1)[1].split("\nif _SUPERVISE", 1)[0]
    assert "restart.sh" not in block, "the loop must not shell out to restart.sh itself"
    assert "pgrep" not in block, "the loop must not grow its own process detector"


def test_stopping_everything_halts_the_scheduler_too():
    """`restart.sh stop` must stay stopped. If only the scheduler lacked a halt
    mark, the loop would revive it and it would revive everything else."""
    src = open(os.path.join(ROOT, "scripts", "restart.sh")).read()
    block = _case_block(src, "stop")
    assert "halt_mark scheduler" in block


def test_restarting_the_scheduler_clears_its_halt():
    src = open(os.path.join(ROOT, "scripts", "restart.sh")).read()
    block = _case_block(src, "sched")
    assert "halt_clear scheduler" in block


def test_concurrent_supervisors_do_not_share_a_state_file(tmp_path, monkeypatch):
    """Two invocations now run at once — the scheduler's own, and the loop's
    scheduler check. Sharing one file lets one drop the other's restart
    record, which quietly weakens the crash-loop cap."""
    monkeypatch.setattr(S, "STATE", str(tmp_path / "sup.json"))
    monkeypatch.setattr(S, "HALT_FILE", str(tmp_path / "halt.json"))
    monkeypatch.setattr(S, "alive", lambda *a, **k: True)
    S.main([])
    S.main(["--components", "scheduler"])
    assert (tmp_path / "sup.json").exists()
    assert (tmp_path / "sup_scheduler.json").exists()
    assert json.loads((tmp_path / "sup.json").read_text())["checked"] == \
        sorted(S.DEFAULT_COMPONENTS)
