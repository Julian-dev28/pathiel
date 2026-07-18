#!/usr/bin/env python
"""v2 shadow loop — Phase-2 entrypoint (MINIMAL_SYSTEM.md §5 Phase 2).

mode=SHADOW is the hardcoded default: full signal cycle, intents written to the
shadow ledger under `v2_`-prefixed book names, ZERO order placement. Placing an
order is impossible unless the process is started with HERMES_V2_LIVE=1 AND
--live is passed (hermes_trader/v2/executor.py refuses to even import the order
layer otherwise — see ShadowModeViolation).

Run it:
    .venv/bin/python scripts/v2_shadow_loop.py            # shadow, forever
    .venv/bin/python scripts/v2_shadow_loop.py --once     # one signal+exit cycle
    HERMES_V2_LIVE=1 .venv/bin/python scripts/v2_shadow_loop.py --live   # Phase 3+

NEVER import scripts/trading_loop.py from here (importing it starts the LIVE v1
loop). This entrypoint only touches hermes_trader.v2.
"""
import argparse
import logging
import os
import sys

# Rate budget: the shadow run gets the dashboard's small bucket (restart.sh
# convention) so v1-loop + v2-shadow together stay under HL's 1,200 weight/min
# per-IP budget. MUST be set before any hermes_trader.client import — the
# HL_LIMITER token bucket is constructed at rate_limit import time.
os.environ.setdefault("HERMES_HL_RATE_CAPACITY", "60")
os.environ.setdefault("HERMES_HL_RATE_REFILL_PER_SEC", "2")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="hermes v2 loop (shadow by default)")
    parser.add_argument("--live", action="store_true",
                        help="run LIVE (also requires HERMES_V2_LIVE=1 in the env)")
    parser.add_argument("--once", action="store_true",
                        help="run one exit+signal cycle and exit (smoke test)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")

    if args.live:
        if os.environ.get("HERMES_V2_LIVE") != "1":
            print("refusing --live: HERMES_V2_LIVE=1 is not set (both walls are required)",
                  file=sys.stderr)
            return 2
        mode = "LIVE"
    else:
        mode = "SHADOW"
        # Shadow must NEVER clobber the v1 loop's live .dsl-state.json: the DSL
        # engine skips every disk write under this flag while still computing
        # floor verdicts for the parity diff.
        os.environ.setdefault("HERMES_STATE_READONLY", "1")

    from hermes_trader.v2 import executor, loop  # after env setup, by design

    if mode == "SHADOW" and executor.live_enabled():
        print("refusing to start: SHADOW mode with HERMES_V2_LIVE=1 set — unset it",
              file=sys.stderr)
        return 2

    loop.run_forever(mode=mode, max_cycles=1 if args.once else None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
