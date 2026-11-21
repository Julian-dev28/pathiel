"""Gate: no test writes to the live shared desk log.

2026-11-20: services/intel/tests/test_finlight_watch.py ran
scripts/trigger_watch.watch() without stubbing _append_watch_line, so every
pytest run (the pre-commit hook included) appended fake
"Tanker struck by missile ... mid=112.04" WATCH lines to
/tmp/oil-loop/progress.log, which the live desk ticks write and the Desk
page's Loop Log card shows the operator. The root conftest's autouse
_desk_log_isolation now points desk_log's default at a per-test tmp file.
"""
import importlib.util
import pathlib

from services.strategies import desk_log

ROOT = pathlib.Path(__file__).resolve().parents[1]
REAL_LOG = "/tmp/oil-loop/progress.log"


def test_the_default_desk_log_is_never_the_live_file_inside_a_test():
    for desk in ("oil", "sp500", "hype", "metals"):
        assert desk_log.log_path(desk) != REAL_LOG


def test_the_watchers_watch_line_lands_in_the_test_tmp_log_not_the_live_one(tmp_path):
    spec = importlib.util.spec_from_file_location("trigger_watch_iso", ROOT / "scripts" / "trigger_watch.py")
    W = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(W)
    W._append_watch_line("oil", "action=WAKE isolation-probe")
    written = pathlib.Path(desk_log.log_path("oil")).read_text()
    assert "WATCH oil" in written and "isolation-probe" in written
    assert desk_log.log_path("oil") != REAL_LOG
