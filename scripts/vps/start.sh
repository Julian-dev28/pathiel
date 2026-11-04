#!/usr/bin/env bash
# Start the desk on the server: venv (first run), the Desk server and log
# rotator, then the desk loops in a tmux session named `desk`.
# Run ON THE SERVER as pathiel, from the repo:
#
#   cd ~/pathiel && scripts/vps/start.sh
#
# First time only: run `claude` once by hand, log in with the Max account,
# trust the folder, approve the project's pathiel MCP server, then /exit.
# start.sh refuses until that login exists, then proves it: Claude calls the
# pathiel MCP server once (scripts/vps/check_claude.sh) before any loop runs.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
export PATH="$ROOT/.venv/bin:$HOME/.local/bin:$PATH"

[[ -f .env.local ]] || { echo "no .env.local: run scripts/vps/push.sh from the Mac first" >&2; exit 1; }
command -v claude >/dev/null || { echo "Claude Code missing: run scripts/vps/setup_server.sh as root" >&2; exit 1; }
if [[ ! -f "$HOME/.claude/.credentials.json" && -z "${PATHIEL_SKIP_LOGIN_CHECK:-}" ]]; then
  echo "log in first: run 'claude', /login, trust the folder, approve the pathiel MCP server, /exit" >&2
  echo "(already logged in another way? rerun with PATHIEL_SKIP_LOGIN_CHECK=1)" >&2
  exit 1
fi

if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q --require-hashes -r requirements.lock
  .venv/bin/pip install -q --no-deps -e .
fi

# Same imports the MCP server and the tick scripts need.
.venv/bin/python -c "import pathiel.server, services.strategies.api, services.intel.api; print('imports ok')"

scripts/restart.sh

# The loops are Claude calling the MCP server. Prove that path works before
# leaving it alone for a trip.
if [[ -z "${PATHIEL_SKIP_LOGIN_CHECK:-}" ]]; then
  scripts/vps/check_claude.sh
fi

if tmux has-session -t desk 2>/dev/null; then
  echo "desk loops already running: tmux attach -t desk"
  exit 0
fi
tmux new-session -d -s desk -c "$ROOT" \
  "claude --model opus --permission-mode auto -n 'pathiel desk loops' '/desk-loops'"

echo "desk loops started in tmux session 'desk'"
echo "watch:   tmux attach -t desk   (detach: ctrl-b d)"
echo "ticks:   tail -f /tmp/oil-loop/progress.log"
