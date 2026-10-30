"""Fixture-backed stand-in for the real "/" (Desk) page's own endpoints.

Serves the real pathiel/templates/oil_desk.html and pathiel/static/* against
a single, real oil DeskBrief (reused from desks_fixture_app._load_oil(), the
actual contracts/strategies/fixtures/oil_*.json re-anchored to "now") and a
single account whose open position is picked by create_app(scenario) — this
is the one knob that drives the Desk page's own oil_desk.js through:

  - a mismatched position: same side/instrument as the thesis call but a
    different entry, leverage or stop (2026-10-24: Julien's own desk showed
    a thesis still reading "long Brent 109.24 at 5x, stop 108.05" against a
    live position at 111.57, 10x, stop 110.9 — this app can reproduce that
    exact pair, or any other mismatch, exactly)
  - a matching position (the thesis and the venue agree) — proves the
    mismatch banner does NOT fire on a true positive
  - no position at all (flat) — proves it does not fire with nothing to
    compare against either

No other route this page calls needs real data to exercise the banner, so
everything else (session, line, levels, log) is a fixed, minimal, valid
payload — just enough that the rest of the page renders with zero console
errors, never under test here.

Run standalone for a manual look (SWARM_RULES: dev servers bind 127.0.0.1 on
a port in 8710-8719):

    PATHIEL_STATE_DIR="$(mktemp -d)" .venv/bin/python -m tests.browser.oil_desk_fixture_app 8711
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from tests.browser.desks_fixture_app import _load_oil

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = REPO_ROOT / "pathiel" / "templates"
STATIC_DIR = REPO_ROOT / "pathiel" / "static"

ACCOUNT_ID = "env"

# Three scenarios oil_desk.js's renderPositionMismatch() must tell apart.
# "mismatch"/"match" are keyed on _load_oil()'s own real thesis call
# (xyz:BRENTOIL, long, entry 109.24, leverage 5x, stop 108.05) so "match" is
# exactly that call's own numbers and "mismatch" is the real pair this was
# written for (2026-10-24 live desk: thesis said 109.24 at 5x, stop 108.05;
# the open position was 111.57 at 10x, stop 110.9).
_POSITION_COMMON = {
    "coin": "xyz:BRENTOIL", "side": "long", "size": 1.62, "margin_type": "isolated",
    "notional_usd": 180.86, "upnl_usd": 0.11, "liq_px": 102.98, "liq_dist_pct": 7.7,
    "tp_px": ["114.0"], "stop_covers": 1.62, "naked": False, "mark_px": 111.665,
    "pnl_pct": 0.09, "to_stop_pct": 0.69, "to_tp_pct": 2.09, "r_now": 0.14,
}
POSITIONS: Dict[str, Optional[Dict[str, Any]]] = {
    "mismatch": {**_POSITION_COMMON, "entry_px": 111.57, "leverage": 10, "stop_px": ["110.9"]},
    "match": {**_POSITION_COMMON, "entry_px": 109.24, "leverage": 5, "stop_px": ["108.05"]},
    "flat": None,
}


def create_app(scenario: str) -> FastAPI:
    """scenario: one of POSITIONS's keys — "mismatch" (position diverges
    from the thesis call), "match" (position agrees with it) or "flat" (no
    open position). Fresh DeskBrief per call — no shared mutable module
    state."""
    position = POSITIONS[scenario]
    oil = _load_oil()
    # The real oil_thesis.json fixture's own call is a WTI (xyz:CL) entry —
    # fine for desk_brief's own tests, but this suite needs the exact
    # Brent call/position pair the 2026-10-24 live bug actually showed, so
    # the call here is pinned to that, independent of whatever the shared
    # fixture file carries.
    oil["thesis"] = oil["thesis"].model_copy(update={"call": oil["thesis"].call.model_copy(update={
        "side": "long", "instrument": "xyz:BRENTOIL",
        "one_liner": "Still long Brent 109.24 at 5x. Stop 108.05 under the 8h low 108.29. "
                      "Target 112.19 (60d high), then 114.",
        "entry_px": 109.24, "entry_zone": [109.0, 109.5], "stop_px": 108.05,
        "targets": [112.19, 114.0], "leverage": 5.0,
    })})
    oil_html = (TEMPLATES / "oil_desk.html").read_text(encoding="utf-8")

    app = FastAPI(title="oil-desk-fixture")
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        return HTMLResponse(oil_html)

    @app.get("/api/session")
    async def session() -> dict:
        return {"local_mode": True, "address": None, "active_account_id": ACCOUNT_ID,
                "accounts_count": 1, "can_trade": True, "needs_onboarding": False}

    @app.get("/api/desks/active")
    async def active() -> dict:
        return {"active_desks": ["oil"], "default_desk": "oil"}

    @app.get("/api/desks/{desk_id}")
    async def get_desk(desk_id: str) -> dict:
        if desk_id != "oil":
            raise HTTPException(status_code=404, detail="unknown desk")
        return {
            "spec": oil["spec"].model_dump(mode="json"),
            "analytics": oil["analytics"].model_dump(mode="json"),
            "intel": oil["intel"].model_dump(mode="json"),
            "thesis": oil["thesis"].model_dump(mode="json"),
            "history": [], "track_record": {"n_graded": 0, "direction_hit_rate": None,
                                              "mean_brier": None, "mean_ret_pct": None,
                                              "by_author": {}, "realized_pnl_usd": None,
                                              "n_trades": 0, "follow_rate": None},
            "stale": [], "refreshing": False, "refresh_job": None,
            "quick_markdown": oil["thesis"].call.one_liner,
        }

    @app.get("/api/desks/{desk_id}/line")
    async def line(desk_id: str, leverage: float = 20) -> dict:
        return {"ok": False, "reason": "fixture: no line computed"}

    @app.get("/api/desks/{desk_id}/next")
    async def next_trade(desk_id: str, leverage: float = 20) -> dict:
        return {"ok": False, "source": "desk_rules", "reason": "fixture: no next trade computed"}

    @app.get("/api/desks/{desk_id}/levels")
    async def levels(desk_id: str) -> dict:
        return {"mid": 111.55, "last_closed_1h": None, "range_8h": None,
                "range_24h": None, "range_48h": None, "atr14_1h": None,
                "atr14_pct": None, "mid_pos_in_24h": None}

    @app.get("/api/desks/{desk_id}/log")
    async def log(desk_id: str, n: int = 30) -> dict:
        return {"lines": []}

    @app.get("/api/accounts")
    async def accounts() -> List[dict]:
        return [{"id": ACCOUNT_ID, "label": "Fixture account", "main_address": "0xFIXTURE",
                  "agent_address": "0xAGENT", "mode": "trade", "source": "env",
                  "is_active": True, "created_at": "2026-10-23T00:00:00Z",
                  "verified_at": "2026-10-23T00:00:00Z", "agent_name": None}]

    @app.get("/api/accounts/{account_id}/positions")
    async def positions(account_id: str) -> dict:
        positions = [position] if position else []
        return {"user": "0xFIXTURE",
                "accounts": [{"dex": "xyz", "equity_usd": 64.03,
                               "notional_usd": position["notional_usd"] if position else 0.0,
                               "positions": positions, "any_naked": False}],
                "any_naked": False}

    return app


if __name__ == "__main__":
    import uvicorn

    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8711
    scenario = sys.argv[2] if len(sys.argv) > 2 else "mismatch"
    if not (8710 <= port <= 8719):
        raise SystemExit(f"port {port} is outside this agent's assigned 8710-8719 band")
    uvicorn.run(create_app(scenario), host="127.0.0.1", port=port, log_level="info")
