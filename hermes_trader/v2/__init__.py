"""hermes_trader.v2 — the minimal defensible rebuild (spec: research/rebuild_2026_07_18/MINIMAL_SYSTEM.md).

7 modules on top of the untouched `hermes_trader/client/` data layer:

    loop.py      ONE process, ONE cadence (30-min signal cycle + 60s exit sub-cycle)
    books.py     the 3 surviving signal generators (extreme_fade, funding_spike_short, xs_momentum)
    executor.py  entry + on-exchange backup SL + v2 claims registry (LIVE gated by HERMES_V2_LIVE=1)
    dsl_exit.py  exit engine — verbatim survivor (re-export of agents/dsl_exit)
    risk.py      kill switch (pct-of-SOD, UTC-date persisted), gross cap, floors, margin preflight
    ledger.py    shadow ledger reuse — v2 books record under `v2_`-prefixed names
    recorder.py  funding/OI accrual (the named data frontier; same files as v1's data_logger)

This package NEVER imports scripts/trading_loop (importing that module starts the
LIVE v1 loop). Order placement is structurally unreachable unless the operator
exports HERMES_V2_LIVE=1 — see executor.ShadowModeViolation.

Intentionally no submodule imports here: importing `hermes_trader.v2` must stay
side-effect free and cheap so the entrypoint can set rate-budget env vars before
any client module constructs its token bucket.
"""
