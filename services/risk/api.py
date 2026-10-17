"""services/risk/api.py — the risk profile + derived limits HTTP surface.

    GET  /api/risk/profile   -> RiskProfile          (config_store.read_risk_profile)
    PUT  /api/risk/profile   -> RiskProfile           body: RiskProfile
    GET  /api/risk/limits    -> DerivedLimits         (active account's equity)
    POST /api/risk/preview   -> DerivedLimits         body: PreviewIn (any equity, any profile)

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
from typing import Optional

from fastapi import APIRouter, HTTPException

from contracts.risk.models import DerivedLimits, PreviewIn, RiskProfile
from pathiel.agents.config_store import read_agent_config, read_risk_profile, write_risk_profile
from pathiel.client.exchange import MIN_ORDER_USD
from services.risk import books as risk_books
from services.risk.derive import VenueRules, derive_limits

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/risk", tags=["risk"])

CONTRACT_VERSION = "1.0.0"

# The real, padded exchange minimum — see services/risk/derive.py's
# VenueRules docstring for why the API uses the padded constant rather than
# the contract's illustrative $10.00 default.
_VENUE = VenueRules(min_order_notional_usd=MIN_ORDER_USD)


def _equity_from_accounts_service() -> Optional[float]:
    """Best-effort read of the active account's equity from services.accounts.

    That service is owned by a different wave-2 agent and may not exist yet
    in a given checkout, or may expose a shape this function doesn't
    anticipate exactly — both degrade to None (caller falls through to
    hl_client) rather than raising, since a missing or unexpected sibling
    service is not this endpoint's failure to report.
    """
    try:
        from services.accounts import api as accounts_api  # type: ignore
    except Exception:
        return None
    for attr in ("get_active_summary", "active_summary"):
        fn = getattr(accounts_api, attr, None)
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
        from pathiel.client.hl_client import fetch_account_state, resolve_user_address
        user = resolve_user_address()
        state = fetch_account_state(user) or {}
        eq = state.get("equity")
        return float(eq) if eq is not None else None
    except Exception as e:
        logger.warning(f"[risk.api] hl_client.fetch_account_state fallback failed: {e}")
        return None


def active_equity_usd() -> float:
    """The active account's current equity, services.accounts preferred."""
    eq = _equity_from_accounts_service()
    if eq is None:
        eq = _equity_from_env_account()
    if eq is None:
        raise HTTPException(
            status_code=503,
            detail=("could not read the active account's equity (services.accounts "
                    "unavailable and the env account's Hyperliquid state read failed)"),
        )
    return eq


@router.get("/profile", response_model=RiskProfile)
def get_profile() -> RiskProfile:
    return read_risk_profile(read_agent_config())


@router.put("/profile", response_model=RiskProfile)
def put_profile(profile: RiskProfile) -> RiskProfile:
    write_risk_profile(profile)
    return profile


@router.get("/limits", response_model=DerivedLimits)
def get_limits() -> DerivedLimits:
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
