"""services/risk/api.py — the risk profile + derived limits HTTP surface.

    GET  /api/risk/profile   -> RiskProfile          (config_store.read_risk_profile)
    PUT  /api/risk/profile   -> RiskProfile           body: RiskProfile
    GET  /api/risk/limits    -> DerivedLimits         (active account's equity)
    POST /api/risk/preview   -> DerivedLimits         body: PreviewIn (any equity, any profile)
    POST /api/risk/size      -> TradeSize             body: TradeSizeIn (active account's equity)

INTEGRATOR: this router is not mounted anywhere. pathiel/server.py is
integrator-only this wave (docs/overhaul/SWARM_RULES.md section 10) — add,
near the other service routers:

    from services.risk.api import router as risk_router
    app.include_router(risk_router)

PUT writes through `pathiel.agents.config_store.write_risk_profile`, which
writes wherever `PATHIEL_AGENT_CONFIG_FILE` / `config_store.CONFIG_PATH`
currently points — atomically (tmp file + fsync + os.replace), never the
live file in a test or dev server that exported
`PATHIEL_STATE_DIR`/`PATHIEL_AGENT_CONFIG_FILE` first.

GET /api/risk/limits reads the ACTIVE ACCOUNT's equity: `services.accounts`
if that service is importable and exposes a usable summary (wave-2's
accounts agent owns that service; this file does not assume its exact
shape beyond "some function returns something with an equity_usd-like
field" and degrades past anything it doesn't recognize), else
`pathiel.client.hl_client.fetch_account_state` for the env-configured
account. Either source failing to produce a number is reported as a 503,
never as a silent $0 equity (a $0 DerivedLimits would claim "add $X to
trade" against an account that may actually be fine — a confident wrong
number is worse than an honest failure here).
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

from fastapi import Request, APIRouter, HTTPException

from contracts.risk.models import DerivedLimits, PreviewIn, RiskProfile, TradeSize, TradeSizeIn
from pathiel.agents.config_store import read_agent_config, read_risk_profile, write_risk_profile
from pathiel.client.exchange import MIN_ORDER_USD
from services.risk import books as risk_books
from services.risk.derive import VenueRules, derive_limits
from services.risk.size import size_trade

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/risk", tags=["risk"])

CONTRACT_VERSION = "1.0.0"

# The real, padded exchange minimum — see services/risk/derive.py's
# VenueRules docstring for why the API uses the padded constant rather than
# the contract's illustrative $10.00 default.
_VENUE = VenueRules(min_order_notional_usd=MIN_ORDER_USD)


def _gate(request: Request) -> None:
    """The profile drives live position sizing and limits reveal the account's
    equity: local mode, or the deployment's operator, only. /preview stays
    open, it computes from caller-supplied numbers and reads no account."""
    from services.auth.integration import require_operator_or_local
    require_operator_or_local(request)


def _equity_from_accounts_service() -> Optional[float]:
    """Best-effort read of the active account's equity from services.accounts.

    That service is owned by a different wave-2 agent and may not exist yet
    in a given checkout, or may expose a shape this function doesn't
    anticipate exactly — both degrade to None (caller falls through to
    hl_client) rather than raising, since a missing or unexpected sibling
    service is not this endpoint's failure to report.
    """
    candidates = []
    try:  # the real home of the function (services/accounts/summary.py)
        from services.accounts import summary as accounts_summary  # type: ignore
        candidates.append(accounts_summary)
    except Exception:
        pass
    try:
        from services.accounts import api as accounts_api  # type: ignore
        candidates.append(accounts_api)
    except Exception:
        pass
    if not candidates:
        return None
    for module, attr in ((m, a) for m in candidates for a in ("get_active_summary", "active_summary")):
        fn = getattr(module, attr, None)
        if callable(fn):
            try:
                summary = fn()
            except Exception as e:
                logger.info(f"[risk.api] services.accounts.{attr}() raised, falling back: {e}")
                return None
            eq = getattr(summary, "equity_usd", None)
            if eq is None and isinstance(summary, dict):
                eq = summary.get("equity_usd")
            if eq is not None:
                try:
                    return float(eq)
                except (TypeError, ValueError):
                    return None
    return None


def _equity_from_env_account() -> Optional[float]:
    try:
        from pathiel.client.account_snapshot import account_state
        from pathiel.client.hl_client import resolve_user_address
        user = resolve_user_address()
        # Every dex, like the services.accounts path above it: main-only read
        # $0.01 for an account holding $15.18 on xyz (2026-10-18).
        state = account_state(user, include_hip3=True) or {}
        eq = state.get("equity")
        return float(eq) if eq is not None else None
    except Exception as e:
        logger.warning(f"[risk.api] hl_client.fetch_account_state fallback failed: {e}")
        return None


# One equity read fans out to every perp dex plus spot (~12 requests on the
# dashboard server's 2-weight/s budget). The desk page asks for it on every
# render ("Your size"), so a read is reused for this long. 0 disables.
_EQUITY_CACHE = {"ts": 0.0, "eq": None}
EQUITY_TTL_S = float(os.environ.get("PATHIEL_RISK_EQUITY_TTL_S", "60"))


def active_equity_usd() -> float:
    """The active account's current equity, services.accounts preferred,
    reused for EQUITY_TTL_S seconds."""
    now = time.monotonic()
    if EQUITY_TTL_S > 0 and _EQUITY_CACHE["eq"] is not None and now - _EQUITY_CACHE["ts"] < EQUITY_TTL_S:
        return _EQUITY_CACHE["eq"]
    eq = _equity_from_accounts_service()
    if eq is None:
        eq = _equity_from_env_account()
    if eq is None:
        raise HTTPException(
            status_code=503,
            detail=("could not read the active account's equity (services.accounts "
                    "unavailable and the env account's Hyperliquid state read failed)"),
        )
    _EQUITY_CACHE.update(ts=now, eq=eq)
    return eq


@router.get("/profile", response_model=RiskProfile)
def get_profile(request: Request) -> RiskProfile:
    _gate(request)
    return read_risk_profile(read_agent_config())


@router.put("/profile", response_model=RiskProfile)
def put_profile(profile: RiskProfile, request: Request) -> RiskProfile:
    _gate(request)
    write_risk_profile(profile)
    return profile


@router.get("/limits", response_model=DerivedLimits)
def get_limits(request: Request) -> DerivedLimits:
    _gate(request)
    config = read_agent_config()
    profile = read_risk_profile(config)
    equity = active_equity_usd()
    books = risk_books.all_book_specs(config)
    return derive_limits(equity, profile, books, _VENUE)


@router.post("/preview", response_model=DerivedLimits)
def post_preview(body: PreviewIn) -> DerivedLimits:
    config = read_agent_config()
    books = risk_books.all_book_specs(config)
    return derive_limits(body.equity_usd, body.profile, books, _VENUE)


def _coin_specs(coin: str):
    """(venue max leverage, size decimals) for a coin; None for either when
    the venue metadata cannot be read (sizing still works without them)."""
    from pathiel.client.exchange import get_coin_index, get_max_leverage
    try:
        max_lev = float(get_max_leverage(coin))
    except Exception:
        max_lev = None
    try:
        sz_dec = int(get_coin_index(coin)[1])
    except Exception:
        sz_dec = None
    return max_lev, sz_dec


def prewarm_coin_specs(coins) -> int:
    """Read the venue metadata "Your size" needs (max leverage, size decimals)
    for these coins before the first request does. The first read builds the
    SDK client, which fetches every dex's meta: about 3 s, and it used to land
    on the first desk a user opened after a server restart (2026-10-19).
    pathiel.client.exchange keeps it an hour. Returns how many coins resolved."""
    return sum(None not in _coin_specs(coin) for coin in coins)


@router.post("/size", response_model=TradeSize)
def post_size(body: TradeSizeIn, request: Request) -> TradeSize:
    """One trade sized for the active account (the desk page's "Your size"
    line). Same rules as the books: services/risk/size.py."""
    _gate(request)
    config = read_agent_config()
    coin_max, sz_dec = _coin_specs(body.coin)
    return size_trade(
        active_equity_usd(), read_risk_profile(config), risk_books.all_book_specs(config),
        entry_px=body.entry_px, stop_px=body.stop_px, leverage=body.leverage,
        coin_max_leverage=coin_max, targets=body.targets, venue=_VENUE, sz_decimals=sz_dec)

