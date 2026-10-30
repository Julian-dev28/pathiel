"""One-shot, paid eval: build TODAY's oil thesis from LIVE market data
(real candleSnapshot reads + real news/intel reads, through the real local
Claude CLI) and score it against the 2026-10-30 realistic-targets checks
(services/thesis/validator.py's check_targets_reachable / check_scenarios_
calibrated), on top of every existing validator rule.

This is deliberately separate from run_eval.py, which replays the FROZEN
2026-10-17 oil fixture (contracts/strategies/fixtures/oil_analytics.json) -
useful for regression-testing the prompt/validator against a known shape,
but it can never prove today's venue extremes/reach quantiles actually flow
through correctly, since the fixture predates those fields. This module
pulls today's real candles instead.

Safety (hard limits for this change): main() pins PATHIEL_STATE_DIR to a
fresh temp directory (pin_temp_state_dir(), below) before making any live
read, so this can never write to the live journal no matter what the
caller's shell has set - candle_store.py/journal.py/intel's env.py all
resolve PATHIEL_STATE_DIR lazily at CALL time, not at import time (see
their own module docstrings), so this does not need to happen at import -
only before the first live call, which is exactly where it happens. The
account read is stubbed to an assumed equity (this eval is about call
quality, not position sizing) so it never touches the live wallet. Nothing
here calls a pathiel MCP tool, places/cancels an order, or reads .env.local.

Run:
    .venv/bin/python -m services.thesis.evals.live_oil_eval

Costs about $1 (one real Claude Code CLI call on the probed default model,
Fable 5.1 - roughly double if the validator forces a repair round). Not
wired into pytest on purpose (see services/thesis/tests/test_live_oil_eval.py
for this module's offline, free coverage of its own pure helpers - that
file never calls main(), so collecting/running the gate suite never spends
money or touches the network).
"""
from __future__ import annotations

import logging
import os
import sys
import tempfile
from typing import List, Optional, Tuple

from contracts.strategies.models import AccountSnapshot, Call, DeskAnalytics, Thesis
from services.strategies import analytics as analytics_mod
from services.strategies.desks import oil as oil_desk
from services.thesis import desk_lookup, write_thesis
from services.thesis.validator import _scenario_implied_median, validate_thesis

ASSUMED_EQUITY_USD = 1000.0   # this eval checks call QUALITY, never sizing - never the live wallet


def pin_temp_state_dir() -> str:
    """Create a fresh temp dir and point PATHIEL_STATE_DIR at it,
    overriding whatever the caller's shell has set. Called once, at the
    very start of main(), before the first live read - see the module
    docstring's Safety note for why this is safe to do here rather than at
    import time. Returns the path, purely so main() can print it."""
    path = tempfile.mkdtemp(prefix="pathiel_live_oil_eval_")
    os.environ["PATHIEL_STATE_DIR"] = path
    return path


class RepairWatcher(logging.Handler):
    """Attached to services.thesis.api's logger for the duration of one
    write_thesis() call. That function logs exactly one WARNING containing
    "repairing" when the validator rejects the first draft and a repair
    round runs (services/thesis/api.py's own logger.warning call) - the
    only signal of a repair round that doesn't cost a second, separately
    billed call to go fish for."""

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.repaired = False
        self.messages: List[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        self.messages.append(msg)
        if "repairing" in msg:
            self.repaired = True


def target_implied_moves(call: Call) -> List[Tuple[float, float]]:
    """[(target, implied horizon-day return)] for a long/short call with an
    entry price - [] for a flat/pair call or one missing entry/targets.
    Pure, offline-testable; the same implied-move formula
    check_targets_reachable uses, kept in one place rather than
    re-derived."""
    if call.side not in ("long", "short") or call.entry_px is None or not call.targets:
        return []
    out = []
    for t in call.targets:
        implied = (t / call.entry_px - 1.0) if call.side == "long" else (1.0 - t / call.entry_px)
        out.append((t, implied))
    return out


def scenario_calibration_report(thesis: Thesis, analytics: DeskAnalytics) -> Optional[dict]:
    """{'implied_median', 'p25', 'p75', 'p10', 'p90'} for the desk's primary
    instrument's empirical final_ret quantiles, or None when there is no
    primary forward stats or no scenarios to check - the same data
    check_scenarios_calibrated grades against, read-only here for the
    report."""
    from services.strategies.desks import oil as _oil  # local: only this eval assumes "oil"

    primary = desk_lookup.primary_instrument(_oil.build_spec(), analytics)
    if primary is None:
        return None
    forward = desk_lookup.find_forward(primary.coin, analytics)
    if forward is None or not thesis.scenarios:
        return None
    implied = _scenario_implied_median(thesis.scenarios)
    if implied is None:
        return None
    q = forward.final_ret
    return {"implied_median": implied, "p10": q.p10, "p25": q.p25, "p75": q.p75, "p90": q.p90}


def format_report(thesis: Thesis, analytics: DeskAnalytics, errors: List[str], repaired: bool) -> str:
    """The full human-readable report, built from pure inputs only - no
    network, no logging side effects - so it has its own offline test."""
    call = thesis.call
    lines = [
        "=" * 72,
        "services/thesis live oil eval report",
        "=" * 72,
        f"call: {call.side} {call.instrument} at {call.entry_px}, stop {call.stop_px}, "
        f"targets {call.targets}, leverage {call.leverage}",
        f"one_liner: {call.one_liner}",
    ]

    structure = desk_lookup.find_structure(call.instrument, analytics)
    if structure is not None:
        if structure.all_time_high is not None:
            lines.append(f"venue all-time high: {structure.all_time_high.price:g} "
                         f"(set {structure.all_time_high.at.date()})")
        if structure.all_time_low is not None:
            lines.append(f"venue all-time low: {structure.all_time_low.price:g} "
                         f"(set {structure.all_time_low.at.date()})")

    forward = desk_lookup.find_forward(call.instrument, analytics)
    reach = None
    if forward is not None and call.side in ("long", "short"):
        reach = forward.long_reach if call.side == "long" else forward.short_reach
    if reach is not None:
        lines.append(f"empirical 7d reach (p25/p50/p75/p90): "
                     f"{reach.p25 * 100:.1f}% / {reach.p50 * 100:.1f}% / "
                     f"{reach.p75 * 100:.1f}% / {reach.p90 * 100:.1f}%")
    for t, implied in target_implied_moves(call):
        lines.append(f"  target {t:g} implies {implied * 100:+.1f}% move")

    calib = scenario_calibration_report(thesis, analytics)
    if calib is not None:
        lines.append(
            f"scenario-implied median: {calib['implied_median'] * 100:+.1f}% "
            f"(empirical p25 {calib['p25'] * 100:+.1f}% / p75 {calib['p75'] * 100:+.1f}%, "
            f"p10 {calib['p10'] * 100:+.1f}% / p90 {calib['p90'] * 100:+.1f}%)"
        )

    lines.append(f"validator: {'PASS' if not errors else 'FAIL'} ({len(errors)} error(s))")
    for e in errors:
        lines.append(f"  - {e}")
    lines.append(f"repair round needed: {'yes' if repaired else 'no'}")
    cost = thesis.usage.cost_usd
    lines.append(
        f"usage: input={thesis.usage.input_tokens} output={thesis.usage.output_tokens} "
        f"cost={'$' + format(cost, '.4f') if cost is not None else 'n/a'}"
    )
    lines.append("=" * 72)
    return "\n".join(lines)


def main() -> int:
    state_dir = pin_temp_state_dir()
    print(f"PATHIEL_STATE_DIR pinned to {state_dir} (temp - never the live journal)")
    spec = oil_desk.build_spec()

    print(f"Building LIVE analytics for '{spec.id}' (real candleSnapshot reads)...")
    analytics = analytics_mod.build_analytics(
        spec,
        # Never the live wallet - this eval grades call quality, not sizing.
        account_fetcher=lambda: AccountSnapshot(equity_usd=ASSUMED_EQUITY_USD, source="assumed"),
    )

    intel = None
    try:
        from services.intel.api import build_packet
        print("Building LIVE intel packet (real news/policy/odds reads)...")
        intel = build_packet(spec)
    except Exception as exc:  # noqa: BLE001 - intel is best-effort for this eval
        print(f"intel packet unavailable ({exc}); writing the thesis from market data alone")

    watcher = RepairWatcher()
    api_logger = logging.getLogger("services.thesis.api")
    api_logger.addHandler(watcher)
    try:
        print("Calling the local Claude CLI (Fable 5.1) to write the thesis...")
        thesis = write_thesis(spec, analytics, intel, None)
    finally:
        api_logger.removeHandler(watcher)

    errors = validate_thesis(thesis, spec, analytics, intel)
    print()
    print(format_report(thesis, analytics, errors, watcher.repaired))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
