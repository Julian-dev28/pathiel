"""One tick of the fib sleeve (services/fib_sleeve), for a cron in the loops
window. Deterministic, no LLM call.

  python3 scripts/fib_sleeve_tick.py                # dry run: read, decide, print; signs and writes nothing
  python3 scripts/fib_sleeve_tick.py --dry-run      # the same, spelled out
  python3 scripts/fib_sleeve_tick.py --live         # act on the sub-account (only when enabled)
  python3 scripts/fib_sleeve_tick.py --status       # config, kill switch, the record from the fills; read only
  python3 scripts/fib_sleeve_tick.py --enable       # write enabled=true to <state dir>/fib_sleeve/config.json
  python3 scripts/fib_sleeve_tick.py --disable      # write enabled=false
  python3 scripts/fib_sleeve_tick.py --rearm        # clear a fired kill switch and start a new record

The sleeve trades xyz:BRENTOIL on its own sub-account (orders carry
vaultAddress = that address, signed by the desk's API wallet), so it never
nets against the oil desk's position on the main account. `--live` with
enabled=false prints {"action": "disabled"} and exits 0 without a single
read. `--enable` and `--rearm` place nothing: the next `--live` tick does.

Prints the decision as one JSON object. Exit 0 on any decision, 1 on
"error", 2 on a bad config.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

STATUS_KEYS = ("desk", "coin", "account", "variant", "enabled", "utc", "action", "why", "account_state",
               "record", "kill", "position", "resting_entry")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="One tick of the fib sleeve.")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="read and decide, sign and write nothing (default)")
    mode.add_argument("--live", action="store_true", help="act on the sub-account (needs enabled=true)")
    mode.add_argument("--status", action="store_true", help="config, kill switch and record; read only")
    mode.add_argument("--enable", action="store_true", help="set enabled=true in the override file")
    mode.add_argument("--disable", action="store_true", help="set enabled=false in the override file")
    mode.add_argument("--rearm", action="store_true",
                      help="clear the kill switch; the next live tick starts a new record")
    args = ap.parse_args(argv)

    from _state_env import load_env_local
    load_env_local()   # keys and PATHIEL_STATE_DIR, before anything resolves a state path

    from services.fib_sleeve import config as C
    from services.fib_sleeve import tick as T
    from services.fib_sleeve.store import Store
    try:
        if args.enable or args.disable:
            cfg = C.write_override({"enabled": bool(args.enable)})
            print(json.dumps({"enabled": cfg.enabled, "file": C.override_path(), "variant": cfg.variant_name}))
            return 0
        cfg = C.load()
    except C.ConfigError as e:
        print(json.dumps({"action": "error", "why": f"config: {e}"}))
        return 2
    if args.rearm:
        store = Store(C.sleeve_dir())
        old = store.load()
        store.save({})
        print(json.dumps({"rearmed": True, "cleared": old,
                          "note": "the next --live tick records a new start and starting equity"}))
        return 0
    live = bool(args.live)
    decision = T.run_tick(cfg, live=live, now_ms=int(time.time() * 1000), deps=T.default_deps(cfg, live))
    if args.status:
        decision = {k: decision.get(k) for k in STATUS_KEYS}
        decision["config"] = cfg.public()
    print(json.dumps(decision, default=str))
    return 1 if decision.get("action") == "error" else 0


if __name__ == "__main__":
    sys.exit(main())
