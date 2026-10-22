"""Pathiel-Trader — FastAPI server exposing the trading agent and Hyperliquid endpoints."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from typing import Any, Dict


from pathiel import compat  # noqa: E402 — pure, no env reads at import


def _load_env_local_early() -> None:
    """Pull `.env.local` into os.environ BEFORE any pathiel imports.

    `client/exchange.py` captures `PRIVATE_KEY_HEX = os.environ.get(...)` at
    module-load time. If `.env.local` is only loaded in the `__main__` block
    at the bottom of this file (the prior layout), every signing call
    afterwards returns "HYPERLIQUID_PRIVATE_KEY not set" because the
    module-level constant was frozen empty during the import chain — fine
    for the trading_loop (which loads env earlier) but broken for the
    server. Loading here, before the imports below, fixes it.
    """
    candidates = [".env.local",
                  os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env.local")]
    for p in candidates:
        if os.path.exists(p):
            with open(p) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, _, v = line.partition("=")
                        os.environ.setdefault(k.strip(), v.strip())
            return


_load_env_local_early()

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response  # noqa: E402
from fastapi.encoders import jsonable_encoder                        # noqa: E402
from fastapi.middleware.cors import CORSMiddleware                   # noqa: E402
from fastapi.responses import JSONResponse                            # noqa: E402
from fastapi.staticfiles import StaticFiles                           # noqa: E402

from pathiel.metrics import render_metrics                      # noqa: E402

from pathiel import __version__, dashboard, session_log         # noqa: E402
from pathiel.dashboard import _require_operator                 # noqa: E402
from pathiel.agents.config_store import (                       # noqa: E402
    read_agent_config,
    update_agent_config,
)
from pathiel.agents.executor import maybe_execute               # noqa: E402
from pathiel.agents.memory import memory                        # noqa: E402
from pathiel.agents.perception import scan_once                 # noqa: E402
from pathiel.agents.research import research                    # noqa: E402
from pathiel.client.hl_client import (                          # noqa: E402
    fetch_account_state,
    fetch_all_mids,
    fetch_hl_candles,
    resolve_user_address,
)
from pathiel.client.universe import get_universe                # noqa: E402

# ── Logging ────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
logger = logging.getLogger("pathiel-server")

# ── Session log ────────────────────────────────────────────────────────────────
# Shared activity feed (pathiel.session_log) — the same JSONL file the
# trading loop and status.py use. Writes run in an executor so the file append
# never blocks the event loop.


async def _append_session_log(entry: Dict[str, Any]) -> None:
    """Append one event to the shared session log (non-blocking)."""
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, session_log.append, entry)


async def _json_object(request: Request, *, empty_ok: bool = False) -> Dict[str, Any]:
    """The request's JSON body as a dict, else a 400. Read on the event loop
    (it is an await); the work the body asks for then goes to a worker thread."""
    if empty_ok and not await request.body():
        return {}
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON")
    if not isinstance(body, dict):
        raise HTTPException(400, "JSON body must be an object")
    return body


# ── PID file helpers (start/stop) ──────────────────────────────────────────────

# Falls back to the pre-rename name so a server started before the rename is
# still found by `pathiel stop`.
PID_FILE = compat.legacy_path(os.path.expanduser("~/.pathiel.pid"),
                              os.path.expanduser("~/.pathiel.pid"))


def _is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


# ── Rate limiter for scan endpoint ─────────────────────────────────────────────

_last_scan_at: float = 0
_SCAN_MIN_SECONDS = 30


# ── Lifespan ───────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load persisted memory on startup. NO shutdown flush (audit 2026-07-10:
    the server's copy goes stale minutes after boot while the LOOP keeps
    writing .agent-memory.json — a graceful shutdown could roll live trade
    memory back to server-start time). PATHIEL_STATE_READONLY guards the
    other write doors (executor-triggered flush / DSL saves) the same way."""
    memory.load()
    logger.info("Pathiel server started — memory loaded (state writes read-only)")
    # Desk analytics warm in a background thread so /desks opens instantly
    # (PATHIEL_DESK_WARMUP=0 skips; the test session sets it).
    from services.strategies.facade import start_warmup
    start_warmup()
    # Same idea for "Your size" on those desks: its venue metadata. Off with
    # PATHIEL_META_PREWARM=0 (the test session sets it).
    if os.environ.get("PATHIEL_META_PREWARM", "1") != "0":
        threading.Thread(target=_prewarm_size_specs, name="size-prewarm", daemon=True).start()
    yield
    logger.info("Pathiel server stopped — no state flush by design")


def _prewarm_size_specs() -> None:
    """Warm the metadata every desk coin's "Your size" reads, at background
    priority so a page request made meanwhile goes first."""
    from pathiel.client.rate_limit import background_priority
    from services.risk.api import prewarm_coin_specs
    from services.strategies import desks
    try:
        coins = sorted({i.coin for d in desks.list_desk_ids() for i in desks.get_spec(d).instruments})
        with background_priority():
            warmed = prewarm_coin_specs(coins)
        logger.info(f"[server] size metadata warm for {warmed}/{len(coins)} desk coins")
    except Exception as exc:  # noqa: BLE001 - best-effort; "Your size" warms lazily
        logger.warning(f"[server] size metadata prewarm failed ({exc}); it warms on first use")


# ── App ────────────────────────────────────────────────────────────────────────

app = FastAPI(title="Pathiel-Trader", version=__version__, lifespan=lifespan)

# Wildcard origins + credentials=True is invalid per the CORS spec and would be
# silently rejected by browsers. Token auth happens via X-Operator-Token /
# ?token=, neither of which is a credential the browser auto-sends, so we don't
# need credentialed CORS. Keep wildcard origins for tool/curl access; flip
# credentials off so a future cookie-auth flow can't be abused cross-origin.

# Optional hard read-only mode: PATHIEL_DASHBOARD_READONLY=1 refuses every
# mutating request (all mutators here are POSTs) regardless of token — for
# exposing the dashboard anywhere you don't fully trust (audit 2026-07-10).
# Signing in is a POST and is not a mutation of anything this flag protects.
# Without this exemption the read-only demo 403s /auth/verify, RainbowKit
# renders "Error verifying signature, please retry!", and the operator retries
# forever against a guard that was never aimed at them. The flag exists to
# freeze TRADING and configuration, so it stops at the session boundary:
# /auth/nonce, /auth/verify, /auth/logout open and close sessions and touch no
# position, no order and no book.
_READONLY_EXEMPT_PREFIXES = ("/auth/",)


@app.middleware("http")
async def _readonly_guard(request: Request, call_next):
    if (request.method == "POST"
            and os.environ.get("PATHIEL_DASHBOARD_READONLY")
            and not request.url.path.startswith(_READONLY_EXEMPT_PREFIXES)):
        return JSONResponse({"detail": "dashboard is read-only (PATHIEL_DASHBOARD_READONLY)"},
                            status_code=403)
    return await call_next(request)


# ── who may read the account ─────────────────────────────────────────────────
#
# Every /api/dashboard/* route was open. Audit 2026-09-04, against a public
# Fly host with force_https and allow_origins=["*"]: an unauthenticated GET to
# /api/dashboard/summary returned equity, free balance, daily P&L and open
# position count, /risk added net capital in and peak equity, and
# /closed-trades the whole trade history. Anyone holding the URL held the
# account's books. For one operator that is a privacy leak; the moment a second
# user exists it is a cross-tenant breach by construction.
#
# Default is now CLOSED. `PATHIEL_PUBLIC_DASHBOARD=1` restores the old open-read
# behaviour for a genuinely single-operator box behind a private network, and
# is named so it is obvious in a diff and in `fly secrets list`.
#
# Read paths that must stay open, and why:
#   /api/health*   the Fly and k8s probes call these with no credential, and a
#                  health check that needs a session cannot report a broken
#                  session.
#   /auth/*        login is unauthenticated by definition.
#   /static, /     the pages themselves; they render a sign-in prompt and fetch
#                  their data over these gated APIs.
_GATED_PREFIXES = ("/api/dashboard", "/api/feed", "/api/hl")
_OPEN_PREFIXES = ("/api/health", "/auth/", "/static", "/docs", "/openapi.json")


@app.middleware("http")
async def _require_session_for_account_data(request: Request, call_next):
    path = request.url.path
    if (path.startswith(_GATED_PREFIXES) and not path.startswith(_OPEN_PREFIXES)
            and not os.environ.get("PATHIEL_PUBLIC_DASHBOARD")):
        from services.auth.deps import current_user
        from services.auth.integration import is_local_mode
        # Local mode (Julien's own machine, loopback peer, no cross-site
        # Origin, no deployment marker) needs no session: connecting a
        # Hyperliquid account replaced the separate sign-in step.
        if not is_local_mode(request) and current_user(request) is None:
            # 401 with a machine-readable marker so the pages can tell "sign in"
            # apart from a genuine failure and show the login prompt instead of
            # an error banner.
            return JSONResponse(
                {"detail": "sign in required", "auth_required": True},
                status_code=401)
    return await call_next(request)


# Registered AFTER the session gate on purpose. Starlette wraps in registration
# order, so the last middleware registered is the outermost — and only the
# outermost one sees a response the gate short-circuited. Declared first, this
# pass never ran on a 401 and every rejected request shipped bare: no CSP, no
# frame-ancestors, on exactly the responses an attacker probing the app sees
# most. Caught by test_security_headers_are_on_errors_too.
# ── security headers ─────────────────────────────────────────────────────────
#
# None of these were set. Each one below is here for a specific reach this app
# hands an attacker, not because a scanner asked for it:
#
#   frame-ancestors  The dashboard has a STOP TRADING button and an operator
#                    token field. Framed on a hostile page, both are one
#                    transparent overlay away from being clicked by someone who
#                    thought they were dismissing a cookie banner.
#   connect-src      The pages hold a live session cookie and, once signed in,
#                    the account's whole book. Restricting them to our own
#                    origin means injected script cannot post any of it out.
#   script-src       'self' blocks a remote script tag outright. 'unsafe-inline'
#                    is present because every page carries its own inline
#                    <script>; it weakens this rule and not the others, and
#                    removing it means moving five page scripts to files, which
#                    is worth doing and is not this commit.
#   HSTS             Fly already sets force_https, which redirects. HSTS stops
#                    the first plaintext request from happening at all.
#
# Applied to every response, including errors: a 500 page is still a page.
_CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data:",
    "connect-src 'self'",
    "frame-ancestors 'none'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
])


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("Content-Security-Policy", _CSP)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    # No page here needs a camera, a microphone, or a location.
    response.headers.setdefault(
        "Permissions-Policy", "geolocation=(), microphone=(), camera=(), payment=()")
    # Only over TLS. Sending HSTS on a plaintext dev server would pin localhost
    # to https in the developer's browser and is a genuinely annoying thing to
    # undo.
    if request.url.scheme == "https":
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response



from services.auth.api import router as _auth_router            # noqa: E402
app.include_router(_auth_router)

from services.strategies.api import router as _strategies_router  # noqa: E402
app.include_router(_strategies_router)
# Accounts: connect a Hyperliquid wallet (approve an agent key), import an API
# key, or watch an address. Gates itself (local mode, else operator only), so
# it is deliberately NOT under _GATED_PREFIXES: /api/session must answer an
# anonymous visitor, and a fresh deployment's first connect needs no session.
from services.accounts.api import router as _accounts_router  # noqa: E402
app.include_router(_accounts_router)
# Risk: the four-knob profile, derived limits, and a preview for any equity.
from services.risk.api import router as _risk_router  # noqa: E402
app.include_router(_risk_router)

_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
class _RevalidatedStatic(StaticFiles):
    """Static assets carry an ETag but used to carry no Cache-Control, so a
    browser applied heuristic freshness (about 10% of the file's age) and kept
    running an old desks.js for hours after a deploy, against new markup
    (seen 2026-10-18: the new "Your size" element, the old script). no-cache
    means revalidate every time: a 304 when unchanged, the new file when not."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers.setdefault("Cache-Control", "no-cache")
        return response


app.mount("/static", _RevalidatedStatic(directory=_STATIC_DIR), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _live_equity() -> float:
    """Live account equity from the shared snapshot; 0.0 if no wallet is configured.

    Honors the runtime HIP-3 flag so the dashboard reflects total tradeable
    USDC across main + HIP-3 dexes when HIP-3 is enabled. Without this the
    equity card only counts the main HL clearinghouse.

    Blocking (a cold read waits on the Hyperliquid budget): call it from a
    plain `def` handler or a worker thread, never from a coroutine.
    """
    user = resolve_user_address()
    if not user:
        return 0.0
    from pathiel.client.account_snapshot import account_state
    return float(account_state(user, _hip3_on()).get("equity", 0))


def _hip3_on() -> bool:
    """Whether HIP-3 (tokenized-equity / commodity perps) is currently enabled.

    The autonomous trading loop reads this at startup; the operator-facing
    endpoints in this module need to honor the same flag so the dashboard
    shows live HIP-3 prices, market lists, and portfolios when the bot is
    actively trading them.
    """
    try:
        return bool(read_agent_config().get("enable_hip3", False))
    except Exception as exc:                       # noqa: BLE001
        # False silently narrows the universe to main-dex only, so HIP-3
        # positions vanish from every view that consults this.
        logger.warning(f"[server] cannot read enable_hip3 "
                       f"({type(exc).__name__}: {exc}) — assuming HIP-3 off")
        return False


# ── Agent endpoints ───────────────────────────────────────────────────────────
#
# No handler here blocks the event loop. uvicorn serves every request for every
# page on one loop, so a Hyperliquid read, a model call or an order placed
# inside an `async def` froze the whole site for as long as it took
# (2026-10-19; dashboard.py's _run_off_loop docstring has the first case). A
# handler that does that work is a plain `def`, which FastAPI runs on its
# thread pool; one that must await its request body reads it on the loop and
# hands the rest to asyncio.to_thread. Enforced by
# tests/test_no_blocking_on_the_event_loop.py.


@app.get("/api/agent/state", dependencies=[Depends(_require_operator)])
def get_agent_state():
    """GET /api/agent/state — full state snapshot for the UI."""
    memory.load()
    state = memory.get_full_state()
    config = read_agent_config()
    live_equity = _live_equity()

    if live_equity > 0:
        memory.update_equity(live_equity)

    state["equity"] = live_equity if live_equity > 0 else state.get("equity", 0)
    state["liveEquity"] = live_equity
    state["config"] = config
    return JSONResponse(content=state)


@app.post("/api/agent/scan", dependencies=[Depends(_require_operator)])
async def run_scan(request: Request):
    """POST /api/agent/scan — sweep markets for trigger signals."""
    global _last_scan_at
    body = await _json_object(request, empty_ok=True)
    min_score = body.get("minScore", 20)

    # Check and claim the window with no await in between, which on the one
    # event loop is atomic: two clicks cannot both start a scan (dozens of
    # Hyperliquid reads) now that the scan itself runs on a worker thread.
    elapsed = time.time() - _last_scan_at
    if elapsed < _SCAN_MIN_SECONDS and _last_scan_at > 0:
        remaining = max(1, int(_SCAN_MIN_SECONDS - elapsed))
        raise HTTPException(
            429,
            detail=f"Rate limited. Try again in {remaining}s",
        )
    claimed_from, _last_scan_at = _last_scan_at, time.time()
    try:
        universe = await asyncio.to_thread(lambda: get_universe(include_hip3=_hip3_on()))
    except Exception:
        _last_scan_at = claimed_from        # a failed universe read never used the window
        raise

    perceptions = await asyncio.to_thread(scan_once, universe=universe, min_score=min_score)

    result = {"perceptions": perceptions, "count": len(perceptions)}
    await _append_session_log({"event": "scan", "perceptions": len(perceptions)})
    return JSONResponse(content=result)


@app.post("/api/agent/research/{coin}", dependencies=[Depends(_require_operator)])
async def run_research(coin: str, request: Request):
    """POST /api/agent/research/{coin} — full AI analysis for one coin."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}

    # A model call, tens of seconds: on a worker thread.
    analysis = await asyncio.to_thread(_research, coin, body)
    await _append_session_log({"event": "research", "coin": coin, "verdict": analysis.get("verdict")})
    return JSONResponse(content=analysis)


def _research(coin: str, body: Dict[str, Any]) -> Dict[str, Any]:
    memory.load()

    # Build a minimal perception from memory or request
    perception: Dict[str, Any] = {"coin": coin, "type": "perp", "mid": 0, "composite_score": 0}

    if body.get("perception"):
        perception.update(body["perception"])
        if "coin" not in perception:
            perception["coin"] = coin
    elif body.get("perceptionId"):
        # Look up from recent perceptions
        for p in memory.get_recent_perceptions(200):
            if p.get("id") == body["perceptionId"] and p.get("coin") == coin:
                perception = p
                break

    return research(coin=coin, perception=perception)


@app.post("/api/agent/execute", dependencies=[Depends(_require_operator)])
async def run_execute(request: Request):
    """POST /api/agent/execute — run risk gates and execute an analysis."""
    body = await _json_object(request)

    analysis_id = body.get("analysisId")
    if not analysis_id:
        raise HTTPException(400, "analysisId required")

    # Risk gates and, when they pass, the orders: on a worker thread.
    result = await asyncio.to_thread(_execute, analysis_id)
    await _append_session_log({
        "event": "execute",
        "analysisId": analysis_id,
        "executed": result.get("executed"),
    })
    return JSONResponse(content=result)


def _execute(analysis_id: str) -> Dict[str, Any]:
    memory.load()
    analysis = memory.get_analysis_by_id(analysis_id)
    if not analysis:
        raise HTTPException(404, f"analysis {analysis_id} not found")
    return maybe_execute(analysis)


@app.get("/api/agent/trades", dependencies=[Depends(_require_operator)])
def get_trades():
    """GET /api/agent/trades — all recorded trades."""
    memory.load()
    return JSONResponse(content=memory.get_all_trades())


@app.get("/api/agent/session-log", dependencies=[Depends(_require_operator)])
def get_session_log():
    """GET /api/agent/session-log — last 50 log entries."""
    return JSONResponse(content=session_log.tail(50))


@app.get("/api/agent/start", dependencies=[Depends(_require_operator)])
def agent_start():
    """GET /api/agent/start — report whether the scanner process is running."""
    if not os.path.exists(PID_FILE):
        return JSONResponse(content={"running": False, "cycle": 0, "lastUpdate": None})

    pid = int(open(PID_FILE).read().strip())
    running = _is_alive(pid)
    return JSONResponse(content={"running": running, "pid": pid if running else None})


@app.post("/api/agent/start", dependencies=[Depends(_require_operator)])
def agent_start_post():
    """POST /api/agent/start — report scanner status.

    The Python agent runs as its own process; this endpoint does not spawn it.
    """
    if os.path.exists(PID_FILE):
        pid = int(open(PID_FILE).read().strip())
        if _is_alive(pid):
            return JSONResponse(content={"status": "already_running", "pid": pid})
        # Stale pid file, clean up
        try:
            os.remove(PID_FILE)
        except OSError:
            pass

    return JSONResponse(content={"status": "stub", "message": "Python agent runs independently"})


@app.post("/api/agent/stop", dependencies=[Depends(_require_operator)])
def agent_stop():
    """POST /api/agent/stop — terminate the scanner process."""
    if not os.path.exists(PID_FILE):
        return JSONResponse(content={"status": "not_running"})

    pid = int(open(PID_FILE).read().strip())
    if _is_alive(pid):
        try:
            os.kill(pid, 15)  # SIGTERM
        except OSError:
            pass

    try:
        os.remove(PID_FILE)
    except OSError:
        pass

    return JSONResponse(content={"status": "stopped", "pid": pid})


@app.get("/api/agent/config", dependencies=[Depends(_require_operator)])
def get_config():
    """GET /api/agent/config — read the agent config."""
    return JSONResponse(content=read_agent_config())


@app.post("/api/agent/config", dependencies=[Depends(_require_operator)])
async def update_config(request: Request):
    """POST /api/agent/config — merge new values into the agent config."""
    body = await _json_object(request)
    merged = await asyncio.to_thread(_merge_config, body)
    return JSONResponse(content={"ok": True, "config": merged})


_CONFIG_LOCK = threading.Lock()


def _merge_config(body: Dict[str, Any]) -> Dict[str, Any]:
    # Read, merge and write as one step. On the event loop two saves could not
    # interleave; on worker threads two landing together would each merge into
    # the same old config and the second write would drop the first's change.
    # update_agent_config merges into the SAVED file (not the defaults-merged
    # view), so a save never pins every built-in default into the file.
    with _CONFIG_LOCK:
        return update_agent_config(body)


# ── HL endpoints ──────────────────────────────────────────────────────────────


@app.get("/api/hl/account", dependencies=[Depends(_require_operator)])
def get_account():
    """GET /api/hl/account — perp + spot account state."""
    user = resolve_user_address()
    if not user:
        raise HTTPException(400, "HL wallet not configured")

    try:
        state = fetch_account_state(user, include_hip3=_hip3_on())
        # jsonable_encoder: the state carries sets (queried_dexes), which
        # json.dumps refuses. Until 2026-10-19 every call answered 500
        # "Object of type set is not JSON serializable".
        return JSONResponse(content=jsonable_encoder(state))
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/hl/all-mids")
def get_all_mids():
    """GET /api/hl/all-mids — all mid prices (incl. HIP-3 when enabled)."""
    try:
        mids = fetch_all_mids(include_hip3=_hip3_on())
        return JSONResponse(content=mids)
    except Exception as e:
        raise HTTPException(502, str(e))


@app.get("/api/hl/universe")
def get_market_universe():
    """GET /api/hl/universe — full market universe (incl. HIP-3 when enabled)."""
    try:
        universe = get_universe(include_hip3=_hip3_on())
        return JSONResponse(content={"markets": universe, "count": len(universe)})
    except Exception as e:
        raise HTTPException(502, str(e))


@app.get("/api/hl/price")
def get_price(coin: str = Query("BTC")):
    """GET /api/hl/price — mid price for a coin.

    Always includes HIP-3 dexes in the mid lookup so a request for
    `xyz:NVDA` etc. resolves even if the bot's `enable_hip3` flag isn't set
    (the operator might want to view a HIP-3 price without enabling the
    autonomous bot to trade it).
    """
    try:
        mids = fetch_all_mids(include_hip3=True)
        price = float(mids.get(coin, "0"))
        return JSONResponse(content={"price": price})
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/hl/candles")
def get_candles(
    coin: str = Query("BTC"),
    interval: str = Query("5m"),
    count: int = Query(100),
):
    """GET /api/hl/candles — OHLCV candles."""
    try:
        candles = fetch_hl_candles(coin, interval, count)
        return JSONResponse(content={"candles": [c.model_dump() for c in candles]})
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/hl/portfolio", dependencies=[Depends(_require_operator)])
def get_portfolio():
    """GET /api/hl/portfolio — positions and equity."""
    user = resolve_user_address()
    if not user:
        raise HTTPException(400, "HL wallet not configured")

    try:
        # Always aggregate HIP-3 dexes here — the portfolio view's job is to
        # show every position the wallet holds, and xyz/vntl/km positions live
        # on separate clearinghouses that the default fetch skips.
        state = fetch_account_state(user, include_hip3=True)
        # Always include HIP-3 mids so the portfolio view can show mark prices
        # for any open xyz:/km:/hyna: positions; without this the mark column
        # would render $0.00 for tokenized markets even when the position is
        # real and trackable.
        mids = fetch_all_mids(include_hip3=True)

        positions = []
        for p in (state.get("asset_positions") or []):
            pos = p.get("position", {})
            szi = float(pos.get("szi", "0"))
            if szi == 0:
                continue
            entry_px = float(pos.get("entryPx", "0"))
            coin = pos.get("coin", "")
            positions.append({
                "coin": coin,
                "side": "long" if szi > 0 else "short",
                "szi": abs(szi),
                "entryPx": entry_px,
                "unrealizedPnl": float(pos.get("unrealizedPnl", "0")),
                "notional": abs(szi) * entry_px,
                "markPx": float(mids.get(coin, "0")),
            })

        equity = float(state.get("equity", 0))

        return JSONResponse(content={
            "equity": equity,
            "totalNotional": float(state.get("total_ntl", 0)),
            "positions": positions,
            "spotBalances": state.get("spot_balances", []),
        })
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/hl/orderbook")
def get_orderbook(coin: str = Query("BTC")):
    """GET /api/hl/orderbook — top-of-book L2 levels."""
    try:
        from pathiel.client.hl_client import _http_post
        raw = _http_post("/info", {"type": "l2Book", "coin": coin}) or {}
        levels = raw.get("levels", [[], []])
        bids_raw = levels[0][:8] if len(levels) > 0 else []
        asks_raw = levels[1][:8] if len(levels) > 1 else []
        bids = [{"px": float(b["px"]), "sz": float(b["sz"])} for b in bids_raw]
        asks = [{"px": float(a["px"]), "sz": float(a["sz"])} for a in asks_raw]
        return JSONResponse(content={"bids": bids, "asks": asks})
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/hl/place-order", dependencies=[Depends(_require_operator)])
async def place_order(request: Request):
    """POST /api/hl/place-order — manual order with ATR-based SL/TP brackets."""
    body = await _json_object(request)
    # Price, leverage, ATR, equity, the order and its two brackets: several
    # exchange round trips, on a worker thread.
    return JSONResponse(content=await asyncio.to_thread(_place_order, body))


def _place_order(body: Dict[str, Any]) -> Dict[str, Any]:
    side = body.get("side", "long")
    coin = (body.get("coin") or "BTC").upper()
    leverage = body.get("leverage", 5)
    is_buy = side.lower() in ("long", "buy")

    try:
        from pathiel.client.exchange import (
            entry_size_for_notional,
            get_hl_atr,
            get_hl_price,
            min_entry_notional_usd,
            place_hl_order,
            place_hl_trigger_order,
            set_leverage,
        )

        mid_price = get_hl_price(coin)
        if mid_price <= 0:
            raise HTTPException(400, f"invalid price for {coin}")

        set_leverage(coin, leverage)
        atr = get_hl_atr("4h", 14, coin)

        # Sizing: use riskUSD if provided, else riskPct of live equity.
        risk_usd = body.get("riskUSD")
        if risk_usd is None:
            risk_pct = body.get("riskPct", 0.01)
            equity = _live_equity()
            risk_usd = max(2, equity * risk_pct)

        cfg = read_agent_config()
        position_notional = risk_usd * leverage
        min_notional = min_entry_notional_usd(coin, mid_price)
        if min_notional > 0 and position_notional < min_notional:
            raise HTTPException(
                400,
                f"order notional ${position_notional:.2f} is below HL minimum ${min_notional:.2f}",
            )
        size_in_coin = entry_size_for_notional(coin, position_notional, mid_price)

        result = place_hl_order(is_buy, size_in_coin, mid_price, coin)

        if not result.get("ok"):
            raise HTTPException(400, f"order failed: {result.get('error')}")

        try:
            fill_px = float(result.get("avg_px") or 0.0)
        except (TypeError, ValueError):
            fill_px = 0.0
        try:
            fill_sz = float(result.get("total_sz") or 0.0)
        except (TypeError, ValueError):
            fill_sz = 0.0
        entry_px = fill_px if fill_px > 0 else mid_price
        if fill_sz > 0:
            size_in_coin = fill_sz

        brackets = []
        if atr > 0 and size_in_coin > 0:
            sl_mult = float(cfg.get("sl_atr_mult", 1.5) or 1.5)
            tp_mult = float(cfg.get("tp_atr_mult", 1.0) or 1.0)
            sl_px = entry_px - atr * sl_mult if is_buy else entry_px + atr * sl_mult
            tp_px = entry_px + atr * tp_mult if is_buy else entry_px - atr * tp_mult

            sl = place_hl_trigger_order(is_buy, size_in_coin, sl_px, "sl", coin)
            tp = place_hl_trigger_order(is_buy, size_in_coin, tp_px, "tp", coin)
            brackets = [
                {"type": "SL", "price": sl_px, "ok": sl.get("ok")},
                {"type": "TP", "price": tp_px, "ok": tp.get("ok")},
            ]

        session_log.append({
            "event": "place_order",
            "coin": coin,
            "side": side,
            "ok": result.get("ok"),
        })

        return {
            **result,
            "coin": coin,
            "side": side,
            "size": size_in_coin,
            "midPrice": mid_price,
            "entryPrice": entry_px,
            "brackets": brackets,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/hl/close-position", dependencies=[Depends(_require_operator)])
async def close_position(request: Request):
    """POST /api/hl/close-position — close an open position for a coin."""
    body = await _json_object(request)
    # A fresh account read, a price and the closing order: on a worker thread.
    return JSONResponse(content=await asyncio.to_thread(_close_position, body))


def _close_position(body: Dict[str, Any]) -> Dict[str, Any]:
    coin = (body.get("coin") or "BTC").upper()
    user = resolve_user_address()

    try:
        from pathiel.client.exchange import get_hl_price, place_hl_order

        # include_hip3=True so a manual close request for xyz:MU/vntl:* can
        # locate the position on the right dex; main-only would 404 every
        # HIP-3 close.
        state = fetch_account_state(user, include_hip3=True)
        pos = None
        for p in (state.get("asset_positions") or []):
            p_coin = p.get("position", {}).get("coin", "")
            if p_coin == coin:
                pos = p
                break

        if not pos:
            raise HTTPException(400, f"no open position for {coin}")

        szi = float(pos.get("position", {}).get("szi", "0"))
        if szi == 0:
            raise HTTPException(400, f"no open position for {coin}")

        is_long = szi > 0
        mid_price = get_hl_price(coin)
        if mid_price <= 0:
            raise HTTPException(400, f"invalid price for {coin}")

        # Close: trade in the opposite direction.
        result = place_hl_order(
            is_buy=not is_long,
            size=abs(szi),
            mid_price=mid_price,
            coin=coin,
        )

        session_log.append({
            "event": "close_position",
            "coin": coin,
            "ok": result.get("ok"),
        })

        return {**result, "coin": coin, "side": "long" if is_long else "short"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/hl/cancel-order", dependencies=[Depends(_require_operator)])
async def cancel_order(request: Request):
    """POST /api/hl/cancel-order — cancel an order by OID."""
    body = await _json_object(request)

    oid = body.get("oid")
    coin = body.get("coin")
    if not oid:
        raise HTTPException(400, "oid required")

    try:
        from pathiel.client.exchange import cancel_orders
        result = await asyncio.to_thread(cancel_orders, oid, coin=coin)
        return JSONResponse(content=result)
    except Exception as e:
        raise HTTPException(500, str(e))


# ── Root ──────────────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    """LIVENESS of this web process only. Always 200 if we can serve a request.

    Deliberately NOT system health. fly.toml and k8s/statefulset.yaml both point
    a livenessProbe here, and a liveness probe that fails because the TRADING
    LOOP died would restart the web container — which fixes nothing and loses
    the dashboard that is the only way to see what happened.

    System health lives at /api/health/system and returns 503. Use that for
    alerting and readiness, never for liveness.

    The one `async def` here with no await: it does no I/O, so it answers even
    when every worker thread is busy.
    """
    return {"service": "Pathiel-Trader", "version": __version__,
            "status": "running", "system_health": "/api/health/system"}


@app.get("/api/health/system")
def health_system(response: Response):
    """Is the SYSTEM working, not just this process. 503 when it is not.

    This exists because /api/health answered "running" unconditionally, so a
    dead trading loop behind a live web process read as perfectly healthy to
    every probe pointed at it — the same silent-failure shape as a data outage
    reading as a quiet market, and as an unrotated log filling a disk.

    Unauthenticated, like /api/health and /metrics, so an external monitor can
    reach it. It exposes no position, size, or credential — only whether the
    parts are alive.
    """
    from pathiel import dashboard as _db

    checks: Dict[str, Any] = {}
    summary = _db._summary_payload()

    # 1. Is the loop alive? The heartbeat is written every cycle; p99 is ~420s
    #    on healthy days, so 900s is a genuinely dead loop, not a slow one.
    age = summary.get("last_tick_age_s")
    loop_ok = age is not None and age <= 900
    checks["loop"] = {"ok": bool(loop_ok), "last_tick_age_s": age,
                      "detail": ("no heartbeat yet" if age is None
                                 else f"last heartbeat {age}s ago")}

    # 2. Is the market data trustworthy? A degraded feed reads downstream as a
    #    quiet market, so it has to surface as a health problem, not silence.
    feed = _db._feed_health()
    feed_ok = feed.get("trustworthy") is not False
    checks["feed"] = {"ok": bool(feed_ok), **feed}

    # 3. Is there room to keep logging? An unrotated disk took this system down
    #    before anything else noticed.
    try:
        from pathiel import log_setup
        g = log_setup.check_disk_guard()
        checks["disk"] = {
            "ok": not g.critical,
            "free_bytes": g.free_bytes,
            "log_dir_bytes": g.log_dir_bytes,
            "warn": bool(g.warn),
            "detail": ("free space below the critical floor" if g.critical
                       else "log directory over its cap" if g.warn
                       else "ok"),
        }
    except Exception as exc:
        # An unavailable disk check must not itself fail the healthcheck, or a
        # monitoring gap becomes an outage.
        checks["disk"] = {"ok": True, "detail": f"unavailable: {str(exc)[:80]}"}

    # 4. Can the AI brain actually run? Every failure here is silent: a missing
    #    CLI binary returns an empty completion, which fails to parse, which has
    #    historically defaulted to PASS. A dead brain looks exactly like a brain
    #    that looked and declined.
    try:
        from pathiel.agents.ai_brain import provider_readiness
        pr = provider_readiness()
        checks["ai_brain"] = {
            "ok": bool(pr.get("ready")),
            "provider": pr.get("provider"),
            "deployable": pr.get("deployable"),
            "detail": pr.get("reason") or "usable",
        }
    except Exception as exc:
        checks["ai_brain"] = {"ok": True, "detail": f"unavailable: {str(exc)[:80]}"}

    ok = all(c.get("ok") for c in checks.values())
    if not ok:
        response.status_code = 503
    # Not a check: where this process's Hyperliquid budget went (weight
    # granted and requests refused, per request type and priority) — the
    # trace for "the site is slow" (2026-10-19). Counts only, no account data.
    from pathiel.client.rate_limit import HL_LIMITER
    return {"ok": ok, "checks": checks,
            "failing": sorted(k for k, v in checks.items() if not v.get("ok")),
            "hl_budget": HL_LIMITER.stats()}


@app.get("/metrics")
def metrics():
    """Prometheus scrape target. Unauthenticated (like /api/health) so the
    scraper needs no operator token; reads local state only — never hits HL."""
    body, content_type = render_metrics()
    return Response(content=body, media_type=content_type)


# Dashboard, SSE feed, and operator console all live in pathiel.dashboard.
# Mounting after the JSON API routes so the dashboard's "/" cannot override them.
dashboard.register_routes(app)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # .env.local is already loaded by _load_env_local_early() at the top of
    # this file — done before pathiel imports so module-level env reads
    # (notably PRIVATE_KEY_HEX in client/exchange.py) capture real values.
    import uvicorn
    port = int(os.environ.get("PATHIEL_PORT", 8000))
    logger.info(f"Starting Pathiel server on port {port}")
    host = os.environ.get("PATHIEL_BIND", "127.0.0.1")   # audit 2026-07-10: was 0.0.0.0
    uvicorn.run("pathiel.server:app", host=host, port=port, reload=False,
                access_log=False)
