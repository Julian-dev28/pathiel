#!/usr/bin/env bash
# The pathiel MCP server as a Claude Code plugin entry point.
#
# .claude-plugin/plugin.json runs this as "${CLAUDE_PLUGIN_ROOT}/scripts/pathiel-mcp".
# It builds the Python environment on first use (into $CLAUDE_PLUGIN_DATA, which
# survives plugin updates), points the code at ~/.pathiel for keys and state,
# marks the process as a public install on mainnet (real money) unless the
# environment or ~/.pathiel/.env.local says PATHIEL_NETWORK=testnet, then
# execs scripts/pathiel-mcp-server.py over
# stdio. Nothing is written to stdout before the exec: that channel is the MCP
# transport.
#
#   scripts/pathiel-mcp --bootstrap   build the environment, seed the config and
#                                     exit (what /pathiel:setup runs, so the
#                                     first real start is instant)
#   scripts/pathiel-mcp --commands    the same, then print paste-ready commands
#                                     (status, builder fee approval) for the
#                                     user's own terminal, one per line on
#                                     stdout, and exit. Never starts the server.
#
# Other MCP clients and the container (Dockerfile.mcp) use this same file, so a
# stranger who cloned the repo gets the public path too. The maintainer's own
# stack does not: it starts scripts/pathiel-mcp-server.py from the repo .mcp.json.
set -euo pipefail
. "$(cd "$(dirname "$0")" && pwd)/_plugin_env.sh"
pathiel_prepare
if [ "${1:-}" = "--bootstrap" ]; then
  echo "pathiel: ready. python=$PATHIEL_VENV/bin/python env=$PATHIEL_ENV_FILE state=$PATHIEL_STATE_DIR config=$PATHIEL_AGENT_CONFIG_FILE network=$PATHIEL_NETWORK" >&2
  exit 0
fi
if [ "${1:-}" = "--commands" ]; then
  pathiel_print_commands
  exit 0
fi
cd "$PATHIEL_ROOT"
exec "$PATHIEL_VENV/bin/python" scripts/pathiel-mcp-server.py "$@"
