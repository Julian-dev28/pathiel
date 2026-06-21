"""Hermes-Trader — FastAPI server exposing the trading agent and Hyperliquid endpoints."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any, Dict


def _load_env_local_early() -> None:
    """Pull `.env.local` into os.environ BEFORE any hermes_trader imports.

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
from fastapi.middleware.cors import CORSMiddleware                   # noqa: E402
from fastapi.responses import JSONResponse                            # noqa: E402

from hermes_trader.metrics import render_metrics                      # noqa: E402

from hermes_trader import __version__, dashboard, session_log         # noqa: E402
from hermes_trader.dashboard import _require_operator                 # noqa: E402
from hermes_trader.agents.config_store import (                       # noqa: E402
    merge_agent_config,
    read_agent_config,
    write_agent_config,
)
from hermes_trader.agents.executor import maybe_execute               # noqa: E402
from hermes_trader.agents.memory import memory                        # noqa: E402
from hermes_trader.agents.perception import scan_once                 # noqa: E402
from hermes_trader.agents.research import research                    # noqa: E402
from hermes_trader.client.hl_client import (                          # noqa: E402
    fetch_account_state,
    fetch_all_mids,
    fetch_hl_candles,
    resolve_user_address,
)
from hermes_trader.client.universe import get_universe                # noqa: E402

# ── Logging ────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
logger = logging.getLogger("hermes-server")

# ── Session log ────────────────────────────────────────────────────────────────
# Shared activity feed (hermes_trader.session_log) — the same JSONL file the
# trading loop and status.py use. Writes run in an executor so the file append
# never blocks the event loop.


async def _append_session_log(entry: Dict[str, Any]) -> None:
    """Append one event to the shared session log (non-blocking)."""
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, session_log.append, entry)


# ── PID file helpers (start/stop) ──────────────────────────────────────────────

PID_FILE = os.path.expanduser("~/.hermes-trader.pid")


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
    """Load persisted memory on startup, flush it on shutdown."""
    memory.load()
    logger.info("Hermes server started — memory loaded")
    yield
    memory.flush()
    logger.info("Hermes server stopped — memory flushed")


# ── App ────────────────────────────────────────────────────────────────────────

app = FastAPI(title="Hermes-Trader", version=__version__, lifespan=lifespan)

# Wildcard origins + credentials=True is invalid per the CORS spec and would be
# silently rejected by browsers. Token auth happens via X-Operator-Token /
# ?token=, neither of which is a credential the browser auto-sends, so we don't
# need credentialed CORS. Keep wildcard origins for tool/curl access; flip
# credentials off so a future cookie-auth flow can't be abused cross-origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Internal helpers ──────────────────────────────────────────────────────────

async def _fetch_live_equity() -> float:
    """Fetch live account equity from HL; returns 0.0 if no wallet is configured.

    Honors the runtime HIP-3 flag so the dashboard reflects total tradeable
    USDC across main + HIP-3 dexes when HIP-3 is enabled. Without this the
    equity card only counts the main HL clearinghouse.
    """
    user = resolve_user_address()
    if not user:
        return 0.0
    state = fetch_account_state(user, include_hip3=_hip3_on())
    return float(state.get("equity", 0))


def _hip3_on() -> bool:
    """Whether HIP-3 (tokenized-equity / commodity perps) is currently enabled.

    The autonomous trading loop reads this at startup; the operator-facing
    endpoints in this module need to honor the same flag so the dashboard
    shows live HIP-3 prices, market lists, and portfolios when the bot is
    actively trading them.
    """
    try:
        return bool(read_agent_config().get("enable_hip3", False))
    except Exception:
        return False


# ── Agent endpoints ───────────────────────────────────────────────────────────


@app.get("/api/agent/state", dependencies=[Depends(_require_operator)])
async def get_agent_state():
    """GET /api/agent/state — full state snapshot for the UI."""
    memory.load()
    state = memory.get_full_state()
    config = read_agent_config()
    live_equity = await _fetch_live_equity()

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

    elapsed = time.time() - _last_scan_at
    if elapsed < _SCAN_MIN_SECONDS and _last_scan_at > 0:
        remaining = max(1, int(_SCAN_MIN_SECONDS - elapsed))
        raise HTTPException(
            429,
            detail=f"Rate limited. Try again in {remaining}s",
        )

    raw_body = await request.body()
    if raw_body:
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "invalid JSON")
    else:
        body = {}
    if not isinstance(body, dict):
        raise HTTPException(400, "JSON body must be an object")
    min_score = body.get("minScore", 20)

    universe = get_universe(include_hip3=_hip3_on())
    _last_scan_at = time.time()

    perceptions = scan_once(universe=universe, min_score=min_score)

    result = {"perceptions": perceptions, "count": len(perceptions)}
    await _append_session_log({"event": "scan", "perceptions": len(perceptions)})
    return JSONResponse(content=result)


@app.post("/api/agent/research/{coin}", dependencies=[Depends(_require_operator)])
async def run_research(coin: str, request: Request):
    """POST /api/agent/research/{coin} — full AI analysis for one coin."""
    memory.load()

    # Build a minimal perception from memory or request
    perception: Dict[str, Any] = {"coin": coin, "type": "perp", "mid": 0, "composite_score": 0}

    if request:
        try:
            body = await request.json()
        except Exception:
            body = {}

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

    analysis = research(coin=coin, perception=perception)
    await _append_session_log({"event": "research", "coin": coin, "verdict": analysis.get("verdict")})
    return JSONResponse(content=analysis)


@app.post("/api/agent/execute", dependencies=[Depends(_require_operator)])
async def run_execute(request: Request):
    """POST /api/agent/execute — run risk gates and execute an analysis."""
    memory.load()

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON")

    analysis_id = body.get("analysisId")
    if not analysis_id:
        raise HTTPException(400, "analysisId required")

    analysis = memory.get_analysis_by_id(analysis_id)
    if not analysis:
        raise HTTPException(404, f"analysis {analysis_id} not found")

    result = maybe_execute(analysis)
    await _append_session_log({
        "event": "execute",
        "analysisId": analysis_id,
        "executed": result.get("executed"),
    })
    return JSONResponse(content=result)


@app.get("/api/agent/trades", dependencies=[Depends(_require_operator)])
async def get_trades():
    """GET /api/agent/trades — all recorded trades."""
    memory.load()
    return JSONResponse(content=memory.get_all_trades())


@app.get("/api/agent/session-log", dependencies=[Depends(_require_operator)])
async def get_session_log():
    """GET /api/agent/session-log — last 50 log entries."""
    return JSONResponse(content=session_log.tail(50))


@app.get("/api/agent/start", dependencies=[Depends(_require_operator)])
async def agent_start():
    """GET /api/agent/start — report whether the scanner process is running."""
    if not os.path.exists(PID_FILE):
        return JSONResponse(content={"running": False, "cycle": 0, "lastUpdate": None})

    pid = int(open(PID_FILE).read().strip())
    running = _is_alive(pid)
    return JSONResponse(content={"running": running, "pid": pid if running else None})


@app.post("/api/agent/start", dependencies=[Depends(_require_operator)])
async def agent_start_post():
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
async def agent_stop():
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
async def get_config():
    """GET /api/agent/config — read the agent config."""
    return JSONResponse(content=read_agent_config())


@app.post("/api/agent/config", dependencies=[Depends(_require_operator)])
async def update_config(request: Request):
    """POST /api/agent/config — merge new values into the agent config."""
    existing = read_agent_config()
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON")

    if not isinstance(body, dict):
        raise HTTPException(400, "JSON body must be an object")

    merged = merge_agent_config(existing, body)
    write_agent_config(merged)
    return JSONResponse(content={"ok": True, "config": merged})


# ── HL endpoints ──────────────────────────────────────────────────────────────


@app.get("/api/hl/account", dependencies=[Depends(_require_operator)])
async def get_account():
    """GET /api/hl/account — perp + spot account state."""
    user = resolve_user_address()
    if not user:
        raise HTTPException(400, "HL wallet not configured")

    try:
        state = fetch_account_state(user, include_hip3=_hip3_on())
        return JSONResponse(content=state)
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/hl/all-mids")
async def get_all_mids():
    """GET /api/hl/all-mids — all mid prices (incl. HIP-3 when enabled)."""
    try:
        mids = fetch_all_mids(include_hip3=_hip3_on())
        return JSONResponse(content=mids)
    except Exception as e:
        raise HTTPException(502, str(e))


@app.get("/api/hl/universe")
async def get_market_universe():
    """GET /api/hl/universe — full market universe (incl. HIP-3 when enabled)."""
    try:
        universe = get_universe(include_hip3=_hip3_on())
        return JSONResponse(content={"markets": universe, "count": len(universe)})
    except Exception as e:
        raise HTTPException(502, str(e))


@app.get("/api/hl/price")
async def get_price(coin: str = Query("BTC")):
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
async def get_candles(
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
async def get_portfolio():
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
async def get_orderbook(coin: str = Query("BTC")):
    """GET /api/hl/orderbook — top-of-book L2 levels."""
    try:
        from hermes_trader.client.hl_client import _http_post
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
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON")

    side = body.get("side", "long")
    coin = (body.get("coin") or "BTC").upper()
    leverage = body.get("leverage", 5)
    is_buy = side.lower() in ("long", "buy")

    try:
        from hermes_trader.client.exchange import (
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
            equity = await _fetch_live_equity()
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

        await _append_session_log({
            "event": "place_order",
            "coin": coin,
            "side": side,
            "ok": result.get("ok"),
        })

        return JSONResponse(content={
            **result,
            "coin": coin,
            "side": side,
            "size": size_in_coin,
            "midPrice": mid_price,
            "entryPrice": entry_px,
            "brackets": brackets,
        })
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/hl/close-position", dependencies=[Depends(_require_operator)])
async def close_position(request: Request):
    """POST /api/hl/close-position — close an open position for a coin."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON")

    coin = (body.get("coin") or "BTC").upper()
    user = resolve_user_address()

    try:
        from hermes_trader.client.exchange import get_hl_price, place_hl_order

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

        await _append_session_log({
            "event": "close_position",
            "coin": coin,
            "ok": result.get("ok"),
        })

        return JSONResponse(content={**result, "coin": coin, "side": "long" if is_long else "short"})
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/hl/cancel-order", dependencies=[Depends(_require_operator)])
async def cancel_order(request: Request):
    """POST /api/hl/cancel-order — cancel an order by OID."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON")

    oid = body.get("oid")
    coin = body.get("coin")
    if not oid:
        raise HTTPException(400, "oid required")

    try:
        from hermes_trader.client.exchange import cancel_orders
        result = cancel_orders(oid, coin=coin)
        return JSONResponse(content=result)
    except Exception as e:
        raise HTTPException(500, str(e))


# ── Root ──────────────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"service": "Hermes-Trader", "version": __version__, "status": "running"}


@app.get("/metrics")
async def metrics():
    """Prometheus scrape target. Unauthenticated (like /api/health) so the
    scraper needs no operator token; reads local state only — never hits HL."""
    body, content_type = render_metrics()
    return Response(content=body, media_type=content_type)


# Dashboard, SSE feed, and operator console all live in hermes_trader.dashboard.
# Mounting after the JSON API routes so the dashboard's "/" doesn't shadow them.
dashboard.register_routes(app)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # .env.local is already loaded by _load_env_local_early() at the top of
    # this file — done before hermes_trader imports so module-level env reads
    # (notably PRIVATE_KEY_HEX in client/exchange.py) capture real values.
    import uvicorn
    port = int(os.environ.get("HERMES_PORT", 8000))
    logger.info(f"Starting Hermes server on port {port}")
    uvicorn.run("hermes_trader.server:app", host="0.0.0.0", port=port, reload=False)
