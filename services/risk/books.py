"""The current book roster, translated into services/risk/derive.py's BookSpec.

Source of truth for WHICH books exist is
`pathiel.agents.rebalancer_owned._ACTIVE_CLAIM_BOOKS` — the SEVEN names
`analysis["strategy_book"]` gets tagged with and the claims registry tracks.
`BOOK_NAMES` below mirrors it exactly; `tests/test_books.py` pins the two
sets equal so they cannot silently drift apart. It is duplicated rather than
imported because `rebalancer_owned` is a live-signing surface (it "freezes
the state directory at import time" — see pyproject.toml's ruff exclusion
comment); this package should never need to load that just to answer "what
books exist".

copy_trade IS NOT RISK-SCALED OR FIXED/STRUCTURAL — IT'S A THIRD SHAPE
It mirrors a chosen leader's own positions at a capped `leverage_mult`
(pathiel/agents/copy_trade_live.py), so its risk comes from someone else's
position sizing, not from a stop-distance risk budget or a fixed rung
structure. Modeling that properly is out of this wave's scope (it is also
`enabled: false` in the live config today — see docs/overhaul/
PROFIT_AUDIT.md's "for the integrator" territory for cross-ownership notes,
none of which cover this). It gets neither `stop_pct` nor
`fixed_min_equity_usd`, so `derive_limits` reports it "has no sizing rule
configured" — honest, not a crash, not a fabricated stop.

Source of truth for HOW a book is sized (stop_pct, leverage) is
`pathiel.agents.book_params.book_params()`, the existing one-precedence
resolver (book value > top-level default > floor). This module does not
re-decide sizing inputs — it only turns the already-resolved numbers into a
`BookSpec` for `derive_limits`.

ONE NAMING SPLIT, PRE-EXISTING, NOT INTRODUCED HERE
`unlock_short_live.py` tags its own analyses `"unlock_short_runin"` (matching
the claims registry) but reads its sizing config under the key
`"unlock_short"` (pathiel/agents/unlock_short_live.py:40,102). Every other
book's strategy_book name equals its config key; `_BOOK_CONFIG_KEY` carries
the one exception.

drawdown_ladder IS THE ONE FIXED/STRUCTURAL BOOK
Five resting rungs at 1x, not a risk-per-trade question — see
`drawdown_ladder_live.py`'s `rung_notional`/`min_equity_to_trade`. Its $525
hard floor is RE-DERIVED here from the exact same formula
(`MIN_ORDER_USD x RUNGS x len(UNIVERSE) / (LEVERAGE x DEFAULT_SLEEVE_FRAC)`
= `10.5 x 5 x 5 / (1 x 0.5)` = `525.0`) rather than imported, for the same
reason as above (that module's import chain reaches live signing).
`tests/test_risk_books_registry.py` pins this constant against the live
formula too, so a future change to those constants fails a test here
instead of silently going stale.
"""
from __future__ import annotations

from typing import Any, Dict, List

from pathiel.agents.book_params import book_params
from services.risk.derive import BookSpec

CONTRACT_VERSION = "1.0.0"

# Mirrors pathiel.agents.rebalancer_owned._ACTIVE_CLAIM_BOOKS. See module docstring.
BOOK_NAMES: tuple = (
    "news_surge_short",
    "news_surge_multi",
    "social_trending",
    "unlock_short_runin",
    "xs_reversal",
    "drawdown_ladder",
    "copy_trade",
)

_BOOK_CONFIG_KEY: Dict[str, str] = {
    "news_surge_short": "news_surge_short",
    "news_surge_multi": "news_surge_multi",
    "social_trending": "social_trending",
    "unlock_short_runin": "unlock_short",
    "xs_reversal": "xs_reversal",
    "drawdown_ladder": "drawdown_ladder",
    "copy_trade": "copy_trade",
}

# drawdown_ladder_live.py constants, re-derived (see module docstring):
#   _MIN_ORDER_USD=10.5, RUNGS=5, len(UNIVERSE)=5, LEVERAGE=1, DEFAULT_SLEEVE_FRAC=0.5
_DRAWDOWN_LADDER_MIN_EQUITY_USD = 10.5 * 5 * 5 / (1 * 0.5)   # == 525.0

_FIXED_BOOKS: Dict[str, Dict[str, Any]] = {
    "drawdown_ladder": {
        "fixed_min_equity_usd": _DRAWDOWN_LADDER_MIN_EQUITY_USD,
        "fixed_description": "five resting rungs of $10.50 each at 1x, buying a 21% drawdown",
    },
}

# Neither risk-scaled nor fixed/structural — see the module docstring's
# "copy_trade is a third shape" section. book_spec_from_config gives these a
# plain BookSpec (no stop_pct, no fixed_min_equity_usd), which derive_limits
# reports as "has no sizing rule configured" rather than guessing.
_UNSIZED_BOOKS = frozenset({"copy_trade"})

RISK_SCALED_BOOK_NAMES: tuple = tuple(
    n for n in BOOK_NAMES if n not in _FIXED_BOOKS and n not in _UNSIZED_BOOKS)


def config_key_for(strategy_book: str) -> str:
    """The config key book_params() resolves sizing from for this
    strategy_book name. Equal to the name itself except
    unlock_short_runin -> unlock_short."""
    return _BOOK_CONFIG_KEY.get(strategy_book, strategy_book)


def _is_enabled(book_cfg: Any) -> bool:
    if not isinstance(book_cfg, dict):
        return False
    # shadow_only folds into "not really tradeable" here: a shadow-demoted
    # book never places a real order (every live book module returns before
    # its execute call when shadow_only is set), so BookFeasibility should
    # not claim can_trade=True for one. "There is no shadow tier: a book
    # trades or it does not exist" (README.md).
    return bool(book_cfg.get("enabled", False)) and not bool(book_cfg.get("shadow_only", False))


def book_spec_from_config(config: Dict[str, Any], strategy_book: str,
                          coin_max_leverage: int = 0) -> BookSpec:
    """Build the one BookSpec `strategy_book` needs for a feasibility check.

    `coin_max_leverage` is the live per-coin leverage cap
    (`pathiel.client.exchange.get_max_leverage(coin)`) when the caller has a
    specific coin in hand (the executor, mid-trade). Pass 0 (default) when
    there is no single coin — a roster-wide preview such as
    `GET /api/risk/limits` — in which case only the book's own configured
    leverage and the profile's cap apply.
    """
    config_key = config_key_for(strategy_book)
    book_cfg = config.get(config_key) or {}
    enabled = _is_enabled(book_cfg)

    fixed = _FIXED_BOOKS.get(strategy_book)
    if fixed is not None:
        return BookSpec(name=strategy_book, enabled=enabled, **fixed)

    if strategy_book in _UNSIZED_BOOKS:
        return BookSpec(name=strategy_book, enabled=enabled)

    p = book_params(config, config_key)
    leverage_caps = [v for v in (float(p.leverage), float(coin_max_leverage)) if v > 0]
    return BookSpec(
        name=strategy_book, enabled=enabled, stop_pct=float(p.stop_pct),
        max_leverage=min(leverage_caps) if leverage_caps else float(p.leverage),
    )


def all_book_specs(config: Dict[str, Any]) -> List[BookSpec]:
    """The full seven-book roster, for the dashboard-wide GET /api/risk/limits
    and POST /api/risk/preview — no live coin in hand, so no per-coin
    leverage lookup (see book_spec_from_config)."""
    return [book_spec_from_config(config, name) for name in BOOK_NAMES]
