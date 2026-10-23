"""Shared state-directory resolver, formerly also the cross-sectional
rebalancers' ownership/claims tracker.

Headless desk (operator decision 2026-10-23): every strategy book that used
`OwnedPositions` (per-book position tracking) and `ClaimsRegistry`
(cross-book coin claims, so two books could never fight over the same coin)
is deleted — news_surge_short_live.py, news_surge_multi.py,
social_trending_recorder.py, unlock_short_live.py, xs_reversal_live.py,
drawdown_ladder_live.py, copy_trade_live.py. Nothing live calls
`OwnedPositions`, `ClaimsRegistry`, `get_claims_registry`,
`active_claim_books` or `prune_claims_to_live` any more (there is exactly one
trader on the wallet now — Claude, via the MCP server — so there is nothing
left to coordinate between), so this module was cut down to the one thing
every other agent module still needs from it: `state_file()`, the shared
state-directory resolver (perception.py, data_logger.py, shadow_ledger.py,
dashboard.py, metrics.py, scripts/preflight_live.py, scripts/_state_env.py
all route through it so `PATHIEL_STATE_DIR` redirects them together in
tests).
"""
from __future__ import annotations

import os

# State-file base dir: PATHIEL_STATE_DIR (tests/conftest.py points it at a temp dir so the suite
# never touches live state) else the project root. Every agent state file (timers,
# the funding/OI logger, the DSL exit registry, ...) routes through state_file()
# so they redirect together.
_STATE_DIR = os.environ.get("PATHIEL_STATE_DIR") or os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def state_file(name: str) -> str:
    """Absolute path for an agent state file, under PATHIEL_STATE_DIR (tests) or the project root."""
    return os.path.join(_STATE_DIR, name)
