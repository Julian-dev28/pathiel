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


def test_the_scheduler_is_not_supervised():
    """The supervisor runs as the scheduler's child. Restarting the scheduler
    would kill the process doing the restarting."""
    assert "scheduler" not in S.COMPONENTS
    assert not any("scheduler.py" in pat for pat, _, _ in S.COMPONENTS.values())


def test_every_component_maps_to_a_real_restart_action():
    """A typo'd action would make every restart a silent no-op."""
    usage = subprocess.run(["bash", os.path.join(ROOT, "scripts", "restart.sh"),
                            "--bogus-action"], capture_output=True, text=True,
                           cwd=ROOT).stderr
    for _, action, label in S.COMPONENTS.values():
        assert action in usage, f"{label}: restart.sh has no '{action}' action"


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


# ── process detection ────────────────────────────────────────────────────────

def test_our_own_command_line_does_not_count_as_a_running_process():
    """A substring test against `ps ax` reported the log rotator "running" one
    second after SIGKILL, because the invoking shell's argv contained the
    pattern. Any check that matches its own caller can never report a process
    down — the supervisor would have been decorative."""
    marker = "hermes-supervisor-selftest-pattern"
    # A shell whose argv contains the pattern, and nothing else that matches.
    # marker as a real argv element — `bash -c "one command"` execs through and
    # would drop it from the command line entirely.
    proc = subprocess.Popen([sys.executable, "-c",
                             "import time; time.sleep(30)", marker])
    try:
        time.sleep(0.3)
        # Seen from a caller that is NOT the sleeper: genuinely running.
        assert S.alive(marker, exclude={os.getpid()}) is True
        # Seen from the sleeper's own tree: must not count itself.
        assert S.alive(marker, exclude={os.getpid(), proc.pid}) is False
    finally:
        proc.kill()
        proc.wait(timeout=10)


def test_ancestors_includes_this_process():
    a = S._ancestors()
    assert os.getpid() in a
    assert len(a) < 25, "ancestor walk must stay bounded"


def test_an_unusable_pgrep_never_triggers_a_blind_restart(monkeypatch):
    """If we cannot tell whether a process is alive, restarting is the
    dangerous guess: it can start a SECOND trading loop."""
    def boom(*a, **k):
        raise OSError("pgrep missing")
    monkeypatch.setattr(subprocess, "run", boom)
    assert S.alive("anything", exclude=set()) is True


def test_preflight_uses_the_same_detector():
    """Two process detectors means one of them rots. The substring version
    lived in both files and was wrong in both."""
    import ast
    src = open(os.path.join(ROOT, "scripts", "preflight_live.py")).read()
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "check_processes")
    code = [ast.get_source_segment(src, st) or "" for st in fn.body
            if not (isinstance(st, ast.Expr) and isinstance(st.value, ast.Constant))]
    code = "\n".join(code)
    assert "sup.alive(" in code, "preflight must use the shared detector"
    assert '"ps"' not in code and "'ps'" not in code, (
        "a second `ps` substring detector has grown back")
