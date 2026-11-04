#!/usr/bin/env bash
# Copy the repo, the live state and a server-only env file to the server.
# Run ON THE MAC from anywhere:
#
#   scripts/vps/push.sh pathiel@SERVER [LOCAL_STATE_DIR]
#
# LOCAL_STATE_DIR defaults to $HOME/Documents/code/pathia/.state (the live
# PATHIEL_STATE_DIR). The env file is built by make_server_env.py, which asks
# for the server's own API wallet key (hidden prompt) and never prints it.
# Nothing secret is written to disk on the Mac except a 0600 temp file that
# is deleted on exit.
set -euo pipefail

HOST="${1:?usage: scripts/vps/push.sh pathiel@SERVER [LOCAL_STATE_DIR]}"
LOCAL_STATE="${2:-$HOME/Documents/code/pathia/.state}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SERVER_HOME="/home/pathiel"
SERVER_REPO="$SERVER_HOME/pathiel"
SERVER_STATE="$SERVER_HOME/state"

[[ -d "$LOCAL_STATE/strategies" ]] || { echo "no live state at $LOCAL_STATE" >&2; exit 1; }

# Two tickers on one account would double-trade. The Mac's loops stop first.
if pgrep -f scripts/trigger_watch.py >/dev/null; then
  echo "the Mac's desk loops are still running. Close the 'pathiel desk loops'" >&2
  echo "window, then: pkill -f scripts/trigger_watch.py" >&2
  exit 1
fi
# Once the server trades, its state is the newer one. Never overwrite it.
if ssh -n "$HOST" 'tmux has-session -t desk 2>/dev/null'; then
  echo "the server's desk loops are running; its state is newer than the Mac's." >&2
  echo "Use scripts/vps/pull_state.sh to bring it home instead." >&2
  exit 1
fi

umask 077
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# 1. Env first, so a refused key stops everything before any copy.
"$ROOT/.venv/bin/python" "$ROOT/scripts/vps/make_server_env.py" \
  --out "$TMP/env" --server-repo "$SERVER_REPO" --server-state "$SERVER_STATE"

# 2. Repo working tree (includes the live .agent-config.json). No git
#    history, no venv, no logs, no env files, no agent worktrees.
rsync -az --delete \
  --exclude .git/ --exclude .venv/ --exclude node_modules/ --exclude logs/ \
  --exclude .claude/worktrees/ --exclude __pycache__/ --exclude '.env*' \
  --exclude .pytest_cache/ \
  "$ROOT/" "$HOST:$SERVER_REPO/"

# 3. Live state: theses, journals, intel cache, desk config.
rsync -az --delete "$LOCAL_STATE/" "$HOST:$SERVER_STATE/"

# 4. The env file, 0600 on arrival. (macOS rsync has no --chmod; ssh + umask
#    works with every client.)
ssh "$HOST" "umask 077 && cat > $SERVER_REPO/.env.local && chmod 600 $SERVER_REPO/.env.local" < "$TMP/env"

echo "pushed repo, state and env to $HOST"
echo "next, on the server: scripts/vps/start.sh"
