"""DSL exit engine — VERBATIM survivor.

The engine itself lives in hermes_trader/agents/dsl_exit.py (739 lines, clean:
stdlib-only imports, no import-time side effects, config read is lazy). Per the
rebuild spec (MINIMAL_SYSTEM.md §3 "Safety rails that survive VERBATIM") v2 does
NOT fork it: this module is a thin re-export so v2 code has a single import path
and the tracker state file (.dsl-state.json / HERMES_DSL_STATE_FILE) stays SHARED
with v1 — that shared state is what makes the Phase-3 per-book cutover safe
(rehydrate_from_exchange adopts any open position it finds).

Any behavioral change belongs in agents/dsl_exit.py with its own tests, never here.
"""
from __future__ import annotations

from hermes_trader.agents.dsl_exit import (
    DSL_STATE_FILE,
    DSLTracker,
    ExitPolicy,
    ExitVerdict,
    RetraceTier,
    active_position_coins,
    check_all_positions,
    deregister_position,
    load_state,
    register_position,
    rehydrate_from_exchange,
)

__all__ = [
    "DSL_STATE_FILE",
    "DSLTracker",
    "ExitPolicy",
    "ExitVerdict",
    "RetraceTier",
    "active_position_coins",
    "check_all_positions",
    "deregister_position",
    "load_state",
    "register_position",
    "rehydrate_from_exchange",
]
