#!/usr/bin/env bash
# End-to-end test of the rented-server path, against a local Ubuntu 24.04
# container standing in for the server (amd64, like a Hetzner CX22).
# Run ON THE MAC with Docker running:
#
#   scripts/vps/e2e_docker.sh [LOCAL_STATE_DIR]
#
# Runs the real setup_server.sh, push.sh, start.sh and pull_state.sh, plus
# the tick's own scripts and the full read-only MCP smoke on the box.
#
# Safe while the Mac's desk is live:
#   - the "Mac env" is a fake file (PATHIEL_ENV_FILE); the real .env.local is
#     never read. Its keys are random and the server's API wallet key is
#     random too, so Hyperliquid has never authorized either: nothing can
#     trade. The main account address is public and is only read.
#   - the live state is COPIED to a temp dir first; pull_state.sh writes into
#     that copy, never into LOCAL_STATE_DIR.
#   - Claude Code is installed but not logged in, so no tick runs. The
#     check that Claude reaches the MCP server is run anyway and must FAIL
#     here (no login): it may never pass on a box that cannot trade.
# A shim stands in for `pgrep` on the Mac (the live watcher would make push.sh
# refuse, correctly); tests/test_vps_deploy.py covers that guard, and this
# script checks it against the real pgrep at the end.
#
# About 5 minutes. Exit 0 = every check passed. Cleans up after itself.
set -euo pipefail

LIVE_STATE="${1:-$HOME/Documents/code/pathia/.state}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NAME="pathiel-vps-e2e"
PORT="${PATHIEL_E2E_PORT:-2222}"
W="$(mktemp -d)"
LOG="/tmp/pathiel-vps-e2e/progress.log"
mkdir -p "$(dirname "$LOG")"
PY="$ROOT/.venv/bin/python"

cleanup() {
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  rm -rf "$W"
}
trap cleanup EXIT

say() { echo "VPS-E2E $(date -u +%H:%M:%SZ) $*" | tee -a "$LOG"; }
fail() { say "FAIL $*"; exit 1; }

docker info >/dev/null 2>&1 || fail "Docker is not running (open -a Docker)"
[[ -d "$LIVE_STATE/strategies" ]] || fail "no state at $LIVE_STATE"

# ── the stand-in server ──────────────────────────────────────────────────────
say "1/7 start Ubuntu 24.04 (amd64) with sshd on 127.0.0.1:$PORT"
ssh-keygen -q -t ed25519 -N "" -f "$W/key" -C vps-e2e
docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" --platform linux/amd64 --cap-add NET_ADMIN \
  -p "127.0.0.1:$PORT:22" ubuntu:24.04 bash -c "
    apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq openssh-server >/dev/null &&
    mkdir -p /run/sshd /root/.ssh && echo '$(cat "$W/key.pub")' > /root/.ssh/authorized_keys &&
    chmod 600 /root/.ssh/authorized_keys && exec /usr/sbin/sshd -D" >/dev/null

mkdir -p "$W/shims"
cat > "$W/shims/ssh" <<EOF
#!/bin/bash
exec /usr/bin/ssh -i "$W/key" -p $PORT -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR "\$@"
EOF
cat > "$W/shims/pgrep" <<'EOF'
#!/bin/bash
for a in "$@"; do [[ "$a" == *trigger_watch* ]] && exit 1; done
exec /usr/bin/pgrep "$@"
EOF
chmod +x "$W/shims/"*
export PATH="$W/shims:$PATH"
for _ in $(seq 1 120); do ssh -n root@localhost true 2>/dev/null && break; sleep 2; done
ssh -n root@localhost true || fail "sshd never came up"
R() { ssh -n pathiel@localhost "cd ~/pathiel && export PATH=\$HOME/pathiel/.venv/bin:\$HOME/.local/bin:\$PATH && $1"; }

# ── setup ────────────────────────────────────────────────────────────────────
say "2/7 setup_server.sh"
ssh root@localhost 'bash -s' < "$ROOT/scripts/vps/setup_server.sh" >"$W/setup.log" 2>&1 \
  || { tail -20 "$W/setup.log"; fail "setup_server.sh"; }
ssh -n root@localhost 'ufw status | grep -q "Status: active" && ! ufw status | grep -q 8000' \
  || fail "firewall not active or :8000 open"

# ── push ─────────────────────────────────────────────────────────────────────
say "3/7 push.sh (fake Mac env, copied state, random API wallet key)"
cp -a "$LIVE_STATE" "$W/state"
umask 077
OLDKEY=$("$PY" -c "import secrets;print('0x'+secrets.token_hex(32))")
NEWKEY=$("$PY" -c "import secrets;print('0x'+secrets.token_hex(32))")
OLDADDR=$("$PY" -c "from eth_account import Account;print(Account.from_key('$OLDKEY').address)")
cat > "$W/mac.env" <<EOF
HYPERLIQUID_WALLET_ADDRESS=$OLDADDR
HYPERLIQUID_PRIVATE_KEY=$OLDKEY
HYPERLIQUID_MASTER_ADDRESS=0x2c2e4efF574B3Fd9879AdeF9c5CAC6fD7C916985
HYPERLIQUID_MASTER_PRIVATE_KEY=0x$(printf 'c%.0s' {1..64})
PATHIEL_STATE_DIR=$W/state
EOF
NEWADDR=$("$PY" -c "from eth_account import Account;print(Account.from_key('$NEWKEY').address)")
printf 'HYPERLIQUID_VPS_WALLET_ADDRESS=%s\nHYPERLIQUID_VPS_PRIVATE_KEY=%s\n' "$NEWADDR" "$NEWKEY" >> "$W/mac.env"
PATHIEL_ENV_FILE="$W/mac.env" "$ROOT/scripts/vps/push.sh" pathiel@localhost "$W/state" \
  >"$W/push.log" 2>&1 || { grep -v "$NEWKEY" "$W/push.log" | tail -10; fail "push.sh"; }
grep -q "$NEWKEY" "$W/push.log" && fail "push.sh printed the key"
R "test \"\$(stat -c %a .env.local)\" = 600" || fail ".env.local is not 0600"
R "grep -qx 'HYPERLIQUID_PRIVATE_KEY=$NEWKEY' .env.local" || fail "server key is not the new API wallet key"
R "! grep -q VPS .env.local" || fail "a HYPERLIQUID_VPS_ name reached the server"
grep -q "server signer  $NEWADDR" "$W/push.log" || fail "push.sh did not report the VPS address as the signer"
R "! grep -q MASTER_PRIVATE .env.local && ! grep -q '$OLDKEY' .env.local" || fail "a Mac key reached the server"
R "test ! -d .git && test -d ~/state/strategies" || fail "repo or state did not land"

# ── start ────────────────────────────────────────────────────────────────────
say "4/7 start.sh (venv from the hashed lock, Desk server, tmux)"
R "PATHIEL_SKIP_LOGIN_CHECK=1 scripts/vps/start.sh" >"$W/start.log" 2>&1 || { tail -20 "$W/start.log"; fail "start.sh"; }
R "test \"\$(curl -s -o /dev/null -w %{http_code} localhost:8000/api/health)\" = 200" || fail "/api/health"
R "test \"\$(curl -s -o /dev/null -w %{http_code} localhost:8000/desk/oil)\" = 200" || fail "/desk/oil"
R "tmux has-session -t desk" || fail "no tmux session"
R "scripts/vps/check_claude.sh" >"$W/claude.log" 2>&1 && fail "check_claude.sh passed without a login"
grep -q "CLAUDE-CHECK FAIL" "$W/claude.log" || { cat "$W/claude.log"; fail "check_claude.sh gave no reason"; }
say "    no login, as expected: $(grep -m1 'CLAUDE-CHECK FAIL' "$W/claude.log" | cut -c1-90)"

# ── what a tick runs ─────────────────────────────────────────────────────────
say "5/7 tick scripts and the MCP smoke on the server"
R "python3 scripts/desk_slot.py --desk oil >/dev/null && python3 scripts/desk_slot.py --desk metals >/dev/null" || fail "desk_slot.py"
R "python3 scripts/oil_levels.py --coin xyz:BRENTOIL >/dev/null" || fail "oil_levels.py"
R "timeout 120 python3 scripts/trigger_watch.py --once >/dev/null" || fail "trigger_watch.py --once"
R "python3 scripts/mcp_smoke.py" >"$W/smoke.log" 2>&1 || { grep -E "FAIL|calls," "$W/smoke.log"; fail "mcp_smoke.py"; }
say "    $(grep 'calls,' "$W/smoke.log")"

# ── guards ───────────────────────────────────────────────────────────────────
say "6/7 push guards"
if echo x | PATHIEL_ENV_FILE="$W/mac.env" "$ROOT/scripts/vps/push.sh" pathiel@localhost "$W/state" >/dev/null 2>&1; then
  fail "push.sh did not refuse while the server's loops run"
fi
if /usr/bin/pgrep -f scripts/trigger_watch.py >/dev/null; then
  if PATH="${PATH#"$W/shims:"}" PATHIEL_ENV_FILE="$W/mac.env" "$ROOT/scripts/vps/push.sh" pathiel@localhost "$W/state" >/dev/null 2>&1; then
    fail "push.sh did not refuse while the Mac's watcher runs"
  fi
fi

# ── pull ─────────────────────────────────────────────────────────────────────
say "7/7 pull_state.sh into a copy (never the live state)"
R "(nohup python3 scripts/trigger_watch.py >/dev/null 2>&1 &) ; echo server-made > ~/state/strategies/E2E_MARKER.txt"
cp -a "$W/state" "$W/home"
snaps() { { compgen -G "/tmp/pathiel-state-before-pull-*" || true; } | wc -l; }
before=$(snaps)
"$ROOT/scripts/vps/pull_state.sh" pathiel@localhost "$W/home" >"$W/pull.log" 2>&1 || { tail -10 "$W/pull.log"; fail "pull_state.sh"; }
grep -q server-made "$W/home/strategies/E2E_MARKER.txt" || fail "server state did not come home"
snap=$(sed -n 's/^state pulled into .*rsync -a --delete \(.*\)\/ .*/\1/p' "$W/pull.log")
[[ $(snaps) -gt $before && -d "$snap" && ! -f "$snap/strategies/E2E_MARKER.txt" ]] || fail "no pre-pull snapshot"
srvlog=$(sed -n 's/^server tick log: //p' "$W/pull.log")
rm -rf "$snap" "$srvlog" 2>/dev/null || true
R "! tmux has-session -t desk 2>/dev/null && ! pgrep -f '[p]athiel.server' && ! pgrep -f '[t]rigger_watch'" >/dev/null \
  || fail "something still runs on the server after pull"
ssh -n pathiel@localhost 'test ! -f ~/pathiel/.env.local' || fail "server key not deleted"

say "PASS: setup, push, start, Claude check refuses, tick scripts, MCP smoke, guards, pull"
