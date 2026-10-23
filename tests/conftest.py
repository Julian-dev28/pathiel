"""Test isolation: redirect agent state files to a throwaway temp dir BEFORE any
pathiel module imports, so a test can never read or truncate the live
.agent-memory.json / .agent-config.json (a pytest run wiped live trading state
on 2026-06-15). This runs at conftest import — before test modules are collected,
hence before memory.py / config_store.py freeze their module-level paths.
"""

import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="pathiel-test-state-")
# Force (not setdefault): even if the dev shell exports these, tests must use
# disposable paths.
os.environ["PATHIEL_AGENT_MEMORY_FILE"] = os.path.join(_tmp, ".agent-memory.json")
os.environ["PATHIEL_AGENT_CONFIG_FILE"] = os.path.join(_tmp, ".agent-config.json")
os.environ["PATHIEL_DSL_STATE_FILE"] = os.path.join(_tmp, ".dsl-state.json")
# Every process-local state file (backup receipts, supervisor/alert-evaluator
# timestamps, ...) routes through pathiel.state.state_file(), which honors
# PATHIEL_STATE_DIR. Point it at the temp dir so the suite never touches the
# live state directory.
os.environ["PATHIEL_STATE_DIR"] = _tmp
# Session log too: session_log.py freezes SESSION_LOG_PATH at import. This was the ONE
# state file not redirected — pytest wrote fabricated C1/C2/C3 book events into the LIVE
# activity feed, which pnl_by_book attribution reads (audit 2026-07-09).
os.environ["SESSION_LOG_PATH"] = os.path.join(_tmp, "session-log.jsonl")


# `_reset_claims_registry` (a claims-registry reset between the strategy-book
# test files) was removed 2026-10-23 (wave 2): `ClaimsRegistry` itself went in
# wave 1 (headless desk) when the seven strategy books were retired, and the
# test files it protected against cross-contaminating went with it — the
# fixture had been a no-op (clearing an attribute and a file nothing sets any
# more) since that wave.
