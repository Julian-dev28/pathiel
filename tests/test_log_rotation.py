"""Gate tests for the log-rotation stack: pathia/log_setup.py (policy +
disk guard) and scripts/log_rotate.py (the copytruncate rotator).

Offline and deterministic: every test runs against a real tmp_path directory
(the file-level behavior — truncation semantics, an open fd surviving
rotation, gzip round-tripping — is exactly what a mock would hand-wave past)
and the disk guard is exercised with an injected `disk_usage_fn` so nothing
here depends on, or touches, the actual machine's free space or the repo's
real logs/ directory.

WHY THIS HAS TO BE COPYTRUNCATE, NOT AN IN-PROCESS RotatingFileHandler:
every pathia process is started by scripts/restart.sh via
`nohup ... >> file 2>&1 &` — a shell-owned, O_APPEND fd that the process
never reopens. See pathia/log_setup.py's module docstring and
scripts/log_rotate.py's for the full argument. The
"writer holding an open fd keeps writing to the right place" test below is
the direct proof: it opens a file exactly the way the shell does
(os.O_APPEND) and rotates underneath it.
"""
from __future__ import annotations

import gzip
import importlib.util
import os
import time
from pathlib import Path


from pathia import log_setup

_SPEC = importlib.util.spec_from_file_location(
    "pathia_log_rotate",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts", "log_rotate.py"),
)
log_rotate = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(log_rotate)


def _write(path: Path, size: int, marker: bytes = b"") -> None:
    with open(path, "wb") as f:
        f.write(b"x" * size)
        f.write(marker)


# ── rotate_file: triggers at the size threshold ─────────────────────────────

def test_rotate_file_below_threshold_is_a_noop(tmp_path):
    p = tmp_path / "small.log"
    _write(p, 100)
    result = log_rotate.rotate_file(p, max_bytes=1000, backup_count=3)
    assert result is None
    assert p.stat().st_size == 100
    assert not (tmp_path / "small.log.1.gz").exists()


def test_rotate_file_at_or_above_threshold_rotates(tmp_path):
    p = tmp_path / "big.log"
    _write(p, 1000)
    result = log_rotate.rotate_file(p, max_bytes=1000, backup_count=3)
    assert result is not None
    assert result["rotated_bytes"] == 1000
    # Truncated in place — same path, zero length — not deleted/recreated.
    assert p.exists()
    assert p.stat().st_size == 0
    backup = tmp_path / "big.log.1.gz"
    assert backup.exists()
    with gzip.open(backup, "rb") as f:
        assert len(f.read()) == 1000


def test_rotate_file_missing_path_is_a_noop(tmp_path):
    assert log_rotate.rotate_file(tmp_path / "nope.log", max_bytes=10, backup_count=3) is None


def test_rotate_file_empty_file_is_a_noop_even_when_forced(tmp_path):
    p = tmp_path / "empty.log"
    p.touch()
    assert log_rotate.rotate_file(p, max_bytes=10, backup_count=3, force=True) is None


def test_rotate_file_force_bypasses_threshold(tmp_path):
    p = tmp_path / "tiny.log"
    _write(p, 10)
    result = log_rotate.rotate_file(p, max_bytes=10_000_000, backup_count=3, force=True)
    assert result is not None
    assert p.stat().st_size == 0


def test_rotate_file_zero_backup_count_still_truncates_but_keeps_no_copy(tmp_path):
    p = tmp_path / "nobackup.log"
    _write(p, 1000)
    result = log_rotate.rotate_file(p, max_bytes=1000, backup_count=0)
    assert result is not None
    assert result["backup"] is None
    assert p.stat().st_size == 0
    assert list(tmp_path.glob("*.gz")) == []


# ── retention: old backups pruned to the configured count ───────────────────

def test_old_backups_pruned_to_retention_count(tmp_path):
    p = tmp_path / "app.log"
    for i in range(7):
        _write(p, 500, marker=f"gen{i}".encode())
        log_rotate.rotate_file(p, max_bytes=500, backup_count=3)

    backups = sorted(tmp_path.glob("app.log.*.gz"))
    assert [b.name for b in backups] == [
        "app.log.1.gz",
        "app.log.2.gz",
        "app.log.3.gz",
    ]
    # Newest generation is always .1.gz; oldest ones fell off the end.
    with gzip.open(tmp_path / "app.log.1.gz", "rb") as f:
        assert f.read().endswith(b"gen6")
    with gzip.open(tmp_path / "app.log.3.gz", "rb") as f:
        assert f.read().endswith(b"gen4")


def test_retention_count_is_configurable(tmp_path):
    p = tmp_path / "app.log"
    for i in range(5):
        _write(p, 200, marker=f"g{i}".encode())
        log_rotate.rotate_file(p, max_bytes=200, backup_count=1)
    backups = sorted(tmp_path.glob("app.log.*.gz"))
    assert [b.name for b in backups] == ["app.log.1.gz"]
    with gzip.open(backups[0], "rb") as f:
        assert f.read().endswith(b"g4")


# ── the actual point: a shell-style open fd survives rotation ───────────────

def test_open_append_fd_keeps_writing_to_the_right_place_after_rotation(tmp_path):
    """Opens the file exactly the way `nohup ... >> file 2>&1 &` does
    (O_APPEND, held open across the rotation) and proves: (1) everything
    written before rotation ends up in the compressed backup, (2) the live
    file contains ONLY what's written after rotation, through the SAME fd,
    with no reopen, no signal, no cooperation from the writer."""
    p = tmp_path / "trading_loop.log"
    p.write_bytes(b"")

    fd = os.open(str(p), os.O_WRONLY | os.O_APPEND | os.O_CREAT)
    try:
        os.write(fd, b"before-rotation " * 100)  # 1600 bytes, over our threshold
        pre_size = p.stat().st_size

        result = log_rotate.rotate_file(p, max_bytes=1000, backup_count=2)
        assert result is not None
        assert result["rotated_bytes"] == pre_size

        # Same fd, no reopen — this is the whole test.
        os.write(fd, b"after-rotation")
    finally:
        os.close(fd)

    live = p.read_bytes()
    assert live == b"after-rotation"
    assert b"before-rotation" not in live

    with gzip.open(tmp_path / "trading_loop.log.1.gz", "rb") as f:
        backup = f.read()
    assert backup == b"before-rotation " * 100
    assert b"after-rotation" not in backup


def test_two_concurrent_open_append_fds_both_keep_writing_after_rotation(tmp_path):
    """pathia's scheduler runs multiple jobs against the SAME log file
    concurrently (poly-board and poly-judgment both target
    logs/polymarket_scout.log). Two independent O_APPEND fds must both land
    correctly after an in-place truncation."""
    p = tmp_path / "shared.log"
    p.write_bytes(b"")
    fd_a = os.open(str(p), os.O_WRONLY | os.O_APPEND | os.O_CREAT)
    fd_b = os.open(str(p), os.O_WRONLY | os.O_APPEND | os.O_CREAT)
    try:
        os.write(fd_a, b"A" * 600)
        os.write(fd_b, b"B" * 600)
        log_rotate.rotate_file(p, max_bytes=1000, backup_count=1)
        os.write(fd_a, b"a-after")
        os.write(fd_b, b"b-after")
    finally:
        os.close(fd_a)
        os.close(fd_b)

    live = p.read_bytes()
    assert b"A" not in live and b"B" not in live
    assert b"a-after" in live and b"b-after" in live


# ── directory-wide size cap ──────────────────────────────────────────────────

def test_enforce_dir_cap_prunes_oldest_backups_first(tmp_path):
    p = tmp_path / "app.log"
    for i in range(4):
        _write(p, 400)
        log_rotate.rotate_file(p, max_bytes=400, backup_count=4)
        time.sleep(0.01)  # distinct mtimes so "oldest" is unambiguous

    total_before = sum(f.stat().st_size for f in tmp_path.iterdir() if f.is_file())
    assert total_before > 0
    removed = log_rotate.enforce_dir_cap(tmp_path, max_total_bytes=total_before // 2, quiet=True)
    assert removed  # something had to go
    for r in removed:
        assert r.name.endswith(".gz")  # never a live .log file
    total_after = sum(f.stat().st_size for f in tmp_path.iterdir() if f.is_file())
    assert total_after <= total_before


def test_enforce_dir_cap_never_deletes_a_live_log_file(tmp_path):
    p = tmp_path / "app.log"
    _write(p, 5000)  # nothing has rotated it — this is the ONLY file present
    log_rotate.enforce_dir_cap(tmp_path, max_total_bytes=1, quiet=True)
    assert p.exists()  # cap can't be met without touching live data — must not delete it


def test_enforce_dir_cap_force_rotates_biggest_live_file_when_no_backups_left(tmp_path):
    p = tmp_path / "app.log"
    _write(p, 5000)
    log_rotate.enforce_dir_cap(tmp_path, max_total_bytes=1, quiet=True)
    # Can't shrink below cap (nothing to prune), so it force-rotates the live
    # file instead of silently leaving it oversized forever.
    assert p.stat().st_size == 0
    assert (tmp_path / "app.log.1.gz").exists()


# ── rotate_all / the CLI entrypoint restart.sh actually calls ──────────────

def test_rotate_all_only_touches_dot_log_files(tmp_path):
    (tmp_path / "keep.json").write_bytes(b"x" * 5000)
    (tmp_path / "big.log").write_bytes(b"x" * 5000)
    log_rotate.rotate_all(tmp_path, max_bytes=1000, backup_count=2, dir_cap=10_000_000, quiet=True)
    assert (tmp_path / "keep.json").stat().st_size == 5000  # untouched, not a .log
    assert (tmp_path / "big.log").stat().st_size == 0
    assert (tmp_path / "big.log.1.gz").exists()


def test_cli_once_rotates_a_tmp_dir(tmp_path):
    _write(tmp_path / "trading_loop.log", 2000)
    rc = log_rotate.main(["--dir", str(tmp_path), "--once", "--max-bytes", "1000",
                          "--backup-count", "2", "--quiet"])
    assert rc == 0
    assert (tmp_path / "trading_loop.log").stat().st_size == 0
    assert (tmp_path / "trading_loop.log.1.gz").exists()


def test_cli_file_force_rotates_one_file(tmp_path):
    small = tmp_path / "server.log"
    _write(small, 10)
    rc = log_rotate.main(["--dir", str(tmp_path), "--file", str(small), "--force", "--quiet"])
    assert rc == 0
    assert small.stat().st_size == 0
    assert (tmp_path / "server.log.1.gz").exists()


# ── disk guard ────────────────────────────────────────────────────────────

class _FakeUsage:
    def __init__(self, free: int):
        self.free = free
        self.total = free * 4
        self.used = self.total - free


def test_disk_guard_ok_when_plenty_of_free_space(tmp_path):
    result = log_setup.check_disk_guard(
        disk_usage_fn=lambda _root: _FakeUsage(free=10 * 1024 ** 3),
        log_dir=tmp_path,
    )
    assert result.ok
    assert not result.critical
    assert not result.warn


def test_disk_guard_warns_below_warn_threshold_but_above_critical(tmp_path, monkeypatch):
    monkeypatch.setenv("PATHIA_DISK_FREE_WARN_MB", "2000")
    monkeypatch.setenv("PATHIA_DISK_FREE_CRITICAL_MB", "500")
    result = log_setup.check_disk_guard(
        disk_usage_fn=lambda _root: _FakeUsage(free=1000 * 1024 * 1024),  # between 500 and 2000 MB
        log_dir=tmp_path,
    )
    assert result.ok  # warn does not block
    assert not result.critical
    assert result.warn


def test_disk_guard_trips_critical_below_threshold(tmp_path, monkeypatch):
    monkeypatch.setenv("PATHIA_DISK_FREE_CRITICAL_MB", "500")
    result = log_setup.check_disk_guard(
        disk_usage_fn=lambda _root: _FakeUsage(free=100 * 1024 * 1024),  # below 500 MB
        log_dir=tmp_path,
    )
    assert not result.ok
    assert result.critical
    assert "CRITICAL" in result.message


def test_disk_guard_warns_when_log_dir_over_its_own_cap_even_with_free_disk(tmp_path, monkeypatch):
    monkeypatch.setenv("PATHIA_LOG_DIR_MAX_BYTES", "1000")
    _write(tmp_path / "big.log", 5000)
    result = log_setup.check_disk_guard(
        disk_usage_fn=lambda _root: _FakeUsage(free=10 * 1024 ** 3),  # disk itself is fine
        log_dir=tmp_path,
    )
    assert result.ok
    assert result.warn
    assert "cap" in result.message


def test_run_guard_cli_exits_nonzero_on_critical(tmp_path, monkeypatch):
    monkeypatch.setenv("PATHIA_DISK_FREE_CRITICAL_MB", "999999999")  # nothing has this much free
    rc = log_rotate.main(["--dir", str(tmp_path), "--guard", "--quiet"])
    assert rc == 1


def test_run_guard_cli_exits_zero_when_ok(tmp_path, monkeypatch):
    # The CLI has no way to inject a fake disk_usage_fn (that's the point of
    # check_disk_guard's own tests above, which do), so this exercises the
    # real machine's free space. Thresholds pinned tiny so the assertion
    # stays deterministic on any runner that isn't itself out of disk.
    monkeypatch.setenv("PATHIA_DISK_FREE_CRITICAL_MB", "1")
    monkeypatch.setenv("PATHIA_DISK_FREE_WARN_MB", "1")
    rc = log_rotate.main(["--dir", str(tmp_path), "--guard", "--quiet"])
    assert rc == 0


# ── policy knobs read from env (pathia/log_setup.py) ────────────────

def test_policy_defaults_when_no_env_set(monkeypatch):
    for var in (
        "PATHIA_LOG_MAX_BYTES", "PATHIA_LOG_BACKUP_COUNT", "PATHIA_LOG_DIR_MAX_BYTES",
        "PATHIA_DISK_FREE_WARN_MB", "PATHIA_DISK_FREE_CRITICAL_MB", "PATHIA_LOG_ROTATE_INTERVAL_SEC",
    ):
        monkeypatch.delenv(var, raising=False)
    assert log_setup.max_bytes() == log_setup.DEFAULT_MAX_BYTES
    assert log_setup.backup_count() == log_setup.DEFAULT_BACKUP_COUNT
    assert log_setup.log_dir_max_bytes() == log_setup.DEFAULT_LOG_DIR_MAX_BYTES
    assert log_setup.disk_free_warn_bytes() == log_setup.DEFAULT_DISK_FREE_WARN_BYTES
    assert log_setup.disk_free_critical_bytes() == log_setup.DEFAULT_DISK_FREE_CRITICAL_BYTES
    assert log_setup.rotate_interval_sec() == log_setup.DEFAULT_ROTATE_INTERVAL_SEC


def test_policy_env_overrides(monkeypatch):
    monkeypatch.setenv("PATHIA_LOG_MAX_BYTES", "123")
    monkeypatch.setenv("PATHIA_LOG_BACKUP_COUNT", "9")
    monkeypatch.setenv("PATHIA_LOG_DIR_MAX_BYTES", "456")
    assert log_setup.max_bytes() == 123
    assert log_setup.backup_count() == 9
    assert log_setup.log_dir_max_bytes() == 456


def test_policy_env_override_ignores_garbage(monkeypatch):
    monkeypatch.setenv("PATHIA_LOG_MAX_BYTES", "not-a-number")
    assert log_setup.max_bytes() == log_setup.DEFAULT_MAX_BYTES


def test_total_log_bytes_sums_live_and_backup_files(tmp_path):
    _write(tmp_path / "a.log", 100)
    _write(tmp_path / "b.log.1.gz", 50)
    assert log_setup.total_log_bytes(tmp_path) == 150


def test_total_log_bytes_missing_dir_is_zero(tmp_path):
    assert log_setup.total_log_bytes(tmp_path / "does-not-exist") == 0


def test_resolve_log_dir_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIA_LOG_DIR", str(tmp_path))
    assert log_setup.resolve_log_dir() == tmp_path


# ── in-process handler helper (unwired today, but must actually rotate+gzip) ─

def test_configure_logging_rotates_and_gzips(tmp_path):

    target = str(tmp_path / "inprocess.log")
    logger = log_setup.configure_logging(
        "pathia_test_log_setup",
        filename=target,
        max_bytes_override=200,
        backup_count_override=2,
    )
    try:
        for i in range(200):
            logger.info("line %d - padding to force rotation soon enough", i)
        # RotatingFileHandler rotates synchronously on the write that crosses
        # the threshold, so at least one backup must exist by now.
        backups = list(tmp_path.glob("inprocess.log.*"))
        assert backups, "expected at least one rotated backup"
        assert any(b.name.endswith(".gz") for b in backups)
    finally:
        for h in list(logger.handlers):
            h.close()
            logger.removeHandler(h)


# ── the log table must describe the logs that exist ─────────────────────────

def test_every_scheduler_job_log_is_documented():
    """docs/LOGGING.md is the map an operator reads at 3am. It listed
    `logs/updown_sampler.log` for a `restart.sh sampler` action that does not
    exist, and omitted the supervisor, the alert evaluator, the backup and
    capital-flows logs entirely — four of the six jobs actually running.
    """
    import importlib.util
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location(
        "sched_logs", root / "scripts" / "scheduler.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)

    doc = (root / "docs" / "LOGGING.md").read_text()
    missing = [log for log in {j["log"] for j in m.JOBS.values()}
               if f"`{log}`" not in doc]
    assert not missing, f"docs/LOGGING.md does not mention: {missing}"


def test_the_log_table_does_not_document_deleted_processes():
    """A row for something that no longer exists sends the operator looking for
    a file that will never appear."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    doc = (root / "docs" / "LOGGING.md").read_text()
    restart = (root / "scripts" / "restart.sh").read_text()
    for gone in ("--sample-daemon", "polymarket_scout", "poly-board",
                 "poly-judgment"):
        assert gone not in doc, f"docs/LOGGING.md still documents {gone}"
    assert "restart.sh sampler" not in doc and "sampler)" not in restart
