"""Test isolation: redirect agent state files to a throwaway temp dir BEFORE any
hermes module imports, so a test can never read or truncate the live
.agent-memory.json / .agent-config.json (a pytest run wiped live trading state
on 2026-06-15). This runs at conftest import — before test modules are collected,
hence before memory.py / config_store.py freeze their module-level paths.
"""

import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="hermes-test-state-")
# Force (not setdefault): even if the dev shell exports these, tests must use
# disposable paths.
os.environ["HERMES_AGENT_MEMORY_FILE"] = os.path.join(_tmp, ".agent-memory.json")
os.environ["HERMES_AGENT_CONFIG_FILE"] = os.path.join(_tmp, ".agent-config.json")
os.environ["HERMES_DSL_STATE_FILE"] = os.path.join(_tmp, ".dsl-state.json")
# Rebalancer state files (timers, owned-position sets, the claims registry, vol-managed history,
# pairs state) all route through rebalancer_owned.state_file(), which honors HERMES_STATE_DIR.
# Point it at the temp dir so the suite never pollutes the live .rebalancer_claims.json /
# .*_positions.json / *_ts / .xs_volmgd_history (builder tests wrote fake coins to these 2026-06-24).
os.environ["HERMES_STATE_DIR"] = _tmp
