"""Defaults and env knobs for services/llm.

Mirrors the env-overrides-everything pattern of the retired pathiel/agents/ai_brain.py,
simplified to one provider: the local Claude Code CLI. Hosted LLM APIs are
banned by project rule (CLAUDE.md, "LLM access") unless Julien explicitly
instructs otherwise, so there is no provider switch here - just knobs on the
one allowed path.

Every path/limit getter is a function, resolved at call time, not a module
constant - PATHIEL_STATE_DIR is only known once a test or script sets it
(tests/conftest.py and SWARM_RULES.md section 2 both point it at a throwaway
dir), so baking it in at import time would freeze the wrong value for tests
that set it after import. Same convention as
pathiel/agents/capital_flows.py:_STATE_DIR.
"""
from __future__ import annotations

import os

# Same tuned ceiling the retired ai_brain.py used (MAX_CLI_TIMEOUT_S). That
# number came from a real incident (2026-07-25, WLD/SPCX/HIMS: 120s SIGKILLed
# a web-search call mid-search). We do not web-search by default here, but a
# shared ceiling means one fewer tuned constant to keep in sync by hand.
MAX_TIMEOUT_S = 240.0
DEFAULT_TIMEOUT_S = 180.0

DEFAULT_CONCURRENCY = 4

# Best-first. Verified live 2026-10-17 (see services/llm/README.md): alias
# "fable" resolves to canonical claude-fable-5-1, "opus" to claude-opus-5-5,
# "sonnet" to claude-sonnet-5-5 - exactly the candidates named in the
# overhaul brief. Aliases are used (not pinned canonical ids) so the default
# keeps tracking the latest point release of each tier without a code change,
# per CLAUDE.md: "Always use the best available model by default ... No
# silent downgrades."
MODEL_PROBE_CANDIDATES: tuple[str, ...] = ("fable", "opus", "sonnet")
MODEL_PROBE_TTL_S = 24 * 3600
# A probe that settled on anything but the best candidate is a degraded
# state, often a transient limit on the best model. Re-probe soon instead of
# pinning the fallback for a day (seen 2026-10-17: one failed fable ping put
# every thesis on opus).
MODEL_PROBE_DEGRADED_TTL_S = 15 * 60

DEFAULT_MAX_TURNS = 1
WEB_SEARCH_MAX_TURNS = 8  # matches ai_brain.ClaudeCliBrain: search+verdict lands in 2-4 turns live
WEB_SEARCH_EXEC_MODEL = "haiku"  # mirrors ai_brain's opus-cannot-run-xhigh-websearch workaround

# Resolved via PATH by default (same as ai_brain.py's own claude_cli
# default), not a hardcoded absolute path into a developer's home
# directory - this repo's own gate (scripts/check_no_absolute_paths.py)
# bans exactly that, since it only works on the one box that happens to
# have that exact home directory. Point CLAUDE_CLI_COMMAND at a full path
# locally if "claude" is not on PATH in a given shell.
CLAUDE_CLI_COMMAND_ENV = "CLAUDE_CLI_COMMAND"
CLAUDE_BIN_DEFAULT = "claude"


def claude_binary() -> str:
    return os.environ.get(CLAUDE_CLI_COMMAND_ENV) or CLAUDE_BIN_DEFAULT


def state_dir() -> str:
    return os.environ.get("PATHIEL_STATE_DIR") or "."


def llm_state_dir() -> str:
    return os.path.join(state_dir(), "llm")


def usage_log_path() -> str:
    return os.path.join(llm_state_dir(), "usage.jsonl")


def model_probe_cache_path() -> str:
    return os.path.join(llm_state_dir(), "model_probe.json")


def concurrency_limit() -> int:
    try:
        return max(1, int(os.environ.get("PATHIEL_LLM_CONCURRENCY", DEFAULT_CONCURRENCY)))
    except (TypeError, ValueError):
        return DEFAULT_CONCURRENCY


def timeout_ceiling(requested: float) -> float:
    """Clamp a caller-requested timeout into (0, MAX_TIMEOUT_S]."""
    try:
        value = float(requested)
    except (TypeError, ValueError):
        value = DEFAULT_TIMEOUT_S
    return max(1.0, min(value, MAX_TIMEOUT_S))
