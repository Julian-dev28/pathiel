"""Public + operator web UI for hermes-trader.

Pages (markup in hermes_trader/templates/*.html, assets vendored in /static):

  GET /                          — landing one-pager: how it works, equity +
                                   today's PnL, equity curve, live-books table
  GET /activity                  — the trading-desk journal: tiered flowing
                                   stream + 6h session strip
  GET /news                      — news-catalyst reads + research news context

JSON APIs:

  GET /api/dashboard/summary     — hero numbers + status
  GET /api/dashboard/books       — live-books table rows
  GET /api/dashboard/activity    — classified session-log events (tiered,
                                   filterable, incremental via ?since=)
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
import re
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
     "Buy the mover the AI just PASSed — its vetoes measured -4.5%/day "
     "forfeited."),
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
    """Map one raw session-log event to a typed activity view model. Pure.

    Every model carries a `tier` (the editorial hierarchy the /activity page
    renders) and — for tier 3 — a `gkey` the client coalesces on:

      tier 1: full card, always — executes, closes, book opens, actionable
              research verdicts (LONG/SHORT/CLOSE), errors.
      tier 2: compact one-liner — research PASS, book cycles with signals,
              loop start/stop, unknown shapes.
      tier 3: never an individual row — gate skips group by (coin, reason),
              scans fold into hourly buckets, quiet book cycles per book,
              heartbeats resolve client-side (changed → line, steady → run).
    """
    ev = str(e.get("event") or "?")
    ts = e.get("ts")

    if ev == "research":
        verdict = e.get("verdict")
        return {
            "type": "research", "ts": ts, "coin": e.get("coin"),
            "verdict": verdict, "confidence": e.get("confidence"),
            "reasoning": e.get("reasoning"),
            "provider": e.get("ai_brain_provider"),
            "web_search_used": bool(e.get("web_search_used")),
            "citations": e.get("web_search_citations") or [],
            "news_risk": e.get("news_risk"),
            "entry_px": e.get("entry_px"), "stop_px": e.get("stop_px"),
            "tp_px": e.get("tp_px"),
            "tier": 1 if verdict in ("LONG", "SHORT", "CLOSE") else 2,
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
            "tier": 1,
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
            "tier": 1,
        }

    if ev == "ai_close":
        return {
            "type": "close", "ts": ts, "coin": e.get("coin"),
            "side": e.get("side"), "leverage": None,
            "reason": e.get("reasoning") or "ai close",
            "pnl_pct": None, "spot_pct": None,
            "fill_px": None, "entry_px": None,
            "executed": bool(e.get("executed")), "source": "ai_close",
            "tier": 1,
        }

    if ev == "book_open":
        extra = {k: v for k, v in e.items()
                 if k not in ("ts", "event", "book", "coin", "side")}
        return {"type": "book", "subtype": "open", "ts": ts,
                "book": e.get("book"), "coin": e.get("coin"),
                "side": e.get("side"), "extra": extra, "tier": 1}

    book = _EVENT_BOOK_ALIASES.get(ev, ev)
    if (book in _KNOWN_BOOK_NAMES or isinstance(e.get("skipped"), dict)
            or "candidates" in e or ("signals" in e and "opened" in e)):
        core = {"ts", "event", "shadow", "signals", "opened", "skipped", "candidates"}
        extra = {k: v for k, v in e.items() if k not in core}
        signals = e.get("signals")
        # A cycle earns a row only when it produced something: signals, opens,
        # candidates — or a signal-less shape that still carries content
        # (xs_rebalance's longs/shorts land in extra with signals=None).
        meaningful = bool((signals or 0) or (e.get("opened") or 0)
                          or e.get("candidates") or (signals is None and extra))
        out = {
            "type": "book", "ts": ts, "book": book,
            "shadow": e.get("shadow"),
            "signals": signals, "opened": e.get("opened"),
            "skipped": e.get("skipped") or {},
            "candidates": e.get("candidates") or [],
            "extra": extra,
            "tier": 2 if meaningful else 3,
        }
        if out["tier"] == 3:
            out["gkey"] = f"quiet|{book}"
        return out

    if ev == "scan":
        coins = e.get("coin_scores") or e.get("coins") or []
        return {"type": "scan", "ts": ts,
                "triggers": e.get("triggers", e.get("perceptions", 0)),
                "coins": coins,
                "tier": 3, "gkey": f"scan|{int((ts or 0) // 3_600_000)}"}

    if ev in ("ta_skip", "entry_preflight"):
        reason = e.get("reason") or e.get("signal")
        # Group key normalizes digits away: "runner_gate_blocked (score=57)"
        # and "(score=61)" are the SAME editorial fact — without this, one
        # coin's repeated skips shatter into per-score groups.
        gkey_reason = re.sub(r"[\d.]+", "", str(reason or ""))
        return {"type": "gate", "ts": ts, "kind": ev, "coin": e.get("coin"),
                "reason": reason,
                "score": e.get("score"), "trigger_score": e.get("trigger_score"),
                "tier": 3, "gkey": f"gate|{e.get('coin')}|{gkey_reason}"}

    if ev == "error":
        return {"type": "error", "ts": ts,
                "scope": e.get("coin") or e.get("scope"),
                "error": str(e.get("error") or "")[:300], "tier": 1}

    if ev == "loop_heartbeat":
        # tier 3, no server gkey: the client's state machine renders a line
        # only when equity moves >$0.05 or the open count changes; unchanged
        # runs collapse into a "steady" divider.
        return {"type": "heartbeat", "ts": ts,
                "equity": e.get("equity"), "daily_pnl": e.get("daily_pnl"),
                "open_positions": e.get("open_positions"), "tier": 3}

    if ev in ("loop_start", "loop_stop"):
        # ONE line. The raw event embeds the entire agent config — that dump
        # must never render inline (operator order 2026-07-12), so only the
        # three facts that matter survive classification.
        cfg = e.get("config") or {}
        fields = {k: v for k, v in {
            "mode": cfg.get("mode"),
            "scan_interval": e.get("scan_interval"),
            "min_score": e.get("min_score"),
        }.items() if v is not None}
        return {"type": "system", "ts": ts, "name": ev, "fields": fields,
                "tier": 2}

    # Unknown shape → graceful key-value rendering, never raw JSON.
    fields = {k: v for k, v in e.items() if k not in ("ts", "event")}
    return {"type": "other", "ts": ts, "name": ev, "fields": fields, "tier": 2}


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


_SESSION_WINDOW_S = 6 * 3600


def _session_strip(window_s: int = _SESSION_WINDOW_S) -> Dict[str, Any]:
    """Rolled-up tape stats for the strip pinned above the /activity stream:
    what happened in the last N hours, at a glance. Pure log walk, no network."""
    cutoff = int(time.time() * 1000) - window_s * 1000
    events = _read_log_lines()
    if len(events) > _ACTIVITY_SCAN_CAP:
        events = events[-_ACTIVITY_SCAN_CAP:]
    scans = triggers = researched = opened = closed = blocks = 0
    realized = 0.0
    equity = open_positions = None
    for e in reversed(events):
        if (e.get("ts") or 0) < cutoff:
            break
        ev = e.get("event")
        if ev == "scan":
            scans += 1
            triggers += int(e.get("triggers") or 0)
        elif ev == "research":
            researched += 1
        elif ev == "execute":
            if e.get("executed"):
                opened += 1
            else:
                blocks += 1
        elif ev in ("ta_skip", "entry_preflight"):
            blocks += 1
        elif ev == "dsl_exit":
            closed += 1
            pnl = e.get("realized_pnl_pct")
            if pnl is None:
                pnl = e.get("leveraged_pct")
            if pnl is None:
                pnl = e.get("unrealized_pct")
            realized += float(pnl or 0)
        elif ev == "loop_heartbeat" and equity is None:
            equity = e.get("equity")
            open_positions = e.get("open_positions")
    return {"window_h": window_s // 3600, "since_ts": cutoff,
            "scans": scans, "candidates": triggers, "researched": researched,
            "opened": opened, "closed": closed,
            "realized_pnl_pct": round(realized, 2), "blocks": blocks,
            "equity": equity, "open_positions": open_positions}


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


# ── route registration ──────────────────────────────────────────────────────


def register_routes(app: FastAPI) -> None:
    """Mount the dashboard pages, JSON APIs, and SSE feed onto an existing
    FastAPI app. Pages: / (landing), /activity, /news. The former /config and
    /operator pages were deleted by operator order 2026-07-12 — those paths
    404 by design; token-gated actions live in server.py's /api/agent + /api/hl
    endpoints, with the token entered via the landing footer (localStorage)."""

    # no-store on the pages so a server restart isn't masked by a cached
    # HTML shell that pre-dates the new JS. The JSON endpoints below are fine
    # to cache for their poll interval.
    _NO_CACHE_HEADERS = {"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"}

    @app.get("/", response_class=HTMLResponse)
    async def public_dashboard() -> HTMLResponse:
        return HTMLResponse(content=_PUBLIC_HTML, headers=_NO_CACHE_HEADERS)

    @app.get("/activity", response_class=HTMLResponse)
    async def activity_page() -> HTMLResponse:
        """Live activity feed — research verdicts, executions with gate
        results, book events, DSL closes. Filterable by book and event type."""
        return HTMLResponse(content=_ACTIVITY_HTML, headers=_NO_CACHE_HEADERS)

    @app.get("/news", response_class=HTMLResponse)
    async def news_page() -> HTMLResponse:
        """News-catalyst reads (shadow ledger) + research events with news context."""
        return HTMLResponse(content=_NEWS_HTML, headers=_NO_CACHE_HEADERS)

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
        session = _ttl_cached("session-strip", 30.0, _session_strip)
        if since:
            # Incremental poll: the reversed walk breaks at the first event
            # <= since, so cost is O(new events). NOT TTL-cached on purpose —
            # every poll carries a fresh `since`, so caching would only grow
            # _TTL_CACHE with dead one-shot keys.
            payload = _activity_payload(limit, book, etype, since)
        else:
            # Full-window load: TTL (8s) >= the page's poll interval (8s).
            payload = _ttl_cached(
                f"activity:{limit}:{book}:{etype}", 8.0,
                lambda: _activity_payload(limit, book, etype))
        return JSONResponse({**payload, "session": session})

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
