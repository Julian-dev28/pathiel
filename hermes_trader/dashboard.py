"""Public + operator web UI for hermes-trader.

Pages (markup in hermes_trader/templates/*.html, assets vendored in /static):

  GET /                          — landing one-pager: how it works, equity +
                                   today's PnL, live-books table
  GET /activity                  — live activity feed with book/type filters
  GET /news                      — news-catalyst reads + research news context
  GET /config                    — live agent-config viewer
  GET /operator                  — operator console (token-gated APIs)

JSON APIs:

  GET /api/dashboard/summary     — hero numbers + status
  GET /api/dashboard/books       — live-books table rows
  GET /api/dashboard/activity    — classified session-log events (filterable)
  GET /api/dashboard/news        — news ledger reads, newest first
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
import collections
import os
import threading
import time
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from hermes_trader import session_log
from hermes_trader.agents import dsl_exit
from hermes_trader.agents.config_store import read_agent_config
from hermes_trader.client.hl_client import fetch_account_state, resolve_user_address
from hermes_trader.positions_snapshot import read_snapshot as read_position_snapshot

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

# Generic TTL cache for read-heavy dashboard endpoints. The dashboard polls
# every few seconds; without this each poll re-reads + re-parses the 800KB+
# session-log JSONL from disk. Keyed by (name, args) so parametrized
# endpoints (equity-curve range, closed-trades limit) cache per-variant.
_TTL_CACHE: Dict[str, tuple] = {}
_TTL_CACHE_LOCK = threading.Lock()


def _ttl_cached(key: str, ttl: float, fn):
    now = time.time()
    with _TTL_CACHE_LOCK:
        hit = _TTL_CACHE.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    val = fn()
    with _TTL_CACHE_LOCK:
        _TTL_CACHE[key] = (now, val)
    return val


_LOG_STATE: Dict[str, Any] = {"offset": 0, "ino": None,
                              "events": collections.deque(maxlen=250_000),
                              "lock": threading.Lock()}


def _read_log_lines() -> List[Dict[str, Any]]:
    """Incremental tail-parse of the session log.

    Audit 2026-07-10: the old full-file re-parse burned 0.83s CPU on the
    uvloop event-loop thread per cache miss against a 101MB log — 178
    CPU-hours and the page jank. Now only bytes appended since the last call
    are parsed; truncation/rotation resets the state. Bounded to the last
    250k events (~several days) — panels degrade gracefully past that."""
    if not _LOG_PATH.exists():
        return []
    st = _LOG_STATE
    with st["lock"]:
        try:
            stat = _LOG_PATH.stat()
            if stat.st_ino != st["ino"] or stat.st_size < st["offset"]:
                st["events"].clear()
                st["offset"] = 0
                st["ino"] = stat.st_ino
            if stat.st_size > st["offset"]:
                with _LOG_PATH.open("rb") as f:
                    f.seek(st["offset"])
                    chunk = f.read()
                last_nl = chunk.rfind(b"\n")
                if last_nl >= 0:
                    for line in chunk[:last_nl].split(b"\n"):
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            st["events"].append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
                    st["offset"] += last_nl + 1
        except OSError:
            pass
        return list(st["events"])


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

    # Heuristic status: heartbeat cadence is p50 ~90s / p99 ~420s on healthy
    # days, so 180s flickered "stale" on ordinary cycles (audit 2026-07-10).
    if not heartbeat:
        status = "offline"
    elif last_tick_age_s is None or last_tick_age_s > 300:
        status = "stale"
    else:
        status = "scanning"

    # Per-dex breakdowns so the dashboard can show where USDC sits
    # (e.g. main $96 + xyz $114 + km $20) instead of one opaque total.
    dex_equity = heartbeat.get("dex_equity") or {}
    dex_available = heartbeat.get("dex_available") or {}

    return {
        "equity": round(equity, 2),
        "available": round(float(heartbeat.get("available", 0) or 0), 2),
        "dex_equity": dex_equity,
        "dex_available": dex_available,
        "spot_usdc": round(float(heartbeat.get("spot_usdc", 0) or 0), 2),
        "daily_pnl": round(daily_pnl, 2),
        "daily_pnl_pct": round(daily_pnl_pct, 2),
        "open_positions": int(heartbeat.get("open_positions", 0) or 0),
        "last_tick_age_s": last_tick_age_s,
        "last_scan_triggers": int((last_scan or {}).get("triggers", 0) or 0),
        "status": status,
        "ts": now_ms,
    }


_POSITIONS_CACHE: Dict[str, Any] = {"ts": 0.0, "data": []}
_POSITIONS_CACHE_TTL_S = 5.0  # acceptable staleness for a display endpoint


def _positions_payload() -> List[Dict[str, Any]]:
    """Join live HL positions with DSL tracker state for the operator/public view.

    Cached for ~5s so repeated dashboard polls don't hammer HL with
    fetch_account_state(include_hip3=True) — each call is ~9 HTTP POSTs
    (1 main + 8 HIP-3 dexes) even with the parallel fan-out. Cache TTL
    is short enough that the position table never feels stuck.
    """
    now = time.time()
    if now - _POSITIONS_CACHE["ts"] < _POSITIONS_CACHE_TTL_S:
        return _POSITIONS_CACHE["data"]
    data = _positions_payload_uncached()
    _POSITIONS_CACHE["ts"] = now
    _POSITIONS_CACHE["data"] = data
    return data


def _positions_payload_uncached() -> List[Dict[str, Any]]:
    dsl_exit.load_state(force=True)
    # The loop process writes each position's entry context (book + open reason) to disk;
    # the dashboard runs in a SEPARATE process whose memory is frozen at startup, so re-read
    # the entry-context map here to surface the 'why this opened' line for live positions.
    try:
        from hermes_trader.agents.memory import memory as _mem
        _mem.reload_entry_ctx()
    except Exception:
        pass

    # Prefer the loop's snapshot: it already fetched account state this cycle,
    # so reading the file avoids a duplicate fetch_account_state (~9 HL POSTs)
    # from this separate process — that duplication was tripping HL's per-IP
    # rate limit. Fall back to a live fetch only when the snapshot is missing
    # or stale (loop not running), so a standalone dashboard still works.
    snap = read_position_snapshot(max_age_s=120.0)
    if snap is not None:
        return _rows_from_state(snap)

    user = resolve_user_address()
    if not user:
        return []
    try:
        # include_hip3=True so xyz:MU / vntl:* positions appear in the
        # dashboard list alongside main-dex positions; HIP-3 dexes are
        # separate clearinghouses that the default fetch ignores.
        state = fetch_account_state(user, include_hip3=True)
    except Exception:
        return []
    return _rows_from_state(state)


def _rows_from_state(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Transform a raw HL account state into dashboard position rows, overlaying
    DSL tracker phase/floor from the shared state file. Pure — no network."""
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

        # "why is this open" — the originating book + signal, captured at entry.
        _ec = {}
        try:
            from hermes_trader.agents.memory import memory as _mem
            _ec = _mem.peek_entry_context(coin, side) or {}
        except Exception:
            _ec = {}
        _reason = str(_ec.get("reason") or "").replace('"', "'").replace("<", "(").replace(">", ")")[:160]
        _book = str(_ec.get("book") or "")

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
            "open_reason": _reason,          # why this opened (book + signal)
            "open_book": _book,
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
                "pnl_pct": None,     # not recorded in the session log — do not fake 0.0
                "spot_pct": None,
                "executed": bool(e.get("ok")),
                "detail": None,
            })
        if len(out) >= limit:
            break
    return out


def _equity_curve_payload(range_s: int) -> List[Dict[str, Any]]:
    """Series of (ts, equity) points from loop_heartbeat events within `range_s`.

    Filters PARTIAL-DEX degraded reads: a heartbeat that momentarily failed to
    fetch a HIP-3 dex reports equity far below trend (main-dex-only, e.g. $88 vs
    the real $220 aggregate). On a 7d/30d view those show as the account crashing
    to ~$20 and back, and they crush the y-axis. Capped positions can't lose tens
    of % in one 60s tick, so a point far below the TRAILING median of accepted
    points is a bad read, not a real move — and using the *trailing* (not global)
    median preserves genuine gradual growth across the window.
    """
    from statistics import median

    cutoff = int(time.time() * 1000) - range_s * 1000
    raw: List[tuple] = []
    for e in _read_log_lines():
        if e.get("event") != "loop_heartbeat":
            continue
        if e.get("ts", 0) < cutoff:
            continue
        eq = float(e.get("equity", 0) or 0)
        if eq <= 0:
            continue
        raw.append((e["ts"], eq))

    series: List[Dict[str, Any]] = []
    window: List[float] = []  # last N accepted equities (trailing reference)
    rejected_streak = 0
    for ts, eq in raw:
        ref = median(window) if window else eq
        if window and eq < 0.7 * ref:
            # Partial-dex degraded reads are SPIKES (1-2 ticks). A real crash
            # re-asserts — after 3 consecutive sub-70% readings, believe it
            # (the old unconditional drop froze the curve forever after any
            # genuine >30% loss; audit 2026-07-10).
            rejected_streak += 1
            if rejected_streak < 3:
                continue
            window.clear()          # re-anchor on the new regime
        rejected_streak = 0
        series.append({"ts": ts, "equity": round(eq, 2)})
        window.append(eq)
        if len(window) > 15:
            window.pop(0)
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


# ── live books ───────────────────────────────────────────────────────────────
# (name, config key, thesis one-liner). Sizing + live/shadow status come from
# the live .agent-config.json at request time, so a shadow flip shows on the
# next poll without a server restart. mover_pass nests under
# mover_recorders.pass_live (config key None → special-cased below).

_BOOKS: List[tuple] = [
    ("xs_momentum", "xs_momentum",
     "Cross-sectional momentum basket — long the strongest, short the weakest "
     "of the top-50 by 7d return, vol-managed rebalance."),
    ("extreme_fade", "extreme_fade",
     "Fade single-day crashes of -12% or worse — long the panic with a wide "
     "20% stop, 3-day hold."),
    ("rally_exhaustion", "rally_exhaustion",
     "Short a +12%/2d rally when BTC is in a downtape — wide stop, $20M "
     "volume floor."),
    ("crash_continue_div_short", "crash_continue_div_short",
     "BTC up while a coin bleeds -8%/2d — short the divergent laggard's "
     "continuation."),
    ("engulf_short", "engulf_short",
     "Bearish daily engulfing candle on a liquid coin — short the next day."),
    ("neg_funding_fade", "neg_funding_fade",
     "Deep negative funding plus a green volume influx — short the failed pop."),
    ("funding_spike_short", "funding_spike_short",
     "Funding-rate z-score spike above 2 — short until funding normalizes."),
    ("majors_swing", "majors_swing",
     "Trend-aligned pullback-resume swings on majors (BTC, ETH, SOL, SP500)."),
    ("young_listings", "young_listings",
     "Fresh xyz listings under the liquidity floor — bounded early-momentum lane."),
    ("unlock_short_runin", "unlock_short",
     "Short the run-in 48-72h before large token unlocks (>=1% of circulating)."),
    ("news_catalyst", "news_catalyst",
     "News-volume surge on a coin's headlines — ride the catalyst for a 1-day hold."),
    ("mover_pass", None,
     "Counterfactual recorder — tracks daily movers the AI passed on, so "
     "vetoes stay measurable."),
]

_KNOWN_BOOK_NAMES = frozenset(name for name, _, _ in _BOOKS)


def _book_size_str(cfg: Dict[str, Any]) -> str:
    """Human sizing line from a book's config block. Deterministic — no guessing."""
    lev = cfg.get("leverage")
    if cfg.get("notional_usd"):
        base = f"${float(cfg['notional_usd']):g}"
    elif cfg.get("equity_fraction"):
        base = f"{float(cfg['equity_fraction']):g}x eq"
    elif cfg.get("k_per_leg"):
        return f"{int(cfg['k_per_leg'])}/leg basket"
    else:
        return "—"
    try:
        return f"{base} @ {int(lev)}x" if lev else base
    except (TypeError, ValueError):
        return base


def _books_payload() -> List[Dict[str, Any]]:
    """Rows for the landing-page live-books table: name, status, size, thesis."""
    try:
        config = read_agent_config() or {}
    except Exception:
        config = {}
    out: List[Dict[str, Any]] = []
    for name, cfg_key, thesis in _BOOKS:
        if cfg_key is None:  # mover_pass lives at mover_recorders.pass_live
            cfg = (config.get("mover_recorders") or {}).get("pass_live") or {}
        else:
            cfg = config.get(cfg_key) or {}
        if not isinstance(cfg, dict) or not cfg or not cfg.get("enabled", False):
            status = "off"
        elif cfg.get("shadow_only"):
            status = "shadow"
        else:
            status = "live"
        out.append({"name": name, "status": status,
                    "size": _book_size_str(cfg if isinstance(cfg, dict) else {}),
                    "thesis": thesis})
    return out


# ── activity feed ────────────────────────────────────────────────────────────
# The session log is a mixed bag of event shapes. The classifier below maps
# every raw line into ONE of a fixed set of typed view models so the /activity
# page never has to render raw JSON. Unknown shapes degrade to type "other"
# with a fields dict the client renders as key-value rows.

# Legacy/aliased event names that are really book events.
_EVENT_BOOK_ALIASES = {
    "extreme_fade_candidates": "extreme_fade",
    "xs_rebalance": "xs_momentum",
}

_ACTIVITY_TYPES = ["book", "research", "execute", "close", "scan",
                   "gate", "error", "heartbeat", "system", "other"]

# Classify at most the newest N events per cache miss. The deque holds up to
# 250k events; walking all of them for a rare filter would block the event
# loop for ~0.25s. 20k ≈ a day of activity — plenty for a feed page.
_ACTIVITY_SCAN_CAP = 20_000


def _classify_event(e: Dict[str, Any]) -> Dict[str, Any]:
    """Map one raw session-log event to a typed activity view model. Pure."""
    ev = str(e.get("event") or "?")
    ts = e.get("ts")

    if ev == "research":
        return {
            "type": "research", "ts": ts, "coin": e.get("coin"),
            "verdict": e.get("verdict"), "confidence": e.get("confidence"),
            "reasoning": e.get("reasoning"),
            "provider": e.get("ai_brain_provider"),
            "web_search_used": bool(e.get("web_search_used")),
            "citations": e.get("web_search_citations") or [],
            "news_risk": e.get("news_risk"),
            "entry_px": e.get("entry_px"), "stop_px": e.get("stop_px"),
            "tp_px": e.get("tp_px"),
        }

    if ev == "execute":
        blocked = e.get("blocked_by")
        if isinstance(blocked, str):
            blocked = [blocked]
        detail = e.get("detail")
        if isinstance(detail, list):
            detail = " · ".join(str(d) for d in detail)
        return {
            "type": "execute", "ts": ts, "coin": e.get("coin"),
            "side": e.get("side"), "executed": bool(e.get("executed")),
            "book": e.get("book"), "entry_via": e.get("entry_via"),
            "ai_verdict": e.get("ai_verdict"),
            "size_usd": e.get("size_usd"), "entry_px": e.get("entry_px"),
            "stop_px": e.get("stop_px"), "tp_px": e.get("tp_px"),
            "gates": [str(b) for b in (blocked or [])],
            "detail": detail if isinstance(detail, str) else None,
            "regime": e.get("regime"),
        }

    if ev == "dsl_exit":
        pnl = e.get("realized_pnl_pct")
        if pnl is None:
            pnl = e.get("leveraged_pct")
        if pnl is None:
            pnl = e.get("unrealized_pct")
        return {
            "type": "close", "ts": ts, "coin": e.get("coin"),
            "side": e.get("side"), "leverage": e.get("leverage"),
            "reason": e.get("reason"), "pnl_pct": pnl,
            "spot_pct": e.get("realized_spot_pct", e.get("unrealized_pct")),
            "fill_px": e.get("fill_px"), "entry_px": e.get("entry_px"),
            "executed": bool(e.get("executed")), "source": "dsl",
        }

    if ev == "ai_close":
        return {
            "type": "close", "ts": ts, "coin": e.get("coin"),
            "side": e.get("side"), "leverage": None,
            "reason": e.get("reasoning") or "ai close",
            "pnl_pct": None, "spot_pct": None,
            "fill_px": None, "entry_px": None,
            "executed": bool(e.get("executed")), "source": "ai_close",
        }

    if ev == "book_open":
        extra = {k: v for k, v in e.items()
                 if k not in ("ts", "event", "book", "coin", "side")}
        return {"type": "book", "subtype": "open", "ts": ts,
                "book": e.get("book"), "coin": e.get("coin"),
                "side": e.get("side"), "extra": extra}

    book = _EVENT_BOOK_ALIASES.get(ev, ev)
    if (book in _KNOWN_BOOK_NAMES or isinstance(e.get("skipped"), dict)
            or "candidates" in e or ("signals" in e and "opened" in e)):
        core = {"ts", "event", "shadow", "signals", "opened", "skipped", "candidates"}
        extra = {k: v for k, v in e.items() if k not in core}
        return {
            "type": "book", "ts": ts, "book": book,
            "shadow": e.get("shadow"),
            "signals": e.get("signals"), "opened": e.get("opened"),
            "skipped": e.get("skipped") or {},
            "candidates": e.get("candidates") or [],
            "extra": extra,
        }

    if ev == "scan":
        coins = e.get("coin_scores") or e.get("coins") or []
        return {"type": "scan", "ts": ts,
                "triggers": e.get("triggers", e.get("perceptions", 0)),
                "coins": coins}

    if ev in ("ta_skip", "entry_preflight"):
        return {"type": "gate", "ts": ts, "kind": ev, "coin": e.get("coin"),
                "reason": e.get("reason") or e.get("signal"),
                "score": e.get("score"), "trigger_score": e.get("trigger_score")}

    if ev == "error":
        return {"type": "error", "ts": ts,
                "scope": e.get("coin") or e.get("scope"),
                "error": str(e.get("error") or "")[:300]}

    if ev == "loop_heartbeat":
        return {"type": "heartbeat", "ts": ts,
                "equity": e.get("equity"), "daily_pnl": e.get("daily_pnl"),
                "open_positions": e.get("open_positions")}

    if ev in ("loop_start", "loop_stop"):
        fields = {k: v for k, v in e.items() if k not in ("ts", "event")}
        return {"type": "system", "ts": ts, "name": ev, "fields": fields}

    # Unknown shape → graceful key-value rendering, never raw JSON.
    fields = {k: v for k, v in e.items() if k not in ("ts", "event")}
    return {"type": "other", "ts": ts, "name": ev, "fields": fields}


def _activity_payload(limit: int = 150, book: str = "", etype: str = "",
                      since_ts: int = 0) -> Dict[str, Any]:
    """Newest-first classified events, optionally filtered by book / type.

    `since_ts` > 0 returns only events strictly newer — the incremental-poll
    path the /activity page uses to PREPEND fresh rows instead of re-rendering
    the whole stream. Log ts stamps are wall-clock at append time, so once the
    reversed walk reaches an event at/older than since_ts it stops:
    incremental polls cost O(new events), not O(window).
    """
    events = _read_log_lines()
    if len(events) > _ACTIVITY_SCAN_CAP:
        events = events[-_ACTIVITY_SCAN_CAP:]
    out: List[Dict[str, Any]] = []
    for e in reversed(events):
        if since_ts and (e.get("ts") or 0) <= since_ts:
            break
        c = _classify_event(e)
        if etype and c.get("type") != etype:
            continue
        if book and c.get("book") != book:
            continue
        out.append(c)
        if len(out) >= limit:
            break
    return {"events": out,
            "books": sorted(_KNOWN_BOOK_NAMES),
            "types": _ACTIVITY_TYPES}


# ── news feed ────────────────────────────────────────────────────────────────


def _news_payload(limit: int = 50) -> Dict[str, Any]:
    """News-catalyst shadow-ledger reads (newest first, breaking flagged) plus
    recent research events that carried news context (citations / news_risk)."""
    from hermes_trader.agents import shadow_ledger

    rows = shadow_ledger.load("news_catalyst")
    rows.sort(key=lambda r: int(r.get("ts") or 0))
    items: List[Dict[str, Any]] = []
    for r in reversed(rows):
        meta = r.get("meta") or {}
        items.append({
            "ts": r.get("ts"), "coin": r.get("coin"), "side": r.get("side"),
            "entry_ref_px": r.get("entry_ref_px"),
            "n_recent": meta.get("n_recent"), "surge_x": meta.get("surge_x"),
            "breaking": bool(meta.get("breaking")),
            "titles": meta.get("top3_titles") or [],
            "shadow": meta.get("shadow"),
        })
        if len(items) >= limit:
            break

    ctx: List[Dict[str, Any]] = []
    events = _read_log_lines()
    if len(events) > _ACTIVITY_SCAN_CAP:
        events = events[-_ACTIVITY_SCAN_CAP:]
    for e in reversed(events):
        if e.get("event") != "research":
            continue
        cites = e.get("web_search_citations") or []
        risk = e.get("news_risk")
        if not cites and (not risk or risk == "none"):
            continue
        ctx.append({
            "ts": e.get("ts"), "coin": e.get("coin"),
            "verdict": e.get("verdict"), "confidence": e.get("confidence"),
            "news_risk": risk, "citations": cites,
            "reasoning": e.get("reasoning"),
            "provider": e.get("ai_brain_provider"),
        })
        if len(ctx) >= 20:
            break

    return {"items": items, "research_context": ctx}


# ── HTML ─────────────────────────────────────────────────────────────────────
# Page markup lives in hermes_trader/templates/*.html — plain files, no
# template engine, fully self-contained assets (vendored /static only).
# Loaded once at import; the pages are static shells that poll the JSON APIs.

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


def _load_template(name: str) -> str:
    return (_TEMPLATE_DIR / name).read_text(encoding="utf-8")


_PUBLIC_HTML = _load_template("landing.html")
_ACTIVITY_HTML = _load_template("activity.html")
_NEWS_HTML = _load_template("news.html")


_CONFIG_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>hermes-trader · config</title>
<script src="/static/tailwind.js"></script>
<style>
  body{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;background:#0a0a0a;color:#e5e5e5}
  .pixel{font-family:'Press Start 2P',ui-monospace,monospace;letter-spacing:.02em;line-height:1.4}
  .lcd{background:#052e1c;border:2px solid #34d399;box-shadow:inset 0 0 0 1px #022c1e,4px 4px 0 #064e3b;padding:8px 12px;color:#6ee7b7;text-shadow:0 0 6px #34d39966}
  section.bg-zinc-900{border:2px solid #27272a;box-shadow:4px 4px 0 #18181b;border-radius:0;background:#0f0f10}
  /* Config rows render as a two-col grid: pixel-font key on the left,
     value (color-coded by type) on the right. */
  .cfg-grid{display:grid;grid-template-columns:minmax(220px,32%) 1fr;gap:6px 16px;align-items:baseline}
  .cfg-key{font-family:'Press Start 2P',monospace;font-size:9px;color:#34d399;text-shadow:0 0 4px rgba(52,211,153,0.45);padding:6px 0;letter-spacing:.06em;word-break:break-all}
  .cfg-val{font-family:ui-monospace,monospace;font-size:13px;padding:6px 0;border-left:2px solid #1f2937;padding-left:14px;word-break:break-word}
  .cfg-val.num{color:#a7f3d0}
  .cfg-val.bool{color:#fde68a}
  .cfg-val.str{color:#bae6fd}
  .cfg-val.null{color:#71717a;font-style:italic}
  .cfg-val.obj{color:#f9a8d4}
  .cfg-val pre{margin:0;font-family:ui-monospace,monospace;font-size:11px;white-space:pre-wrap;color:#e5e5e5;background:#020a05;border:1px solid #064e3b;padding:6px 8px;max-width:100%;overflow-x:auto}
  /* Section break inside the cfg grid */
  .cfg-section-head{grid-column:1/-1;font-family:'Press Start 2P',monospace;font-size:8px;color:#71717a;letter-spacing:.2em;padding:12px 0 4px;border-top:1px solid #1f2937;margin-top:8px}
  .cfg-section-head:first-child{border-top:0;margin-top:0;padding-top:4px}
  /* Tip pill at the bottom */
  .cfg-tip{font-family:'Press Start 2P',monospace;font-size:9px;color:#fbbf24;letter-spacing:.06em;text-align:center;padding:8px;margin-top:14px;border:2px dashed #78350f;background:#1f1300}
  /* Mode pill */
  .cfg-mode{display:inline-flex;align-items:center;gap:6px;padding:6px 12px;font-family:'Press Start 2P',monospace;font-size:10px;border:2px solid currentColor;letter-spacing:.1em}
  .cfg-mode.LIVE{background:#064e3b;color:#6ee7b7}
  .cfg-mode.OFF{background:#450a0a;color:#fca5a5}
  /* Primary navbar — must match dashboard.html's nav-link rules */
  .nav-link{display:inline-block;padding:7px 11px;font-size:9px;letter-spacing:.12em;color:#a3a3a3;background:#18181b;border:2px solid #3f3f46;box-shadow:2px 2px 0 #0a0a0a;text-decoration:none;transition:transform .08s ease,box-shadow .08s ease}
  .nav-link:hover{color:#a7f3d0;border-color:#047857;box-shadow:2px 2px 0 #022c1e}
  .nav-link:active{transform:translate(2px,2px);box-shadow:none}
  .nav-link.nav-active{background:#064e3b;color:#6ee7b7;border-color:#34d399;box-shadow:2px 2px 0 #022c1e}
</style>
</head>
<body class="min-h-screen">
<div class="max-w-[1100px] mx-auto px-6 py-6">

  <header class="flex items-center justify-between mb-3 gap-3 flex-wrap">
    <div class="flex items-center gap-3">
      <span class="lcd pixel text-sm tracking-tight">HERMES-TRADER · CONFIG</span>
    </div>
  </header>

  <nav class="flex items-center gap-2 mb-6 flex-wrap" id="hermes-nav">
    <a href="/" data-nav="/" class="nav-link pixel">DASHBOARD</a>
    <a href="/activity" data-nav="/activity" class="nav-link pixel">ACTIVITY</a>
    <a href="/news" data-nav="/news" class="nav-link pixel">NEWS</a>
    <a href="/config" data-nav="/config" class="nav-link pixel">CONFIG</a>
    <a href="/operator" data-nav="/operator" class="nav-link pixel">OPERATOR</a>
  </nav>

  <section class="bg-zinc-900 p-6 mb-6">
    <div class="flex items-center justify-between mb-4">
      <span class="pixel text-[10px] text-zinc-500">.agent-config.json (live, hot-reloaded every cycle)</span>
      <span id="cfg-mode-pill" class="cfg-mode OFF">—</span>
    </div>
    <div id="cfg-grid" class="cfg-grid">
      <div class="cfg-section-head">loading…</div>
    </div>
    <div class="cfg-tip">
      to change a value: edit .agent-config.json (hot-reloaded every cycle) or POST the operator terminal API: `set &lt;key&gt; &lt;value&gt;`
    </div>
  </section>

  <footer class="text-[10px] text-zinc-600 mt-6 text-center pixel">
    one wallet · live · not financial advice
  </footer>
</div>

<script>
// Group the live agent config into named sections for readability. Anything
// not in the explicit grouping falls into "other" so future config keys
// still appear without code changes.
const SECTIONS = [
  { label: 'mode + sizing', keys: ['mode','equity_fraction_per_trade','leverage','max_concurrent','max_trade_notional_usd','asset_notional_multiplier','max_total_notional_pct'] },
  { label: 'safety',        keys: ['max_daily_loss_usd','cooldown_min','min_ai_confidence','counter_regime_min_conf','max_crypto_long_correlated'] },
  { label: 'liquidity',     keys: ['min_market_volume_usd','min_hip3_volume_usd'] },
  { label: 'filters',       keys: ['coin_allowlist','coin_blocklist'] },
  { label: 'markets',       keys: ['enable_crypto','enable_hip3'] },
  { label: 'dsl exit',      keys: ['dsl_exit'] },
];
const SECTION_KEYS = new Set(SECTIONS.flatMap(s => s.keys));

function classifyVal(v) {
  if (v === null || v === undefined) return 'null';
  const t = typeof v;
  if (t === 'number') return 'num';
  if (t === 'boolean') return 'bool';
  if (t === 'string') return 'str';
  return 'obj';
}
function formatVal(v) {
  if (v === null || v === undefined) return 'null';
  if (typeof v === 'object') return `<pre>${JSON.stringify(v, null, 2)}</pre>`;
  if (typeof v === 'string') return `"${v}"`;
  return String(v);
}

async function loadConfig() {
  try {
    const r = await fetch('/api/dashboard/config');
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const cfg = await r.json();
    const grid = document.getElementById('cfg-grid');
    grid.innerHTML = '';
    // Mode pill at the top
    const mode = cfg.mode || 'OFF';
    const pill = document.getElementById('cfg-mode-pill');
    pill.textContent = '◆ ' + mode;
    pill.className = 'cfg-mode ' + (mode === 'LIVE' ? 'LIVE' : 'OFF');
    // Render section by section
    const renderSection = (label, keys) => {
      const present = keys.filter(k => k in cfg);
      if (!present.length) return;
      const head = document.createElement('div');
      head.className = 'cfg-section-head';
      head.textContent = '── ' + label + ' ──';
      grid.appendChild(head);
      for (const k of present) {
        const keyEl = document.createElement('div'); keyEl.className = 'cfg-key'; keyEl.textContent = k;
        const valEl = document.createElement('div'); valEl.className = 'cfg-val ' + classifyVal(cfg[k]);
        valEl.innerHTML = formatVal(cfg[k]);
        grid.appendChild(keyEl); grid.appendChild(valEl);
      }
    };
    for (const s of SECTIONS) renderSection(s.label, s.keys);
    // "other" — anything not in the grouping
    const otherKeys = Object.keys(cfg).filter(k => !SECTION_KEYS.has(k));
    if (otherKeys.length) renderSection('other', otherKeys);
  } catch (e) {
    document.getElementById('cfg-grid').innerHTML =
      '<div class="cfg-section-head">load failed: ' + (e.message || e) + '</div>';
  }
}

loadConfig();
setInterval(loadConfig, 5000); // hot-reloads alongside the trading loop

// Highlight the active page. Token stays in localStorage only — never
// appended to nav URLs (audit 2026-07-10: ?token= leaked into history).
(function(){
  const here = window.location.pathname.replace(/\\/$/, '') || '/';
  document.querySelectorAll('a[data-nav]').forEach(a => {
    if (a.dataset.nav === here) a.classList.add('nav-active');
  });
})();
</script>
</body>
</html>
"""


_OPERATOR_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>hermes-trader · operator</title>
<script src="/static/tailwind.js"></script>
<style>
  body{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;background:#0a0a0a;color:#e5e5e5}
  .pixel{font-family:'Press Start 2P',ui-monospace,monospace;letter-spacing:.02em;line-height:1.4}
  .lcd{background:#052e1c;border:2px solid #34d399;box-shadow:inset 0 0 0 1px #022c1e,4px 4px 0 #064e3b;padding:8px 12px;color:#6ee7b7;text-shadow:0 0 6px #34d39966}
  section.bg-zinc-900{border:2px solid #27272a;box-shadow:4px 4px 0 #18181b;border-radius:0;background:#0f0f10}
  .btn{padding:6px 12px;border-radius:6px;background:#27272a;color:#e5e5e5;font-size:12px}
  .btn:hover{background:#3f3f46}
  .btn.danger{background:#7f1d1d;color:#fecaca}
  .btn.danger:hover{background:#991b1b}
  pre{font-size:11px;line-height:1.5}
  /* Primary navbar (mirrors / and /config) */
  .nav-link{display:inline-block;padding:7px 11px;font-size:9px;letter-spacing:.12em;color:#a3a3a3;background:#18181b;border:2px solid #3f3f46;box-shadow:2px 2px 0 #0a0a0a;text-decoration:none;transition:transform .08s ease,box-shadow .08s ease}
  .nav-link:hover{color:#a7f3d0;border-color:#047857;box-shadow:2px 2px 0 #022c1e}
  .nav-link:active{transform:translate(2px,2px);box-shadow:none}
  .nav-link.nav-active{background:#064e3b;color:#6ee7b7;border-color:#34d399;box-shadow:2px 2px 0 #022c1e}
  .op-banner{font-family:'Press Start 2P',monospace;font-size:9px;color:#fbbf24;text-align:center;padding:6px;border:2px dashed #78350f;background:#1f1300;margin-bottom:14px;letter-spacing:.06em}
  .op-banner.op-ok{color:#6ee7b7;border-color:#047857;background:#022c1e}
</style>
</head>
<body class="min-h-screen">
<div class="max-w-[1100px] mx-auto px-6 py-6">

  <header class="flex items-center justify-between mb-3 gap-3 flex-wrap">
    <div class="flex items-center gap-3">
      <span class="lcd pixel text-sm tracking-tight">HERMES-TRADER · OPERATOR</span>
    </div>
    <button id="op-token-btn" class="btn" title="paste/clear HERMES_OPERATOR_TOKEN (localStorage only)">set token</button>
  </header>

  <nav class="flex items-center gap-2 mb-4 flex-wrap" id="hermes-nav">
    <a href="/" data-nav="/" class="nav-link pixel">DASHBOARD</a>
    <a href="/activity" data-nav="/activity" class="nav-link pixel">ACTIVITY</a>
    <a href="/news" data-nav="/news" class="nav-link pixel">NEWS</a>
    <a href="/config" data-nav="/config" class="nav-link pixel">CONFIG</a>
    <a href="/operator" data-nav="/operator" class="nav-link pixel">OPERATOR</a>
  </nav>

  <div id="op-banner" class="op-banner">checking operator token…</div>

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
// Token resolution mirrors the public dashboard: ?token= in URL wins,
// then localStorage `hermes-op-token`, else empty. If a fresh URL token
// is present, persist it so navigating between pages keeps the session.
const params = new URLSearchParams(location.search);
const tokenFromUrl = params.get('token') || '';
const tokenFromStore = localStorage.getItem('hermes-op-token') || '';
const token = tokenFromUrl || tokenFromStore;
if (tokenFromUrl) localStorage.setItem('hermes-op-token', tokenFromUrl);
const auth = () => ({'X-Operator-Token': token || ''});

function setBanner(msg, ok) {
  const el = document.getElementById('op-banner');
  if (!el) return;
  el.textContent = msg;
  el.className = 'op-banner' + (ok ? ' op-ok' : '');
}

// Highlight the active page in the navbar. Token stays in localStorage only —
// never appended to nav URLs (audit 2026-07-10: ?token= leaked into history).
(function(){
  const here = window.location.pathname.replace(/\\/$/, '') || '/';
  document.querySelectorAll('a[data-nav]').forEach(a => {
    if (a.dataset.nav === here) a.classList.add('nav-active');
  });
})();

if (!token) {
  setBanner('NO TOKEN · click "set token" to paste HERMES_OPERATOR_TOKEN', false);
} else {
  setBanner('operator session ACTIVE · token loaded', true);
}

// Token entry lives here now (the landing page no longer has operator chrome).
// Stored ONLY in this browser's localStorage; sent as X-Operator-Token.
document.getElementById('op-token-btn')?.addEventListener('click', () => {
  const current = localStorage.getItem('hermes-op-token') || '';
  if (current) {
    if (confirm('Clear operator token and revert to read-only?')) {
      localStorage.removeItem('hermes-op-token');
      location.reload();
    }
    return;
  }
  const t = prompt('Paste your HERMES_OPERATOR_TOKEN:\\n(stored only in this browser via localStorage)');
  if (!t || !t.trim()) return;
  localStorage.setItem('hermes-op-token', t.trim());
  location.reload();
});

// Config dump moved to its own /config page (linked in the navbar above) —
// the operator console focuses on actions (close, set mode) and live state.
async function loadTrackers() {
  if (!token) return;
  const r = await fetch('/api/dashboard/operator/trackers', {headers: auth()});
  if (r.status === 401) { setBanner('TOKEN REJECTED by server (401) · re-enter via 🔒 op', false); return; }
  const data = await r.json();
  const el = document.getElementById('trackers');
  if (!Array.isArray(data) || data.length === 0) {
    el.textContent = 'no active DSL trackers — nothing currently being managed.\n(this is normal when 0 positions are open.)';
    el.style.color = '#71717a';
    el.style.fontStyle = 'italic';
  } else {
    el.textContent = JSON.stringify(data, null, 2);
    el.style.color = '';
    el.style.fontStyle = '';
  }
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
}

loadTrackers(); loadPositions();
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

    @app.get("/config", response_class=HTMLResponse)
    async def config_page() -> HTMLResponse:
        """Live agent-config viewer. Read-only — mutations happen via the
        Cmd+K terminal's `set <key> <value>` command."""
        return HTMLResponse(content=_CONFIG_HTML, headers=_NO_CACHE_HEADERS)

    @app.get("/activity", response_class=HTMLResponse)
    async def activity_page() -> HTMLResponse:
        """Live activity feed — research verdicts, executions with gate
        results, book events, DSL closes. Filterable by book and event type."""
        return HTMLResponse(content=_ACTIVITY_HTML, headers=_NO_CACHE_HEADERS)

    @app.get("/news", response_class=HTMLResponse)
    async def news_page() -> HTMLResponse:
        """News-catalyst reads (shadow ledger) + research events with news context."""
        return HTMLResponse(content=_NEWS_HTML, headers=_NO_CACHE_HEADERS)

    @app.get("/api/dashboard/config")
    async def dashboard_config() -> JSONResponse:
        """Read-only JSON dump of `.agent-config.json` for the /config page.
        Hot-reloads alongside the trading loop (no caching)."""
        return JSONResponse(read_agent_config())

    @app.get("/api/dashboard/books")
    async def dashboard_books() -> JSONResponse:
        """Live-books table rows: name, live/shadow/off status, size, thesis."""
        return JSONResponse(_ttl_cached("books", 30.0, _books_payload))

    @app.get("/api/dashboard/activity")
    async def dashboard_activity(
        limit: int = Query(150, ge=1, le=500),
        book: str = Query("", max_length=64),
        etype: str = Query("", alias="type", max_length=32),
        since: int = Query(0, ge=0),
    ) -> JSONResponse:
        if since:
            # Incremental poll: the reversed walk breaks at the first event
            # <= since, so cost is O(new events). NOT TTL-cached on purpose —
            # every poll carries a fresh `since`, so caching would only grow
            # _TTL_CACHE with dead one-shot keys.
            return JSONResponse(_activity_payload(limit, book, etype, since))
        # Full-window load: TTL (8s) >= the page's poll interval (8s).
        return JSONResponse(_ttl_cached(
            f"activity:{limit}:{book}:{etype}", 8.0,
            lambda: _activity_payload(limit, book, etype)))

    @app.get("/api/dashboard/news")
    async def dashboard_news(limit: int = Query(50, ge=1, le=200)) -> JSONResponse:
        # TTL (30s) >= the page's poll interval (30s).
        return JSONResponse(_ttl_cached(f"news:{limit}", 30.0,
                                        lambda: _news_payload(limit)))

    @app.get("/api/dashboard/summary")
    async def dashboard_summary() -> JSONResponse:
        return JSONResponse(_ttl_cached("summary", 6.0, _summary_payload))

    @app.get("/api/dashboard/positions")
    async def dashboard_positions() -> JSONResponse:
        return JSONResponse(_positions_payload())  # already 5s-cached internally

    @app.get("/api/dashboard/equity-curve")
    async def dashboard_equity_curve(range_s: int = Query(86400, ge=60, le=2_592_000)) -> JSONResponse:
        return JSONResponse(_ttl_cached(f"equity-curve:{range_s}", 65.0,
                                        lambda: _equity_curve_payload(range_s)))

    @app.get("/api/dashboard/closed-trades")
    async def dashboard_closed_trades(limit: int = Query(20, ge=1, le=200)) -> JSONResponse:
        return JSONResponse(_ttl_cached(f"closed-trades:{limit}", 25.0,
                                        lambda: _closed_trades_payload(limit)))

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

    @app.post("/api/dashboard/operator/terminal")
    async def operator_terminal(request: Request) -> JSONResponse:
        """Hermes command-center terminal — routes a free-form command line.

        Built-in commands resolve locally (no LLM call): `status`, `pause`,
        `resume`, `close <coin>`, `regime`, `config`, `help`. Anything else
        falls through to Nous Hermes via OpenRouter, primed with a compact
        snapshot of recent agent state so the chat is grounded in the bot's
        actual world. Requires the operator token like every operator route.
        """
        _require_operator(request)
        body = await request.json()
        cmd = (body.get("command") or "").strip()
        if not cmd:
            return JSONResponse({"response": "", "kind": "noop"})
        parts = cmd.split()
        verb = parts[0].lower()

        # ── built-in commands ─────────────────────────────────────────────
        if verb in ("help", "?"):
            return JSONResponse({"response": (
                "commands:\n"
                "  status                — equity, daily PnL, open, tick, scan triggers\n"
                "  positions             — live positions w/ uPnL (winners + losers grouped)\n"
                "  trades [n]            — last n real fills from memory (default 10)\n"
                "  config                — dump current .agent-config.json\n"
                "  dump                  — full state (config + positions + last events)\n"
                "  regime                — cached regime per proxy\n"
                "  pause / resume        — flip mode OFF/LIVE\n"
                "  close <coin>          — market-close a single position\n"
                "  close all             — market-close every open position\n"
                "  close losing          — market-close every position with uPnL < 0\n"
                "  close winning         — market-close every position with uPnL > 0\n"
                "  set <key> <value>     — update .agent-config.json (int/float/bool/str inferred)\n"
                "  kill                  — pause trading then close all (panic button)\n"
                "  help                  — this list. anything else → ask the chat model"
            ), "kind": "help"})

        if verb == "status":
            try:
                events = session_log.tail(50) or []
                last_hb = next((e for e in reversed(events) if e.get("event") == "loop_heartbeat"), {})
                last_scan = next((e for e in reversed(events) if e.get("event") == "scan"), {})
                age_s = max(0, int(time.time() - (last_hb.get("ts", 0) / 1000))) if last_hb else None
                msg = (f"equity ${last_hb.get('equity', 0):.2f}  "
                       f"daily {last_hb.get('daily_pnl', 0):+.2f}  "
                       f"open {last_hb.get('open_positions', 0)}  "
                       f"tick {age_s}s ago  "
                       f"last scan: {last_scan.get('triggers', 0)} triggers")
                return JSONResponse({"response": msg, "kind": "status"})
            except Exception as e:
                return JSONResponse({"response": f"status read failed: {e}", "kind": "error"})

        if verb in ("pause", "resume"):
            new_mode = "OFF" if verb == "pause" else "LIVE"
            from hermes_trader.agents.config_store import write_agent_config
            cfg = read_agent_config()
            old = cfg.get("mode", "?")
            cfg["mode"] = new_mode
            write_agent_config(cfg)
            return JSONResponse({"response": f"mode {old} → {new_mode}", "kind": "action"})

        # ── close: single coin, all, losing, or winning ─────────────────
        if verb == "close" and len(parts) >= 2:
            from hermes_trader.agents.executor import close_position_market
            target = parts[1].lower()
            if target in ("all", "losing", "winning"):
                # Bulk close — iterate live positions, filter, close each.
                try:
                    user = resolve_user_address()
                    # include_hip3=True so `close all` also closes xyz:/vntl:/...
                    # positions, not just main-dex.
                    state = fetch_account_state(user, include_hip3=True) if user else {}
                    open_pos = [
                        {
                            "coin": p.get("position", {}).get("coin"),
                            "szi": float(p.get("position", {}).get("szi", "0") or 0),
                            "uPnL": float(p.get("position", {}).get("unrealizedPnl", "0") or 0),
                        }
                        for p in state.get("asset_positions", []) or []
                        if float(p.get("position", {}).get("szi", "0") or 0) != 0
                    ]
                except Exception as e:
                    return JSONResponse({"response": f"could not read live positions: {e}", "kind": "error"})

                if target == "losing":
                    targets = [p for p in open_pos if p["uPnL"] < 0]
                elif target == "winning":
                    targets = [p for p in open_pos if p["uPnL"] > 0]
                else:  # all
                    targets = open_pos

                if not targets:
                    return JSONResponse({"response": f"no positions matched `close {target}`", "kind": "info"})

                results = []
                for p in targets:
                    coin = p["coin"]
                    try:
                        r = close_position_market(coin)
                        ok = bool(r.get("ok") or r.get("executed"))
                        results.append(f"  {coin:<14} {('✓' if ok else '✗')} uPnL={p['uPnL']:+.2f}")
                    except Exception as e:
                        results.append(f"  {coin:<14} ✗ {e}")
                head = f"closed {len(targets)} position(s) [{target}]:\n"
                return JSONResponse({"response": head + "\n".join(results), "kind": "action"})

            # Single-coin close (preserve original behavior)
            coin = parts[1] if ":" in parts[1] else parts[1].upper()
            result = close_position_market(coin)
            return JSONResponse({"response": f"close {coin}: {result}", "kind": "action"})

        # ── positions: live list grouped by winners / losers ───────────
        if verb == "positions":
            try:
                rows = _positions_payload()
                if not rows:
                    return JSONResponse({"response": "no open positions", "kind": "info"})
                rows.sort(key=lambda r: -float(r.get("unrealized_pnl_usd") or 0))
                lines = [f"  {r['coin']:<14} {r['side']:<5} size={r['size']:>9.4f} "
                         f"entry={r['entry_px']:<10} uPnL={float(r.get('unrealized_pnl_usd') or 0):+.2f}"
                         for r in rows]
                total = sum(float(r.get("unrealized_pnl_usd") or 0) for r in rows)
                head = f"{len(rows)} open · total uPnL ${total:+.2f}\n"
                return JSONResponse({"response": head + "\n".join(lines), "kind": "status"})
            except Exception as e:
                return JSONResponse({"response": f"positions read failed: {e}", "kind": "error"})

        # ── trades [n]: last n real fills from memory ──────────────────
        if verb == "trades":
            try:
                from hermes_trader.agents.memory import memory as _mem
                _mem.load()
                n = 10
                if len(parts) >= 2:
                    try: n = max(1, min(50, int(parts[1])))
                    except ValueError: pass
                real = [t for t in (_mem.get_recent_trades(50) or []) if float(t.get("size_usd") or 0) > 0]
                last_n = real[-n:]
                if not last_n:
                    return JSONResponse({"response": "no real trades in memory yet", "kind": "info"})
                from datetime import datetime
                lines = []
                for t in last_n:
                    ts = datetime.fromtimestamp(t["executed_at"]/1000).strftime("%m-%d %H:%M:%S")
                    lines.append(f"  {ts}  {t.get('coin'):<14} {t.get('side','?'):<5} "
                                 f"entry={t.get('entry_px',0):<10} size=${float(t.get('size_usd') or 0):.2f}")
                return JSONResponse({"response": f"last {len(last_n)} fills:\n" + "\n".join(lines), "kind": "info"})
            except Exception as e:
                return JSONResponse({"response": f"trades read failed: {e}", "kind": "error"})

        # ── set <key> <value>: update agent config (type-inferred) ─────
        if verb == "set" and len(parts) >= 3:
            from hermes_trader.agents.config_store import write_agent_config
            key = parts[1]
            raw = " ".join(parts[2:]).strip()
            # Type coercion: int, float, bool, json, else string.
            def _coerce(s: str):
                if s.lower() in ("true", "false"):
                    return s.lower() == "true"
                if s.lower() in ("null", "none"):
                    return None
                try: return int(s)
                except ValueError: pass
                try: return float(s)
                except ValueError: pass
                if (s.startswith("{") and s.endswith("}")) or (s.startswith("[") and s.endswith("]")):
                    try: return json.loads(s)
                    except Exception: pass
                return s
            new_val = _coerce(raw)
            cfg = read_agent_config()
            old_val = cfg.get(key, "<unset>")
            cfg[key] = new_val
            write_agent_config(cfg)
            return JSONResponse({"response": f"config[{key}]: {old_val} → {new_val}  (type={type(new_val).__name__})",
                                  "kind": "action"})

        # ── kill: pause + close all (panic button) ─────────────────────
        if verb == "kill":
            from hermes_trader.agents.config_store import write_agent_config
            from hermes_trader.agents.executor import close_position_market
            cfg = read_agent_config()
            cfg["mode"] = "OFF"
            write_agent_config(cfg)
            try:
                user = resolve_user_address()
                state = fetch_account_state(user, include_hip3=True) if user else {}
                open_coins = [
                    p["position"]["coin"]
                    for p in state.get("asset_positions", []) or []
                    if float(p.get("position", {}).get("szi", "0") or 0) != 0
                ]
            except Exception as e:
                return JSONResponse({"response": f"mode → OFF, but position-list fetch failed: {e}", "kind": "error"})
            closed = []
            for c in open_coins:
                try:
                    r = close_position_market(c)
                    closed.append(f"  {c}: {'✓' if (r.get('ok') or r.get('executed')) else '✗'}")
                except Exception as e:
                    closed.append(f"  {c}: ✗ {e}")
            head = f"KILL · mode → OFF · closed {len(open_coins)} position(s):\n"
            return JSONResponse({"response": head + ("\n".join(closed) if closed else "  (no positions to close)"),
                                  "kind": "action"})

        # ── dump: full state snapshot (config + positions + last events) ─
        if verb == "dump":
            try:
                user = resolve_user_address()
                state = fetch_account_state(user, include_hip3=True) if user else {}
                events = session_log.tail(10) or []
                positions = [
                    {"coin": p.get("position", {}).get("coin"),
                     "szi": float(p.get("position", {}).get("szi", "0") or 0),
                     "uPnL": float(p.get("position", {}).get("unrealizedPnl", "0") or 0)}
                    for p in state.get("asset_positions", []) or []
                    if float(p.get("position", {}).get("szi", "0") or 0) != 0
                ]
                snap = {
                    "config": read_agent_config(),
                    "equity": float(state.get("equity", 0) or 0),
                    "open_positions": positions,
                    "recent_events": [{k: v for k, v in e.items() if k != "ts"} for e in events],
                }
                return JSONResponse({"response": json.dumps(snap, indent=2, default=str), "kind": "info"})
            except Exception as e:
                return JSONResponse({"response": f"dump failed: {e}", "kind": "error"})

        if verb == "regime":
            try:
                from hermes_trader.agents.market_regime import regime_snapshot
                snap = regime_snapshot()
                lines = [f"  {p}: {info.get('regime', '?')}  ({int(info.get('age_s', 0))}s old)"
                         for p, info in snap.items()]
                return JSONResponse({"response": "regime snapshot:\n" + "\n".join(lines) if lines else "no cached regimes yet",
                                      "kind": "info"})
            except Exception as e:
                return JSONResponse({"response": f"regime fetch failed: {e}", "kind": "error"})

        if verb == "config":
            cfg = read_agent_config()
            return JSONResponse({"response": json.dumps(cfg, indent=2), "kind": "info"})

        # ── LLM fallback (Nous Hermes via OpenRouter) ─────────────────────
        try:
            import httpx
            key = os.environ.get("OPENROUTER_API_KEY", "")
            if not key:
                return JSONResponse({"response": "Hermes chat unavailable: OPENROUTER_API_KEY not set", "kind": "error"})

            # Real trades come from memory (the 100-entry trade ring buffer);
            # the feed supplies recent DSL exits + skips so "why did X close"
            # questions have context.
            from hermes_trader.agents.memory import memory as _mem
            _mem.load()
            events = session_log.tail(80) or []
            last_hb = next((e for e in reversed(events) if e.get("event") == "loop_heartbeat"), {})

            # Last 8 executed trades (size_usd > 0 means it actually placed)
            mem_trades = _mem.get_recent_trades(50) or []
            real_trades = [t for t in mem_trades if float(t.get("size_usd") or 0) > 0][-8:]

            # Open positions from the live exchange state (already maintained
            # by the heartbeat sync); fall back to memory if heartbeat is stale.
            try:
                user = resolve_user_address()
                state = fetch_account_state(user, include_hip3=True) if user else {}
                open_pos = [
                    {
                        "coin": p.get("position", {}).get("coin"),
                        "side": "long" if float(p.get("position", {}).get("szi", "0") or 0) > 0 else "short",
                        "szi": float(p.get("position", {}).get("szi", "0") or 0),
                        "entry": float(p.get("position", {}).get("entryPx", "0") or 0),
                        "uPnL": float(p.get("position", {}).get("unrealizedPnl", "0") or 0),
                    }
                    for p in state.get("asset_positions", []) or []
                    if float(p.get("position", {}).get("szi", "0") or 0) != 0
                ]
            except Exception:
                open_pos = []

            recent_dsl_exits = [e for e in events if e.get("event") == "dsl_exit"][-5:]
            recent_ta_skips = [e for e in events if e.get("event") == "ta_skip"][-5:]
            recent_entry_preflights = [e for e in events if e.get("event") == "entry_preflight"][-5:]
            recent_research = [e for e in events if e.get("event") == "research"][-5:]

            ctx = {
                "equity": last_hb.get("equity"),
                "daily_pnl": last_hb.get("daily_pnl"),
                "open_position_count": last_hb.get("open_positions"),
                "config_snippet": last_hb.get("config", {}),
                "open_positions": open_pos[:20],
                "recent_trades": [
                    {
                        "coin": t.get("coin"),
                        "side": t.get("side"),
                        "entry_px": t.get("entry_px"),
                        "size_usd": t.get("size_usd"),
                        "executed_at": t.get("executed_at"),
                    } for t in real_trades
                ],
                "recent_dsl_exits": [
                    {"coin": e.get("coin"), "reason": e.get("reason"),
                     "pnl_pct": e.get("realized_pnl_pct") or e.get("unrealized_pct"),
                     "ts": e.get("ts")}
                    for e in recent_dsl_exits
                ],
                "recent_ta_skips": [
                    {"coin": e.get("coin"), "signal": e.get("signal"), "score": e.get("score"), "ts": e.get("ts")}
                    for e in recent_ta_skips
                ],
                "recent_entry_preflights": [
                    {"coin": e.get("coin"), "reason": e.get("reason"), "score": e.get("score"), "ts": e.get("ts")}
                    for e in recent_entry_preflights
                ],
                "recent_research_verdicts": [
                    {"coin": e.get("coin"), "verdict": e.get("verdict"),
                     "confidence": e.get("confidence"),
                     "reasoning": (e.get("reasoning") or "")[:160], "ts": e.get("ts")}
                    for e in recent_research
                ],
            }
            system_msg = (
                "You are Hermes, the autonomous trading agent's voice. You're embedded in "
                "a Tamagotchi-style dashboard. Be concise (2-4 sentences max), specific, and "
                "operator-grade — no hedging fluff. Answer using ONLY the LIVE STATE below.\n\n"
                "Field map:\n"
                "  • open_positions = live exchange state (the source of truth for what's open)\n"
                "  • recent_trades = last 8 actually-filled trades from memory (with size_usd > 0)\n"
                "  • recent_dsl_exits = positions the DSL exit engine closed (and why)\n"
                "  • recent_research_verdicts = analysis results that fed execution decisions\n"
                "  • recent_ta_skips = signals the TA filter rejected before paid AI research\n"
                "  • recent_entry_preflights = deterministic live gates that skipped paid AI research\n\n"
                "Rules: if asked about \"the last trade\", look at recent_trades[-1]. If asked "
                "\"why X\", check recent_research_verdicts for the reasoning. If asked why a "
                "position closed, check recent_dsl_exits. NEVER predict future prices.\n\n"
                f"LIVE STATE: {json.dumps(ctx, default=str)}"
            )
            # Model is env-overridable so the operator can swap without a
            # code change. Default is xAI Grok 4.3 — fast, strong on
            # numeric/financial reasoning, and the operator picked it.
            # Override with HERMES_CHAT_MODEL=<openrouter-slug> in .env.local.
            # Catalog: https://openrouter.ai/models
            chat_model = os.environ.get("HERMES_CHAT_MODEL", "x-ai/grok-4.3")
            async def _call():
                async with httpx.AsyncClient(timeout=20.0) as client:
                    r = await client.post(
                        "https://openrouter.ai/api/v1/chat/completions",
                        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                        json={
                            "model": chat_model,
                            "messages": [
                                {"role": "system", "content": system_msg},
                                {"role": "user", "content": cmd},
                            ],
                            "max_tokens": 240,
                            "temperature": 0.6,
                        },
                    )
                    r.raise_for_status()
                    return r.json()
            # We're inside FastAPI's event loop here, so just await directly.
            data = await _call()
            content = data["choices"][0]["message"]["content"].strip()
            return JSONResponse({"response": content, "kind": "chat", "model": chat_model})
        except Exception as e:
            return JSONResponse({"response": f"chat error: {e}", "kind": "error"})
