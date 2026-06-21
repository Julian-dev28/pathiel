"""External-alpha signal sources — validated edges that live OUTSIDE the candle/AI
pipeline. Two sources, both backtested OOS (scripts/edge_smartmoney.py,
edge_basis_gap.py):

  1. smart_money  — copy fresh entries of HL traders who were skilled in-sample
                    (+0.49%/trade, 60% win, 17k OOS trades, walk-forward).
  2. basis_gap    — tokenized-stock perps catch up to their real underlying's
                    overnight gap (+0.21%/trade, OOS-robust on a stock basket).

Each `*_signals()` returns a list of dicts {coin, side, source, reason, strength}
that the trading loop converts into a synthetic analysis and routes through the
EXISTING gates/sizing/exit (route_verdict -> maybe_execute). Config-gated and
shadow-aware; a fetch outage degrades to "no signal" and never breaks the loop.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List

import httpx

logger = logging.getLogger(__name__)

_LEADERBOARD = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
_INFO = "https://api.hyperliquid.xyz/info"
_TRADERS_CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                              ".smart-money-traders.json")
_SEEN_FILLS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                           ".smart-money-seen.json")

# Tokenized-stock perp -> Yahoo ticker (the basket that was OOS-robust + liquid).
BASIS_BASKET = {
    "xyz:SNDK": "SNDK", "xyz:AMD": "AMD", "xyz:INTC": "INTC", "xyz:ARM": "ARM",
    "xyz:MU": "MU", "xyz:MRVL": "MRVL",
}


# ───────────────────────── smart money ─────────────────────────
def _post(body: Dict[str, Any], retries: int = 3):
    for _ in range(retries):
        try:
            r = httpx.post(_INFO, json=body, timeout=15)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        time.sleep(1.0)
    return None


def compute_skilled_traders(pool: int = 80, max_keep: int = 30,
                            min_acct: float = 100_000) -> List[str]:
    """Leaderboard -> candidates (month ROI>0, real size) -> keep those with positive
    realized PnL over the FIRST 60% of their fills (in-sample skill). Cached to disk;
    callers refresh on an interval. Returns trader addresses."""
    try:
        rows = httpx.get(_LEADERBOARD, headers={"User-Agent": "Mozilla/5.0"}, timeout=90).json()["leaderboardRows"]
    except Exception as e:
        logger.warning(f"[smart_money] leaderboard fetch failed: {e}")
        return []
    cand = []
    for x in rows:
        try:
            av = float(x.get("accountValue") or 0)
            wp = dict(x.get("windowPerformances") or [])
            mo = wp.get("month") or {}
            roi = float(mo.get("roi") or 0); vlm = float(mo.get("vlm") or 0)
            if av >= min_acct and vlm >= 1_000_000 and roi > 0:
                cand.append((roi, x["ethAddress"]))
        except Exception:
            pass
    cand.sort(reverse=True)
    skilled = []
    for _, addr in cand[:pool]:
        fl = _post({"type": "userFills", "user": addr}) or []
        if len(fl) < 40:
            continue
        fl = sorted(fl, key=lambda f: f.get("time", 0))
        split = int(len(fl) * 0.6)
        if sum(float(f.get("closedPnl") or 0) for f in fl[:split]) > 0:
            skilled.append(addr)
        if len(skilled) >= max_keep:
            break
    if skilled:
        try:
            json.dump({"ts": time.time(), "traders": skilled}, open(_TRADERS_CACHE, "w"))
        except Exception:
            pass
    return skilled


def _load_skilled(refresh_hours: float) -> List[str]:
    try:
        d = json.load(open(_TRADERS_CACHE))
        if time.time() - d.get("ts", 0) < refresh_hours * 3600 and d.get("traders"):
            return d["traders"]
    except Exception:
        pass
    return compute_skilled_traders()


def smart_money_signals(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Fresh open-entry signals from skilled traders since the last poll. Dedups via a
    persisted seen-fill set so each copied entry fires once."""
    cfg = config.get("smart_money") or {}
    if not bool(cfg.get("enabled", False)):
        return []
    traders = _load_skilled(float(cfg.get("refresh_traders_hours", 24)))
    if not traders:
        return []
    try:
        seen = set(json.load(open(_SEEN_FILLS)).get("hashes", []))
    except Exception:
        seen = set()
    fresh_window_ms = float(cfg.get("max_signal_age_min", 30)) * 60_000
    now = time.time() * 1000
    out, new_seen = [], []
    for addr in traders:
        fl = _post({"type": "userFills", "user": addr}) or []
        for f in fl[-20:]:
            h = f.get("hash", "")
            if not h or h in seen:
                continue
            new_seen.append(h)
            d = (f.get("dir") or "")
            side = "long" if "Open Long" in d else "short" if "Open Short" in d else None
            if side and (now - int(f.get("time", 0))) <= fresh_window_ms:
                out.append({"coin": f["coin"], "side": side, "source": "smart_money",
                            "reason": f"copy skilled trader {addr[:8]} {d}", "strength": 1.0})
    if new_seen:
        try:
            json.dump({"hashes": list(seen | set(new_seen))[-5000:]}, open(_SEEN_FILLS, "w"))
        except Exception:
            pass
    return out


# ───────────────────────── basis overnight-gap ─────────────────────────
def _yahoo_hourly(ticker: str) -> Dict[int, float]:
    try:
        res = httpx.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
                        params={"interval": "1h", "range": "5d"},
                        headers={"User-Agent": "Mozilla/5.0"}, timeout=15).json()["chart"]["result"][0]
        return {int(t) // 3600: c for t, c in zip(res["timestamp"], res["indicators"]["quote"][0]["close"]) if c is not None}
    except Exception:
        return {}


def basis_gap_signals(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Fire when a basket stock just opened with a gap > threshold and we're inside the
    catch-up window — trade the perp in the gap direction."""
    cfg = config.get("basis_gap") or {}
    if not bool(cfg.get("enabled", False)):
        return []
    min_gap = float(cfg.get("min_gap_pct", 0.5)) / 100.0
    window_h = float(cfg.get("catchup_hours", 4))
    now_h = int(time.time()) // 3600
    out = []
    basket = cfg.get("basket") or list(BASIS_BASKET.keys())
    for perp in basket:
        ticker = BASIS_BASKET.get(perp)
        if not ticker:
            continue
        und = _yahoo_hourly(ticker)
        if len(und) < 8:
            continue
        uh = sorted(und)
        # find the most recent session open (a >4h jump in market hours)
        open_h = None
        for i in range(len(uh) - 1, 0, -1):
            if uh[i] - uh[i - 1] > 4:
                open_h, prev_close_h = uh[i], uh[i - 1]
                break
        if open_h is None or und[prev_close_h] <= 0:
            continue
        # only while inside the catch-up window after this open
        if not (0 <= now_h - open_h <= window_h):
            continue
        gap = und[open_h] / und[prev_close_h] - 1
        if abs(gap) < min_gap:
            continue
        key = f"{perp}:{open_h}"        # one signal per (coin, session-open)
        if key in _seen_gaps():
            continue
        _mark_gap(key)
        out.append({"coin": perp, "side": "long" if gap > 0 else "short",
                    "source": "basis_gap",
                    "reason": f"{ticker} opened {gap*100:+.1f}% gap; perp catch-up",
                    "strength": min(1.0, abs(gap) / 0.02)})
    return out


_SEEN_GAPS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                          ".basis-gap-seen.json")


def _seen_gaps() -> set:
    try:
        return set(json.load(open(_SEEN_GAPS)).get("keys", []))
    except Exception:
        return set()


def _mark_gap(key: str) -> None:
    try:
        json.dump({"keys": (list(_seen_gaps()) + [key])[-500:]}, open(_SEEN_GAPS, "w"))
    except Exception:
        pass


def external_alpha_signals(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """All enabled external-alpha signals for this cycle. Each source is wrapped so one
    outage can't suppress the other or break the loop."""
    sigs: List[Dict[str, Any]] = []
    for fn in (smart_money_signals, basis_gap_signals):
        try:
            sigs.extend(fn(config))
        except Exception as e:
            logger.warning(f"[external_alpha] {fn.__name__} failed: {e}")
    return sigs
