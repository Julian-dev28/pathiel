# Sourced by scripts/pathiel-mcp and scripts/pathiel-py. Not a program.
#
# The Claude Code plugin install of pathiel: the plugin root is a cache
# directory Claude Code replaces on every update, so nothing durable lives
# there. This file decides where the durable things go, which network the
# process trades on, and builds the Python environment once.
#
#   PATHIEL_HOME     ~/.pathiel            keys (.env.local), state/, .agent-config.json
#   PATHIEL_DATA     $CLAUDE_PLUGIN_DATA   the venv (kept across plugin updates);
#                    or $PATHIEL_HOME/data when Claude Code does not set it
#   PATHIEL_INSTALL  public                the public code path (contracts/runtime);
#                                          the operator's repo .mcp.json never
#                                          runs this file
#   PATHIEL_NETWORK  mainnet (REAL MONEY) unless the environment or the env
#                    file says PATHIEL_NETWORK=testnet
#
# Every line of output here goes to stderr: scripts/pathiel-mcp speaks MCP
# over stdout and one stray byte breaks the handshake. The one exception is
# pathiel_print_commands, which only `scripts/pathiel-mcp --commands` calls,
# and that path exits before the server starts.
#
# Bash 3.2 (macOS /bin/bash): no ${var,,}, no declare -g, no mapfile.

pathiel_fail() { echo "pathiel: $*" >&2; exit 1; }

pathiel_warn() { echo "pathiel: warning: $*" >&2; }

# Make a path absolute against the current directory. The launchers cd to
# the plugin root before exec, so a relative PATHIEL_HOME would otherwise
# point somewhere else once Python starts. Sets the named variable.
pathiel_absolutize() {
  local __name="$1" __value
  eval "__value=\"\${$__name:-}\""
  case "$__value" in
    ""|/*) ;;
    *) eval "$__name=\"\$PWD/\$__value\"" ;;
  esac
}

# 1. Where things live. An operator who already set these in their shell
#    keeps their own values.
pathiel_paths() {
  PATHIEL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  PATHIEL_HOME="${PATHIEL_HOME:-$HOME/.pathiel}"
  pathiel_absolutize PATHIEL_HOME
  PATHIEL_DATA="${CLAUDE_PLUGIN_DATA:-$PATHIEL_HOME/data}"
  pathiel_absolutize PATHIEL_DATA
  PATHIEL_VENV="$PATHIEL_DATA/venv"
  export PATHIEL_ROOT PATHIEL_HOME PATHIEL_DATA PATHIEL_VENV
  export PATHIEL_INSTALL="${PATHIEL_INSTALL:-public}"

  # Where the pathiel code looks for keys and its config.
  export PATHIEL_ENV_FILE="${PATHIEL_ENV_FILE:-$PATHIEL_HOME/.env.local}"
  export PATHIEL_AGENT_CONFIG_FILE="${PATHIEL_AGENT_CONFIG_FILE:-$PATHIEL_HOME/.agent-config.json}"
  pathiel_absolutize PATHIEL_ENV_FILE
  pathiel_absolutize PATHIEL_AGENT_CONFIG_FILE
}

# 2. Which network. The process env wins, else the first PATHIEL_NETWORK line
#    of the env file ('export ' before the name and a trailing ' # comment'
#    are allowed). Lowercased, whitespace and quotes stripped. Nothing set
#    means mainnet: real money (decision of 2026-11-12); testnet is the
#    explicit opt-in, PATHIEL_NETWORK=testnet. A value that is set but is not
#    mainnet or testnet means testnet plus one warning: the user tried to
#    choose something, and testnet is the side that cannot lose real money.
#    Exported, so Python (which loads the same file with setdefault) can
#    never read the line differently.
#    PATHIEL_NETWORK_SOURCE says where the answer came from (environment,
#    env file, default, invalid), so status output can tell a user's choice
#    from the default even though PATHIEL_NETWORK is always exported.
#    The key matches in any case, a UTF-8 BOM and CRLF endings are ignored, and
#    an empty PATHIEL_NETWORK= line is skipped: the first line with a value
#    wins, with one warning when later lines disagree with it.
pathiel_network_values() {
  local bom
  bom="$(printf '\357\273\277')"
  LC_ALL=C sed -e "1s/^$bom//" -e 's/\r$//' "$1" 2>/dev/null \
    | LC_ALL=C grep -i -E '^[[:space:]]*(export[[:space:]]+)?PATHIEL_NETWORK[[:space:]]*=' \
    | sed -e 's/^[^=]*=//' -e 's/[[:space:]]#.*$//' \
    | tr -d "[:blank:]\"'" | tr '[:upper:]' '[:lower:]' | grep -v '^$' || true
}

pathiel_resolve_network() {
  local raw="${PATHIEL_NETWORK:-}" from="the environment" source="environment" net values
  if [ -z "$raw" ] && [ -f "${PATHIEL_ENV_FILE:-}" ]; then
    values="$(pathiel_network_values "$PATHIEL_ENV_FILE")"
    raw="$(printf '%s\n' "$values" | head -n 1)"
    if [ "$(printf '%s\n' "$values" | grep -v '^$' | sort -u | wc -l | tr -d ' ')" -gt 1 ]; then
      pathiel_warn "PATHIEL_NETWORK is set more than once in $PATHIEL_ENV_FILE with different values; using the first: $raw"
    fi
    from="$PATHIEL_ENV_FILE"
    source="env file"
  fi
  net="$(printf '%s' "$raw" | tr -d "[:space:]\"'" | tr '[:upper:]' '[:lower:]')"
  case "$net" in
    mainnet|testnet) ;;
    "") net="mainnet"; source="default" ;;
    *)
      pathiel_warn "PATHIEL_NETWORK in $from is not mainnet or testnet; using testnet"
      net="testnet"
      source="invalid"
      ;;
  esac
  export PATHIEL_NETWORK="$net"
  export PATHIEL_NETWORK_SOURCE="$source"
}

# 3. State is split by network: mainnet keeps ~/.pathiel/state (where 0.3.0
#    wrote it), testnet gets ~/.pathiel/state-testnet. A testnet thesis or
#    ledger never lands in mainnet state.
pathiel_state_paths() {
  local suffix=""
  if [ "${PATHIEL_NETWORK:-mainnet}" = "testnet" ]; then
    suffix="-testnet"
  fi
  export PATHIEL_STATE_DIR="${PATHIEL_STATE_DIR:-$PATHIEL_HOME/state$suffix}"
  pathiel_absolutize PATHIEL_STATE_DIR
  export PATHIEL_UNIVERSE_CACHE_DIR="${PATHIEL_UNIVERSE_CACHE_DIR:-$PATHIEL_STATE_DIR/universe_cache}"
  pathiel_absolutize PATHIEL_UNIVERSE_CACHE_DIR
  mkdir -p "$PATHIEL_STATE_DIR" "$PATHIEL_UNIVERSE_CACHE_DIR"
}

# 4. The Python environment, built once into $PATHIEL_DATA/venv.
pathiel_build_venv() {
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
}

# 5. A fresh install gets the public default config (3x, 1% a trade, one
#    position) from config/public/agent-config.json. Never overwrites a file.
#    A failure is a warning: pathiel.agents.config_store falls back to the
#    same profile when no config exists.
pathiel_seed_config() {
  if [ ! -e "$PATHIEL_AGENT_CONFIG_FILE" ] && [ ! -L "$PATHIEL_AGENT_CONFIG_FILE" ]; then
    "$PATHIEL_VENV/bin/python" "$PATHIEL_ROOT/scripts/config_preset.py" seed-public >&2 \
      || pathiel_warn "could not write $PATHIEL_AGENT_CONFIG_FILE; the public default risk profile still applies"
  fi
}

pathiel_prepare() {
  pathiel_paths
  pathiel_resolve_network
  pathiel_state_paths
  pathiel_build_venv
  pathiel_seed_config
}

# Paste-ready commands for the user's own terminal, where CLAUDE_PLUGIN_ROOT
# and CLAUDE_PLUGIN_DATA do not exist. Every path is absolute and quoted with
# printf %q, and CLAUDE_PLUGIN_DATA is set inline so pathiel-py finds the same
# venv instead of building a second one. PATHIEL_PYTHON pins that venv's
# own interpreter: a plain terminal's python3 can be another one than Claude
# Code saw (macOS /usr/bin/python3 is 3.9), and pathiel_build_venv checks the
# version of PATHIEL_PYTHON even when the venv is ready. One
# 'label<TAB>command' line each.
# Writes to STDOUT on purpose: only `scripts/pathiel-mcp --commands` calls it.
pathiel_print_commands() {
  local prefix status_cmd dry_cmd
  prefix="$(printf '%q ' env "CLAUDE_PLUGIN_DATA=$PATHIEL_DATA" "PATHIEL_HOME=$PATHIEL_HOME" \
            "PATHIEL_PYTHON=$PATHIEL_VENV/bin/python" "$PATHIEL_ROOT/scripts/pathiel-py")"
  status_cmd="$prefix$(printf '%q' "$PATHIEL_ROOT/scripts/public_status.py")"
  dry_cmd="$prefix$(printf '%q' "$PATHIEL_ROOT/scripts/approve_builder_fee.py")"
  printf '%s\t%s\n' "status" "$status_cmd" "approve-dry" "$dry_cmd" "approve-sign" "$dry_cmd --yes"
}
