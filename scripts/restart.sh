#!/usr/bin/env bash
# Restart the trading loop + FastAPI server cleanly.
#
# Usage:
#   scripts/restart.sh                # stop both, start both
#   scripts/restart.sh loop           # restart trading loop only
#   scripts/restart.sh server         # restart FastAPI server only
#   scripts/restart.sh stop           # stop both, don't start
#   scripts/restart.sh status         # show what's running
#
# Two processes are managed:
#   1. Trading loop  — scripts/trading_loop.py        (continuous scan→trade)
#   2. API server    — python -m hermes_trader.server (FastAPI dashboard on HERMES_PORT, default 8000)
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
LOOP_PATTERN="scripts/trading_loop.py"
SERVER_PATTERN="hermes_trader.server"

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
  HERMES_STARTUP_GRACE_S="${HERMES_STARTUP_GRACE_S:-0}" \
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
  nohup env HERMES_HL_RATE_REFILL_PER_SEC="${HERMES_SERVER_RATE_REFILL:-5}" \
    HERMES_HL_RATE_CAPACITY="${HERMES_SERVER_RATE_CAPACITY:-200}" \
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

show_status() {
  printf "\n%sStatus%s\n" "$C_DIM" "$C_OFF"
  local loop_pids server_pids
  loop_pids="$(pids_for "$LOOP_PATTERN")"
  server_pids="$(pids_for "$SERVER_PATTERN")"
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
  printf "\n"
}

action="${1:-restart}"
case "$action" in
  restart|"")
    stop_proc "trading loop" "$LOOP_PATTERN"
    stop_proc "server" "$SERVER_PATTERN"
    start_server
    start_loop
    show_status
    ;;
  loop)
    stop_proc "trading loop" "$LOOP_PATTERN"
    start_loop
    show_status
    ;;
  server)
    stop_proc "server" "$SERVER_PATTERN"
    start_server
    show_status
    ;;
  stop)
    stop_proc "trading loop" "$LOOP_PATTERN"
    stop_proc "server" "$SERVER_PATTERN"
    show_status
    ;;
  status)
    show_status
    ;;
  *)
    err "unknown action: $action"
    err "usage: $0 [restart|loop|server|stop|status]"
    exit 2
    ;;
esac
