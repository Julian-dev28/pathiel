"""Eval: the dry-run tick against LIVE public reads. Read only.

Runs `tick.run_tick(live=False)` with the real readers: the sub-account's
position, resting orders, equity and fills, and Brent 1h and 15m candles
from Hyperliquid's public info endpoint. The exchange facade is read-only
(every signing call raises) and so is the store, and this eval wraps the
facade once more to COUNT any attempt, so a pass also proves the dry run
never reached for a signed action.

    python3 -m services.fib_sleeve.evals.live_dry_run [--now-ms N]

Pass (exit 0): the tick returned a decision other than "error", it carries
the account's equity and (when the rule had a swing) a plan whose entry sits
between its stop and its target, a "would_place" pays at least the 2R floor
and risks no more than the configured share of the sub-account's equity,
and zero signing calls were attempted. Exit 1 otherwise. Paid: nothing.
Network: public reads only (about 10 requests).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any, Dict, List, Optional


def check(decision: Dict[str, Any], cfg: Any, attempts: List[str]) -> List[str]:
    """The failures of one dry-run decision; [] is a pass. Pure."""
    bad: List[str] = []
    if attempts:
        bad.append(f"signing calls attempted in a dry run: {attempts}")
    if decision.get("mode") != "dry_run":
        bad.append("mode is not dry_run")
    if decision.get("action") in (None, "error"):
        bad.append(f"no decision: {decision.get('why')}")
        return bad
    if "equity" not in (decision.get("account_state") or {}):
        bad.append("no equity in the decision")
    plan = decision.get("plan") or {}
    if "entry" in plan:
        lo, hi = sorted((plan["stop"], plan["target"]))
        if not lo < plan["entry"] < hi:
            bad.append("plan entry is not between its stop and target")
    if decision.get("action") == "would_place":
        b, size = decision["bracket"], decision["size"]
        if b.get("r_multiple", 0) < cfg.min_r:
            bad.append(f"would place under {cfg.min_r}R")
        if size["risk_usd"] > cfg.risk_frac * size["equity"] * 1.0001:
            bad.append("risk above the configured share of the sub-account's equity")
        if size["notional"] > cfg.leverage * size["equity"]:
            bad.append("notional above the leverage cap")
    return bad


class Counting:
    """The facade, with every reach for a signing function recorded."""

    def __init__(self, inner: Any, attempts: List[str]) -> None:
        self._inner, self._attempts = inner, attempts

    def __getattr__(self, name: str) -> Any:
        from pathiel.client.subaccount import MUTATING
        if name in MUTATING:
            self._attempts.append(name)
        return getattr(self._inner, name)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="The fib sleeve's dry-run tick on live public reads.")
    ap.add_argument("--now-ms", type=int, default=None)
    args = ap.parse_args(argv)
    from services.fib_sleeve import config as C
    from services.fib_sleeve import tick as T
    cfg = C.load()
    deps = T.default_deps(cfg, live=False)
    attempts: List[str] = []
    deps.ex = Counting(deps.ex, attempts)
    decision = T.run_tick(cfg, live=False, now_ms=args.now_ms or int(time.time() * 1000), deps=deps)
    failures = check(decision, cfg, attempts)
    print(json.dumps(decision, indent=2, default=str))
    print(json.dumps({"eval": "fib_sleeve.live_dry_run", "pass": not failures, "failures": failures,
                      "signing_calls_attempted": len(attempts)}))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
