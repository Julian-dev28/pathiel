"""The current book roster, translated into services/risk/derive.py's BookSpec.

Headless desk (operator decision 2026-10-23): the autonomous strategy books
are retired — pathiel/agents/news_surge_short_live.py,
news_surge_multi.py, social_trending_recorder.py, unlock_short_live.py,
xs_reversal_live.py, drawdown_ladder_live.py and copy_trade_live.py are all
deleted. `BOOK_NAMES` is now empty on purpose — there is no NAMED book left
— but `all_book_specs()` does NOT return `[]`: `derive_limits` computes
`can_trade = any(r.can_trade for r in rows)`, so an empty roster would make
every dashboard risk panel, `GET /api/risk/limits`, and the MCP `risk_limits`
/ `risk_size_trade` tools report "No book is configured to trade yet." even
on a well-funded account — there is no book, but there IS exactly one
tradeable path now: the bookless main-engine / MCP-submitted entry executor.py
actually sizes through. `all_book_specs()` returns a one-item roster built by
`main_engine_spec()`, mirroring the `BookSpec` `pathiel.agents.executor`
builds inline for its own feasibility check on every real trade.

Source of truth for HOW a bookless entry is sized (stop_pct, leverage) is
still `pathiel.agents.book_params.book_params()`, the one-precedence
resolver (book value > top-level default > floor) — unchanged by the book
roster going empty.
"""
from __future__ import annotations

from typing import Any, Dict, List

from pathiel.agents.book_params import book_params
from services.risk.derive import BookSpec

CONTRACT_VERSION = "1.0.0"

# Empty on purpose — see module docstring. Was the seven-name live-book
# roster; every one of those books is deleted.
BOOK_NAMES: tuple = ()

_BOOK_CONFIG_KEY: Dict[str, str] = {}

_FIXED_BOOKS: Dict[str, Dict[str, Any]] = {}

_UNSIZED_BOOKS: frozenset = frozenset()

RISK_SCALED_BOOK_NAMES: tuple = ()


def config_key_for(strategy_book: str) -> str:
    """The config key book_params() resolves sizing from for this
    strategy_book name. Identity map now that BOOK_NAMES is empty
    (_BOOK_CONFIG_KEY has no entries to override it)."""
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


MAIN_ENGINE = "main_engine"


def main_engine_spec(config: Dict[str, Any], coin_max_leverage: int = 0) -> BookSpec:
    """The one BookSpec for the bookless (main-engine / MCP-submitted) entry
    path — the only trader on the wallet since the strategy books were
    retired 2026-10-23. `enabled` is forced True (not read from a config
    block's `enabled` key, which this path has never had): whether this
    caller may trade at all is decided earlier by `mode == "LIVE"` and
    `main_engine.entries_enabled` (pathiel.agents.executor.maybe_execute),
    not by anything `derive_limits` checks. Mirrors the `_RiskBookSpec`
    `executor.maybe_execute` builds inline for its own live feasibility
    check — this is the same shape, for callers with no coin in hand yet
    (the dashboard-wide risk preview, the MCP `risk_limits` tool)."""
    config_key = config_key_for(MAIN_ENGINE)
    p = book_params(config, config_key)
    leverage_caps = [v for v in (float(p.leverage), float(coin_max_leverage)) if v > 0]
    return BookSpec(
        name=MAIN_ENGINE, enabled=True, stop_pct=float(p.stop_pct),
        max_leverage=min(leverage_caps) if leverage_caps else float(p.leverage),
    )


def all_book_specs(config: Dict[str, Any]) -> List[BookSpec]:
    """The full roster, for the dashboard-wide GET /api/risk/limits and
    POST /api/risk/preview — no live coin in hand, so no per-coin leverage
    lookup. `BOOK_NAMES` is empty (headless desk, 2026-10-23: every named
    strategy book is deleted), so this is always the one-item
    `[main_engine_spec(config)]` roster rather than one row per `BOOK_NAMES`
    entry — see `main_engine_spec` for why an empty list here would be
    wrong, not just quiet."""
    return [main_engine_spec(config)] + [book_spec_from_config(config, name) for name in BOOK_NAMES]
