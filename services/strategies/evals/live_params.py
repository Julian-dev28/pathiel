"""Single source of truth for the desks' LIVE `Params`, 2026-11-03.

The live oil rule has used a 15% risk cap since 2026-11-02
(`desk_tick_sim.Params.risk_cap_frac = 0.15`, `LOSS_AUDIT_2026-11-02.md`,
`docs/strategy/OIL_STRATEGY.md`). Several eval harnesses built their own
"live" baseline `Params` by hand --
`dataclasses.replace(base_params(), starting_equity=...)` for Brent,
`dataclasses.replace(scouting_params(...), starting_equity=..., **SILVER_LIVE)`
for silver -- and every one of them forgot `risk_cap_frac`, so their "live"
row has been full-port size since the cap went live, not the real live
rule, even where the docstring says otherwise. Found by
`extended_history_test.py` (`EXTENDED_HISTORY_TEST_2026-11-03.md`'s
Caveats), which first caught it on `closer_target_test.py` and
`brent_stop_buffer_test.py`.

`live_brent_params` / `live_silver_params` are now the one place that
builds that baseline. Any eval that claims to run "the live rules" should
build its baseline through one of these two, not by hand --
`services/strategies/tests/test_live_params.py` fails the day a new
harness (or an edit to an old one) drifts from them again.

`desk_tick_sim.Params`'s own default stays `risk_cap_frac=None` (the
pre-2026-11-02 behaviour), so every golden test of the sim's old output
stays byte-identical. Only these two functions, and the harnesses that
call them, know about the cap.

Second gap, closed 2026-11-13 (RULE_FUSION_2026-11-13.md section 5): the
live 20/15 leverage fit (`next_trade.LEVERAGE_FIT`, on since 2026-11-04)
was set here for gold only; Brent and silver evals replayed a fixed-20x
rule that refused every stop the desk took at 15x. `live_leverage_fit`
reads each coin's candidates off `next_trade.leverage_fit_for`, the same
function the live tick (`services/strategies/api.py`) and the MCP server's
`handle_place_bracket` call, so the eval baseline cannot drift from live
again without `tests/test_live_params.py` failing.
"""
from __future__ import annotations

import dataclasses
from typing import Any, Optional, Tuple

from services.strategies.backtest.desk_tick_sim import Params
from services.strategies.backtest.desk_tick_sim import live_params as desk_live_params
from services.strategies.evals.market_scout import scouting_params
from services.strategies.next_trade import (ATR_BUFFER_MULT, BRENT, GOLD, SILVER, STOP_BUFFER_FLOOR_FRAC,
                                            leverage_fit_for, stop_buffer_floor_frac_for)

# The live oil rule's risk cap since 2026-11-02 (LOSS_AUDIT_2026-11-02.md,
# OIL_STRATEGY.md): a stop at the fill may cost at most this share of the
# desk's own equity. The position's leverage, and so its liquidation price,
# is unchanged -- only the size shrinks (desk_tick_sim.risk_capped_notional).
LIVE_RISK_CAP_FRAC = 0.15


def live_leverage_fit(coin: str) -> Optional[Tuple[float, ...]]:
    """`Params.entry_fit_leverage` for `coin`'s LIVE rule, read off the one
    place the live tick and place_bracket read it from
    (`next_trade.leverage_fit_for`; `services/strategies/api.py`
    next_trade(..., leverage_fit=leverage_fit_for(coin)) and
    `scripts/pathiel-mcp-server.py` handle_place_bracket's fit_leverage
    call). None when the coin has no fit (the sim's "fixed leverage").

    Corrected 2026-11-13 (RULE_FUSION_2026-11-13.md section 5): the desk
    has fitted Brent's leverage from 20/15 since 11-04, but
    `live_brent_params` never set `entry_fit_leverage`, so every eval that
    replayed "today's live setup" on Brent sized wide-stop entries
    differently from the real desk (the 20x gate refused what live took at
    15x). Only gold went through `leverage_fit_for`. Deriving the value
    here, never typing it, is what keeps the eval baseline from drifting
    from live again: `tests/test_live_params.py` fails the day
    `next_trade.LEVERAGE_FIT`/`LEVERAGE_FIT_COINS` and this disagree."""
    fit = leverage_fit_for(coin)
    return tuple(float(c) for c in fit) if fit else None


def live_brent_params(**overrides: Any) -> Params:
    """The live Brent/oil rule: `desk_tick_sim.live_params()` (market entry
    off the 24h range, stop 0.75 beyond structure, target 0.5% inside the
    far edge as a limit, 2R gate, 20x, 15-minute ticks, no entry after a
    break, weekend flat since 2026-11-05) plus the 15% risk cap and the
    live 20/15 leverage fit (`live_leverage_fit(BRENT)`, live since
    2026-11-04; missing here until 2026-11-13). A replay of
    a trade from before a rule change starts from the sim's
    `live_params_before_*` instead. `**overrides` applies last (`dataclasses.replace`), so a caller
    can still set e.g. `starting_equity` or the Brent-only
    `size_slippage=True` fidelity knob (`base_params()`'s own addition --
    EXIT_SLIP_CURVE is calibrated off the live xyz:BRENTOIL book, so it is
    never set here for silver's sake)."""
    live = {"risk_cap_frac": LIVE_RISK_CAP_FRAC, "entry_fit_leverage": live_leverage_fit(BRENT)}
    return dataclasses.replace(desk_live_params(), **{**live, **overrides})


def live_silver_params(venue_max_leverage: float, atr_price_mean: float, **overrides: Any) -> Params:
    """The live silver rule: `market_scout.scouting_params` (leverage capped
    at the venue max) built on top of `live_brent_params()` so
    `risk_cap_frac` carries over, with its default fixed, MEAN-ATR stop
    buffer overridden to the real live rule -- `next_trade.stop_buffer_for`'s
    per-TICK buffer (`Params.stop_buffer_atr = next_trade.ATR_BUFFER_MULT`,
    1.3 x that tick's own closed-1h ATR14, not the dataset's mean ATR) with
    its live floor (`Params.stop_buffer_floor_frac = next_trade.
    STOP_BUFFER_FLOOR_FRAC`, 0.8% of the mid; `SILVER_STOP_FLOOR_TEST_2026-
    11-02.md`). Silver trades the weekend (`scouting_params` sets
    `weekend_off=None`: the oil weekend flat lost on silver). `**overrides`
    applies last."""
    base = scouting_params(venue_max_leverage, atr_price_mean, base=live_brent_params())
    # silver's own fit (`leverage_fit_for(SILVER)`, 20/15 since 2026-11-04),
    # not Brent's carried over: the two are read off next_trade separately.
    live = {"stop_buffer_atr": ATR_BUFFER_MULT, "stop_buffer_floor_frac": STOP_BUFFER_FLOOR_FRAC,
            "entry_fit_leverage": live_leverage_fit(SILVER)}
    return dataclasses.replace(base, **{**live, **overrides})


def live_gold_params(venue_max_leverage: float, atr_price_mean: float, **overrides: Any) -> Params:
    """The live gold rule (the metals desk's primary since 2026-11-05,
    GOLD_STRATEGY_TEST_2026-11-05.md): `live_silver_params`' shape with
    gold's own floor (`next_trade.stop_buffer_floor_frac_for(GOLD)`, 0.8%
    of the mid, the test's plateau) and the leverage fit the live tick
    applies on gold (`next_trade.leverage_fit_for(GOLD)`, 20/15; None here
    when the coin has none). The one place an eval builds gold's live
    baseline; `test_gold_desk.py` pins it to the test's chosen variant.
    `**overrides` applies last."""
    base = live_silver_params(venue_max_leverage, atr_price_mean)
    live = {"stop_buffer_floor_frac": stop_buffer_floor_frac_for(GOLD), "entry_fit_leverage": live_leverage_fit(GOLD)}
    return dataclasses.replace(base, **{**live, **overrides})
