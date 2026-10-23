"""Positions + stop coverage for any Hyperliquid address, across the main
dex and every HIP-3 dex it holds margin on.

`assess()` is the pure part, moved out of `scripts/watch_wallet.py`
(2026-10-23) unchanged so the Oil Desk dashboard's Wallets section
(`GET /api/accounts/{id}/positions`) and that script's CLI share one
implementation. The script stays a thin CLI that imports this.

`fetch_positions_for_account()` is the live leg: it reads the account's
already-shared snapshot (`pathiel.client.account_snapshot`) to find which
dexes it actually holds margin on, then re-reads each of THOSE dexes'
`clearinghouseState` + `frontendOpenOrders` directly (the shared snapshot
only carries positions, not the reduce-only orders protecting them, and
`assess()` needs both to flag a naked position). Read-only: no order is
placed or cancelled. Uses `pathiel.client.hl_client._http_post` (the same
authenticated HTTP leg every other in-process Hyperliquid read uses), not
the public-script's curl subprocess.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------- pure part
def _f(v: Any) -> Optional[float]:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _mark_fields(side: str, entry: float, mark: Optional[float], stops: List[Any], tps: List[Any]) -> Dict[str, Any]:
    """Where the position stands against its own levels, from the live mark:
    P&L %, distance to the nearest stop and target (as % of mark), and R so
    far (gain over the distance to the stop). None when a piece is missing."""
    out: Dict[str, Any] = {"mark_px": mark, "pnl_pct": None, "to_stop_pct": None, "to_tp_pct": None, "r_now": None}
    if not mark or not entry:
        return out
    sign = 1.0 if side == "long" else -1.0
    out["pnl_pct"] = round(sign * (mark - entry) / entry * 100, 3)
    stop_vals = [v for v in (_f(x) for x in stops) if v]
    tp_vals = [v for v in (_f(x) for x in tps) if v]
    if stop_vals:
        stop = max(stop_vals) if side == "long" else min(stop_vals)      # the nearest stop
        out["to_stop_pct"] = round(abs(mark - stop) / mark * 100, 3)
        risk = abs(entry - stop)
        if risk > 0:
            out["r_now"] = round(sign * (mark - entry) / risk, 2)
    if tp_vals:
        tp = min(tp_vals) if side == "long" else max(tp_vals)             # the nearest target
        out["to_tp_pct"] = round(abs(tp - mark) / mark * 100, 3)
    return out


def assess(state: Dict[str, Any], orders: List[Dict[str, Any]], dex: str = "",
           mids: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Positions with their protection. `naked` is true for any position whose
    reduce-only stop orders cover less than its size. With `mids` (coin -> mark)
    each position also carries mark_px, pnl_pct, to_stop_pct, to_tp_pct, r_now."""
    ms = state.get("marginSummary") or {}
    positions = []
    for row in state.get("assetPositions") or []:
        p = (row or {}).get("position") or {}
        coin = p.get("coin")
        try:
            szi = float(p.get("szi") or 0)
        except (TypeError, ValueError):
            continue
        if not coin or szi == 0:
            continue
        mine = [o for o in orders if o.get("coin") == coin and o.get("reduceOnly")]
        stops = [o for o in mine if "Stop" in str(o.get("orderType", ""))]
        tps = [o for o in mine if "Take Profit" in str(o.get("orderType", ""))]
        covered = sum(float(o.get("sz") or 0) for o in stops)
        lev = p.get("leverage") or {}
        entry = float(p.get("entryPx") or 0)
        liq = p.get("liquidationPx")
        liq_f = float(liq) if liq not in (None, "") else None
        positions.append({
            "coin": coin, "side": "long" if szi > 0 else "short", "size": abs(szi), "entry_px": entry,
            "leverage": lev.get("value") if isinstance(lev, dict) else lev,
            "margin_type": lev.get("type") if isinstance(lev, dict) else None,
            "notional_usd": round(abs(float(p.get("positionValue") or 0)), 2),
            "upnl_usd": round(float(p.get("unrealizedPnl") or 0), 2),
            "liq_px": liq_f,
            "liq_dist_pct": (round(abs(entry - liq_f) / entry * 100, 3) if liq_f and entry else None),
            "stop_px": [o.get("triggerPx") for o in stops], "tp_px": [o.get("triggerPx") for o in tps],
            "stop_covers": covered, "naked": covered + 1e-9 < abs(szi),
            **_mark_fields("long" if szi > 0 else "short", entry, _f((mids or {}).get(coin)),
                           [o.get("triggerPx") for o in stops], [o.get("triggerPx") for o in tps]),
        })
    return {"dex": dex or "main", "equity_usd": float(ms.get("accountValue") or 0),
            "notional_usd": float(ms.get("totalNtlPos") or 0), "positions": positions,
            "any_naked": any(p["naked"] for p in positions)}


# ------------------------------------------------------------------ live leg
def _fetch_raw(user: str, dex: str) -> Dict[str, Any]:
    """clearinghouseState + frontendOpenOrders (+ allMids) for one dex.

    Strict: a read the venue never answered (rate budget exhausted, 429,
    timeout) RAISES HLRequestError instead of posing as an empty state.
    2026-10-24: the server's 120 weight/min budget starved the xyz read
    behind five dust dexes; the starved read came back as {} and the Desk
    page rendered the live Brent position as "No open positions" for 15 s
    at a time. A missing answer is an error, never a flat account.
    """
    from pathiel.client.hl_client import _http_post_strict
    body: Dict[str, Any] = {"type": "clearinghouseState", "user": user}
    orders: Dict[str, Any] = {"type": "frontendOpenOrders", "user": user}
    if dex:
        body["dex"] = dex
        orders["dex"] = dex
    mids_req: Dict[str, Any] = {"type": "allMids"}
    if dex:
        mids_req["dex"] = dex
    state = _http_post_strict("/info", body)
    raw_orders = _http_post_strict("/info", orders)
    mids = _http_post_strict("/info", mids_req)
    return {"state": state if isinstance(state, dict) else {},
            "orders": raw_orders if isinstance(raw_orders, list) else [],
            "mids": mids if isinstance(mids, dict) else {}}


# ── small in-process TTL caches ──────────────────────────────────────────────
# The Desk page polls positions for every account; each poll per dex is three
# venue reads, one of them frontendOpenOrders at weight 20. Without a cache the
# dashboard alone burned the 1,200/min budget (2026-10-23 17:40 UTC: 227
# starved open-order reads in 15 minutes, every Wallets request timing out).
import time as _time

_now = _time.monotonic
_CACHE: Dict[str, Any] = {}
POSITIONS_TTL_S = 15.0
DEXES_TTL_S = 120.0
# A HIP-3 dex holding less than this (and no position) is dust left behind
# by a closed trade; reading it costs the same 24 weight as a funded dex.
DUST_USD = 1.0


def _cached(key: str, ttl_s: float, build: Callable[[], Any],
            good: Optional[Callable[[Any], bool]] = None) -> Any:
    """TTL cache that keeps the last GOOD value. When `build` raises, or
    returns a value `good` rejects, the previous good value is served again
    (with a `stale_s` age when it is a dict) instead of caching the failure:
    a venue hiccup must not replace a live position with a blank for a TTL.
    With no previous value the failure propagates / the bad value is served
    uncached, so the next call retries at once."""
    hit = _CACHE.get(key)
    now = _now()
    if hit is not None and now - hit[0] < ttl_s:
        return hit[1]
    try:
        val = build()
    except Exception:
        if hit is None:
            raise
        return _stale(hit, now)
    if good is not None and not good(val):
        if hit is not None:
            return _stale(hit, now)
        return val                       # nothing better to serve; do not cache it
    _CACHE[key] = (now, val)
    return val


def _stale(hit: Any, now: float) -> Any:
    at, val = hit
    if isinstance(val, dict):
        return {**val, "stale_s": round(now - at, 1)}
    return val


def clear_cache() -> None:
    _CACHE.clear()


def _dexes_with_margin(user: str) -> List[str]:
    return _cached(f"dexes:{user.lower()}", DEXES_TTL_S, lambda: _dexes_with_margin_uncached(user),
                   good=bool)


def _dexes_with_margin_uncached(user: str) -> List[str]:
    """Every dex this account holds at least DUST_USD of equity or a
    position on, read from the shared account snapshot — one cheap
    aggregated read instead of probing every registered HIP-3 dex one by
    one. Main ("") only when it qualifies too, or when nothing does (a flat
    account still reads as main-only). Dust dexes are skipped: each one
    costs 24 weight a poll, and on 2026-10-24 four of them (a few cents
    each) starved the one dex holding the live position."""
    from pathiel.client.account_snapshot import account_state
    state = account_state(user, include_hip3=True)
    dexes = set()
    # Main qualifies through ITS OWN equity (dex_equity[""]), never the
    # snapshot's top-level total, which sums every dex.
    for d, v in (state.get("dex_equity") or {}).items():
        try:
            if float(v or 0) >= DUST_USD:
                dexes.add(d or "")
        except (TypeError, ValueError):
            continue
    for p in state.get("asset_positions") or []:
        coin = ((p or {}).get("position") or {}).get("coin") or ""
        dexes.add(coin.split(":", 1)[0] if ":" in coin else "")
    if not dexes:
        dexes = {""}
    return sorted(dexes, key=lambda d: (d != "", d))


def fetch_positions_for_account(address: str, *, dexes: Optional[List[str]] = None) -> Dict[str, Any]:
    """Cached for POSITIONS_TTL_S per (address, dexes); see _fetch_positions_uncached."""
    key = f"positions:{address.lower()}:{','.join(dexes) if dexes is not None else '*'}"
    return _cached(key, POSITIONS_TTL_S, lambda: _fetch_positions_uncached(address, dexes=dexes),
                   good=lambda v: not v.get("errors"))


def _fetch_positions_uncached(address: str, *, dexes: Optional[List[str]] = None) -> Dict[str, Any]:
    """assess() per dex this account holds margin on. Same shape as
    scripts/watch_wallet.py's CLI output:
    {"user", "accounts": [...one assess() dict per dex...], "any_naked"}.

    `dexes` overrides dex discovery (tests; a caller that already knows
    which dexes matter). A per-dex read failure degrades that dex to an
    empty, non-naked account rather than failing the whole response — one
    dead HIP-3 dex must not blank out every other dex's positions.
    """
    target_dexes = dexes if dexes is not None else _dexes_with_margin(address)
    out_accounts = []
    errors: List[str] = []
    for dex in target_dexes:
        try:
            raw = _fetch_raw(address, dex)
            out_accounts.append(assess(raw["state"], raw["orders"], dex, mids=raw.get("mids")))
        except Exception as e:  # noqa: BLE001 - one dead dex must not blank the others
            logger.warning(f"[accounts.positions] read failed for {address} on dex {dex or 'main'}: {e}")
            errors.append(dex or "main")
            out_accounts.append({"dex": dex or "main", "equity_usd": 0.0, "notional_usd": 0.0,
                                 "positions": [], "any_naked": False, "error": str(e)})
    out: Dict[str, Any] = {"user": address, "accounts": out_accounts,
                           "any_naked": any(a["any_naked"] for a in out_accounts)}
    if errors:
        out["errors"] = errors            # the cache keeps the last good snapshot instead
    return out


__all__ = ["assess", "fetch_positions_for_account"]
