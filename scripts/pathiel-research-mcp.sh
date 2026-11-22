#!/usr/bin/env bash
# The pathiel research MCP server as a Claude Code plugin entry point.
#
# .claude-plugin/plugin.json (bundle root; the checked-in copy lives at
# services/research_mcp/.claude-plugin/plugin.json and is relocated there by
# scripts/build_plugin_bundle.py --plugin research) runs this as
# "${CLAUDE_PLUGIN_ROOT}/scripts/pathiel-research-mcp.sh". Claude Code only
# copies a plugin's OWN source directory into its cache -- a relative-path
# marketplace entry scoped to services/research_mcp/ alone never carries
# scripts/, services/intel/, services/strategies/ or contracts/ along with
# it, so this file could not import the server at all (see
# code.claude.com/docs/en/plugins/loading#in-place-and-copied-plugins). The
# real install instead comes from the self-contained `plugin-research`
# branch, whose root holds this same scripts/ directory alongside every
# other file the server's import closure touches -- see
# .claude-plugin/marketplace.json's pathiel-research entry (a `github`
# source pinned to that branch) and build_plugin_bundle.py's own
# RESEARCH_* constants for the exact file list.
#
# Deliberately does NOT source scripts/_plugin_env.sh: that file resolves
# PATHIEL_HOME=~/.pathiel and loads the trading server's keys/config. This
# server must never touch either (CLAUDE.md: "never open .env.local or
# ~/.pathiel" for the research MCP), so it builds its own, separate venv
# under its own, separate data directory and loads no env file at all.
#
#   scripts/pathiel-research-mcp.sh --bootstrap   build the environment and exit
#
# Nothing is written to stdout before the exec: that channel is the MCP
# transport.
set -euo pipefail

RESEARCH_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RESEARCH_DATA="${CLAUDE_PLUGIN_DATA:-$HOME/.pathiel-research/data}"
RESEARCH_VENV="$RESEARCH_DATA/venv"

research_fail() { echo "pathiel-research: $*" >&2; exit 1; }

py="${PATHIEL_PYTHON:-python3}"
command -v "$py" >/dev/null 2>&1 || research_fail "python3 not found; install Python 3.11 or newer (or set PATHIEL_PYTHON)"
"$py" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
  || research_fail "$("$py" --version 2>&1) is too old; needs Python 3.11 or newer"

want="$("$py" -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$RESEARCH_ROOT/requirements.lock")"
have="$(cat "$RESEARCH_VENV/.requirements.sha256" 2>/dev/null || true)"
if [ ! -x "$RESEARCH_VENV/bin/python" ] || [ "$have" != "$want" ]; then
  echo "pathiel-research: building the Python environment in $RESEARCH_VENV (first run, about a minute)" >&2
  mkdir -p "$RESEARCH_DATA"
  "$py" -m venv "$RESEARCH_VENV" >&2 || research_fail "could not create a venv at $RESEARCH_VENV"
  "$RESEARCH_VENV/bin/python" -m pip install --quiet --disable-pip-version-check \
    -r "$RESEARCH_ROOT/requirements.lock" >&2 || research_fail "pip install failed; see the lines above"
  printf '%s\n' "$want" > "$RESEARCH_VENV/.requirements.sha256"
  echo "pathiel-research: environment ready" >&2
fi

if [ "${1:-}" = "--bootstrap" ]; then
  echo "pathiel-research: ready. python=$RESEARCH_VENV/bin/python state=${PATHIEL_RESEARCH_STATE_DIR:-$HOME/.pathiel-research/state}" >&2
  exit 0
fi

cd "$RESEARCH_ROOT"
exec "$RESEARCH_VENV/bin/python" scripts/pathiel-research-mcp.py "$@"
