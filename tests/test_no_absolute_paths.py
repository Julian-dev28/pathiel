"""The regression test for the bug that kept CI red for weeks.

50 tracked files carried `REPO = "/Users/julian_dev/Documents/code/hermes-trader"`.
The suite was green on the one machine where that path existed and red on every
push. Nothing caught it, so this is the thing that catches it now.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_no_absolute_paths.py"

_spec = importlib.util.spec_from_file_location("check_no_absolute_paths", SCRIPT)
checker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(checker)


def test_the_repo_has_no_machine_bound_absolute_paths():
    assert checker.main() == 0


def test_the_pattern_catches_the_exact_bug_that_shipped():
    """The literal that was in W-X2_xs_widening.py:74."""
    line = 'REPO = "/Users/julian_dev/Documents/code/hermes-trader"'
    assert checker.PATTERN.search(line) is not None


def test_the_pattern_catches_a_linux_home_too():
    assert checker.PATTERN.search('P = "/home/runner/work/thing"') is not None


def test_the_pattern_leaves_machine_independent_paths_alone():
    """/tmp and /usr/local exist on any box — flagging them would make the
    check noisy enough that someone turns it off."""
    for ok in ('X = "/tmp/hermes/state.json"',
               'BIN = "/usr/local/bin/claude"',
               'p = Path(__file__).resolve().parents[1]'):
        assert checker.PATTERN.search(ok) is None, ok


def test_ci_runs_the_check_before_the_suite():
    """A guard that only exists locally is not a guard. It must be wired into
    the workflow, and BEFORE pytest so the failure names the real cause."""
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "check_no_absolute_paths.py" in ci
    assert ci.index("check_no_absolute_paths.py") < ci.index("run: pytest")


def test_ci_installs_from_the_lockfile():
    """CI passing against a different dependency set than production runs is
    not evidence about production."""
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "requirements.lock" in ci
    assert (ROOT / "requirements.lock").exists()


def test_the_allowlist_is_only_the_files_that_must_carry_the_pattern():
    """The escape hatch has to stay tiny. Every entry is a file whose job is to
    describe the pattern; anything else in here would be a real offender being
    waved through."""
    assert checker.ALLOWED == {
        ".env.local.example",
        "scripts/check_no_absolute_paths.py",
        "tests/test_no_absolute_paths.py",
    }
