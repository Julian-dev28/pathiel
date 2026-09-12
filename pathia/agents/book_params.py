"""One resolver for the three numbers every book needs: notional, leverage, stop.

WHY THIS EXISTS
Each book used to read its own sizing with its own inline fallback, and the
fallbacks did not agree. Measured 2026-09-06:

    book                leverage default   notional default   stop default
    xs_reversal                1                 $11              15%
    news_surge_short          10                 $20              15%
    news_surge_multi          10                 $20               6%
    unlock_short               1                 $20              15%

Every book also carried an explicit value in `.agent-config.json`, so the
fallbacks never fired and the disagreement was invisible. That made the config
look full of redundant duplicates - six books repeating leverage 3, notional
$11, stop 15% - and the obvious tidy-up, deleting the duplicates, would have
silently moved news_surge_short and news_surge_multi to 10x leverage and $20
notional on a $34 account with a $5.20 daily kill. The duplication WAS the
safety.

So the duplicates are not removed by deleting them. They are removed by making
the fallback correct: one precedence, one set of defaults, taken from the top of
the config where the operator can actually see them.

PRECEDENCE
    1. the book's own value            (per-book override, still supported)
    2. the top-level default           (`leverage`, `default_notional_usd`,
                                        `default_stop_pct`)
    3. the conservative floor below    (only if the config is missing both)

THE INVARIANT THAT MAKES OMISSION SAFE
A missing key must never resolve to something LARGER than the top-level
default. Leverage and notional are risk, so their floors are the smallest
sensible values, never the biggest: an absent config produces the smallest
position this account can place, not a 10x one. `test_book_params.py` asserts
this directly, so the landmine cannot be reintroduced by editing a default.
"""
from __future__ import annotations

from typing import Any, Dict, NamedTuple

# Floors used ONLY when the config supplies neither a book value nor a
# top-level default. Deliberately the least aggressive values that still
# produce a placeable order - see the invariant above.
FLOOR_LEVERAGE = 1
FLOOR_NOTIONAL_USD = 10.5      # pathia.client.exchange.MIN_ORDER_USD
FLOOR_STOP_PCT = 15.0

# ── the backup-SL clamp, in one place ───────────────────────────────────────
#
# `executor._backup_sl_price` bounds the server-side stop to
# `max_frac_of_liq / leverage` of entry so it always sits inside liquidation,
# and `executor.stop_honoring_leverage` walks leverage down to fit a requested
# stop under the same rule. Both read these defaults; so does
# `scripts/autonomous_cycle.py`, which sizes a promoted book to sit exactly on
# the boundary.
#
# They live HERE rather than in executor.py because executor imports the
# exchange client, and the evidence loop should not have to load a signing stack
# to find out how wide a stop it may ask for. This module is stdlib-only.
BACKUP_SL_MAX_FRAC_OF_LIQ = 0.60
LIQ_SAFETY_FRAC = 0.85


def max_stop_pct_at_leverage(leverage: int,
                             max_frac_of_liq: float = BACKUP_SL_MAX_FRAC_OF_LIQ,
                             coin_max_leverage: int = 0,
                             liq_safety_frac: float = LIQ_SAFETY_FRAC) -> float:
    """The widest stop, in percent, that survives the clamp at `leverage`.

    The inverse of `executor.stop_honoring_leverage`, which answers the same
    question from the other end: that one takes a stop and returns the highest
    leverage honoring it, this takes a leverage and returns the widest stop it
    will not shrink. Round-tripping either way is asserted in
    tests/test_book_params.py, which is what keeps them from drifting apart.

    Two bounds, exactly as in `stop_honoring_leverage`, and for the same
    reasons:

      width  stop <= max_frac_of_liq / leverage — the clamp itself. Ask for
             more and `_backup_sl_price` silently narrows the stop, which
             changes the strategy rather than the risk.
      liq    stop <= liq_safety_frac * (1/leverage - maintenance) — the stop has
             to be REACHABLE. `1/lev` overstates the liquidation distance
             because maintenance margin eats into it, so on a low-maxLeverage
             coin the naive bound authorizes a stop the position dies before
             reaching. Pass `coin_max_leverage` to close that; omit it for the
             naive (optimistic) bound, which is right when sizing a book rather
             than a specific coin.

    Returns `inf` when neither bound is active, which means nothing here
    constrains the stop — not that any stop is safe.
    """
    lev = max(1, int(leverage or 1))
    bounds = []
    frac = float(max_frac_of_liq or 0.0)
    if frac > 0:
        bounds.append(frac / lev)
    safety = float(liq_safety_frac or 0.0)
    if safety > 0:
        maint = (1.0 / (2.0 * int(coin_max_leverage))) if coin_max_leverage else 0.0
        bounds.append(safety * (1.0 / lev - maint))
    if not bounds:
        return float("inf")
    # Rounded to shed binary-float dust: 0.60/10 is 0.059999999999999998, and a
    # config carrying 5.999999999999999 instead of 6.0 is noise in every diff
    # it touches. Six places is far finer than any stop anyone sets, and the
    # round-trip test proves the rounded value still clears the clamp.
    return round(100.0 * max(0.0, min(bounds)), 6)


class BookParams(NamedTuple):
    notional_usd: float
    leverage: int
    stop_pct: float


def _pick(book_cfg: Dict[str, Any], config: Dict[str, Any],
          book_key: str, top_key: str, floor: Any) -> Any:
    for src, key in ((book_cfg, book_key), (config, top_key)):
        v = src.get(key)
        if v is not None:
            return v
    return floor


def book_params(config: Dict[str, Any], book: str) -> BookParams:
    """Resolve sizing for `book`. `config` is the WHOLE agent config."""
    book_cfg = config.get(book) or {}
    if not isinstance(book_cfg, dict):
        book_cfg = {}
    try:
        notional = float(_pick(book_cfg, config, "notional_usd",
                               "default_notional_usd", FLOOR_NOTIONAL_USD))
    except (TypeError, ValueError):
        notional = FLOOR_NOTIONAL_USD
    try:
        leverage = max(1, int(_pick(book_cfg, config, "leverage",
                                    "leverage", FLOOR_LEVERAGE)))
    except (TypeError, ValueError):
        leverage = FLOOR_LEVERAGE
    try:
        stop = float(_pick(book_cfg, config, "stop_pct",
                           "default_stop_pct", FLOOR_STOP_PCT))
    except (TypeError, ValueError):
        stop = FLOOR_STOP_PCT
    return BookParams(notional_usd=notional, leverage=leverage, stop_pct=stop)
