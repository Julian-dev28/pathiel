# Sourced by scripts/pathiel-mcp and scripts/pathiel-py. Not a program.
#
# The Claude Code plugin install of pathiel: the plugin root is a cache
# directory Claude Code replaces on every update, so nothing durable lives
# there. This file decides where the durable things go and builds the Python
# environment once.
#
#   PATHIEL_HOME   ~/.pathiel            keys (.env.local), state/, .agent-config.json
#   PATHIEL_DATA   $CLAUDE_PLUGIN_DATA   the venv (kept across plugin updates);
#                  or $PATHIEL_HOME/data when Claude Code does not set it
#
# Every line of output here goes to stderr: scripts/pathiel-mcp speaks MCP
# over stdout and one stray byte breaks the handshake.

pathiel_fail() { echo "pathiel: $*" >&2; exit 1; }

pathiel_prepare() {
  PATHIEL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  PATHIEL_HOME="${PATHIEL_HOME:-$HOME/.pathiel}"
  PATHIEL_DATA="${CLAUDE_PLUGIN_DATA:-$PATHIEL_HOME/data}"
  PATHIEL_VENV="$PATHIEL_DATA/venv"
  export PATHIEL_ROOT PATHIEL_HOME PATHIEL_DATA PATHIEL_VENV

  local py="${PATHIEL_PYTHON:-python3}"
  command -v "$py" >/dev/null 2>&1 || pathiel_fail "python3 not found; install Python 3.11 or newer (or set PATHIEL_PYTHON)"
  "$py" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
    || pathiel_fail "$("$py" --version 2>&1) is too old; pathiel needs Python 3.11 or newer"

  # Rebuild the venv when the lockfile changes; a hash stamp decides, not a
  # timestamp, so a reinstall of the same lock is a no-op.
  local want have
  want="$("$py" -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$PATHIEL_ROOT/requirements.lock")"
  have="$(cat "$PATHIEL_VENV/.requirements.sha256" 2>/dev/null || true)"
  if [ ! -x "$PATHIEL_VENV/bin/python" ] || [ "$have" != "$want" ]; then
    echo "pathiel: building the Python environment in $PATHIEL_VENV (first run, about a minute)" >&2
    mkdir -p "$PATHIEL_DATA"
    "$py" -m venv "$PATHIEL_VENV" >&2 || pathiel_fail "could not create a venv at $PATHIEL_VENV"
    "$PATHIEL_VENV/bin/python" -m pip install --quiet --disable-pip-version-check \
      -r "$PATHIEL_ROOT/requirements.lock" >&2 || pathiel_fail "pip install failed; see the lines above"
    printf '%s\n' "$want" > "$PATHIEL_VENV/.requirements.sha256"
    echo "pathiel: environment ready" >&2
  fi

  # Where the pathiel code looks for keys and writes state. An operator who
  # already set these in their shell keeps their own values.
  export PATHIEL_ENV_FILE="${PATHIEL_ENV_FILE:-$PATHIEL_HOME/.env.local}"
  export PATHIEL_STATE_DIR="${PATHIEL_STATE_DIR:-$PATHIEL_HOME/state}"
  export PATHIEL_AGENT_CONFIG_FILE="${PATHIEL_AGENT_CONFIG_FILE:-$PATHIEL_HOME/.agent-config.json}"
  mkdir -p "$PATHIEL_STATE_DIR"
}
