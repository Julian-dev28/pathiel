#!/usr/bin/env bash
# After the trip: stop the server's loops, bring its state home, remove its
# key. Run ON THE MAC:
#
#   scripts/vps/pull_state.sh pathiel@SERVER [LOCAL_STATE_DIR]
#
# The Mac's state is snapshotted to /tmp first so the pull can be undone.
# Then revoke the server's API wallet on Hyperliquid (More > API) and start
# the Mac loops again (/desk-loops).
set -euo pipefail

HOST="${1:?usage: scripts/vps/pull_state.sh pathiel@SERVER [LOCAL_STATE_DIR]}"
LOCAL_STATE="${2:-$HOME/Documents/code/pathia/.state}"
SERVER_HOME="/home/pathiel"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
SNAP="/tmp/pathiel-state-before-pull-$STAMP"

# 1. Stop everything that writes state on the server.
ssh "$HOST" 'tmux kill-session -t desk 2>/dev/null || true;
  pkill -f scripts/trigger_watch.py || true;
  cd ~/pathiel && scripts/restart.sh stop'

# 2. Snapshot the Mac's state, then pull the server's over it.
umask 077
cp -a "$LOCAL_STATE" "$SNAP"
rsync -az "$HOST:$SERVER_HOME/state/" "$LOCAL_STATE/"

# 3. Keep the server's tick log next to the Mac's.
mkdir -p /tmp/oil-loop
rsync -az "$HOST:/tmp/oil-loop/progress.log" "/tmp/oil-loop/progress.server-$STAMP.log" || true

# 4. The server's key is useless once revoked; delete it anyway.
ssh "$HOST" 'shred -u ~/pathiel/.env.local 2>/dev/null || rm -f ~/pathiel/.env.local'

echo "state pulled into $LOCAL_STATE (undo: rsync -a --delete $SNAP/ $LOCAL_STATE/)"
echo "server tick log: /tmp/oil-loop/progress.server-$STAMP.log"
echo "next: revoke the server API wallet on Hyperliquid, then run /desk-loops on the Mac"
