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
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------- pure part
def assess(state: Dict[str, Any], orders: List[Dict[str, Any]], dex: str = "") -> Dict[str, Any]:
    """Positions with their protection. `naked` is true for any position whose
    reduce-only stop orders cover less than its size."""
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
        })
    return {"dex": dex or "main", "equity_usd": float(ms.get("accountValue") or 0),
            "notional_usd": float(ms.get("totalNtlPos") or 0), "positions": positions,
            "any_naked": any(p["naked"] for p in positions)}


# ------------------------------------------------------------------ live leg
def _fetch_raw(user: str, dex: str) -> Dict[str, Any]:
    from pathiel.client.hl_client import _http_post
    body: Dict[str, Any] = {"type": "clearinghouseState", "user": user}
    orders: Dict[str, Any] = {"type": "frontendOpenOrders", "user": user}
    if dex:
        body["dex"] = dex
        orders["dex"] = dex
    state = _http_post("/info", body) or {}
    raw_orders = _http_post("/info", orders)
    return {"state": state if isinstance(state, dict) else {},
            "orders": raw_orders if isinstance(raw_orders, list) else []}


def _dexes_with_margin(user: str) -> List[str]:
    """main ("") plus every HIP-3 dex this account holds equity or a
    position on, read from the shared account snapshot — one cheap
    aggregated read instead of probing every registered HIP-3 dex one by
    one."""
    from pathiel.client.account_snapshot import account_state
    state = account_state(user, include_hip3=True)
    dexes = {""}
    for d, v in (state.get("dex_equity") or {}).items():
        try:
            if d and float(v or 0) != 0:
                dexes.add(d)
        except (TypeError, ValueError):
            continue
    for p in state.get("asset_positions") or []:
        coin = ((p or {}).get("position") or {}).get("coin") or ""
        if ":" in coin:
            dexes.add(coin.split(":", 1)[0])
    return sorted(dexes, key=lambda d: (d != "", d))


def fetch_positions_for_account(address: str, *, dexes: Optional[List[str]] = None) -> Dict[str, Any]:
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
    for dex in target_dexes:
        try:
            raw = _fetch_raw(address, dex)
            out_accounts.append(assess(raw["state"], raw["orders"], dex))
        except Exception as e:  # noqa: BLE001 - one dead dex must not blank the others
            logger.warning(f"[accounts.positions] read failed for {address} on dex {dex or 'main'}: {e}")
            out_accounts.append({"dex": dex or "main", "equity_usd": 0.0, "notional_usd": 0.0,
                                 "positions": [], "any_naked": False, "error": str(e)})
    return {"user": address, "accounts": out_accounts,
            "any_naked": any(a["any_naked"] for a in out_accounts)}


__all__ = ["assess", "fetch_positions_for_account"]
