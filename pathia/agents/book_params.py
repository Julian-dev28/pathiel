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
