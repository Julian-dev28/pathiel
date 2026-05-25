"""Public + operator web UI for hermes-trader.

Two surfaces, one module:

  GET /                          — public dashboard (anyone)
  GET /operator                  — operator console (token-gated)
  GET /api/dashboard/summary     — hero numbers + status
  GET /api/dashboard/positions   — open positions + DSL tracker state
  GET /api/dashboard/equity-curve?range=24h|7d|30d
  GET /api/feed/stream           — Server-Sent Events tailing the session log

All data flows from the same JSONL session log + in-memory DSL registry the
trading loop already maintains, so the UI is read-only by default and there
is no second source of truth to keep in sync.

Operator routes require `HERMES_OPERATOR_TOKEN`; missing/wrong token → 401.
The variable is checked at request time, not import time, so rotating it
doesn't require a restart.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from hermes_trader import session_log
from hermes_trader.agents import dsl_exit
from hermes_trader.agents.config_store import read_agent_config
from hermes_trader.client.hl_client import fetch_account_state, resolve_user_address

_LOG_PATH = Path(session_log.SESSION_LOG_FILE)

# Hyperliquid taker fee — 2.5bps per fill, paid on notional. We close with IOC
# orders so all closes are taker. Round-trip cost on margin: 2 fills × 0.025% × leverage.
HL_TAKER_FEE_PCT = 0.025
HL_ROUND_TRIP_FILLS = 2

# HL per-coin max leverage table, built lazily from one info.meta() call so the
# closed-trades fallback can compute a sane historical leverage estimate
# without spamming the API per row.
_max_lev_table: Optional[Dict[str, int]] = None


def _load_max_lev_table() -> Dict[str, int]:
    global _max_lev_table
    if _max_lev_table is not None:
        return _max_lev_table
    try:
        from hermes_trader.client.exchange import _get_info
        meta = _get_info().meta() or {}
        _max_lev_table = {
            u["name"]: int(u.get("maxLeverage", 1) or 1)
            for u in meta.get("universe", []) if "name" in u
        }
    except Exception:
        _max_lev_table = {}
    return _max_lev_table


# ── data helpers ─────────────────────────────────────────────────────────────


def _read_log_lines() -> List[Dict[str, Any]]:
    if not _LOG_PATH.exists():
        return []
    out: List[Dict[str, Any]] = []
    with _LOG_PATH.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _last_event(events: List[Dict[str, Any]], name: str) -> Optional[Dict[str, Any]]:
    for e in reversed(events):
        if e.get("event") == name:
            return e
    return None


def _summary_payload() -> Dict[str, Any]:
    """Equity, daily PnL, open count, last-tick — derived from the session log so
    the dashboard works even if the live HL fetch is rate-limited."""
    events = _read_log_lines()
    heartbeat = _last_event(events, "loop_heartbeat") or {}
    last_scan = _last_event(events, "scan")
    last_event_ts = events[-1]["ts"] if events else 0

    equity = float(heartbeat.get("equity", 0) or 0)
    daily_pnl = float(heartbeat.get("daily_pnl", 0) or 0)
    # Start-of-day equity = equity - daily_pnl (heartbeat-consistent)
    sod = equity - daily_pnl
    daily_pnl_pct = (daily_pnl / sod * 100) if sod > 0 else 0.0

    now_ms = int(time.time() * 1000)
    last_tick_age_s = max(0, (now_ms - last_event_ts) // 1000) if last_event_ts else None

    # Heuristic status: "scanning" if a heartbeat hit in the last 3min;
    # "stale" if older; "offline" if no heartbeat ever.
    if not heartbeat:
        status = "offline"
    elif last_tick_age_s is None or last_tick_age_s > 180:
        status = "stale"
    else:
        status = "scanning"

    return {
        "equity": round(equity, 2),
        "available": round(float(heartbeat.get("available", 0) or 0), 2),
        "spot_usdc": round(float(heartbeat.get("spot_usdc", 0) or 0), 2),
        "daily_pnl": round(daily_pnl, 2),
        "daily_pnl_pct": round(daily_pnl_pct, 2),
        "open_positions": int(heartbeat.get("open_positions", 0) or 0),
        "last_tick_age_s": last_tick_age_s,
        "last_scan_triggers": int((last_scan or {}).get("triggers", 0) or 0),
        "status": status,
        "ts": now_ms,
    }


def _positions_payload() -> List[Dict[str, Any]]:
    """Join live HL positions with DSL tracker state for the operator/public view.

    The DSL registry is in the *trading loop's* memory; the web server is a
    separate process. The loop persists tracker state to disk on every advance,
    so `load_state()` here gives us the same view (one tick stale at worst).
    The function is idempotent — repeated calls cost a single JSON parse.
    """
    dsl_exit.load_state(force=True)
    user = resolve_user_address()
    if not user:
        return []
    try:
        state = fetch_account_state(user)
    except Exception:
        return []

    rows: List[Dict[str, Any]] = []
    for p in state.get("asset_positions", []):
        pos = p.get("position", {})
        coin = pos.get("coin")
        try:
            szi = float(pos.get("szi", "0") or 0)
            entry = float(pos.get("entryPx") or 0)
            mark = float(pos.get("positionValue", 0) or 0) / abs(szi) if szi else 0
            unrealized_usd = float(pos.get("unrealizedPnl", 0) or 0)
            margin_used = float(pos.get("marginUsed", 0) or 0)
        except (TypeError, ValueError):
            continue
        if szi == 0 or not coin:
            continue
        side = "long" if szi > 0 else "short"

        # HL stores leverage as {"value": N, "type": "cross"|"isolated"}; older
        # records (and synthesized stubs) may store it as a bare int.
        leverage_obj = pos.get("leverage")
        if isinstance(leverage_obj, dict):
            leverage = int(leverage_obj.get("value", 1) or 1)
        else:
            leverage = int(leverage_obj or 1)

        spot_pct = ((mark - entry) / entry * 100 if side == "long"
                    else (entry - mark) / entry * 100) if entry else 0
        # ROE = unrealizedPnl / marginUsed — this is what HL's "PNL (ROE %)"
        # column displays, and it already accounts for the open-side fee paid.
        roe_pct = (unrealized_usd / margin_used * 100) if margin_used > 0 else spot_pct * leverage

        tracker = dsl_exit._active_positions.get(f"{coin}_{side}")
        dsl_info = None
        if tracker:
            dsl_info = {
                "peak_px": tracker.peak_px,
                "floor_px": tracker._last_floor,
                "phase": "phase2" if tracker._last_floor and (
                    (side == "long" and tracker._last_floor > tracker.entry_px)
                    or (side == "short" and tracker._last_floor < tracker.entry_px)
                ) else "phase1",
            }

        rows.append({
            "coin": coin,
            "side": side,
            "size": abs(szi),
            "leverage": leverage,
            "entry_px": entry,
            "mark_px": mark,
            "unrealized_pnl_usd": unrealized_usd,
            "unrealized_pct": roe_pct,       # leveraged ROE — matches HL
            "spot_pct": spot_pct,            # bare price move, for the curious
            "dsl": dsl_info,
        })
    return rows


def _closed_trades_payload(limit: int = 20) -> List[Dict[str, Any]]:
    """Walk the session log for close events (dsl_exit, close_position).

    Returns newest-first. Each row carries:
      - `spot_pct`: raw price-move %. This is what the DSL engine measures
        and what HL would show you as "unrealized PnL %" on the position.
      - `pnl_pct`: leveraged margin PnL — what shows up in the HL P&L view.
        Equals spot_pct × leverage.
      - `side` and `leverage`: pulled from the event itself for new closes;
        for older events lacking those fields, walked back to the matching
        execute event (for side) and the live config (for leverage).
    """
    events = _read_log_lines()
    n = len(events)
    cfg_leverage: Optional[int] = None  # lazy-fetched fallback

    def _find_open_side(coin: str, before_idx: int) -> Optional[str]:
        for j in range(before_idx - 1, -1, -1):
            pe = events[j]
            if pe.get("event") == "execute" and pe.get("coin") == coin:
                return pe.get("side")
        return None

    def _cfg_leverage() -> int:
        nonlocal cfg_leverage
        if cfg_leverage is None:
            try:
                cfg_leverage = int(read_agent_config().get("leverage", 1) or 1)
            except Exception:
                cfg_leverage = 1
        return cfg_leverage

    def _estimate_leverage(coin: str) -> int:
        # Mirrors executor.py: actual leverage = min(config.leverage, HL per-coin max).
        # Not perfectly accurate for old trades (config may have changed), but
        # closer than config alone — and for most coins HL's cap is the binding one.
        coin_max = _load_max_lev_table().get(coin, 0)
        cfg = _cfg_leverage()
        return min(cfg, coin_max) if coin_max else cfg

    out: List[Dict[str, Any]] = []
    for i in range(n - 1, -1, -1):
        e = events[i]
        ev = e.get("event")
        if ev == "dsl_exit":
            coin = e.get("coin", "?")
            side = e.get("side") or _find_open_side(coin, i) or "?"
            has_explicit_lev = e.get("leverage") is not None
            leverage = int(e["leverage"]) if has_explicit_lev else _estimate_leverage(coin)

            # If the close logged an actual fill price, use the realized PnL —
            # it matches HL exactly. Otherwise estimate from the DSL trigger
            # mark and subtract round-trip taker fees.
            if e.get("realized_pnl_pct") is not None:
                spot_pct = float(e.get("realized_spot_pct") or 0)
                net_pnl_pct = float(e["realized_pnl_pct"])
                gross_pnl_pct = spot_pct * leverage
                fees_pct = float(e.get("fees_pct") or (HL_TAKER_FEE_PCT * HL_ROUND_TRIP_FILLS * leverage))
                pnl_source = "fill"
            else:
                spot_pct = float(e.get("unrealized_pct", 0) or 0)
                gross_pnl_pct = (float(e["leveraged_pct"]) if e.get("leveraged_pct") is not None
                                 else spot_pct * leverage)
                fees_pct = HL_TAKER_FEE_PCT * HL_ROUND_TRIP_FILLS * leverage
                net_pnl_pct = gross_pnl_pct - fees_pct
                pnl_source = "estimated"

            out.append({
                "ts": e.get("ts"),
                "coin": coin,
                "source": "dsl",
                "side": side,
                "leverage": leverage,
                "leverage_estimated": not has_explicit_lev,
                "reason": e.get("reason", ""),
                "pnl_pct": net_pnl_pct,
                "pnl_pct_gross": gross_pnl_pct,
                "pnl_source": pnl_source,  # "fill" = exact, "estimated" = pre-trade mid × lev − fees
                "fees_pct": fees_pct,
                "spot_pct": spot_pct,
                "fill_px": e.get("fill_px"),
                "entry_px": e.get("entry_px"),
                "executed": bool(e.get("executed")),
                "detail": e.get("detail"),
            })
        elif ev == "close_position":
            coin = e.get("coin", "?")
            out.append({
                "ts": e.get("ts"),
                "coin": coin,
                "source": "manual",
                "side": _find_open_side(coin, i) or "?",
                "leverage": _estimate_leverage(coin),
                "leverage_estimated": True,
                "reason": "manual_close",
                "pnl_pct": 0.0,
                "spot_pct": 0.0,
                "executed": bool(e.get("ok")),
                "detail": None,
            })
        if len(out) >= limit:
            break
    return out


def _equity_curve_payload(range_s: int) -> List[Dict[str, Any]]:
    """Series of (ts, equity) points from loop_heartbeat events within `range_s`."""
    cutoff = int(time.time() * 1000) - range_s * 1000
    series: List[Dict[str, Any]] = []
    for e in _read_log_lines():
        if e.get("event") != "loop_heartbeat":
            continue
        if e.get("ts", 0) < cutoff:
            continue
        eq = float(e.get("equity", 0) or 0)
        if eq <= 0:
            continue
        series.append({"ts": e["ts"], "equity": round(eq, 2)})
    return series


# ── SSE feed ─────────────────────────────────────────────────────────────────


async def _tail_log_sse() -> AsyncIterator[str]:
    """Stream new session-log lines as SSE events. Replays the last 50 first."""
    # Replay buffer so a fresh connection sees the recent past, not just future events.
    for e in session_log.tail(50):
        yield f"data: {json.dumps(e)}\n\n"

    last_size = _LOG_PATH.stat().st_size if _LOG_PATH.exists() else 0
    # Heartbeat every 15s keeps proxies (nginx, Cloudflare) from closing idle SSE.
    last_heartbeat = time.time()

    while True:
        await asyncio.sleep(1.0)
        if not _LOG_PATH.exists():
            continue
        size = _LOG_PATH.stat().st_size
        if size < last_size:
            # File rotated; start over.
            last_size = 0
        if size > last_size:
            with _LOG_PATH.open() as f:
                f.seek(last_size)
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        json.loads(line)  # validate before sending
                    except json.JSONDecodeError:
                        continue
                    yield f"data: {line}\n\n"
            last_size = size

        if time.time() - last_heartbeat > 15:
            yield ": keepalive\n\n"
            last_heartbeat = time.time()


# ── operator gate ────────────────────────────────────────────────────────────


def _require_operator(request: Request) -> None:
    """401 unless `?token=` or `X-Operator-Token` matches `HERMES_OPERATOR_TOKEN`.

    Checking at request time (not import time) means rotating the token doesn't
    need a restart. Missing env var = operator surface is closed.
    """
    expected = os.environ.get("HERMES_OPERATOR_TOKEN", "")
    if not expected:
        raise HTTPException(status_code=503, detail="operator surface disabled (set HERMES_OPERATOR_TOKEN)")
    provided = request.query_params.get("token") or request.headers.get("X-Operator-Token", "")
    if provided != expected:
        raise HTTPException(status_code=401, detail="invalid operator token")


# ── HTML ─────────────────────────────────────────────────────────────────────


_PUBLIC_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>hermes-trader · live</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Press+Start+2P&display=swap" rel="stylesheet">
<script src="https://cdn.tailwindcss.com"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3.0.0/dist/chartjs-adapter-date-fns.bundle.min.js"></script>
<style>
  body{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;background:#0a0a0a;color:#e5e5e5;image-rendering:pixelated}
  /* Pixel-font headings only — body text stays readable mono. */
  .pixel{font-family:'Press Start 2P',ui-monospace,monospace;letter-spacing:.02em;line-height:1.4}
  /* Chunky pixel-card: 2px border + hard 4px offset shadow, no rounded corners. */
  .pixel-card{border:2px solid #27272a;box-shadow:4px 4px 0 #18181b;background:#0f0f10;border-radius:0}
  .pixel-card.accent{border-color:#34d399;box-shadow:4px 4px 0 #064e3b}
  .pixel-btn{border:2px solid currentColor;box-shadow:2px 2px 0 #18181b;border-radius:0;image-rendering:pixelated}
  .pixel-btn:active{transform:translate(2px,2px);box-shadow:none}
  /* LCD-style title strip */
  .lcd{background:#052e1c;border:2px solid #34d399;box-shadow:inset 0 0 0 1px #022c1e,4px 4px 0 #064e3b;padding:8px 12px;color:#6ee7b7;text-shadow:0 0 6px #34d39966}
  /* Agent pet — bounces gently */
  .pet{font-size:28px;display:inline-block;animation:pet-bounce 1.4s ease-in-out infinite;filter:drop-shadow(2px 2px 0 #064e3b)}
  @keyframes pet-bounce{0%,100%{transform:translateY(0)}50%{transform:translateY(-4px)}}
  .pet.shake{animation:pet-shake 0.4s linear infinite}
  @keyframes pet-shake{0%,100%{transform:translateX(0)}25%{transform:translateX(-2px)}75%{transform:translateX(2px)}}
  .pet.sleep{animation:pet-sleep 3s ease-in-out infinite;filter:none;opacity:.7}
  @keyframes pet-sleep{0%,100%{transform:scale(1)}50%{transform:scale(0.95)}}
  /* Pixel "mood bar" — chunky blocks */
  .mood-bar{font-family:'Press Start 2P',monospace;font-size:10px;letter-spacing:2px;color:#34d399}
  .feed-row{font-size:12px;line-height:1.6;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .feed-row.scan{color:#9ca3af}
  .feed-row.research{color:#a5b4fc}
  .feed-row.execute{color:#86efac}
  .feed-row.execute-fail{color:#fca5a5}
  .feed-row.error{color:#f87171}
  .feed-row.heartbeat{color:#71717a}
  .feed-row.dsl_exit{color:#fbbf24}
  /* Status pill — pixel-block style with sharp corners */
  .pill{display:inline-flex;align-items:center;gap:6px;padding:4px 10px;font-size:10px;font-weight:600;font-family:'Press Start 2P',monospace;border:2px solid currentColor;border-radius:0;letter-spacing:.05em}
  .pill.scanning{background:#064e3b;color:#6ee7b7}
  .pill.stale{background:#451a03;color:#fbbf24}
  .pill.offline{background:#450a0a;color:#fca5a5}
  .num{font-variant-numeric:tabular-nums}
  .blink{animation:blink 1.6s ease-in-out infinite}
  @keyframes blink{0%,100%{opacity:1}50%{opacity:.4}}
  /* Override Tailwind's rounded-lg on existing sections to keep pixel feel */
  section.bg-zinc-900{border:2px solid #27272a;box-shadow:4px 4px 0 #18181b;border-radius:0;background:#0f0f10}
  /* ── Matrix-rain right sidebar ── */
  .matrix-pane{
    background:linear-gradient(180deg,#02110a 0%,#000805 100%);
    border:2px solid #047857;
    box-shadow:4px 4px 0 #022c1e, inset 0 0 24px rgba(52,211,153,0.08);
    border-radius:0;
    position:relative;overflow:hidden;
  }
  .matrix-pane::before{
    /* scanline overlay — barely visible, sells the CRT vibe */
    content:'';position:absolute;inset:0;pointer-events:none;
    background:repeating-linear-gradient(0deg,rgba(0,0,0,0) 0,rgba(0,0,0,0) 2px,rgba(0,0,0,0.18) 3px,rgba(0,0,0,0) 4px);
    z-index:1;
  }
  .matrix-feed{position:relative;z-index:2;overflow-y:auto;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
  .matrix-feed .feed-row{
    /* Override the global .feed-row nowrap/ellipsis — in the matrix sidebar we
       want full readability over single-line truncation. Hanging indent so the
       wrapped continuation lines sit under the message body, not under the
       timestamp. */
    white-space:normal;word-break:break-word;overflow:visible;text-overflow:clip;
    text-indent:-1.25em;padding:2px 2px 2px 1.5em;line-height:1.5;
    color:#6ee7b7;text-shadow:0 0 4px rgba(52,211,153,0.5);
    animation:matrix-in .55s cubic-bezier(.2,.7,.3,1);
  }
  /* Last row glows a bit hotter — like the "head" of a matrix stream */
  .matrix-feed .feed-row:last-child{color:#a7f3d0;text-shadow:0 0 8px rgba(167,243,208,0.7)}
  .matrix-feed .feed-row.error{color:#fca5a5;text-shadow:0 0 4px rgba(248,113,113,0.5)}
  .matrix-feed .feed-row.execute{color:#86efac;text-shadow:0 0 6px rgba(134,239,172,0.6)}
  .matrix-feed .feed-row.dsl_exit{color:#fde68a;text-shadow:0 0 6px rgba(253,230,138,0.6)}
  .matrix-feed::-webkit-scrollbar{width:6px}
  .matrix-feed::-webkit-scrollbar-track{background:#02110a}
  .matrix-feed::-webkit-scrollbar-thumb{background:#047857}
  @keyframes matrix-in{
    0%{opacity:0;transform:translateY(-6px);filter:blur(1px)}
    60%{opacity:.85}
    100%{opacity:1;transform:translateY(0);filter:blur(0)}
  }
  /* Sticky on wide screens, scrolls inline on narrow */
  @media (min-width:1024px){.matrix-pane{position:sticky;top:1.5rem;height:calc(100vh - 3rem)}}
  /* ── HERMES.HAMSTER habitat — nous-research-flavored tamagotchi at top of matrix pane ── */
  .habitat{padding:10px 12px 8px;border-bottom:2px solid #047857;background:linear-gradient(180deg,#02160c 0%,#000805 100%);position:relative;z-index:3}
  .habitat-name{font-family:'Press Start 2P',monospace;font-size:8px;color:#34d399;letter-spacing:.15em;text-align:center;margin-bottom:4px;text-shadow:0 0 4px rgba(52,211,153,0.6)}
  .habitat-name .psi{color:#fbbf24;margin-left:4px;text-shadow:0 0 6px rgba(251,191,36,0.6)}
  .habitat-pet{display:flex;flex-direction:column;align-items:center;gap:0;padding:2px 0 4px;line-height:1}
  /* White rabbit: brightness+saturate to wash the default emoji tone toward
     white, plus a soft white glow. Animation filters in keyframes override
     this briefly during celebrate/victory/defeat. */
  .hamster-body{font-size:30px;display:inline-block;animation:hamster-run .42s ease-in-out infinite;filter:brightness(1.55) saturate(0.25) drop-shadow(0 0 6px rgba(255,255,255,0.65))}
  @keyframes hamster-run{0%,100%{transform:translateY(0)}50%{transform:translateY(-3px) rotate(-2deg)}}
  .hamster-wheel{font-size:14px;display:inline-block;animation:wheel-spin .8s linear infinite;opacity:.75;color:#34d399}
  @keyframes wheel-spin{from{transform:rotate(0)}to{transform:rotate(360deg)}}
  .habitat-quote{font-family:'Press Start 2P',monospace;font-size:7px;color:#34d399aa;text-align:center;padding:6px 2px 0;min-height:14px;letter-spacing:.05em;transition:opacity .3s ease}
  .habitat-quote::before{content:'» '}
  .habitat-quote::after{content:' «'}
  /* ── Hamster reactions to live trading events ── */
  /* execute → yellow celebrate (lightning bolt burst) */
  .hamster-body.celebrate{animation:hamster-celebrate 1.2s ease-out}
  @keyframes hamster-celebrate{
    0%{transform:translateY(0) scale(1) rotate(0);filter:drop-shadow(0 0 4px rgba(251,191,36,0.5))}
    20%{transform:translateY(-10px) scale(1.35) rotate(-15deg);filter:drop-shadow(0 0 14px #fde047) brightness(1.4)}
    40%{transform:translateY(-6px) scale(1.25) rotate(15deg)}
    60%{transform:translateY(-3px) scale(1.15) rotate(-8deg)}
    100%{transform:translateY(0) scale(1) rotate(0)}
  }
  /* dsl_exit profitable → victory wiggle */
  .hamster-body.victory{animation:hamster-victory 1.4s ease-out}
  @keyframes hamster-victory{
    0%,100%{transform:rotate(0) scale(1)}
    15%{transform:rotate(-20deg) scale(1.2);filter:drop-shadow(0 0 10px #34d399)}
    30%{transform:rotate(20deg) scale(1.2)}
    45%{transform:rotate(-15deg) scale(1.15)}
    60%{transform:rotate(15deg) scale(1.1)}
    75%{transform:rotate(-5deg)}
  }
  /* dsl_exit loss → defeat shake */
  .hamster-body.defeat{animation:hamster-defeat 1.2s ease-out}
  @keyframes hamster-defeat{
    0%,100%{transform:translateX(0) rotate(0)}
    10%,30%,50%,70%,90%{transform:translateX(-4px) rotate(-6deg);filter:drop-shadow(0 0 8px #f87171)}
    20%,40%,60%,80%{transform:translateX(4px) rotate(6deg)}
  }
  /* Habitat background flash on event */
  .habitat.flash-yellow{animation:habitat-flash-y 1.2s ease-out}
  @keyframes habitat-flash-y{0%{background:linear-gradient(180deg,#3f2e00 0%,#150e00 100%);box-shadow:inset 0 0 20px rgba(251,191,36,0.4)}100%{background:linear-gradient(180deg,#02160c 0%,#000805 100%)}}
  .habitat.flash-green{animation:habitat-flash-g 1.2s ease-out}
  @keyframes habitat-flash-g{0%{background:linear-gradient(180deg,#064e3b 0%,#001f12 100%);box-shadow:inset 0 0 20px rgba(52,211,153,0.4)}100%{background:linear-gradient(180deg,#02160c 0%,#000805 100%)}}
  .habitat.flash-red{animation:habitat-flash-r 1.2s ease-out}
  @keyframes habitat-flash-r{0%{background:linear-gradient(180deg,#450a0a 0%,#1f0000 100%);box-shadow:inset 0 0 20px rgba(248,113,113,0.4)}100%{background:linear-gradient(180deg,#02160c 0%,#000805 100%)}}
  /* Floating burst icon — ⚡/💰/💀 rises and fades */
  .habitat-burst{position:absolute;top:32px;left:50%;font-size:20px;opacity:0;pointer-events:none;z-index:5;text-shadow:0 0 8px rgba(255,255,255,0.6)}
  .habitat-burst.show{animation:burst-rise 1.1s ease-out forwards}
  @keyframes burst-rise{
    0%{opacity:0;transform:translateX(-50%) translateY(0) scale(0.5)}
    20%{opacity:1;transform:translateX(-50%) translateY(-8px) scale(1.5)}
    100%{opacity:0;transform:translateX(-50%) translateY(-36px) scale(1)}
  }
</style>
</head>
<body class="min-h-screen">
<div class="max-w-[1600px] mx-auto px-6 py-6">

  <header class="flex items-center justify-between mb-6 gap-3 flex-wrap">
    <div class="flex items-center gap-3">
      <span id="pet" class="pet" title="agent mood — reacts to status + PnL">🤖</span>
      <div class="flex flex-col">
        <span class="lcd pixel text-sm tracking-tight">HERMES-TRADER</span>
        <span class="text-[10px] text-zinc-500 mt-1 pixel">AUTONOMOUS · HYPERLIQUID</span>
      </div>
    </div>
    <div class="flex items-center gap-2 text-xs">
      <select id="ccy-sel" class="bg-zinc-800 text-zinc-300 rounded px-2 py-1 text-xs border-0 focus:outline-none cursor-pointer">
        <option value="USD">USD $</option>
        <option value="EUR">EUR €</option>
        <option value="JPY">JPY ¥</option>
        <option value="GBP">GBP £</option>
        <option value="CNY">CNY ¥</option>
        <option value="KRW">KRW ₩</option>
        <option value="SGD">SGD S$</option>
        <option value="PHP">PHP ₱</option>
        <option value="MYR">MYR RM</option>
        <option value="THB">THB ฿</option>
        <option value="IDR">IDR Rp</option>
        <option value="VND">VND ₫</option>
        <option value="AUD">AUD A$</option>
        <option value="CAD">CAD C$</option>
        <option value="CHF">CHF</option>
      </select>
      <select id="lang-sel" class="bg-zinc-800 text-zinc-300 rounded px-2 py-1 text-xs border-0 focus:outline-none cursor-pointer">
        <option value="en">EN</option>
        <option value="zh">中文</option>
        <option value="ja">日本語</option>
        <option value="ko">한국어</option>
        <option value="fr">Français</option>
        <option value="es">Español</option>
        <option value="id">Bahasa</option>
        <option value="tl">Tagalog</option>
        <option value="vi">Tiếng Việt</option>
        <option value="th">ไทย</option>
      </select>
      <span id="status-pill" class="pill offline">offline</span>
      <a href="https://github.com/Julian-dev28/hermes-trader" class="text-zinc-400 hover:text-zinc-200">github</a>
    </div>
  </header>

  <div class="grid grid-cols-1 lg:grid-cols-[1fr_560px] gap-6">
  <main class="min-w-0 space-y-6">

  <section class="grid grid-cols-2 md:grid-cols-4 gap-4">
    <div class="bg-zinc-900 rounded-lg p-4">
      <div class="text-[10px] text-zinc-500 pixel" data-i18n="equity">equity</div>
      <div class="text-2xl font-bold num" id="kpi-equity">$0.00</div>
    </div>
    <div class="bg-zinc-900 rounded-lg p-4">
      <div class="text-[10px] text-zinc-500 pixel" data-i18n="today">today</div>
      <div class="text-2xl font-bold num" id="kpi-pnl">$0.00</div>
      <div class="text-xs num" id="kpi-pnl-pct">—</div>
    </div>
    <div class="bg-zinc-900 rounded-lg p-4">
      <div class="text-[10px] text-zinc-500 pixel" data-i18n="open_positions">open positions</div>
      <div class="text-2xl font-bold num" id="kpi-open">0</div>
    </div>
    <div class="bg-zinc-900 rounded-lg p-4">
      <div class="text-[10px] text-zinc-500 pixel" data-i18n="last_tick">last tick</div>
      <div class="text-2xl font-bold num" id="kpi-tick">—</div>
      <div class="text-[10px] text-zinc-500 pixel" id="kpi-tick-detail" data-i18n="no_scan_yet">no scan yet</div>
    </div>
  </section>

  <section class="bg-zinc-900 rounded-lg p-4 text-xs leading-relaxed text-zinc-400">
    <div class="text-zinc-500 mb-2 uppercase tracking-wider text-[10px]">how it works</div>
    <p>
      Autonomous trading agent on
      <a class="text-emerald-400 hover:underline" href="https://hyperliquid.xyz">Hyperliquid</a>
      perpetuals — crypto, equities, commodities.
      Every minute the engine scans 60+ markets for statistical triggers (volume
      spikes, breakouts, momentum bursts), runs a free TA filter, and only spends
      AI tokens on confirmed setups. Trades clear 11 risk gates, size by half-Kelly,
      and exit through a two-phase dynamic stop-loss (loss protection → profit
      locking with one-way trailing floor).
      <span class="text-zinc-500">Live on one wallet. Not financial advice.</span>
    </p>
  </section>

  <section class="bg-zinc-900 rounded-lg p-4">
    <div class="flex items-center justify-between mb-2">
      <div class="text-[10px] text-zinc-500 pixel" data-i18n="equity_curve">equity curve</div>
      <div class="flex gap-1 text-xs">
        <button data-range="86400" class="range-btn px-2 py-1 rounded bg-zinc-800 hover:bg-zinc-700">24h</button>
        <button data-range="604800" class="range-btn px-2 py-1 rounded bg-zinc-800 hover:bg-zinc-700">7d</button>
        <button data-range="2592000" class="range-btn px-2 py-1 rounded bg-zinc-800 hover:bg-zinc-700">30d</button>
      </div>
    </div>
    <div class="relative">
      <canvas id="equity-chart" height="110"></canvas>
      <div id="equity-empty" class="hidden absolute inset-0 flex items-center justify-center text-xs text-zinc-500">
        no heartbeats yet in this window
      </div>
    </div>
  </section>

  <section class="bg-zinc-900 rounded-lg p-4">
    <div class="flex items-center justify-between mb-2">
      <div class="text-[10px] text-zinc-500 pixel" data-i18n="open_positions">open positions</div>
      <div class="flex gap-1 text-[10px]">
        <button data-sort="default" data-i18n="default" class="pos-sort-btn px-2 py-0.5 rounded bg-zinc-800 hover:bg-zinc-700">default</button>
        <button data-sort="pnl_desc" class="pos-sort-btn px-2 py-0.5 rounded bg-emerald-700">PnL ↓</button>
        <button data-sort="pnl_asc"  class="pos-sort-btn px-2 py-0.5 rounded bg-zinc-800 hover:bg-zinc-700">PnL ↑</button>
      </div>
    </div>
    <div id="positions" class="text-sm">
      <div class="text-zinc-500 text-xs" data-i18n="none">none</div>
    </div>
  </section>

  <section class="bg-zinc-900 rounded-lg p-4">
    <div class="flex items-center justify-between mb-2">
      <div class="text-[10px] text-zinc-500 pixel" data-i18n="recent_closes">recent closes</div>
      <div class="text-xs text-zinc-600" id="closes-stats"></div>
    </div>
    <div id="closes" class="text-sm">
      <div class="text-zinc-500 text-xs" data-i18n="none_yet">none yet</div>
    </div>
  </section>

  </main>

  <aside class="matrix-pane flex flex-col">
    <div class="habitat">
      <div class="habitat-pet">
        <span class="hamster-body" title="follow the white rabbit">🐇</span>
        <span class="hamster-wheel">⚙</span>
      </div>
      <div class="habitat-quote" id="hamster-quote">awakening</div>
    </div>
    <div class="flex items-center justify-between px-3 py-2 border-b-2 border-emerald-800/60 bg-black/40 relative z-10">
      <div class="text-[10px] text-emerald-400 pixel" data-i18n="live_activity">live activity</div>
      <span class="text-[10px] text-emerald-400 blink pixel" data-i18n="following">▶ following</span>
    </div>
    <div id="feed" class="matrix-feed flex-1 px-3 py-2 space-y-0.5"></div>
  </aside>
  </div>

  <footer class="text-[10px] text-zinc-600 mt-6 text-center pixel" data-i18n="footer">
    one wallet · live · not financial advice
  </footer>
</div>

<script>
// ── locale / currency state ──
// USD values from the API are multiplied by ccyState.rate at display time. FX
// rates pulled from open.er-api.com (free, no key) and cached 1h in localStorage.
let ccyState = { code: 'USD', rate: 1 };
let langState = 'en';
const ZERO_DECIMAL_CCY = new Set(['JPY', 'KRW', 'VND', 'IDR']);

function fmtMoney(usd, opts = {}) {
  const v = (usd ?? 0) * ccyState.rate;
  const digits = ZERO_DECIMAL_CCY.has(ccyState.code) ? 0 : 2;
  try {
    return new Intl.NumberFormat(undefined, {
      style: 'currency',
      currency: ccyState.code,
      signDisplay: opts.signed ? 'always' : 'auto',
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    }).format(v);
  } catch (e) {
    return (v >= 0 ? '' : '-') + Math.abs(v).toFixed(digits);
  }
}
const fmtPct = n => (n >= 0 ? '+' : '') + n.toFixed(2) + '%';
const fmtAge = s => s == null ? '—' : (s < 60 ? s + 's' : Math.floor(s/60) + 'm ago');
const fmtTime = ms => { const d = new Date(ms); return d.toTimeString().slice(0,8); };

// ── i18n ──
const I18N = {
  en: { equity:'equity', today:'today', open_positions:'open positions', last_tick:'last tick', no_scan_yet:'no scan yet', equity_curve:'equity curve', recent_closes:'recent closes', live_activity:'live activity', none:'none', none_yet:'none yet', following:'▶ following', footer:'one wallet · live · not financial advice', default:'default', triggers:'triggers' },
  zh: { equity:'净值', today:'今日', open_positions:'持仓', last_tick:'上次更新', no_scan_yet:'尚未扫描', equity_curve:'净值曲线', recent_closes:'最近平仓', live_activity:'实时活动', none:'无', none_yet:'尚无', following:'▶ 关注中', footer:'单一钱包 · 实盘 · 非投资建议', default:'默认', triggers:'触发' },
  ja: { equity:'純資産', today:'本日', open_positions:'ポジション', last_tick:'最終更新', no_scan_yet:'スキャン未実施', equity_curve:'純資産推移', recent_closes:'最近のクローズ', live_activity:'ライブアクティビティ', none:'なし', none_yet:'まだなし', following:'▶ 追跡中', footer:'単一ウォレット · ライブ · 投資助言ではありません', default:'デフォルト', triggers:'トリガー' },
  ko: { equity:'자본', today:'오늘', open_positions:'보유 포지션', last_tick:'마지막 틱', no_scan_yet:'스캔 전', equity_curve:'자본 곡선', recent_closes:'최근 청산', live_activity:'실시간 활동', none:'없음', none_yet:'아직 없음', following:'▶ 추적 중', footer:'단일 지갑 · 실시간 · 투자 조언 아님', default:'기본', triggers:'트리거' },
  fr: { equity:'capital', today:"aujourd'hui", open_positions:'positions ouvertes', last_tick:'dernier tick', no_scan_yet:'pas encore scanné', equity_curve:'courbe du capital', recent_closes:'clôtures récentes', live_activity:'activité en direct', none:'aucune', none_yet:'aucune encore', following:'▶ en cours', footer:'un portefeuille · en direct · pas un conseil financier', default:'défaut', triggers:'déclencheurs' },
  es: { equity:'capital', today:'hoy', open_positions:'posiciones abiertas', last_tick:'último tick', no_scan_yet:'sin escaneo aún', equity_curve:'curva de capital', recent_closes:'cierres recientes', live_activity:'actividad en vivo', none:'ninguna', none_yet:'ninguna aún', following:'▶ siguiendo', footer:'una cartera · en vivo · no es consejo financiero', default:'por defecto', triggers:'disparadores' },
  id: { equity:'ekuitas', today:'hari ini', open_positions:'posisi terbuka', last_tick:'tick terakhir', no_scan_yet:'belum pindai', equity_curve:'kurva ekuitas', recent_closes:'penutupan terbaru', live_activity:'aktivitas langsung', none:'tidak ada', none_yet:'belum ada', following:'▶ mengikuti', footer:'satu dompet · langsung · bukan nasihat keuangan', default:'bawaan', triggers:'pemicu' },
  tl: { equity:'puhunan', today:'ngayon', open_positions:'bukas na posisyon', last_tick:'huling tick', no_scan_yet:'wala pang scan', equity_curve:'kurba ng puhunan', recent_closes:'kamakailang isinara', live_activity:'live na aktibidad', none:'wala', none_yet:'wala pa', following:'▶ sumusunod', footer:'isang wallet · live · hindi payong pinansyal', default:'default', triggers:'trigger' },
  vi: { equity:'vốn', today:'hôm nay', open_positions:'vị thế mở', last_tick:'tick cuối', no_scan_yet:'chưa quét', equity_curve:'đường vốn', recent_closes:'đóng gần đây', live_activity:'hoạt động trực tiếp', none:'không có', none_yet:'chưa có', following:'▶ đang theo', footer:'một ví · trực tiếp · không phải lời khuyên tài chính', default:'mặc định', triggers:'kích hoạt' },
  th: { equity:'ทุน', today:'วันนี้', open_positions:'สถานะเปิด', last_tick:'อัปเดตล่าสุด', no_scan_yet:'ยังไม่สแกน', equity_curve:'เส้นทุน', recent_closes:'ปิดล่าสุด', live_activity:'กิจกรรมสด', none:'ไม่มี', none_yet:'ยังไม่มี', following:'▶ ติดตาม', footer:'หนึ่งวอลเล็ต · สด · ไม่ใช่คำแนะนำทางการเงิน', default:'ค่าเริ่มต้น', triggers:'ทริกเกอร์' },
};
function applyI18n() {
  const dict = I18N[langState] || I18N.en;
  document.querySelectorAll('[data-i18n]').forEach(el => {
    const k = el.dataset.i18n;
    if (dict[k]) el.textContent = dict[k];
  });
}

async function loadRates() {
  const cached = JSON.parse(localStorage.getItem('hermes-fx') || 'null');
  if (cached && Date.now() - cached.t < 3600000) return cached.rates;
  try {
    const r = await fetch('https://open.er-api.com/v6/latest/USD');
    const d = await r.json();
    if (d.result !== 'success') throw new Error('rate fetch failed');
    const rates = { USD: 1, ...d.rates };
    localStorage.setItem('hermes-fx', JSON.stringify({ t: Date.now(), rates }));
    return rates;
  } catch (e) {
    return cached?.rates || { USD: 1 };
  }
}

// ── HERMES.HAMSTER habitat ──
// Tamagotchi-meets-Nous: hamster contemplates the digital rain. Stats are
// recomputed from the dashboard summary; cryptic quotes rotate on a timer.
const HAMSTER_QUOTES = [
  'follow the white rabbit',
  'down the rabbit hole',
  'the rabbit hole goes deeper',
  'wake up neo',
  'the signal is clear',
  'compute is destiny',
  'all paths converge',
  'i dream in OHLC',
  'the wheel knows',
  'world model loaded',
  'hermes opens the path',
  'ψ awaits',
  'there is no candle',
  'momentum is prayer',
  'noesis in the orderbook',
  'let entropy speak',
  'risk is a koan',
  'we run because we must',
];
let hamsterQuoteIdx = -1;
function rotateHamsterQuote() {
  const el = document.getElementById('hamster-quote');
  if (!el) return;
  hamsterQuoteIdx = (hamsterQuoteIdx + 1) % HAMSTER_QUOTES.length;
  el.style.opacity = '0';
  setTimeout(() => { el.textContent = HAMSTER_QUOTES[hamsterQuoteIdx]; el.style.opacity = '1'; }, 250);
}
// Hamster reacts to live trading events: execute → celebrate (yellow ⚡),
// dsl_exit profit → victory wiggle (green 💰), dsl_exit loss → defeat shake
// (red 💀). Animation classes auto-clear so subsequent events restart cleanly.
function triggerHamsterReaction(eventType, pnlPct) {
  const body = document.querySelector('.hamster-body');
  const habitat = document.querySelector('.habitat');
  if (!body || !habitat) return;
  body.classList.remove('celebrate', 'victory', 'defeat');
  habitat.classList.remove('flash-yellow', 'flash-green', 'flash-red');
  let bodyClass, habitatClass, burstChar;
  if (eventType === 'execute') {
    bodyClass = 'celebrate'; habitatClass = 'flash-yellow'; burstChar = '⚡';
  } else if (eventType === 'dsl_exit') {
    if ((pnlPct ?? 0) >= 0) { bodyClass = 'victory'; habitatClass = 'flash-green'; burstChar = '💰'; }
    else { bodyClass = 'defeat'; habitatClass = 'flash-red'; burstChar = '💀'; }
  } else { return; }
  // Force reflow so the same class re-fires on rapid repeat events.
  void body.offsetWidth;
  body.classList.add(bodyClass);
  habitat.classList.add(habitatClass);
  // Floating burst icon above the hamster
  const burst = document.createElement('span');
  burst.className = 'habitat-burst show';
  burst.textContent = burstChar;
  habitat.appendChild(burst);
  setTimeout(() => burst.remove(), 1200);
  setTimeout(() => {
    body.classList.remove(bodyClass);
    habitat.classList.remove(habitatClass);
  }, 1500);
}

// ── KPIs ──
// Agent-pet mood: status sets the base, then PnL nudges it. Big winners → 🤑,
// big losers → 😱. The pet element gets a CSS modifier class for animation
// (shake on executing, sleep on offline/stale, default gentle bounce otherwise).
function updatePet(status, dailyPnlPct) {
  const pet = document.getElementById('pet');
  if (!pet) return;
  let face = '🤖', mood = '';
  if (status === 'offline') { face = '💤'; mood = 'sleep'; }
  else if (status === 'stale') { face = '😴'; mood = 'sleep'; }
  else if (status === 'executing') { face = '⚡'; mood = 'shake'; }
  else if (status === 'scanning') { face = '👀'; }
  // PnL overrides for strong signals
  if (status !== 'offline' && status !== 'stale') {
    if (dailyPnlPct >= 5) face = '🤑';
    else if (dailyPnlPct <= -5) face = '😱';
    else if (dailyPnlPct >= 1.5) face = '😎';
    else if (dailyPnlPct <= -1.5) face = '😰';
  }
  pet.textContent = face;
  pet.className = 'pet' + (mood ? ' ' + mood : '');
}

async function refreshSummary() {
  try {
    const r = await fetch('/api/dashboard/summary');
    const s = await r.json();
    document.getElementById('kpi-equity').textContent = fmtMoney(s.equity);
    const pnlEl = document.getElementById('kpi-pnl');
    pnlEl.textContent = fmtMoney(s.daily_pnl);
    pnlEl.className = 'text-2xl font-bold num ' + (s.daily_pnl >= 0 ? 'text-emerald-400' : 'text-red-400');
    document.getElementById('kpi-pnl-pct').textContent = fmtPct(s.daily_pnl_pct);
    document.getElementById('kpi-open').textContent = s.open_positions;
    document.getElementById('kpi-tick').textContent = fmtAge(s.last_tick_age_s);
    document.getElementById('kpi-tick-detail').textContent = s.last_scan_triggers + ' triggers';
    const pill = document.getElementById('status-pill');
    pill.textContent = s.status;
    pill.className = 'pill ' + s.status;
    updatePet(s.status, s.daily_pnl_pct);
  } catch (e) {}
}

// ── Positions ──
let currentPosSort = 'pnl_desc';
async function refreshPositions() {
  try {
    const r = await fetch('/api/dashboard/positions');
    const ps = await r.json();
    const el = document.getElementById('positions');
    if (!ps.length) { el.innerHTML = '<div class="text-zinc-500 text-xs">none</div>'; return; }
    if (currentPosSort === 'pnl_desc') ps.sort((a, b) => (b.unrealized_pct ?? 0) - (a.unrealized_pct ?? 0));
    else if (currentPosSort === 'pnl_asc') ps.sort((a, b) => (a.unrealized_pct ?? 0) - (b.unrealized_pct ?? 0));
    el.innerHTML = ps.map(p => {
      const pnlColor = p.unrealized_pct >= 0 ? 'text-emerald-400' : 'text-red-400';
      const sideTag = p.side === 'long'
        ? '<span class="text-[10px] text-emerald-400 font-semibold">LONG</span>'
        : '<span class="text-[10px] text-red-400 font-semibold">SHORT</span>';
      const levTag = p.leverage > 1 ? `<span class="text-[10px] text-zinc-500">${p.leverage}x</span>` : '';
      const sizeFmt = p.size >= 1 ? p.size.toFixed(2) : p.size.toFixed(4);
      const pxFmt = (v) => v < 1 ? v.toFixed(5) : v < 100 ? v.toFixed(3) : v.toFixed(2);
      const floor = p.dsl?.floor_px ? ('floor ' + pxFmt(p.dsl.floor_px)) : '<span class="text-zinc-700">no DSL</span>';
      const phase = p.dsl?.phase || '';
      const usd = p.unrealized_pnl_usd;
      const usdStr = fmtMoney(usd, { signed: true });
      const spotNote = p.leverage > 1
        ? `<span class="text-zinc-600 text-[10px] ml-1" title="spot ${p.spot_pct >= 0 ? '+' : ''}${p.spot_pct.toFixed(2)}% × ${p.leverage}x leverage = ROE shown">(spot ${p.spot_pct >= 0 ? '+' : ''}${p.spot_pct.toFixed(2)}%)</span>`
        : '';
      return `<div class="grid grid-cols-12 gap-2 py-1 border-b border-zinc-800 last:border-0 num text-xs items-center">
        <div class="col-span-2 flex items-baseline gap-2"><span class="font-bold text-sm">${p.coin}</span>${sideTag} ${levTag}</div>
        <div class="col-span-2 text-zinc-400">${sizeFmt} @ ${pxFmt(p.entry_px)}</div>
        <div class="col-span-2 text-zinc-400">mark ${pxFmt(p.mark_px)}</div>
        <div class="col-span-3 ${pnlColor} text-sm font-semibold">${usdStr} (${p.unrealized_pct >= 0 ? '+' : ''}${p.unrealized_pct.toFixed(1)}%)${spotNote}</div>
        <div class="col-span-3 text-zinc-500 text-[11px]">${floor} ${phase}</div>
      </div>`;
    }).join('');
  } catch (e) {}
}

// ── Closed trades ──
async function refreshCloses() {
  try {
    const r = await fetch('/api/dashboard/closed-trades?limit=20');
    const cs = await r.json();
    const el = document.getElementById('closes');
    if (!cs.length) { el.innerHTML = '<div class="text-zinc-500 text-xs">none yet</div>'; return; }
    el.innerHTML = cs.map(c => {
      const ageMin = Math.max(0, Math.round((Date.now() - c.ts) / 60000));
      const ageStr = ageMin < 60 ? ageMin + 'm ago' : ageMin < 1440 ? Math.floor(ageMin/60) + 'h ago' : Math.floor(ageMin/1440) + 'd ago';
      const pnlColor = c.pnl_pct >= 0 ? 'text-emerald-400' : 'text-red-400';
      const sideTag = c.side === 'long' ? '<span class="text-emerald-400 text-[10px] font-semibold">LONG</span>'
                    : c.side === 'short' ? '<span class="text-red-400 text-[10px] font-semibold">SHORT</span>'
                    : '<span class="text-zinc-500 text-[10px]">—</span>';
      const levMark = c.leverage_estimated ? '~' : '';
      const levTag = c.leverage > 1 ? `<span class="text-zinc-500 text-[10px]" title="${c.leverage_estimated ? 'leverage estimated from HL per-coin max — not recorded for this old trade' : ''}">${levMark}${c.leverage}x</span>` : '';
      const sourceTag = c.source === 'dsl' ? '<span class="text-amber-400 text-[10px]">dsl</span>'
                                           : '<span class="text-zinc-500 text-[10px]">manual</span>';
      const failedTag = c.executed ? '' : ' <span class="text-red-400 text-[10px]">FAILED</span>';
      const pnlExactMark = c.pnl_source === 'fill' ? '' : '~';
      const tipLines = [
        `spot move × ${c.leverage}x = ${c.pnl_pct_gross.toFixed(2)}% gross`,
        `minus ${c.fees_pct.toFixed(2)}% taker fees`,
        `= ${c.pnl_pct.toFixed(2)}% net`,
        c.pnl_source === 'fill' ? `realized at fill ${c.fill_px} (entry ${c.entry_px})` : 'estimated from DSL trigger mark (no fill captured)',
      ].join(' · ');
      const spotNote = c.spot_pct && c.leverage > 1
        ? `<span class="text-zinc-600 text-[10px] ml-1" title="${tipLines}">(spot ${c.spot_pct >= 0 ? '+' : ''}${c.spot_pct.toFixed(2)}%)</span>` : '';
      return `<div class="grid grid-cols-12 gap-2 py-1 border-b border-zinc-800 last:border-0 num text-xs items-center">
        <div class="col-span-2 flex items-baseline gap-2"><span class="font-bold text-sm">${c.coin}</span>${sideTag} ${levTag}</div>
        <div class="col-span-5 text-zinc-400 truncate" title="${c.reason}">${c.reason}${failedTag}</div>
        <div class="col-span-3 ${pnlColor} text-sm font-semibold">${pnlExactMark}${c.pnl_pct >= 0 ? '+' : ''}${c.pnl_pct.toFixed(1)}%${spotNote}</div>
        <div class="col-span-1 text-zinc-500">${sourceTag}</div>
        <div class="col-span-1 text-zinc-500 text-right">${ageStr}</div>
      </div>`;
    }).join('');
    const dslOnly = cs.filter(c => c.source === 'dsl');
    const wins = dslOnly.filter(c => c.pnl_pct > 0).length;
    const total = dslOnly.length;
    if (total > 0) {
      const avgPnl = (dslOnly.reduce((a,c)=>a+c.pnl_pct,0) / total).toFixed(1);
      document.getElementById('closes-stats').textContent = `${wins}/${total} winners · avg ${avgPnl >= 0 ? '+' : ''}${avgPnl}% (leveraged)`;
    }
  } catch (e) {}
}

// ── Equity curve ──
let chart;
let currentRange = 86400;
const RANGE_UNIT = {86400: 'hour', 604800: 'day', 2592000: 'day'};

function makeGradient(ctx, area) {
  const g = ctx.createLinearGradient(0, area.top, 0, area.bottom);
  g.addColorStop(0,   'rgba(16, 185, 129, 0.28)');
  g.addColorStop(0.6, 'rgba(16, 185, 129, 0.06)');
  g.addColorStop(1,   'rgba(16, 185, 129, 0)');
  return g;
}

async function refreshChart(rangeSec) {
  currentRange = rangeSec;
  try {
    const r = await fetch('/api/dashboard/equity-curve?range_s=' + rangeSec);
    const series = await r.json();
    const data = series.map(p => ({x: p.ts, y: p.equity}));
    const empty = document.getElementById('equity-empty');
    empty.classList.toggle('hidden', data.length > 0);
    if (!chart) {
      const ctx = document.getElementById('equity-chart').getContext('2d');
      chart = new Chart(ctx, {
        type: 'line',
        data: { datasets: [{
          data,
          borderColor: '#34d399',
          borderWidth: 1.75,
          borderJoinStyle: 'round',
          borderCapStyle: 'round',
          cubicInterpolationMode: 'monotone',
          tension: 0.35,
          pointRadius: 0,
          pointHoverRadius: 4,
          pointHoverBackgroundColor: '#34d399',
          pointHoverBorderColor: '#0a0a0a',
          pointHoverBorderWidth: 2,
          fill: true,
          backgroundColor: (c) => {
            const {ctx, chartArea} = c.chart;
            if (!chartArea) return 'rgba(16,185,129,0.1)';
            return makeGradient(ctx, chartArea);
          },
        }] },
        options: {
          responsive: true, animation: false, parsing: false,
          interaction: { mode: 'index', intersect: false },
          plugins: {
            legend: { display: false },
            decimation: { enabled: true, algorithm: 'lttb', samples: 80, threshold: 100 },
            tooltip: {
              backgroundColor: '#18181b', borderColor: '#27272a', borderWidth: 1,
              titleColor: '#a1a1aa', bodyColor: '#e5e5e5', padding: 8, displayColors: false,
              callbacks: {
                title: (items) => new Date(items[0].parsed.x).toLocaleString(),
                label: (item) => fmtMoney(item.parsed.y),
              }
            }
          },
          scales: {
            x: {
              type: 'time', time: { unit: RANGE_UNIT[rangeSec] || 'hour' },
              ticks: { color: '#52525b', maxTicksLimit: 6, font: { size: 10 } },
              grid: { display: false }, border: { display: false },
            },
            y: {
              ticks: { color: '#52525b', callback: v => fmtMoney(v), font: { size: 10 }, maxTicksLimit: 6 },
              grid: { color: '#18181b', drawTicks: false }, border: { display: false },
            },
          }
        }
      });
    } else {
      chart.data.datasets[0].data = data;
      chart.options.scales.x.time.unit = RANGE_UNIT[rangeSec] || 'hour';
      chart.update('none');
    }
  } catch (e) { console.error('chart error', e); }
}
document.querySelectorAll('.range-btn').forEach(b => {
  b.addEventListener('click', () => {
    refreshChart(parseInt(b.dataset.range));
    document.querySelectorAll('.range-btn').forEach(x => x.classList.remove('bg-emerald-700'));
    b.classList.add('bg-emerald-700');
  });
});
document.querySelectorAll('.pos-sort-btn').forEach(b => {
  b.addEventListener('click', () => {
    currentPosSort = b.dataset.sort;
    document.querySelectorAll('.pos-sort-btn').forEach(x => { x.classList.remove('bg-emerald-700'); x.classList.add('bg-zinc-800'); });
    b.classList.remove('bg-zinc-800');
    b.classList.add('bg-emerald-700');
    refreshPositions();
  });
});

// ── Live feed (SSE) ──
function fmtPx(v) { if (v == null) return '?'; return v < 1 ? v.toFixed(5) : v < 100 ? v.toFixed(3) : v.toFixed(2); }

function renderEvent(e) {
  const ts = fmtTime(e.ts || Date.now());
  const ev = e.event || '?';
  let glyph = '?', text = '', cls = ev, detail = '', tooltip = '';
  if (ev === 'loop_heartbeat') {
    glyph = '♥'; cls = 'heartbeat';
    text = `perp=$${(e.equity||0).toFixed(2)} avail=$${(e.available||0).toFixed(2)} daily=${(e.daily_pnl||0)>=0?'+':''}$${(e.daily_pnl||0).toFixed(2)} open=${e.open_positions||0}`;
  } else if (ev === 'scan') {
    glyph = '•'; cls = 'scan';
    // Prefer scored coin list if present (newer events); fall back to plain names.
    const scored = e.coin_scores || [];
    const coinsStr = scored.length
      ? scored.slice(0, 6).map(c => `${c.coin}(${c.score})`).join(', ') + (scored.length > 6 ? ` (+${scored.length-6})` : '')
      : ((e.coins || []).slice(0, 6).join(', ') + (e.coins?.length > 6 ? ` (+${e.coins.length-6})` : ''));
    text = `scan       ${e.triggers||0} triggers${coinsStr ? ' — ' + coinsStr : ''}`;
    if (scored.length) tooltip = scored.map(c => `${c.coin}: score ${c.score}` + (c.triggers?.length ? ` [${c.triggers.join(', ')}]` : '')).join('\\n');
  } else if (ev === 'ta_skip') {
    glyph = '✗'; cls = 'scan';
    const scoreNote = e.score != null ? ` ta=${e.score}` : '';
    const trigNote = e.trigger_score != null ? ` trig=${e.trigger_score}` : '';
    text = `ta_skip    ${e.coin} (${e.signal})${scoreNote}${trigNote}`;
  } else if (ev === 'research') {
    glyph = '?'; cls = 'research';
    text = `research   ${e.coin} → ${e.verdict} (conf ${e.confidence})`;
    // Inline preview of reasoning + entry/stop/tp when present.
    const priceTriad = (e.entry_px || e.stop_px || e.tp_px)
      ? ` · entry ${fmtPx(e.entry_px)}/sl ${fmtPx(e.stop_px)}/tp ${fmtPx(e.tp_px)}` : '';
    const reasonPreview = e.reasoning ? ` — ${e.reasoning.slice(0, 90)}${e.reasoning.length > 90 ? '…' : ''}` : '';
    detail = priceTriad + reasonPreview;
    if (e.reasoning && e.reasoning.length > 90) tooltip = e.reasoning;
  } else if (ev === 'execute') {
    const ok = e.executed;
    glyph = ok ? '✓' : '✗'; cls = ok ? 'execute' : 'execute-fail';
    if (ok) {
      const sz = e.size_usd != null ? ` $${e.size_usd.toFixed(2)}` : '';
      const ep = e.entry_px != null ? ` @ ${fmtPx(e.entry_px)}` : '';
      text = `execute    ${e.coin} ${e.side || '?'}${sz}${ep}  ${e.detail || ''}`;
      if (e.stop_px || e.tp_px) tooltip = `entry ${fmtPx(e.entry_px)}\nstop ${fmtPx(e.stop_px)}\ntp ${fmtPx(e.tp_px)}\nsize $${(e.size_usd||0).toFixed(2)}\norder ${e.detail || ''}`;
    } else {
      const blocked = Array.isArray(e.blocked_by) ? e.blocked_by.join(' · ') : (e.blocked_by || e.detail || '');
      text = `execute    ${e.coin} ${e.side || '?'}  BLOCKED: ${blocked}`;
      if (Array.isArray(e.blocked_by) && e.blocked_by.length > 1) tooltip = e.blocked_by.join('\\n');
    }
  } else if (ev === 'dsl_exit') {
    glyph = '⏹'; cls = 'dsl_exit';
    const side = e.side ? `${e.side} ` : '';
    const lev = e.leverage ? `${e.leverage}x ` : '';
    const pnlPct = e.realized_pnl_pct != null ? e.realized_pnl_pct : (e.unrealized_pct || 0);
    text = `dsl_exit   ${e.coin} ${side}${lev} ${e.reason}  (${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(2)}%)`;
    if (e.fill_px) tooltip = `entry ${fmtPx(e.entry_px)}\nfill ${fmtPx(e.fill_px)}\nspot ${(e.realized_spot_pct||0).toFixed(2)}%\nleveraged ${(pnlPct||0).toFixed(2)}%`;
  } else if (ev === 'error') {
    glyph = '!'; cls = 'error';
    text = `error      ${e.coin || e.scope || 'loop'}: ${(e.error || '').slice(0, 120)}`;
  } else if (ev === 'loop_start') {
    glyph = '▶'; text = `loop_start interval=${e.scan_interval}s min_score=${e.min_score}`;
  } else if (ev === 'loop_stop') {
    glyph = '■'; text = `loop_stop`;
  } else {
    text = `${ev}      ${JSON.stringify({...e, ts: undefined, event: undefined})}`;
  }
  const row = document.createElement('div');
  row.className = 'feed-row ' + cls;
  row.textContent = `[${ts}] ${glyph}  ${text}${detail}`;
  if (tooltip) row.title = tooltip;
  return row;
}

const feed = document.getElementById('feed');
const es = new EventSource('/api/feed/stream');
es.onmessage = (m) => {
  try {
    const e = JSON.parse(m.data);
    feed.appendChild(renderEvent(e));
    while (feed.childNodes.length > 200) feed.removeChild(feed.firstChild);
    feed.scrollTop = feed.scrollHeight;
    // Hamster reacts to executes (successful only) and DSL exits
    if (e.event === 'execute' && e.executed) {
      triggerHamsterReaction('execute');
    } else if (e.event === 'dsl_exit') {
      const pnlPct = e.realized_pnl_pct != null ? e.realized_pnl_pct : (e.unrealized_pct || 0);
      triggerHamsterReaction('dsl_exit', pnlPct);
    }
  } catch {}
};

// ── locale init + change handlers ──
async function initLocale() {
  const rates = await loadRates();
  const savedCcy = localStorage.getItem('hermes-ccy') || 'USD';
  const savedLang = localStorage.getItem('hermes-lang') || 'en';
  ccyState = { code: savedCcy, rate: rates[savedCcy] || 1 };
  langState = savedLang;
  const ccySel = document.getElementById('ccy-sel');
  const langSel = document.getElementById('lang-sel');
  if (ccySel) ccySel.value = savedCcy;
  if (langSel) langSel.value = savedLang;
  applyI18n();
}
document.getElementById('ccy-sel')?.addEventListener('change', async (e) => {
  const code = e.target.value;
  localStorage.setItem('hermes-ccy', code);
  const rates = await loadRates();
  ccyState = { code, rate: rates[code] || 1 };
  refreshSummary(); refreshPositions(); refreshCloses();
  if (chart) refreshChart(currentRange);
});
document.getElementById('lang-sel')?.addEventListener('change', (e) => {
  langState = e.target.value;
  localStorage.setItem('hermes-lang', langState);
  applyI18n();
});

// ── kickoff + polling ──
initLocale().then(() => {
  refreshSummary(); refreshPositions(); refreshCloses(); refreshChart(86400);
  rotateHamsterQuote();
});
setInterval(refreshSummary, 5000);
setInterval(refreshPositions, 15000);
setInterval(refreshCloses, 20000);
setInterval(() => refreshChart(currentRange), 60000);
setInterval(rotateHamsterQuote, 7000);
</script>
</body>
</html>
"""


_OPERATOR_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>hermes-trader · operator</title>
<script src="https://cdn.tailwindcss.com"></script>
<style>
  body{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;background:#0a0a0a;color:#e5e5e5}
  .btn{padding:6px 12px;border-radius:6px;background:#27272a;color:#e5e5e5;font-size:12px}
  .btn:hover{background:#3f3f46}
  .btn.danger{background:#7f1d1d;color:#fecaca}
  .btn.danger:hover{background:#991b1b}
  pre{font-size:11px;line-height:1.5}
</style>
</head>
<body class="min-h-screen">
<div class="max-w-5xl mx-auto px-4 py-6">

  <header class="flex items-center justify-between mb-6">
    <div class="flex items-baseline gap-3">
      <span class="text-lg font-bold tracking-tight">hermes-trader</span>
      <span class="text-xs text-amber-400">operator console</span>
    </div>
    <a href="/" class="text-xs text-zinc-400 hover:text-zinc-200">← public dashboard</a>
  </header>

  <section class="bg-zinc-900 rounded-lg p-4">
    <div class="text-xs text-zinc-500 mb-2">config (.agent-config.json)</div>
    <pre id="config" class="text-zinc-300 overflow-x-auto">loading…</pre>
  </section>

  <section class="bg-zinc-900 rounded-lg p-4">
    <div class="text-xs text-zinc-500 mb-2">positions — force close</div>
    <div id="positions" class="text-sm">loading…</div>
  </section>

  <section class="bg-zinc-900 rounded-lg p-4">
    <div class="text-xs text-zinc-500 mb-2">DSL trackers (in-memory + persisted)</div>
    <pre id="trackers" class="text-zinc-300 overflow-x-auto">loading…</pre>
  </section>

  <section class="bg-zinc-900 rounded-lg p-4">
    <div class="text-xs text-zinc-500 mb-2">danger zone</div>
    <button class="btn danger" onclick="setMode('OFF')">set mode OFF (halt new trades)</button>
    <button class="btn" onclick="setMode('LIVE')">set mode LIVE</button>
  </section>
</div>

<script>
const params = new URLSearchParams(location.search);
const token = params.get('token');
const auth = () => ({'X-Operator-Token': token || ''});

async function loadConfig() {
  const r = await fetch('/api/dashboard/operator/config', {headers: auth()});
  document.getElementById('config').textContent = JSON.stringify(await r.json(), null, 2);
}
async function loadTrackers() {
  const r = await fetch('/api/dashboard/operator/trackers', {headers: auth()});
  document.getElementById('trackers').textContent = JSON.stringify(await r.json(), null, 2);
}
async function loadPositions() {
  const r = await fetch('/api/dashboard/positions');
  const ps = await r.json();
  const el = document.getElementById('positions');
  if (!ps.length) { el.innerHTML = '<div class="text-zinc-500 text-xs">none</div>'; return; }
  el.innerHTML = ps.map(p => `<div class="flex items-center justify-between py-1 border-b border-zinc-800 last:border-0">
    <span><b>${p.coin}</b> ${p.side} ${p.size.toFixed(4)} @ ${p.entry_px.toFixed(2)} (${p.unrealized_pct >= 0 ? '+' : ''}${p.unrealized_pct.toFixed(2)}%)</span>
    <button class="btn danger" onclick="closeCoin('${p.coin}')">close</button>
  </div>`).join('');
}
async function closeCoin(coin) {
  if (!confirm('Force close ' + coin + '?')) return;
  const r = await fetch('/api/dashboard/operator/close', {
    method: 'POST', headers: {...auth(), 'Content-Type': 'application/json'},
    body: JSON.stringify({coin})
  });
  alert(JSON.stringify(await r.json(), null, 2));
  loadPositions();
}
async function setMode(mode) {
  if (mode === 'LIVE' && !confirm('Switch to LIVE mode?')) return;
  const r = await fetch('/api/dashboard/operator/mode', {
    method: 'POST', headers: {...auth(), 'Content-Type': 'application/json'},
    body: JSON.stringify({mode})
  });
  alert('mode → ' + (await r.json()).mode);
  loadConfig();
}

loadConfig(); loadTrackers(); loadPositions();
setInterval(loadTrackers, 10000);
setInterval(loadPositions, 10000);
</script>
</body>
</html>
"""


# ── route registration ──────────────────────────────────────────────────────


def register_routes(app: FastAPI) -> None:
    """Mount dashboard + SSE + operator routes onto an existing FastAPI app."""

    # no-store on both dashboards so a server restart isn't masked by a cached
    # HTML shell that pre-dates the new JS. The JSON endpoints below are fine
    # to cache for their poll interval.
    _NO_CACHE_HEADERS = {"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"}

    @app.get("/", response_class=HTMLResponse)
    async def public_dashboard() -> HTMLResponse:
        return HTMLResponse(content=_PUBLIC_HTML, headers=_NO_CACHE_HEADERS)

    @app.get("/operator", response_class=HTMLResponse)
    async def operator_console() -> HTMLResponse:
        # No token gate on the HTML itself — the page is a shell that calls
        # token-gated APIs. Without a valid ?token=… the AJAX calls 401 and the
        # page shows "loading…" with no data. Cheap defense, no auth library.
        return HTMLResponse(content=_OPERATOR_HTML, headers=_NO_CACHE_HEADERS)

    @app.get("/api/dashboard/summary")
    async def dashboard_summary() -> JSONResponse:
        return JSONResponse(_summary_payload())

    @app.get("/api/dashboard/positions")
    async def dashboard_positions() -> JSONResponse:
        return JSONResponse(_positions_payload())

    @app.get("/api/dashboard/equity-curve")
    async def dashboard_equity_curve(range_s: int = Query(86400, ge=60, le=2_592_000)) -> JSONResponse:
        return JSONResponse(_equity_curve_payload(range_s))

    @app.get("/api/dashboard/closed-trades")
    async def dashboard_closed_trades(limit: int = Query(20, ge=1, le=200)) -> JSONResponse:
        return JSONResponse(_closed_trades_payload(limit))

    @app.get("/api/feed/stream")
    async def feed_stream() -> StreamingResponse:
        return StreamingResponse(
            _tail_log_sse(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",  # disable nginx buffering
                "Connection": "keep-alive",
            },
        )

    # ── operator (token-gated) ──

    @app.get("/api/dashboard/operator/config")
    async def operator_config(request: Request) -> JSONResponse:
        _require_operator(request)
        return JSONResponse(read_agent_config())

    @app.get("/api/dashboard/operator/trackers")
    async def operator_trackers(request: Request) -> JSONResponse:
        _require_operator(request)
        dsl_exit.load_state(force=True)
        out = []
        for key, t in dsl_exit._active_positions.items():
            out.append({
                "key": key, "coin": t.coin, "side": t.side,
                "entry_px": t.entry_px, "peak_px": t.peak_px,
                "floor_px": t._last_floor, "entry_time": t.entry_time,
                "consecutive_breaches": t.consecutive_breaches,
            })
        return JSONResponse(out)

    @app.post("/api/dashboard/operator/close")
    async def operator_close(request: Request) -> JSONResponse:
        _require_operator(request)
        body = await request.json()
        coin = (body.get("coin") or "").upper()
        if not coin:
            raise HTTPException(400, "coin required")
        from hermes_trader.agents.executor import close_position_market
        return JSONResponse(close_position_market(coin))

    @app.post("/api/dashboard/operator/mode")
    async def operator_mode(request: Request) -> JSONResponse:
        _require_operator(request)
        from hermes_trader.agents.config_store import write_agent_config
        body = await request.json()
        mode = (body.get("mode") or "").upper()
        if mode not in {"OFF", "LIVE"}:
            raise HTTPException(400, "mode must be OFF or LIVE")
        cfg = read_agent_config()
        cfg["mode"] = mode
        write_agent_config(cfg)
        return JSONResponse({"mode": mode})
