"""Public + operator web UI for pathiel.

Pages (markup in pathiel/templates/*.html, assets vendored in /static):

  GET /                          — the Desk landing page: the active desk's
                                   call + 20x line, wallets, levels,
                                   headlines/catalysts, loop log, track
                                   record. Parameterized by desk id (agent
                                   config active_desks, default ["oil"]) —
                                   see docs/overhaul/DESK_UI_2026-10-23.md.
                                   Shows an onboarding wizard first for a
                                   brand-new visitor.
  GET /account                   — Account & Risk: active Hyperliquid account,
                                   risk preset

The desk data itself (thesis, levels, candle log, track record) is served by
services/strategies/api.py (GET /api/desks/...), not this module. This
module's own JSON APIs are what's left of the retired bot UI plus the
house/customer account split:

  GET /api/dashboard/account     — the SIGNED-IN WALLET's own Hyperliquid
                                   account (not the house's)
  GET /api/dashboard/positions   — open positions + DSL tracker state
                                   (operator-gated; no page calls this any
                                   more, kept for
                                   tests/test_account_reads_shared.py's
                                   shared-account-read-cache contract)

Operator routes need local mode or a signed-in operator; anyone else → 401.
The variable is checked at request time, not import time, so rotating it
doesn't require a restart.
"""

from __future__ import annotations

import asyncio
import json
import logging
import collections
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from pathiel import session_log
from pathiel.agents import capital_flows
from pathiel.agents.bracket import min_tradable_equity as _min_tradable_equity
from pathiel.agents.config_store import read_agent_config
from pathiel.client.account_snapshot import account_state
from pathiel.client.hl_client import resolve_user_address

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
        from pathiel.client.exchange import _get_info
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
_TTL_CACHE: Dict[str, Tuple[float, Any]] = {}
_TTL_CACHE_LOCK = threading.Lock()


def _ttl_cached(key: str, ttl: float, fn: Callable[[], Any]) -> Any:
    now = time.time()
    with _TTL_CACHE_LOCK:
        hit = _TTL_CACHE.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    val = fn()
    with _TTL_CACHE_LOCK:
        _TTL_CACHE[key] = (now, val)
    return val


async def _run_off_loop(fn: Callable[..., Any], *args: Any) -> Any:
    """Run a payload builder on the executor, not the request coroutine.

    coin_chart's fetch_hl_candles is a real HL round trip called directly
    inside `async def` with no `await` — on uvicorn's one event loop that
    blocks every other request, on every page, for every visitor, for its
    duration (confirmed: concurrent /analytics calls clustered at the chart
    call's ~200ms, the signature of head-of-line blocking). Every route uses
    this now so the next payload function that adds a blocking call is
    automatically off the hot path too. Cache-hit cost: one thread-pool
    round trip, sub-millisecond."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, fn, *args)


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


_LIVE_EQ_CACHE: Dict[str, Any] = {"ts": 0.0, "data": None}
_LIVE_EQ_TTL_S = 30.0
_LIVE_EQ_LOCK = threading.Lock()


def _live_equity_enabled() -> bool:
    # Off in gate tests (root conftest), which must not reach Hyperliquid.
    return os.environ.get("PATHIEL_DASHBOARD_LIVE_EQUITY", "1") == "1"


def _live_equity() -> Optional[Dict[str, Any]]:
    """The ACTIVE account's live equity across every perp dex (HIP-3
    included), cached 30s. None when disabled or unreadable.

    The heartbeat is the loop's own snapshot and freezes when the loop stops:
    on 2026-10-18 the dashboard showed $1.37 while the account held $15.21
    (xyz $15.18 + main $0.01 + dust), because the last heartbeat was weeks old.
    """
    if not _live_equity_enabled():
        return None
    now = time.time()
    with _LIVE_EQ_LOCK:
        if _LIVE_EQ_CACHE["data"] is not None and now - _LIVE_EQ_CACHE["ts"] < _LIVE_EQ_TTL_S:
            return _LIVE_EQ_CACHE["data"]
    try:
        from services.accounts.summary import get_active_summary
        summ = get_active_summary()
        if summ.equity_usd is None:
            return None
        data = {"equity": float(summ.equity_usd),
                "withdrawable": float(summ.withdrawable_usd or 0.0),
                "open_positions": int(summ.open_positions or 0)}
    except Exception as e:  # never break a page over a display read
        logger.warning(f"[dashboard] live equity read failed: {e}")
        return None
    with _LIVE_EQ_LOCK:
        _LIVE_EQ_CACHE.update(ts=now, data=data)
    return data


def _summary_payload() -> Dict[str, Any]:
    """Equity, daily PnL, open count, last-tick — derived from the session log so
    the dashboard works even if the live HL fetch is rate-limited."""
    events = _read_log_lines()
    heartbeat = _last_event(events, "loop_heartbeat") or {}
    last_scan = _last_event(events, "scan")
    # The age that matters is the age of the LOOP HEARTBEAT, not of the last
    # log line of any kind. Using events[-1] meant ANY write to the session log
    # — a dashboard action, an operator audit entry, a scheduler job — reset the
    # "loop is alive" signal. Found 2026-08-29 with the loop dead for 27 days
    # while this reported 325 seconds, because an audit write had just landed.
    # The status heuristic below has always been written in terms of heartbeat
    # cadence (p50 ~90s / p99 ~420s), so the heartbeat was the intent all along.
    last_hb_ts = int(heartbeat.get("ts") or 0)
    last_event_ts = events[-1]["ts"] if events else 0

    equity = float(heartbeat.get("equity", 0) or 0)
    spot_usdc = float(heartbeat.get("spot_usdc", 0) or 0)
    daily_pnl = float(heartbeat.get("daily_pnl", 0) or 0)
    # Start-of-day equity = equity - daily_pnl (heartbeat-consistent)
    sod = equity - daily_pnl
    daily_pnl_pct = (daily_pnl / sod * 100) if sod > 0 else 0.0

    now_ms = int(time.time() * 1000)
    last_tick_age_s = max(0, (now_ms - last_hb_ts) // 1000) if last_hb_ts else None
    last_event_age_s = max(0, (now_ms - last_event_ts) // 1000) if last_event_ts else None

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

    # A heartbeat older than the loop's cadence is history, not the balance.
    # Read the account live instead and say which number is shown.
    equity_source = "heartbeat"
    open_positions = int(heartbeat.get("open_positions", 0) or 0)
    if status != "scanning":
        live = _live_equity()
        if live is not None:
            equity, spot_usdc = live["equity"], 0.0
            open_positions = live["open_positions"]
            dex_equity, dex_available = {}, {}
            equity_source = "live"

    return {
        # True account equity as the exchange app shows it: perps across every
        # dex PLUS idle spot USDC (operator correction 2026-07-17 — the KPI
        # must match HL's own "Account Equity", not the perps-only subtotal).
        "equity": round(equity + spot_usdc, 2),
        "available": round(float(heartbeat.get("available", 0) or 0), 2),
        "dex_equity": dex_equity,
        "dex_available": dex_available,
        "spot_usdc": round(spot_usdc, 2),
        "daily_pnl": round(daily_pnl, 2),
        "daily_pnl_pct": round(daily_pnl_pct, 2),
        "open_positions": open_positions,
        "equity_source": equity_source,
        "last_tick_age_s": last_tick_age_s,
        # Kept separate and named honestly: this moves on ANY log write, so it
        # says the process is writing, not that the loop is running.
        "last_event_age_s": last_event_age_s,
        "last_scan_triggers": int((last_scan or {}).get("triggers", 0) or 0),
        "status": status,
        "ts": now_ms,
    }


_POSITIONS_CACHE: Dict[str, Any] = {"ts": 0.0, "data": []}
logger = logging.getLogger(__name__)

_POSITIONS_CACHE_TTL_S = 5.0  # acceptable staleness for a display endpoint
# _run_off_loop means this can now be hit from two executor threads at once;
# lock it like _TTL_CACHE_LOCK.
_POSITIONS_CACHE_LOCK = threading.Lock()


def _positions_payload() -> List[Dict[str, Any]]:
    """Join live HL positions with DSL tracker state for the operator/public view.

    Cached for ~5s so repeated dashboard polls don't hammer HL with
    fetch_account_state(include_hip3=True) — each call is ~9 HTTP POSTs
    (1 main + 8 HIP-3 dexes) even with the parallel fan-out. Cache TTL
    is short enough that the position table never feels stuck.
    """
    now = time.time()
    with _POSITIONS_CACHE_LOCK:
        if now - _POSITIONS_CACHE["ts"] < _POSITIONS_CACHE_TTL_S:
            return _POSITIONS_CACHE["data"]
    try:
        data = _positions_payload_uncached()
    except Exception as exc:                       # noqa: BLE001
        # An empty list means FLAT to everyone reading it — the operator view,
        # the public view, /api/positions. Returning [] because the fetch threw
        # tells the operator they have no positions while they may have several
        # open and unmanaged. Serve the last known good read instead, say so in
        # the log, and let the short TTL retry immediately.
        logger.warning(f"[positions] HL fetch failed ({type(exc).__name__}: "
                       f"{exc}) — serving the last good read rather than "
                       f"reporting flat")
        with _POSITIONS_CACHE_LOCK:
            return _POSITIONS_CACHE["data"]
    with _POSITIONS_CACHE_LOCK:
        _POSITIONS_CACHE["ts"] = now
        _POSITIONS_CACHE["data"] = data
        return data


def _positions_payload_uncached() -> List[Dict[str, Any]]:
    user = resolve_user_address()
    if not user:
        # Was `return []` — the exact "failure reads as flat" bug the comment
        # two lines down says this function must avoid, reached from a
        # different door. Reproduced live: snapshot stale + no address
        # configured silently overwrote a 3-position cache with "flat",
        # while the KPI (a different, 300s-not-120s staleness window) kept
        # showing 3. Raising lets the except-block below do what it already
        # does on a fetch failure: serve last-known-good instead of a false zero.
        raise RuntimeError("no HL address resolvable — cannot confirm positions")
    # include_hip3=True so xyz:MU / vntl:* positions appear in the dashboard
    # list alongside main-dex positions; HIP-3 dexes are separate
    # clearinghouses that the default fetch ignores.
    #
    # Deliberately NOT guarded here: a fetch failure must not become an empty
    # position list, which reads as "flat". _positions_payload() catches it and
    # serves the last known good read instead.
    #
    # The shared snapshot (pathiel.client.account_snapshot), not a fresh read
    # per poll: the landing page asks every 10 s and a read costs 12 s of the
    # server's budget, so polls piled up (2026-10-19).
    state = account_state(user, include_hip3=True)
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
        # Liquidation price — None when HL omits it (cross positions far from
        # liq report null). The landing page flags liq within 10% of mark.
        try:
            liq_px: Optional[float] = float(pos.get("liquidationPx") or 0) or None
        except (TypeError, ValueError):
            liq_px = None
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

        # DSL exit engine and the entry-context memory ledger were retired
        # 2026-10-23 (wave 2: the autonomous scan/research/execute loop has
        # no caller left — Claude trades through the MCP server now). Both
        # fields are kept in the payload, always empty, rather than removed:
        # dropping a key is a breaking change for any client still reading
        # it, and "no DSL tracker / no recorded reason" is an honest answer,
        # not a broken one.
        dsl_info = None
        _reason = ""
        _book = ""

        rows.append({
            "coin": coin,
            "side": side,
            "size": abs(szi),
            "leverage": leverage,
            "entry_px": entry,
            "mark_px": mark,
            "liq_px": liq_px,
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


def _feed_health() -> Dict[str, Any]:
    """Scan feed integrity.

    2026-10-23 (wave 2): the scanner (`pathiel.agents.perception`) that fed
    this is retired with the rest of the autonomous engine — Claude trades
    through the MCP server, which reads prices directly per call rather than
    maintaining a scanned feed with its own integrity state. `trustworthy:
    None` reads as "ok" everywhere this is consumed (GET /api/health/system,
    the risk payload), which is the honest answer: there is no feed left to
    be degraded."""
    return {"gap_frac": None, "gaps": 0, "markets": 0,
            "trustworthy": None, "ts": 0}


def _derived_limits_for(equity: float, cfg: Dict[str, Any]):
    """services.risk's DerivedLimits at this equity and config. Pure, no
    network. None if the risk service cannot load (the panel then falls back
    to the legacy floor rather than breaking)."""
    try:
        from pathiel.agents.config_store import read_risk_profile
        from services.risk import books as risk_books
        from services.risk.api import _VENUE
        from services.risk.derive import derive_limits
        return derive_limits(max(0.0, equity), read_risk_profile(cfg),
                             risk_books.all_book_specs(cfg), _VENUE)
    except Exception as e:
        logger.warning(f"[dashboard] derived limits unavailable: {e}")
        return None


def _risk_payload(range_s: int = 90 * 86400) -> Dict[str, Any]:
    """What a person with money asks first: how much can this lose, how much has
    it already lost from its high, what does trading itself cost, and where is
    the off switch.

    Every number is derived from data the dashboard already holds — the
    heartbeat equity series and the closed-trade log — so this endpoint adds no
    network calls. It reuses _equity_curve_payload deliberately: that function
    already filters partial-dex degraded reads, and a drawdown computed over
    unfiltered equity would invent a 60% crash every time a HIP-3 fetch blipped.

    `fee_drag_pct` is fees as a share of GROSS profit-and-loss magnitude, not of
    equity. That is the number that answers "is churn eating this", which is the
    failure mode this account actually had.

    Drawdown is computed on a flow-neutral NAV index whenever
    pathiel.agents.capital_flows has deposits and withdrawals recorded
    across the whole window. Raw equity would count a withdrawal as a loss; the
    NAV index normalises each interval by the capital actually at risk during
    it, so moving money in or out leaves the number alone.

    When the flow record does NOT cover the window the panel falls back to raw
    equity AND says so, rather than silently upgrading its own confidence. A
    drawdown that cannot tell a withdrawal from a loss is not a risk metric, and
    pretending otherwise is the flattering misreport this panel exists to stop.
    """
    curve = _equity_curve_payload(range_s)
    equities = [float(p["equity"]) for p in curve if p.get("equity")]

    peak = max(equities) if equities else 0.0
    current = equities[-1] if equities else 0.0

    # Drawdown on a FLOW-NEUTRAL NAV index when capital flows are recorded for
    # the whole window, and on raw equity only when they are not. Raw equity
    # counts a withdrawal as a loss, which is the defect this replaces; the NAV
    # index normalises each interval by the capital actually at risk during it,
    # so moving money in or out does not move the number.
    flows = capital_flows.load_flows()
    cover = capital_flows.coverage(curve, flows)
    # A drawdown needs a peak and a trough. With fewer than two samples there is
    # no path to measure, and every formula below returns exactly 0.0 — which
    # the panel then renders as a green "+0.00%". Measured 2026-09-02: the curve
    # held ONE point ($12.93) and the panel reported 0.00% drawdown and 0.00%
    # max drawdown on an account holding $12.94 against $117.38 of net
    # deposits, down 89%. Zero-because-unmeasured must not be presented as
    # zero-because-flat; that is the flattering misreport this panel exists to
    # stop, arriving through the one door nothing was watching.
    too_few = len(equities) < 2
    if too_few:
        dd_now_pct = max_dd_pct = None
        dd_basis = "insufficient_history"
    elif cover.get("covered"):
        nav = capital_flows.nav_series(curve, flows)
        dd = capital_flows.drawdown_from_nav(nav)
        dd_now_pct, max_dd_pct = dd["drawdown_pct"], dd["max_drawdown_pct"]
        dd_basis = "nav"
    else:
        dd_now_pct = ((current - peak) / peak * 100) if peak > 0 else 0.0
        max_dd_pct, running_peak = 0.0, 0.0
        for eq in equities:
            running_peak = max(running_peak, eq)
            if running_peak > 0:
                max_dd_pct = min(max_dd_pct, (eq - running_peak) / running_peak * 100)
        dd_basis = "equity"

    closes = _closed_trades_payload(limit=500)
    graded = [c for c in closes if c.get("pnl_pct") is not None]
    wins = [c for c in graded if float(c["pnl_pct"]) > 0]
    win_rate = (len(wins) / len(graded)) if graded else None

    gross_abs = sum(abs(float(c.get("spot_pct") or 0) * float(c.get("leverage") or 1))
                    for c in graded)
    fees_total = sum(float(c.get("fees_pct") or 0) for c in graded)
    fee_drag_pct = (fees_total / gross_abs * 100) if gross_abs > 0 else None

    summary = _summary_payload()
    try:
        cfg = read_agent_config()
    except Exception:
        cfg = {}
    kill_at = float(cfg.get("max_daily_loss_usd", 0) or 0)
    daily_pnl = float(summary.get("daily_pnl") or 0)
    _lim = _derived_limits_for(float(summary.get("equity") or 0), cfg)
    # Fraction of the way to the hard kill, 0 when green and 1 at the floor.
    kill_used = (min(1.0, daily_pnl / kill_at) if kill_at < 0 and daily_pnl < 0 else 0.0)

    return {
        "equity": summary.get("equity"),
        "peak_equity": round(peak, 2) if equities else None,
        # None, never 0.0, when there is not enough history to measure.
        "drawdown_pct": round(dd_now_pct, 2) if dd_now_pct is not None else None,
        "max_drawdown_pct": round(max_dd_pct, 2) if max_dd_pct is not None else None,
        "win_rate": round(win_rate, 4) if win_rate is not None else None,
        "trades_graded": len(graded),
        "fee_drag_pct": round(fee_drag_pct, 2) if fee_drag_pct is not None else None,
        "fees_pct_total": round(fees_total, 2),
        "daily_pnl": daily_pnl,
        "kill_at_usd": kill_at,
        "kill_used_frac": round(kill_used, 4),
        "mode": str(cfg.get("mode", "UNKNOWN")).upper(),
        # `mode: LIVE` is not the same thing as "can actually trade". Below the
        # structural dust floor the executor refuses every order regardless of
        # mode, and the panel must say so rather than showing a green LIVE badge
        # over an account that cannot place a trade.
        # The smallest equity at which ANY book can trade, from the same
        # derived limits the executor enforces (services/risk). The old global
        # floor ($26 = every book at once) blocked accounts that can trade.
        "min_tradable_equity": ((_lim.min_equity_to_trade_usd or _lim.min_order_notional_usd)
                                if _lim is not None else _min_tradable_equity(cfg)),
        "trade_headline": _lim.headline if _lim is not None else None,
        # Feed health from the last completed scan. A degraded feed reads
        # downstream as a quiet market, so it has to be visible next to the
        # numbers people would otherwise interpret as "nothing is happening".
        "feed": _feed_health(),
        "can_trade": (bool(_lim.can_trade) if _lim is not None
                      else bool(float(summary.get("equity") or 0) >= _min_tradable_equity(cfg))),
        "window_days": round(range_s / 86400, 1),
        "points": len(equities),
        "capital_flows_tracked": bool(cover.get("covered")),
        "drawdown_basis": dd_basis,
        "net_capital_in": round(sum(float(f.get("usd") or 0) for f in flows
                                    if not str(f.get("kind", "")).startswith("_")), 2),
        "flow_events": sum(1 for f in flows
                           if not str(f.get("kind", "")).startswith("_")),
        # Precedence matters: "we cannot measure this" outranks "we measured it
        # on a basis that cannot tell a withdrawal from a loss".
        "drawdown_caveat": (
            (f"not enough equity history to measure a drawdown "
             f"({len(equities)} point{'' if len(equities) == 1 else 's'} in "
             f"{round(range_s / 86400)}d) — this is not a flat account, it is an "
             f"unmeasured one") if too_few
            else "" if cover.get("covered") else
            "equity decline, not necessarily a trading loss: "
            f"{cover.get('reason')} — run "
            "scripts/record_capital_flows.py"),
    }


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
    raw: List[Tuple[int, float]] = []
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


def _require_operator_role(request: Request) -> None:
    """The operator gate: local mode, else a signed-in operator, else 401.

    Until 2026-10-25 a third door existed: a shared PATHIEL_OPERATOR_TOKEN
    header for "machine callers" (the scheduler, the supervisor, smoke
    checks). Every one of those callers is gone, the MCP server calls the
    Python directly, and the operator works from local mode, so the token
    guarded nothing and was one more secret to provision, rotate and leak.
    A deployment is read-only for everyone but a signed-in operator.
    """
    # The single-operator escape hatch means exactly that: one operator, one
    # account, a private network. There are no other tenants to protect from
    # each other, so the role check has nothing to decide.
    if os.environ.get("PATHIEL_PUBLIC_DASHBOARD"):
        return
    # Local mode: the owner on his own machine reads his own book with no
    # session (wave 2 retired the separate sign-in). Loopback peer, no
    # cross-site Origin and no deployment marker, see services/auth/integration.
    from services.auth.integration import is_local_mode
    if is_local_mode(request):
        return
    from services.auth.deps import current_user
    user = current_user(request)
    if user is not None and user.is_operator:
        return
    if user is not None:
        raise HTTPException(status_code=403,
                            detail="this is the house trading account; "
                                   "your own is at /api/dashboard/account")
    raise HTTPException(status_code=401, detail="sign in required", headers={})


# ── live books ───────────────────────────────────────────────────────────────
# (name, config key, thesis one-liner). Sizing + live/shadow status come from
# the live .agent-config.json at request time, so a shadow flip shows on the
# next poll without a server restart.

_BOOKS: List[Tuple[str, str, str]] = [
    ("unlock_short_runin", "unlock_short",
     "Short the run-in 48-72h before large token unlocks (>=1% of circulating). "
     "VALIDATED n=14: +3.75%/sig net25, halves +0.71/+7.06, mc_p=0.0375."),
    ("news_surge_short", "news_surge_short",
     "Short a breaking Google News coverage surge, 15% stop, 1-day hold. "
     "VALIDATED n=255: +1.24%/sig net25, halves +0.58/+2.16, mc_p=0.0005."),
    ("news_surge_multi", "news_surge_multi",
     "The same surge measured across 15 pooled finance/tech firehoses. "
     "VALIDATED n=230: +1.87%/sig net25, halves +1.50/+2.50, mc_p=0.0005."),
    ("social_trending", "social_trending",
     "Long a coin entering CoinGecko's trending list — an attention spike. "
     "VALIDATED n=185: +0.89%/sig net25, halves +0.54/+1.50, mc_p=0.0005."),
    ("xs_reversal", "xs_reversal",
     "Short the top decile of 3d cross-sectional return, but only where funding "
     "has been off the venue baseline — no positioning, nothing to unwind. "
     "VALIDATED n=1995: +2.474%/sig net25, all four quartiles positive, p=0.0000."),
    ("drawdown_ladder", "drawdown_ladder",
     "Buy a 21% drawdown in BTC/ETH/SOL/BNB/XRP on five rungs 7.17% apart, exit at "
     "+5.14% over average entry, 1x, no stop. VALIDATED W-WH2: 2.93x over 6y, "
     "max DD -43.5%, halves 1.52x/1.43x, null p=0.0015. Buy-and-hold did 8.37x."),
    ("copy_trade", "copy_trade",
     "Mirror wallet 0xe282...df29's new positions at leverage_mult x his exposure, "
     "no stop. REFUTED W-CP1 (liquidated in every 2025-03..10 start month, 0.5x-3x; "
     "survivors lose to random entry, p 0.79-0.85). Operator override."),
]

_KNOWN_BOOK_NAMES = frozenset(name for name, _, _ in _BOOKS)

# Books deleted 2026-08-29. Nothing writes their events any more, but the
# session log is append-only and still holds months of them, so the /activity
# classifier has to recognise these names or that history renders as untyped
# `other` rows. Deliberately NOT part of _BOOKS: the books panel shows what can
# trade today, the activity feed shows what happened.


# ── activity feed ────────────────────────────────────────────────────────────
# The session log is a mixed bag of event shapes. The classifier below maps
# every raw line into ONE of a fixed set of typed view models so the /activity
# page never has to render raw JSON. Unknown shapes degrade to type "other"
# with a fields dict the client renders as key-value rows.

# Legacy/aliased event names that are really book events.
# HISTORICAL. These books were deleted 2026-08-29, but their events are already
# written into the session log and the /activity page still has to render that
# history correctly. Deleting a live code path is not a licence to misrender the
# past, so this map stays even though nothing writes these events any more.


# Classify at most the newest N events per cache miss. The deque holds up to
# 250k events; walking all of them for a rare filter would block the event
# loop for ~0.25s. 20k ≈ a day of activity — plenty for a feed page.


# ── flight-log translation: machine reason -> human sentence ────────────────
# Ordered (regex, template) pairs; the first match wins and interpolates its
# captured numbers. Vocabulary mined from the real loop log 2026-07-12.
# Unknown reasons fall back to the raw string — never drop information.


# Machine text must never reach a page verbatim. The old fallback returned the
# raw string, which is how MAIN_ENGINE_DELETED and a parenthetical naming the
# config key `min_tradable_equity_usd` ended up as headline copy on the
# dashboard. Untranslated identifiers still have to say something useful, so
# they are turned into a sentence rather than replaced with "blocked".


# ── citation parsing ─────────────────────────────────────────────────────────
# Web-search citations arrive as "title — url", legacy "url — url", bare
# "url", or already-structured dicts. The templates were hyperlinking the
# WHOLE string, gluing " — https://…" into the href (404s with %20%E2%80%94
# in the address bar — operator screenshot 2026-07-13). Parse once here so
# both /activity and /news render {url, title} objects.


# Time-decay coalescing (operator order 2026-07-12): events younger than this
# window render as INDIVIDUAL slim rows — the constant-flow feel of the old
# terminal — and only fold into coalesced groups once they age out. The server
# stamps `fresh` at classification time; the client re-sweeps on its own clock.


# ── analytics: funnel, book league, funding heat, tapes, coin chart ─────────
# Every panel here is derived from data we ALREADY pay for (the session log,
# the shadow ledgers, the funding/OI accrual file) — the point of this page
# is consumption, not new collection. Zero new network calls except the
# coin-chart candles (one coin at a time, already 90s-cached in hl_client,
# further capped by this route's own TTL).

_DAY_MS = 86_400_000


# Reason codes that name something no longer in the system. They are dropped
# from the funnel rather than translated: there is no discretionary entry path,
# so "no book claimed this coin" is normal operation, not a refusal.


# ── the viewer's own account ─────────────────────────────────────────────────
#
# Every payload below this point except this one describes the DEPLOYMENT's
# trading account: the wallet the loop signs with, its equity, its positions,
# its P&L. That is exactly one account, and _summary_payload reads it from the
# loop's own heartbeat.
#
# Gating those routes behind a session (2026-09-04) stopped strangers reading
# them and did nothing about the real problem: every signed-in user saw the same
# numbers, which are the operator's. Sign in as anyone and the dashboard showed
# $12.94 — someone else's balance, presented as yours.
#
# This reads the account of whoever is looking, and it needs no key to do it.
# Hyperliquid's /info clearinghouseState takes a plain address, so a SIWE login
# already carries everything required: the user proved they control the wallet,
# and we read it read-only. Nothing is stored, nothing is signed, and the
# product cannot trade on their behalf even by accident. Non-custodial is a
# property of the architecture here, not a promise in a policy.

_ACCOUNT_TTL_S = 20.0
_ACCOUNT_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
# Same reasoning as _POSITIONS_CACHE_LOCK.
_ACCOUNT_CACHE_LOCK = threading.Lock()


def _viewer_account_payload(address: str, now: Optional[float] = None) -> Dict[str, Any]:
    """Read-only Hyperliquid state for one address, whoever is signed in.

    An address with no Hyperliquid account is not an error and must not read as
    one: it is the ordinary state of somebody who has just connected a wallet.
    It returns funded=False so the page can say "deposit to get started" rather
    than rendering a row of zeros that look like losses.
    """
    now = now if now is not None else time.time()
    addr = (address or "").lower()
    with _ACCOUNT_CACHE_LOCK:
        hit = _ACCOUNT_CACHE.get(addr)
        if hit and now - hit[0] < _ACCOUNT_TTL_S:
            return hit[1]

    # Whether this viewer IS the account pathiel trades.
    #
    # The distinction the page has to get right. A visitor's wallet and the
    # house book are two different accounts: the loop and the MCP server sign
    # with the deployment's key and have never touched a visitor's, so showing
    # "trades pathiel made" under a visitor's profile would be attributing the
    # operator's record to a stranger — which is the 2026-09-04 leak in a
    # friendlier shape.
    #
    # Computed here rather than by handing the house address to the browser:
    # the answer the page needs is a yes or no, and a non-operator has no reason
    # to be told which address the deployment trades.
    try:
        house = (resolve_user_address() or "").lower()
    except Exception:
        house = ""
    out: Dict[str, Any] = {"address": addr, "funded": False, "equity": 0.0,
                           "available": 0.0, "positions": [], "status": "ok",
                           "is_house": bool(house) and addr == house}
    try:
        from pathiel.client.hl_client import fetch_account_state
        state = fetch_account_state(addr, include_hip3=False) or {}
        equity = float(state.get("equity") or 0.0)
        positions = []
        for entry in (state.get("asset_positions") or []):
            pos = (entry or {}).get("position") or {}
            try:
                szi = float(pos.get("szi") or 0)
            except (TypeError, ValueError):
                continue
            if szi == 0:
                continue
            positions.append({
                "coin": pos.get("coin"),
                "side": "long" if szi > 0 else "short",
                "size": abs(szi),
                "entry_px": float(pos.get("entryPx") or 0) or None,
                "notional": abs(float(pos.get("positionValue") or 0)),
                "unrealized_pnl": float(pos.get("unrealizedPnl") or 0),
            })
        out.update({
            "equity": round(equity, 2),
            "available": round(float(state.get("available") or 0.0), 2),
            "positions": positions,
            # Funded means "this wallet has a Hyperliquid account with something
            # in it". An empty wallet is a normal starting state, not a failure.
            "funded": equity > 0 or bool(positions),
        })
    except Exception as exc:
        # A rate-limited or failing venue read must say so rather than render as
        # a zero balance. Zero is a number a user will believe.
        out["status"] = "unavailable"
        out["error"] = str(exc)[:200]

    with _ACCOUNT_CACHE_LOCK:
        _ACCOUNT_CACHE[addr] = (now, out)
    return out


# ── HTML ─────────────────────────────────────────────────────────────────────
# Page markup lives in pathiel/templates/*.html — plain files, no
# template engine, fully self-contained assets (vendored /static only).
# Loaded once at import; the pages are static shells that poll the JSON APIs.

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


def _load_template(name: str) -> str:
    return (_TEMPLATE_DIR / name).read_text(encoding="utf-8")


_PUBLIC_HTML = _load_template("oil_desk.html")
_ACCOUNT_HTML = _load_template("account.html")


# ── route registration ──────────────────────────────────────────────────────


def register_routes(app: FastAPI) -> None:
    """Mount the dashboard pages and JSON APIs onto an existing FastAPI app.
    Pages: / (the Desk landing page, parameterized by desk id, default
    "oil" — docs/overhaul/DESK_UI_2026-10-23.md), /account. /activity,
    /news, /analytics, /trends and their JSON routes were removed
    2026-10-23 along with the bot UI they served; /api/dashboard/positions
    stays (tests/test_account_reads_shared.py exercises it directly as part
    of the shared-account-read contract, independent of any page). The
    former /config and /operator pages were deleted by operator order
    2026-07-12 — those paths 404 by design; token-gated actions live in
    server.py's /api/agent + /api/hl endpoints, with the token entered via
    the Desk page's header (localStorage).

    /account (wave-2 onboarding, 2026-10-17) is a static shell like every
    other page here: it reads contracts/accounts/models.py's
    ActiveSummary and contracts/risk/models.py's DerivedLimits entirely
    client-side, over routes this module does not own and does not mount —
    the accounts + risk routers land on pathiel.server separately, mounted
    by the integrator once every wave-2 agent's work merges (SWARM_RULES
    section 10)."""

    # no-store on the pages so a server restart isn't masked by a cached
    # HTML shell that pre-dates the new JS. The JSON endpoints below are fine
    # to cache for their poll interval.
    _NO_CACHE_HEADERS = {"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"}

    @app.get("/", response_class=HTMLResponse)
    async def public_dashboard() -> HTMLResponse:
        """The Oil Desk — the one screen, per docs/overhaul/OIL_DESK_UI_2026-10-23.md.
        Shows the onboarding wizard first for a brand-new visitor (client-side,
        on /api/session's needs_onboarding); the same page becomes the Oil
        Desk once an account is connected."""
        return HTMLResponse(content=_PUBLIC_HTML, headers=_NO_CACHE_HEADERS)

    @app.get("/account", response_class=HTMLResponse)
    async def account_page() -> HTMLResponse:
        """Account & Risk: the active Hyperliquid account (contracts/accounts)
        and the risk preset that scales sizing to its equity
        (contracts/risk). Bounces to "/" client-side when SessionInfo says
        needs_onboarding — there is nothing to manage yet, and "/" is where
        that starts."""
        return HTMLResponse(content=_ACCOUNT_HTML, headers=_NO_CACHE_HEADERS)

    @app.get("/api/dashboard/account")
    async def dashboard_account(request: Request) -> JSONResponse:
        """The signed-in wallet's OWN Hyperliquid account, read-only.

        This is the only account route that is not the house's. It needs no
        stored key: /info clearinghouseState takes a plain address, and SIWE
        already proved the caller controls it.
        """
        from services.auth.deps import current_user
        user = await _run_off_loop(current_user, request)   # a session-store lookup
        if user is None:
            return JSONResponse({"detail": "sign in required", "auth_required": True},
                                status_code=401)
        return JSONResponse(await _run_off_loop(_viewer_account_payload, user.address))

    @app.get("/api/dashboard/positions", dependencies=[Depends(_require_operator_role)])
    async def dashboard_positions() -> JSONResponse:
        """Open positions + DSL tracker state, operator-gated. No page calls
        this any more (the Desk page's Wallets section reads
        /api/accounts/{id}/positions instead) — kept because
        tests/test_account_reads_shared.py exercises it directly as the
        second leg of the shared-account-read-cache contract
        (pathiel.client.account_snapshot), a capability independent of any
        one page."""
        return JSONResponse(await _run_off_loop(_positions_payload))  # already 5s-cached internally

    @app.get("/api/dashboard/risk", dependencies=[Depends(_require_operator_role)])
    async def dashboard_risk(range_s: int = Query(7_776_000, ge=86_400,
                                                  le=31_536_000)) -> JSONResponse:
        """Drawdown, fee drag, win rate, distance to the hard kill, feed
        health. Operator-gated; no page renders this any more (it was the
        old landing page's risk band) — kept because pathiel/metrics.py's
        /metrics endpoint reads it (CAN_TRADE/DRAWDOWN_PCT/MAX_DRAWDOWN_PCT
        gauges) and tests/test_risk_panel.py tests its payload shape
        directly over HTTP, both independent of any page."""
        return JSONResponse(await _run_off_loop(
            _ttl_cached, f"risk:{range_s}", 60.0, lambda: _risk_payload(range_s)))
