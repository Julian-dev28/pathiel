# Logging & log rotation — Reference

Before this existed: `logs/` grew unbounded. On 2026-08-29, before rotation
shipped, it was 84 MB and climbing — `trading_loop.log` 36 MB,
`updown_sampler.log` 25 MB, `server.log` 15 MB — with zero cap anywhere. On a
small deployed box that fills the disk and takes the bot down silently (a
full disk breaks `.state/` writes, order placement, everything). This doc is
the reference for what rotates, how, and how to check or force it.

## Where each process logs

Every pathia process is started by `scripts/restart.sh` with shell
append redirection — `nohup "$PY" ... >> logs/<name>.log 2>&1 &`. That `>>`
opens the file once, in append mode, and the process's entire stdout/stderr
(its `logging` output, any bare `print()`, and any uncaught traceback) flows
through that one file descriptor for its whole life. See "Why rotation runs
outside every process" below for why that fact drives the whole design.

| File | Written by | Started via |
|---|---|---|
| `logs/trading_loop.log` | `scripts/trading_loop.py` | `restart.sh loop` / `restart.sh restart` |
| `logs/server.log` | `python -m pathia.server` | `restart.sh server` / `restart.sh restart` |
| `logs/scheduler.log` | `scripts/scheduler.py`'s own stdout/stderr | `restart.sh sched` / `restart.sh restart` |
| `logs/log_rotate.log` | the log-rotator daemon itself | `restart.sh rotate` / `restart.sh restart` |
| `logs/autonomous_cycle.log` | `scheduler.py` job `autonomous-cycle` | fired by the scheduler |
| `logs/trend_engine.log` | `scheduler.py` job `trends-price` | fired by the scheduler |
| `logs/capital_flows.log` | `scheduler.py` job `capital-flows` | fired by the scheduler |
| `logs/supervisor.log` | `scheduler.py` job `supervisor` — restarts dead processes | fired by the scheduler |
| `logs/alerts_eval.log` | `scheduler.py` job `alerts` — each evaluation pass | fired by the scheduler |
| `logs/alerts.log` | `scripts/alert_eval.py` — one line per alert that FIRED or resolved, not per pass | written by the `alerts` job |
| `logs/backup_state.log` | `scheduler.py` job `backup-state` | fired by the scheduler |

`scheduler.py` opens each job's log fresh, in append mode, for the duration
of that one `subprocess.run()` call (see `scripts/scheduler.py:run_job`) —
it does not hold the fd open between runs the way the four `nohup`-started
processes above do.

Any **new** log file works automatically as long as its name ends in
`.log` and it lives directly under `logs/` — the rotator globs `logs/*.log`,
nothing is hardcoded to today's filenames.

## Why rotation runs outside every process

A `logging.handlers.RotatingFileHandler` configured *inside* one of these
processes would rotate its own, second handle to the file while the shell's
`nohup >> file` fd — the thing actually catching stdout/stderr — kept
appending to whatever inode it was opened against. Renaming the file out
from under that fd doesn't stop the process's real output; it just moves
where the growth happens, silently, with the "new" file at the original path
staying empty forever. This is exactly the scenario `logrotate`'s
`copytruncate` option exists for: an appender that will never be told to
reopen its output.

So `scripts/log_rotate.py` rotates **in place** — same path, same inode:

1. snapshot the file's current size `S`
2. read exactly `S` bytes (never "until EOF" — a concurrent writer only ever
   appends past `S`, so this can't pick up a partial write)
3. `os.truncate(path, 0)` — the *path's* length goes to zero; any fd already
   open on that inode (the shell's `nohup` fd, or several concurrent ones —
   see below) keeps writing at its own current position via `O_APPEND`, which
   now means "right after the new EOF." No signal, no cooperation from the
   writer required.
4. off the hot path: gzip the captured bytes to `name.log.N.gz`, shifting
   older numbered backups down and dropping anything past the retention count

Full reasoning and the in-process `RotatingFileHandler` alternative (kept
available via `pathia.log_setup.configure_logging()` for a future
process that owns its own fd outright) live in that module's docstring.

### The copytruncate race, named and bounded

Between step 2 finishing and step 3 running, a write landing in that gap is
in neither the captured backup (already read) nor the truncated file
(zeroed regardless of what was there) — it's lost. POSIX has no atomic
"read-and-truncate." This is not eliminated; it's bounded:

- nothing except the read and the truncate happens in between — no gzip, no
  renames — so the window is the time to read up to `PATHIA_LOG_MAX_BYTES`
  (default 20 MB) off local disk: low single-digit milliseconds in practice
- a file only rotates once it's already over threshold, swept on a fixed
  interval (default every 300s) — the window opens rarely, not per write
- worst case: one `write()` call (at most a few KB, one log line) lands in
  the gap, on a file that's already megabytes past its rotation size.
  Operationally negligible against the alternative — unbounded growth to a
  full disk.

### Concurrent writers

The rotator truncates a log in place while the process that owns it still
holds an open `O_APPEND` fd — it never reopens on rotation. Writes after the
truncation still land correctly, and they do so even when several fds are
open on the same file, covered by
`test_two_concurrent_open_append_fds_both_keep_writing_after_rotation` in
`tests/test_log_rotation.py`. This is inherent to `O_APPEND` semantics on a
local filesystem, not something the rotator has to do anything special for.

## Rotation policy

All defaults live in `pathia/log_setup.py`, overridable via env
(set in `.env.local` or the process environment):

| Setting | Env var | Default |
|---|---|---|
| Per-file size that triggers rotation | `PATHIA_LOG_MAX_BYTES` | 20 MB |
| Backups kept per file (`name.log.1.gz` .. `.N.gz`) | `PATHIA_LOG_BACKUP_COUNT` | 5 |
| Cap on `logs/`'s total size (live + backups) | `PATHIA_LOG_DIR_MAX_BYTES` | 750 MB |
| Rotator daemon sweep interval | `PATHIA_LOG_ROTATE_INTERVAL_SEC` | 300s (5 min) |
| Disk-free level that just warns | `PATHIA_DISK_FREE_WARN_MB` | 2048 MB |
| Disk-free level that refuses to start | `PATHIA_DISK_FREE_CRITICAL_MB` | 500 MB |
| Override the critical-disk refusal | `PATHIA_SKIP_DISK_GUARD=1` | unset |
| Point rotation at a different directory | `PATHIA_LOG_DIR` | `<repo>/logs` |

**Compression**: every rotated backup is gzip'd (`name.log.N.gz`); nothing is
kept uncompressed once truncation has happened.

**Retention**: newest generation is always `.1.gz`; on the next rotation
`.1.gz`→`.2.gz`, …, and anything beyond `PATHIA_LOG_BACKUP_COUNT` is deleted.
Retention is per-file, not calendar-based — a quiet file's backups simply sit
there until that file itself rotates again.

**Directory cap**: if `logs/`'s total size (summed across every live file and
every backup) exceeds `PATHIA_LOG_DIR_MAX_BYTES`, the oldest `*.gz` backups
are deleted first (never a live, non-backup `.log` file). If the cap is still
exceeded after every backup is gone — meaning live files alone account for
more than the cap — the single largest live file is force-rotated
immediately, regardless of whether it's individually over
`PATHIA_LOG_MAX_BYTES` yet, rather than ever deleting log content that
hasn't been captured anywhere.

## Disk guard

`scripts/restart.sh` runs `scripts/log_rotate.py --guard` before every
action. Two things can happen:

- **free disk < `PATHIA_DISK_FREE_CRITICAL_MB`**: `restart.sh` refuses to
  start any process (`loop`, `server`, `sched`, `sampler`, or plain
  `restart`) and exits 1. Override for a deliberate emergency start with
  `PATHIA_SKIP_DISK_GUARD=1`. This never blocks an action that doesn't start
  a process (`status`, `stop`, `stoploop`, `stopsampler`, `stoprotate`).
- **free disk < `PATHIA_DISK_FREE_WARN_MB`, or `logs/` over its cap**: prints
  a loud warning but does not block anything.

Every action **except `status`** also runs one immediate rotation pass
(`log_rotate.py --once`) as part of the guard step — restart.sh is the one
reliably-invoked touchpoint on this box (cron and launchd are TCC-blocked
here, see `scripts/scheduler.py`'s module docstring), so every time the
operator touches it is also a chance to sweep `logs/` without waiting for
the daemon's next tick. `status` stays a pure read with no side effect.

## The rotator daemon

Because every managed process can run for days between `restart.sh`
invocations, an immediate sweep at startup isn't enough — rotation needs to
run continuously while the box is up. `restart.sh` manages a fifth
long-lived process for exactly that:

```
scripts/log_rotate.py --daemon     # loops forever, sweeps logs/ every PATHIA_LOG_ROTATE_INTERVAL_SEC
```

Started/stopped the same way as the other managed processes:

```bash
scripts/restart.sh restart      # stops+starts loop, server, scheduler, AND the rotator
scripts/restart.sh rotate       # restart the rotator only
scripts/restart.sh stoprotate   # stop the rotator only
scripts/restart.sh status       # shows whether it's running, plus a live disk-guard readout
```

A single bad sweep (permission error, disk full mid-write) is caught and
logged; the daemon loop keeps running rather than exiting.

## Inspecting and forcing rotation

```bash
# Read-only: what would rotate, what's the current disk/logs-dir state
.venv/bin/python scripts/log_rotate.py --guard

# One sweep of the whole logs/ directory right now (same thing restart.sh
# runs on every non-status action)
.venv/bin/python scripts/log_rotate.py --once

# Force-rotate one specific file even if it's under the size threshold
.venv/bin/python scripts/log_rotate.py --file logs/trading_loop.log --force

# Point at a different directory (e.g. to dry-run against a copy)
.venv/bin/python scripts/log_rotate.py --dir /path/to/some/logs --once

# Decompress a backup to read it
gzip -dc logs/trading_loop.log.1.gz | less
```

## What still logs unbounded

Nothing under `logs/*.log` — the rotator globs the whole directory. Two
things worth knowing:

- **`pathia/log_setup.py`'s `configure_logging()` (in-process
  `RotatingFileHandler`) is not wired into any current entrypoint.**
  `scripts/trading_loop.py` and `pathia/server.py` both call
  `logging.basicConfig()` with no `filename=`, i.e. they log to
  stdout/stderr — which is exactly the fd `restart.sh`'s `nohup >>`
  redirection owns, so the external rotator (not this helper) is what
  actually bounds their log files today. The helper exists, tested
  (`test_configure_logging_rotates_and_gzips`), and ready for the day either
  entrypoint is changed to own its fd directly instead of relying on shell
  redirection — at that point it should call `configure_logging()` instead
  of `logging.basicConfig()`, and stop needing the external rotator for that
  one file.
- **A process's own stderr traceback on crash** still goes through the same
  shell-owned fd as everything else, so it lands in the `.log` file and gets
  rotated like any other content — nothing special needed, called out here
  only because it's the detail that rules out relying on the `logging`
  module's own machinery instead of the external rotator.
