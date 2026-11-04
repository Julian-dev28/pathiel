#!/usr/bin/env bash
# Prove Claude Code can run here and reach the pathiel MCP server, the same
# path every tick and thesis rewrite takes. Run ON THE SERVER as pathiel,
# from the repo (start.sh runs it before it launches the loops):
#
#   cd ~/pathiel && scripts/vps/check_claude.sh
#
# One short `claude -p` call that may use exactly one tool, desk_list, which
# only reads. Exit 0 = logged in, MCP server approved and answering, desks
# listed. Anything else names which of the three is missing.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
export PATH="$ROOT/.venv/bin:$HOME/.local/bin:$PATH"
LOG="$(mktemp)"
trap 'rm -f "$LOG"' EXIT

command -v claude >/dev/null || { echo "CLAUDE-CHECK FAIL Claude Code is not installed" >&2; exit 1; }

PROMPT='Call the pathiel MCP tool desk_list exactly once. Reply with one line:
DESKS=<the active desk ids, comma separated>. If no pathiel tool is available
to you, reply exactly NO_MCP. Do nothing else.'

# Ubuntu has timeout(1); a Mac without coreutils runs the check unbounded.
T=(); command -v timeout >/dev/null && T=(timeout 300)
rc=0
# The prompt goes first: --allowedTools takes a list and would swallow it.
${T[@]+"${T[@]}"} claude -p "$PROMPT" --model opus \
  --allowedTools mcp__pathiel__desk_list </dev/null >"$LOG" 2>&1 || rc=$?
out="$(tr -d '\r' <"$LOG")"

if grep -q '^DESKS=.*oil' <<<"$out"; then
  echo "CLAUDE-CHECK OK $(grep -m1 '^DESKS=' <<<"$out")"
  exit 0
fi
if grep -q 'NO_MCP' <<<"$out"; then
  echo "CLAUDE-CHECK FAIL logged in, but the pathiel MCP server is not loaded." >&2
  echo "  run 'claude' here, approve the project's pathiel MCP server (or /mcp), /exit, retry" >&2
  exit 1
fi
if [[ $rc -eq 124 ]]; then
  echo "CLAUDE-CHECK FAIL claude did not answer in 5 minutes" >&2
  exit 1
fi
if grep -qiE 'login|log in|api key|auth|credential' <<<"$out"; then
  echo "CLAUDE-CHECK FAIL not logged in: run 'claude', /login with the Max account, /exit, retry" >&2
  exit 1
fi
echo "CLAUDE-CHECK FAIL unexpected answer (exit $rc):" >&2
tail -5 <<<"$out" >&2
exit 1
