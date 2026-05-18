"""Hermes Agent — FastAPI server exposing the agent and Hyperliquid endpoints."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any, Dict

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from hermes_agent import __version__
from hermes_agent.agents.config_store import read_agent_config, write_agent_config
from hermes_agent.agents.executor import maybe_execute
from hermes_agent.agents.memory import memory
from hermes_agent.agents.perception import scan_once
from hermes_agent.agents.research import research
from hermes_agent.client.hl_client import (
    fetch_account_state,
    fetch_all_mids,
    fetch_hl_candles,
    resolve_user_address,
)
from hermes_agent.client.universe import get_universe

# ── Logging ────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
logger = logging.getLogger("hermes-server")

# ── Session log ────────────────────────────────────────────────────────────────

SESSION_LOG_FILE = os.environ.get(
    "SESSION_LOG_PATH",
    os.path.expanduser("~/.hermes-trader-session-log.jsonl"),
)
_session_log_lock = asyncio.Lock()


async def _append_session_log(entry: Dict[str, Any]) -> None:
    """Append one JSONL line to the session log."""
    try:
        async with _session_log_lock:
            loop = asyncio.get_running_loop()
            fut = loop.run_in_executor(None, _sync_append, SESSION_LOG_FILE, entry)
            await fut
    except Exception as e:
        logger.warning(f"Failed to write session log: {e}")


def _sync_append(path: str, entry: Dict[str, Any]) -> None:
    with open(path, "a") as f:
        f.write(json.dumps(entry) + "\n")


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

app = FastAPI(title="Hermes Agent", version=__version__, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Internal helpers ──────────────────────────────────────────────────────────

async def _fetch_live_equity() -> float:
    """Fetch live account equity from HL; returns 0.0 if no wallet is configured."""
    user = resolve_user_address()
    if not user:
        return 0.0
    state = fetch_account_state(user)
    return float(state.get("equity", 0))


# ── Agent endpoints ───────────────────────────────────────────────────────────


@app.get("/api/agent/state")
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


@app.post("/api/agent/scan")
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

    body = await request.json() if await request.body() else {}
    min_score = body.get("minScore", 20)

    universe = get_universe()
    _last_scan_at = time.time()

    perceptions = scan_once(universe=universe, min_score=min_score)

    result = {"perceptions": perceptions, "count": len(perceptions)}
    await _append_session_log({"event": "scan", "perceptions": len(perceptions)})
    return JSONResponse(content=result)


@app.post("/api/agent/research/{coin}")
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


@app.post("/api/agent/execute")
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


@app.get("/api/agent/trades")
async def get_trades():
    """GET /api/agent/trades — all recorded trades."""
    memory.load()
    return JSONResponse(content=memory.get_all_trades())


@app.get("/api/agent/session-log")
async def get_session_log():
    """GET /api/agent/session-log — last 50 log entries."""
    try:
        with open(SESSION_LOG_FILE, "r") as f:
            lines = f.readlines()
        last_50 = lines[-50:]
        entries = []
        for line in last_50:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
        return JSONResponse(content=entries)
    except FileNotFoundError:
        return JSONResponse(content=[])


@app.get("/api/agent/start")
async def agent_start():
    """GET /api/agent/start — report whether the scanner process is running."""
    if not os.path.exists(PID_FILE):
        return JSONResponse(content={"running": False, "cycle": 0, "lastUpdate": None})

    pid = int(open(PID_FILE).read().strip())
    running = _is_alive(pid)
    return JSONResponse(content={"running": running, "pid": pid if running else None})


@app.post("/api/agent/start")
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


@app.post("/api/agent/stop")
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


@app.get("/api/agent/config")
async def get_config():
    """GET /api/agent/config — read the agent config."""
    return JSONResponse(content=read_agent_config())


@app.post("/api/agent/config")
async def update_config(request: Request):
    """POST /api/agent/config — merge new values into the agent config."""
    existing = read_agent_config()
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON")

    merged = {**existing, **body}
    write_agent_config(merged)
    return JSONResponse(content={"ok": True, "config": merged})


# ── HL endpoints ──────────────────────────────────────────────────────────────


@app.get("/api/hl/account")
async def get_account():
    """GET /api/hl/account — perp + spot account state."""
    user = resolve_user_address()
    if not user:
        raise HTTPException(400, "HL wallet not configured")

    try:
        state = fetch_account_state(user)
        return JSONResponse(content=state)
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/hl/all-mids")
async def get_all_mids():
    """GET /api/hl/all-mids — all mid prices."""
    try:
        mids = fetch_all_mids()
        return JSONResponse(content=mids)
    except Exception as e:
        raise HTTPException(502, str(e))


@app.get("/api/hl/universe")
async def get_market_universe():
    """GET /api/hl/universe — full market universe."""
    try:
        universe = get_universe()
        return JSONResponse(content={"markets": universe, "count": len(universe)})
    except Exception as e:
        raise HTTPException(502, str(e))


@app.get("/api/hl/price")
async def get_price(coin: str = Query("BTC")):
    """GET /api/hl/price — mid price for a coin."""
    try:
        mids = fetch_all_mids()
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


@app.get("/api/hl/portfolio")
async def get_portfolio():
    """GET /api/hl/portfolio — positions and equity."""
    user = resolve_user_address()
    if not user:
        raise HTTPException(400, "HL wallet not configured")

    try:
        state = fetch_account_state(user)
        mids = fetch_all_mids()

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
        from hermes_agent.client.hl_client import _http_post
        raw = _http_post("/info", {"type": "l2Book", "coin": coin}) or {}
        levels = raw.get("levels", [[], []])
        bids_raw = levels[0][:8] if len(levels) > 0 else []
        asks_raw = levels[1][:8] if len(levels) > 1 else []
        bids = [{"px": float(b["px"]), "sz": float(b["sz"])} for b in bids_raw]
        asks = [{"px": float(a["px"]), "sz": float(a["sz"])} for a in asks_raw]
        return JSONResponse(content={"bids": bids, "asks": asks})
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/hl/place-order")
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
        from hermes_agent.client.exchange import (
            get_hl_atr,
            get_hl_price,
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

        position_notional = risk_usd * leverage
        size_in_coin = position_notional / mid_price

        result = place_hl_order(is_buy, size_in_coin, mid_price, coin)

        if not result.get("ok"):
            raise HTTPException(400, f"order failed: {result.get('error')}")

        brackets = []
        if atr > 0 and size_in_coin > 0:
            sl_px = mid_price - atr * 3.5 if is_buy else mid_price + atr * 3.5
            tp_px = mid_price + atr * 1.0 if is_buy else mid_price - atr * 1.0

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
            "brackets": brackets,
        })
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/hl/close-position")
async def close_position(request: Request):
    """POST /api/hl/close-position — close an open position for a coin."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON")

    coin = (body.get("coin") or "BTC").upper()
    user = resolve_user_address()

    try:
        from hermes_agent.client.exchange import get_hl_price, place_hl_order

        state = fetch_account_state(user)
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


@app.post("/api/hl/cancel-order")
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
        from hermes_agent.client.exchange import cancel_orders
        result = cancel_orders(oid, coin=coin)
        return JSONResponse(content=result)
    except Exception as e:
        raise HTTPException(500, str(e))


# ── Root ──────────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {"service": "Hermes Agent", "version": __version__, "status": "running"}


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("HERMES_PORT", 8000))
    logger.info(f"Starting Hermes server on port {port}")
    uvicorn.run("hermes_agent.server:app", host="0.0.0.0", port=port, reload=False)
