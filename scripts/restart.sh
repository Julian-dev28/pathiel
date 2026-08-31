#!/usr/bin/env bash
# Restart the trading loop + FastAPI server cleanly.
#
# Usage:
#   scripts/restart.sh                # stop both, start both
#   scripts/restart.sh loop           # restart trading loop only
#   scripts/restart.sh server         # restart FastAPI server only
#   scripts/restart.sh stoploop       # STOP the trading loop, keep server + scheduler up
#   scripts/restart.sh sched          # restart the job scheduler only
#   scripts/restart.sh rotate         # restart the log rotator daemon only
#   scripts/restart.sh stoprotate     # stop the log rotator daemon only
#   scripts/restart.sh stop           # stop both, don't start
#   scripts/restart.sh status         # show what's running
#
# Four processes are managed:
#   1. Trading loop  — scripts/trading_loop.py        (continuous scan→trade)
#   2. API server    — python -m hermes_trader.server (FastAPI dashboard on HERMES_PORT, default 8000)
#   3. Scheduler     — scripts/scheduler.py            (cron replacement; cron+launchd are TCC-blocked)
#   4. Log rotator   — scripts/log_rotate.py --daemon  (see docs/LOGGING.md — every process here
#                       logs via `nohup ... >> file 2>&1`, an append fd the process never reopens,
#                       so rotation has to run externally, on an interval, for the whole time the
#                       box is up — not just at restart.sh invocation time)
#
# The MCP server (scripts/hermes-mcp-server.py) is intentionally NOT managed
# here — it's a transient stdio process respawned by Hermes Agent on each
# tool call.

set -euo pipefail

# Resolve project root from this script's location so the command works
# regardless of CWD.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

# Prefer the project venv interpreter (it has the full dep set incl.
# prometheus_client + the hyperliquid stack). Bare `python3` on PATH was a
# different interpreter missing server deps. Override with HERMES_PY if needed.
if [[ -n "${HERMES_PY:-}" ]]; then
  PY="$HERMES_PY"
elif [[ -x "$ROOT/.venv/bin/python" ]]; then
  PY="$ROOT/.venv/bin/python"
else
  PY="python3"
fi

LOG_DIR="$ROOT/logs"
mkdir -p "$LOG_DIR"

LOOP_LOG="$LOG_DIR/trading_loop.log"
SERVER_LOG="$LOG_DIR/server.log"
SCHED_LOG="$LOG_DIR/scheduler.log"
LOOP_PATTERN="scripts/trading_loop.py"
SERVER_PATTERN="hermes_trader.server"
SCHED_PATTERN="scripts/scheduler.py"
ROTATOR_LOG="$LOG_DIR/log_rotate.log"
ROTATOR_PATTERN="log_rotate\.py --daemon"

# Our own PID — must not be killed by pgrep matches.
SELF_PID=$$

# Color helpers (no-op if not a TTY)
if [[ -t 1 ]]; then
  C_RED=$'\033[31m'; C_GRN=$'\033[32m'; C_YEL=$'\033[33m'; C_DIM=$'\033[2m'; C_OFF=$'\033[0m'
else
  C_RED=""; C_GRN=""; C_YEL=""; C_DIM=""; C_OFF=""
fi

info()  { printf "%s[restart]%s %s\n" "$C_DIM" "$C_OFF" "$*"; }
ok()    { printf "%s✓%s %s\n" "$C_GRN" "$C_OFF" "$*"; }
warn()  { printf "%s!%s %s\n" "$C_YEL" "$C_OFF" "$*"; }
err()   { printf "%s✗%s %s\n" "$C_RED" "$C_OFF" "$*" >&2; }

# ── deliberate-halt marker ───────────────────────────────────────────────────
# A supervisor restarts anything it finds dead. Without this marker it would
# also restart what the OPERATOR just stopped, which would quietly break
# `stoploop` as a kill switch — the one control that has to work. So every
# stop-and-stay-stopped action records its component here, and every start
# clears it. The marker is the operator's intent; the supervisor obeys it.
# Must match STATE_DIR in scripts/supervise_processes.py — HERMES_STATE_DIR,
# else the project root. A halt marker written where the supervisor does not
# look is a kill switch that silently does nothing, which is exactly what
# happened: .env.local carries HERMES_STATE_DIR and this script never read it,
# so `stoploop` wrote the marker to the repo root while the supervisor — run by
# the scheduler, which does load the env — looked in .state/ and restarted the
# loop two minutes later.
if [[ -z "${HERMES_STATE_DIR:-}" && -f "$ROOT/.env.local" ]]; then
  HERMES_STATE_DIR="$(grep -E '^HERMES_STATE_DIR=' "$ROOT/.env.local" | tail -1 | cut -d= -f2-)"
  export HERMES_STATE_DIR
fi
HALT_FILE="${HERMES_STATE_DIR:-$ROOT}/supervisor_halt.json"

halt_mark() {   # halt_mark <component>
  mkdir -p "$(dirname "$HALT_FILE")"
  "$PY" - "$HALT_FILE" "$1" mark <<'PYEOF'
import json, os, sys
path, comp, op = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    cur = set(json.load(open(path)).get("halted", []))
except Exception:
    cur = set()
cur.add(comp) if op == "mark" else cur.discard(comp)
tmp = path + ".tmp"
with open(tmp, "w") as fh:
    json.dump({"halted": sorted(cur)}, fh)
    fh.flush(); os.fsync(fh.fileno())
os.replace(tmp, path)
PYEOF
}

halt_clear() {  # halt_clear <component>
  mkdir -p "$(dirname "$HALT_FILE")"
  "$PY" - "$HALT_FILE" "$1" clear <<'PYEOF'
import json, os, sys
path, comp, op = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    cur = set(json.load(open(path)).get("halted", []))
except Exception:
    cur = set()
cur.add(comp) if op == "mark" else cur.discard(comp)
tmp = path + ".tmp"
with open(tmp, "w") as fh:
    json.dump({"halted": sorted(cur)}, fh)
    fh.flush(); os.fsync(fh.fileno())
os.replace(tmp, path)
PYEOF
}

# Find PIDs matching a pattern, excluding our own shell + grep.
pids_for() {
  local pattern="$1"
  local pids=""
  # -f matches the full command line. On some managed macOS shells pgrep can
  # temporarily fail when sysmond is unavailable; fall back to ps so orphaned
  # screen/nohup loops are still found before starting another trader.
  if command -v pgrep >/dev/null 2>&1; then
    pids="$(pgrep -f "$pattern" 2>/dev/null || true)"
  fi
  if [[ -z "$pids" ]]; then
    pids="$(
      ps ax -o pid= -o command= | awk -v pat="$pattern" -v self="$SELF_PID" '
        {
          pid=$1
          $1=""
          sub(/^ +/, "", $0)
          if (pid != self && $0 ~ pat && $0 !~ /scripts\/restart\.sh/ && $0 !~ /awk -v pat/) {
            print pid
          }
        }'
    )"
  fi
  printf "%s\n" "$pids" | awk -v self="$SELF_PID" 'NF && $1 != self && !seen[$1]++ {print $1}'
}

stop_proc() {
  local label="$1" pattern="$2"
  local pids
  pids="$(pids_for "$pattern")"
  if [[ -z "$pids" ]]; then
    info "$label: not running"
    return 0
  fi
  info "$label: sending SIGTERM to $(echo "$pids" | tr '\n' ' ')"
  echo "$pids" | xargs -I {} kill {} 2>/dev/null || true
  # Wait up to 5s for graceful exit.
  for _ in 1 2 3 4 5; do
    sleep 1
    pids="$(pids_for "$pattern")"
    [[ -z "$pids" ]] && { ok "$label: stopped"; return 0; }
  done
  warn "$label: did not exit on SIGTERM, sending SIGKILL"
  pids="$(pids_for "$pattern")"
  [[ -n "$pids" ]] && echo "$pids" | xargs -I {} kill -9 {} 2>/dev/null || true
  sleep 1
  pids="$(pids_for "$pattern")"
  if [[ -n "$pids" ]]; then
    err "$label: still alive after SIGKILL (pids: $pids)"
    return 1
  fi
  ok "$label: killed"
}

start_loop() {
  local pids
  pids="$(pids_for "$LOOP_PATTERN")"
  if [[ -n "$pids" ]]; then
    warn "trading loop already running (pids: $pids) — skipping"
    return 0
  fi
  info "starting trading loop (log: $LOOP_LOG)"
  HERMES_STARTUP_GRACE_S="${HERMES_STARTUP_GRACE_S:-12}" \
  HERMES_META_PREWARM_TIMEOUT_S="${HERMES_META_PREWARM_TIMEOUT_S:-3}" \
    nohup "$PY" "$ROOT/scripts/trading_loop.py" >> "$LOOP_LOG" 2>&1 &
  local pid=$!
  disown "$pid" 2>/dev/null || true
  sleep 1
  if kill -0 "$pid" 2>/dev/null; then
    ok "trading loop: pid $pid"
    # Keep the host awake while the loop runs. On a laptop the trading process
    # otherwise freezes on idle/maintenance sleep (multi-minute-to-hour scan
    # blackouts; only the server-side SL/TP brackets protect positions then).
    # `-w $pid` ties the assertion to the loop's lifetime; the watchdog re-execs
    # in place (same pid), so this survives a self-heal. Best-effort — a missing
    # caffeinate (non-macOS) just means no keep-awake. Tip: stay on AC power, as
    # battery + closed lid can still clamshell-sleep despite this.
    if command -v caffeinate >/dev/null 2>&1; then
      nohup caffeinate -i -m -w "$pid" >/dev/null 2>&1 &
      disown 2>/dev/null || true
      info "caffeinate: holding system awake while loop $pid runs"
    fi
  else
    err "trading loop died immediately — see $LOOP_LOG"
    tail -n 20 "$LOOP_LOG" >&2 || true
    return 1
  fi
}

start_server() {
  local pids
  pids="$(pids_for "$SERVER_PATTERN")"
  if [[ -n "$pids" ]]; then
    warn "server already running (pids: $pids) — skipping"
    return 0
  fi
  local port="${HERMES_PORT:-8000}"
  info "starting FastAPI server on port $port (log: $SERVER_LOG)"
  # Dashboard shares the IP with the trading loop; HL rate-limits per-IP. Give the
  # server a HARD-throttled token bucket (~1/4 budget) so its background polls yield
  # to the loop's fetches — cuts the chronic ~24% /info 429 collisions. The loop keeps
  # its full budget (it's the money path). Tunable: bump if the dashboard feels sluggish.
  nohup env HERMES_STATE_READONLY=1 \
    HERMES_HL_RATE_REFILL_PER_SEC="${HERMES_SERVER_RATE_REFILL:-2}" \
    HERMES_HL_RATE_CAPACITY="${HERMES_SERVER_RATE_CAPACITY:-60}" \
    "$PY" -m hermes_trader.server >> "$SERVER_LOG" 2>&1 &
  local pid=$!
  disown "$pid" 2>/dev/null || true
  sleep 2
  if kill -0 "$pid" 2>/dev/null; then
    ok "server: pid $pid → http://localhost:$port"
  else
    err "server died immediately — see $SERVER_LOG"
    tail -n 20 "$SERVER_LOG" >&2 || true
    return 1
  fi
}

start_sched() {
  local pids
  pids="$(pids_for "$SCHED_PATTERN")"
  if [[ -n "$pids" ]]; then
    warn "scheduler already running (pids: $pids) — skipping"
    return 0
  fi
  # Scheduled jobs MUST be launched from here, not from cron or launchd. macOS
  # TCC gates ~/Documents on the responsible process; neither cron nor launchd
  # holds that grant, so both die with "Operation not permitted" before Python
  # boots (measured 2026-07-25: the autonomous evidence loop never ran once in
  # five days). This shell runs in the operator's session, which does hold it,
  # and the child inherits it.
  info "starting scheduler (log: $SCHED_LOG)"
  nohup "$PY" "$ROOT/scripts/scheduler.py" >> "$SCHED_LOG" 2>&1 &
  local pid=$!
  disown "$pid" 2>/dev/null || true
  sleep 1
  if kill -0 "$pid" 2>/dev/null; then
    ok "scheduler: pid $pid"
  else
    err "scheduler died immediately — see $SCHED_LOG"
    tail -n 20 "$SCHED_LOG" >&2 || true
    return 1
  fi
}

show_status() {
  printf "\n%sStatus%s\n" "$C_DIM" "$C_OFF"
  local loop_pids server_pids sched_pids rotator_pids
  loop_pids="$(pids_for "$LOOP_PATTERN")"
  server_pids="$(pids_for "$SERVER_PATTERN")"
  sched_pids="$(pids_for "$SCHED_PATTERN")"
  rotator_pids="$(pids_for "$ROTATOR_PATTERN")"
  if [[ -n "$loop_pids" ]]; then
    ok "trading loop: pids $loop_pids"
  else
    warn "trading loop: stopped"
  fi
  if [[ -n "$server_pids" ]]; then
    ok "server:       pids $server_pids"
  else
    warn "server:       stopped"
  fi
  if [[ -n "$sched_pids" ]]; then
    ok "scheduler:    pids $sched_pids"
  else
    warn "scheduler:    stopped"
  fi
  if [[ -n "$rotator_pids" ]]; then
    ok "log rotator:  pids $rotator_pids"
  else
    warn "log rotator:  stopped"
  fi
  printf "\n"
  "$PY" "$ROOT/scripts/log_rotate.py" --guard 2>&1 | sed "s/^/${C_DIM}[disk]${C_OFF} /" || true
  printf "\n"
}

action="${1:-restart}"
start_rotator() {
  local pids
  pids="$(pids_for "$ROTATOR_PATTERN")"
  if [[ -n "$pids" ]]; then
    warn "log rotator already running (pids: $pids) — skipping"
    return 0
  fi
  info "starting log rotator (log: $ROTATOR_LOG)"
  # Every process above logs via shell `>>` append redirection — an fd none
  # of them ever reopens (see hermes_trader/log_setup.py). Rotation has to
  # run externally, on its own clock, for as long as the box is up; it
  # cannot be a one-shot step at restart.sh invocation time, since a process
  # can run for days between restarts. This mirrors the caffeinate
  # pattern above: its own long-lived nohup'd process, managed the same way.
  nohup "$PY" "$ROOT/scripts/log_rotate.py" --daemon >> "$ROTATOR_LOG" 2>&1 &
  local pid=$!
  disown "$pid" 2>/dev/null || true
  sleep 1
  if kill -0 "$pid" 2>/dev/null; then
    ok "log rotator: pid $pid"
  else
    err "log rotator died immediately — see $ROTATOR_LOG"
    tail -n 20 "$ROTATOR_LOG" >&2 || true
  fi
}

# Refuse to (re)start processes when free disk is critically low, and (for
# every action except a pure `status` read) run one immediate rotation pass
# so a long-since-oversized logs/ doesn't have to wait for the daemon's next
# tick. `HERMES_SKIP_DISK_GUARD=1` overrides the refusal for a deliberate
# emergency start (e.g. restarting just to free a wedged process while disk
# cleanup happens by hand); it never skips the rotation pass itself.
run_disk_guard() {
  local enforce="$1" do_rotate="$2"
  local out rc
  # `out="$(...)"` alone would abort the script right here under `set -e`
  # when the guard exits 1 (critical) — a plain assignment's exit status IS
  # the substitution's, and set -e treats that as any other failing simple
  # command. The `&& rc=0 || rc=$?` keeps this a compound list, which set -e
  # does not abort on, so the critical/override decision below actually runs.
  out="$("$PY" "$ROOT/scripts/log_rotate.py" --guard 2>&1)" && rc=0 || rc=$?
  if [[ $rc -ne 0 ]]; then
    err "DISK GUARD: $out"
    if [[ "$enforce" == "1" && "${HERMES_SKIP_DISK_GUARD:-0}" != "1" ]]; then
      err "refusing to start — free disk below critical threshold."
      err "free up space, or set HERMES_SKIP_DISK_GUARD=1 to override and start anyway."
      exit 1
    else
      warn "DISK GUARD tripped but not blocking ($([[ "$enforce" == "1" ]] && echo "HERMES_SKIP_DISK_GUARD=1 set" || echo "action does not start processes"))"
    fi
  elif [[ "$out" == *WARN:* ]]; then
    warn "$out"
  fi
  # Full status line (ok/warn/critical) is echoed again at the bottom of
  # show_status on every action; only surface it here when it's actionable.
  if [[ "$do_rotate" == "1" ]]; then
    "$PY" "$ROOT/scripts/log_rotate.py" --once --quiet || true
  fi
}

# Disk-guard enforcement: block only actions that (re)start a trading/serving
# process. Actions that merely stop things, check status, or manage the
# rotator itself must never be blocked by the guard they exist to unblock.
#
# Rotation sweep: run on every action that actually manages a process —
# every one of them represents the operator touching the box, and this is
# the only reliable periodic touchpoint outside the rotator daemon itself
# (cron/launchd are TCC-blocked here, see scripts/scheduler.py). `status` is
# the one exception, kept a pure read with no side effect.
DISK_GUARD_ENFORCE=0
DISK_GUARD_ROTATE=1
case "$action" in
  restart|""|loop|server|sched|scheduler) DISK_GUARD_ENFORCE=1 ;;
esac
case "$action" in
  status) DISK_GUARD_ROTATE=0 ;;
esac
run_disk_guard "$DISK_GUARD_ENFORCE" "$DISK_GUARD_ROTATE"

case "$action" in
  restart|"")
    stop_proc "trading loop" "$LOOP_PATTERN"
    stop_proc "server" "$SERVER_PATTERN"
    stop_proc "scheduler" "$SCHED_PATTERN"
    halt_clear loop; halt_clear server; halt_clear rotator; halt_clear scheduler
    start_server
    start_loop
    start_sched
    start_rotator
    show_status
    ;;
  loop)
    stop_proc "trading loop" "$LOOP_PATTERN"
    halt_clear loop
    start_loop
    show_status
    ;;
  stoploop)
    # stop ONLY the trading loop (no scans/trades); leave the dashboard +
    # scheduler running so the /trends cache refresh stays up.
    halt_mark loop
    stop_proc "trading loop" "$LOOP_PATTERN"
    show_status
    ;;
  server)
    stop_proc "server" "$SERVER_PATTERN"
    halt_clear server
    start_server
    show_status
    ;;
  sched|scheduler)
    stop_proc "scheduler" "$SCHED_PATTERN"
    halt_clear scheduler
    start_sched
    show_status
    ;;
  rotate|rotator)
    stop_proc "log rotator" "$ROTATOR_PATTERN"
    halt_clear rotator
    start_rotator
    show_status
    ;;
  stoprotate|stoprotator)
    halt_mark rotator
    stop_proc "log rotator" "$ROTATOR_PATTERN"
    show_status
    ;;
  stop)
    halt_mark loop; halt_mark server; halt_mark rotator; halt_mark scheduler
    stop_proc "trading loop" "$LOOP_PATTERN"
    stop_proc "server" "$SERVER_PATTERN"
    stop_proc "scheduler" "$SCHED_PATTERN"
    stop_proc "log rotator" "$ROTATOR_PATTERN"
    show_status
    ;;
  status)
    show_status
    ;;
  *)
    err "unknown action: $action"
    err "usage: $0 [restart|loop|server|sched|stoploop|rotate|stoprotate|stop|status]"
    exit 2
    ;;
esac
