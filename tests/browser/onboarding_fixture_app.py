"""Fixture-backed stand-in for the accounts + risk routers this wave builds.

Serves the REAL pathiel.dashboard routes (db.register_routes) — so "/" and
"/account" are byte-identical to production — plus the accounts + risk
routes contracts/accounts/models.py and contracts/risk/models.py define,
answered from those contracts' own committed fixtures wherever the request
matches one exactly, and from a small internally-consistent stand-in
calculator otherwise (equity-scaled by preset — ILLUSTRATIVE, same
disclaimer contracts/risk/fixtures/_build.py carries: services/risk computes
the real numbers). This agent owns no part of services/accounts or
services/risk; this file exists so onboarding's own e2e suite can drive the
real templates and real pathiel.js against *something* shaped exactly like
the wave-2 contract, without waiting on two sibling agents' worktrees.

window.PathielWallet is NOT served from here — tests/browser/conftest.py's
`wallet_stub_page` fixture injects it client-side via Playwright's
add_init_script, exactly as SWARM_RULES section 10 asks for: "a stub
window.PathielWallet that records open() calls and can fire
pathiel:account-changed".

A test drives the server's state through POST /api/_test/seed (fixture-only;
never a real contract route) BEFORE navigating, then lets the page's own
PathielWallet stub fire pathiel:account-changed with a crafted SessionInfo
to simulate a connect completing — the real wallet-ui modal never has to
exist for this suite to prove onboarding's own wiring is correct.

Run standalone for a manual look (SWARM_RULES: dev servers bind 127.0.0.1 on
a port in 8700-8799; this wave's onboarding band is 8840-8849):

    PATHIEL_STATE_DIR="$(mktemp -d)" .venv/bin/python -m tests.browser.onboarding_fixture_app 8840
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

REPO_ROOT = Path(__file__).resolve().parents[2]
ACCOUNTS_FIXTURES = REPO_ROOT / "contracts" / "accounts" / "fixtures"
RISK_FIXTURES = REPO_ROOT / "contracts" / "risk" / "fixtures"
STATIC_DIR = REPO_ROOT / "pathiel" / "static"

sys.path.insert(0, str(REPO_ROOT))


def _load(path: Path) -> dict:
    return json.loads(path.read_text())


SESSION_NEW = _load(ACCOUNTS_FIXTURES / "session_new.json")
SESSION_READY = _load(ACCOUNTS_FIXTURES / "session_ready.json")
ACCOUNT_TRADE = _load(ACCOUNTS_FIXTURES / "account_trade.json")
SUMMARY_TRADE = _load(ACCOUNTS_FIXTURES / "summary_trade.json")
SUMMARY_WATCH = _load(ACCOUNTS_FIXTURES / "summary_watch.json")
LIMITS_15 = _load(RISK_FIXTURES / "limits_15.json")     # equity $15.21, preset starter
LIMITS_5 = _load(RISK_FIXTURES / "limits_5.json")       # equity $5.00, preset starter (can't trade)

# A $5 ActiveSummary isn't in the committed accounts fixtures (only $15.21
# and the $48k watch account are) — contracts/risk/fixtures/limits_5.json
# IS exactly this equity though, so this is the summary that pairs with it,
# built the same way the committed fixtures' own _build.py would.
SUMMARY_LOW_EQUITY = {
    **SUMMARY_TRADE,
    "account": {**SUMMARY_TRADE["account"], "id": "acc_low", "label": "Just connected"},
    "equity_usd": 5.0, "withdrawable_usd": 5.0, "can_trade": False,
    "blockers": ["Equity is below the $6 floor to place a trade."],
}

# ── a small, clearly-illustrative stand-in for services/risk's derive_limits
#
# Exact (equity, preset) matches return the REAL committed fixture verbatim.
# Everything else — a different preset at the same equity, an advanced-field
# override, a different equity entirely — goes through this scaled formula,
# which exists ONLY so the UI has something internally consistent to render
# while this agent's e2e suite proves the WIRING (preset change -> new
# preview; advanced field -> new preview; save -> PUT with the right body),
# not the real risk math (that is services/risk's contract to fulfil).
PRESET_RISK_PCT = {"starter": 3.0, "small": 5.0, "standard": 8.0, "pro": 12.0}
PRESET_DAILY_PCT = {"starter": 8.0, "small": 12.0, "standard": 18.0, "pro": 25.0}
PRESET_MAX_LEV = {"starter": 10.0, "small": 10.0, "standard": 15.0, "pro": 20.0}
PRESET_MAX_POS = {"starter": 1, "small": 2, "standard": 3, "pro": 5}
MIN_ORDER_USD = 10.0
MIN_EQUITY_TO_TRADE_USD = 6.0


def _resolve_auto(equity_usd: float) -> str:
    if equity_usd < 50:
        return "starter"
    if equity_usd < 500:
        return "small"
    if equity_usd < 5000:
        return "standard"
    return "pro"


def compute_limits(equity_usd: float, profile: Dict[str, Any]) -> Dict[str, Any]:
    preset = profile.get("preset") or "auto"
    if preset in (None, "auto"):
        applied = _resolve_auto(equity_usd)
    else:
        applied = preset

    # Exact match against a committed fixture: return it byte-faithful.
    if applied == "starter" and preset in ("auto", "starter") and not any(
        profile.get(k) is not None
        for k in ("risk_per_trade_pct", "daily_loss_limit_pct", "max_leverage", "max_open_positions")
    ):
        if abs(equity_usd - 15.21) < 0.001:
            return LIMITS_15
        if abs(equity_usd - 5.0) < 0.001:
            return LIMITS_5

    risk_pct = profile.get("risk_per_trade_pct") or PRESET_RISK_PCT.get(applied, 3.0)
    daily_pct = profile.get("daily_loss_limit_pct") or PRESET_DAILY_PCT.get(applied, 8.0)
    max_lev = profile.get("max_leverage") or PRESET_MAX_LEV.get(applied, 10.0)
    max_pos = int(profile.get("max_open_positions") or PRESET_MAX_POS.get(applied, 1))

    risk_usd = round(equity_usd * risk_pct / 100.0, 2)
    daily_usd = round(equity_usd * daily_pct / 100.0, 2)
    can_trade = equity_usd >= MIN_EQUITY_TO_TRADE_USD

    books: List[Dict[str, Any]] = []
    for book, lev in (("news_surge_short", max_lev), ("xs_reversal", min(3.0, max_lev))):
        notional = max(MIN_ORDER_USD, equity_usd * lev * (risk_pct / 100.0))
        bumped = notional <= MIN_ORDER_USD + 0.01 and equity_usd * lev * (risk_pct / 100.0) < MIN_ORDER_USD
        book_can_trade = can_trade
        reason = (
            f"Sized up to Hyperliquid's $10 minimum: risks ${round(MIN_ORDER_USD * risk_pct / 100.0, 2)}."
            if bumped else
            f"Fits: ${notional:.0f} at {lev:g}x, risks ${risk_usd}."
        ) if book_can_trade else f"Hyperliquid's $10 minimum order would need more than ${equity_usd:.0f} allows."
        books.append({
            "book": book, "enabled": True, "can_trade": book_can_trade,
            "notional_usd": round(notional, 2) if book_can_trade else None,
            "leverage": lev if book_can_trade else None,
            "margin_usd": round(notional / lev, 2) if book_can_trade else None,
            "stop_pct": 6.0 if book_can_trade else None,
            "risk_usd": risk_usd if book_can_trade else None,
            "risk_pct": risk_pct if book_can_trade else None,
            "bumped_to_min": bumped,
            "needs_equity_usd": None if book_can_trade else round(MIN_ORDER_USD / lev / (risk_pct / 100.0), 2),
            "reason": reason,
        })

    headline = (
        f"You can trade {max_pos} position{'s' if max_pos != 1 else ''} at a time, "
        f"risking about ${risk_usd} per trade."
        if can_trade else
        f"Add {'$' + str(round(MIN_EQUITY_TO_TRADE_USD - equity_usd, 2)).rstrip('0').rstrip('.')} "
        f"to place your first trade."
    )

    # Mirrors services/risk/derive.py's _notes(): the daily-pause note
    # always comes first, then — plainly, so it's never a surprise — which
    # books got bumped to the exchange minimum and the risk% that was
    # allowed for it (starter's own bump cap is 15%; see
    # services/risk/presets.py and this wave's integrator note).
    bumped_books = [b["book"] for b in books if b.get("bumped_to_min")]
    notes = [f"Daily pause after -${daily_usd}."]
    if can_trade and bumped_books:
        bump_cap = profile.get("max_bumped_risk_pct") or (risk_pct * 2)
        notes.append(f"{', '.join(bumped_books)} sized up to the exchange minimum "
                     f"(risk allowed up to {bump_cap:.1f}% for this).")

    return {
        "equity_usd": equity_usd, "preset_applied": applied,
        "profile": {
            "preset": applied, "risk_per_trade_pct": risk_pct, "daily_loss_limit_pct": daily_pct,
            "max_leverage": max_lev, "max_open_positions": max_pos,
            "allow_min_order_bump": True, "max_bumped_risk_pct": risk_pct * 2,
        },
        "risk_per_trade_usd": risk_usd, "daily_loss_limit_usd": daily_usd,
        "max_open_positions": max_pos, "min_order_notional_usd": MIN_ORDER_USD,
        "min_equity_to_trade_usd": MIN_EQUITY_TO_TRADE_USD, "can_trade": can_trade,
        "books": books, "headline": headline,
        "notes": notes if can_trade else [],
    }


class SeedIn(BaseModel):
    scenario: str   # "fresh" | "connected" | "low_equity" | "watch"


def create_app() -> FastAPI:
    """Fresh mutable STATE per call, so parallel test modules never collide
    — the same discipline tests/browser/desks_fixture_app.py's create_app()
    documents for the same reason."""
    from pathiel import dashboard as db

    STATE: Dict[str, Any] = {
        "session": dict(SESSION_NEW),
        "summary": dict(SUMMARY_TRADE),
        "profile": {"preset": "auto", "risk_per_trade_pct": None, "daily_loss_limit_pct": None,
                   "max_leverage": None, "max_open_positions": None},
    }

    app = FastAPI(title="onboarding-fixture")
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    db.register_routes(app)   # the REAL "/", "/account", /api/dashboard/* etc.

    @app.post("/api/_test/seed")
    async def seed(body: SeedIn) -> dict:
        if body.scenario == "fresh":
            STATE["session"] = dict(SESSION_NEW)
            STATE["summary"] = dict(SUMMARY_TRADE)
        elif body.scenario == "connected":
            STATE["session"] = dict(SESSION_READY)
            STATE["summary"] = dict(SUMMARY_TRADE)
        elif body.scenario == "env_account":
            # The legacy .env.local path (contracts/accounts/models.py's
            # source="env") — Julien's actual CURRENT live setup, before
            # any wallet-connect account exists. The MAIN key (not a
            # delegated agent key) is what's really on this box; the
            # account.html security note is keyed on this field, not on
            # any blocker string (services/accounts/summary.py's four
            # blockers never mention a key).
            STATE["session"] = dict(SESSION_READY)
            STATE["summary"] = {
                **dict(SUMMARY_TRADE),
                "account": {**dict(SUMMARY_TRADE)["account"], "source": "env", "agent_address": None},
            }
        elif body.scenario == "low_equity":
            # A FIRST-time connect whose Hyperliquid account happens to be
            # low on funds — needs_onboarding is still true until the
            # wizard's own connect step flips it (simulated client-side via
            # the PathielWallet stub firing pathiel:account-changed, same as
            # "fresh"); only the equity behind it differs.
            STATE["session"] = dict(SESSION_NEW)
            STATE["summary"] = dict(SUMMARY_LOW_EQUITY)
        elif body.scenario == "watch":
            STATE["session"] = {**dict(SESSION_READY), "can_trade": False,
                               "active_account_id": SUMMARY_WATCH["account"]["id"]}
            STATE["summary"] = dict(SUMMARY_WATCH)
        elif body.scenario == "julien_custom":
            # An EXISTING account (not onboarding) whose .agent-config.json
            # has the legacy risk keys services/risk/migrate.py maps —
            # verified for real against services.risk.migrate.migrate_legacy
            # + services.risk.derive.derive_limits in
            # tests/test_dashboard_pages.py's
            # test_a_migrated_legacy_profile_is_detectable_as_custom_front_end_side
            # (0.35 equity-fraction, 3x leverage, max_daily_loss_pct 0.121):
            # preset_applied resolves to "starter" at this equity, but every
            # number is the migrated override, not starter's own default.
            STATE["session"] = dict(SESSION_READY)
            STATE["summary"] = {**dict(SUMMARY_TRADE), "equity_usd": 15.19, "withdrawable_usd": 15.19}
            STATE["profile"] = {
                "preset": "auto", "risk_per_trade_pct": 15.75, "daily_loss_limit_pct": 12.1,
                "max_leverage": 3.0, "max_open_positions": 3,
            }
            return {"ok": True}
        else:
            return {"ok": False, "error": f"unknown scenario {body.scenario!r}"}
        STATE["profile"] = {"preset": "auto", "risk_per_trade_pct": None, "daily_loss_limit_pct": None,
                            "max_leverage": None, "max_open_positions": None}
        return {"ok": True}

    @app.get("/api/session")
    async def session() -> dict:
        return STATE["session"]

    @app.get("/api/accounts/active/summary")
    async def active_summary() -> dict:
        return STATE["summary"]

    @app.get("/api/risk/profile")
    async def get_profile() -> dict:
        return STATE["profile"]

    @app.put("/api/risk/profile")
    async def put_profile(body: dict) -> dict:
        STATE["profile"] = {
            "preset": body.get("preset", "auto"),
            "risk_per_trade_pct": body.get("risk_per_trade_pct"),
            "daily_loss_limit_pct": body.get("daily_loss_limit_pct"),
            "max_leverage": body.get("max_leverage"),
            "max_open_positions": body.get("max_open_positions"),
            "allow_min_order_bump": body.get("allow_min_order_bump"),
            "max_bumped_risk_pct": body.get("max_bumped_risk_pct"),
        }
        return STATE["profile"]

    @app.get("/api/risk/limits")
    async def limits() -> dict:
        equity = STATE["summary"].get("equity_usd") or 0.0
        return compute_limits(equity, STATE["profile"])

    @app.post("/api/risk/preview")
    async def preview(body: dict) -> dict:
        equity = float(body.get("equity_usd") or 0.0)
        profile = body.get("profile") or {}
        return compute_limits(equity, profile)

    return app


if __name__ == "__main__":
    import uvicorn
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8840
    uvicorn.run(create_app(), host="127.0.0.1", port=port)
