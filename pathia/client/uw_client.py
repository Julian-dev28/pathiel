"""Unusual Whales API client — options-flow / dark-pool alt-data (2026-07-23).

Thesis: smart-money options flow LEADS spot. Our best book trades xyz tokenized US
equities (xyz:AAPL, xyz:NVDA, ...) — UW gives the options flow on the SAME underlyings.
This client wraps the directional endpoints; a W-UW backtest adjudicates edge vs a matched
null before any capital, same discipline as every other signal.

Auth: Bearer UW_API_KEY (from env / .env.local). Best-effort: never raises into a caller,
returns None/[] on failure. Optional on-disk cache so backtests don't re-hit the API.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

BASE = "https://api.unusualwhales.com"
_CACHE_DIR = os.environ.get("UW_CACHE_DIR", "")


def _key() -> str:
    k = os.environ.get("UW_API_KEY", "").strip()
    if not k:
        # lazy-load .env.local so scripts don't have to export it
        env = Path(__file__).resolve().parents[2] / ".env.local"
        if env.exists():
            for line in env.read_text().splitlines():
                if line.startswith("UW_API_KEY"):
                    k = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    return k


def _get(path: str, params: Optional[Dict[str, Any]] = None,
         cache_key: Optional[str] = None) -> Optional[Any]:
    if cache_key and _CACHE_DIR:
        cp = Path(_CACHE_DIR) / f"{cache_key}.json"
        if cp.exists():
            try:
                return json.loads(cp.read_text())
            except Exception:
                pass
    key = _key()
    if not key:
        return None
    clean = {k: v for k, v in (params or {}).items() if v is not None}
    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
    for attempt in range(3):
        try:
            resp = requests.get(BASE + path, params=clean, headers=headers, timeout=30)
            if resp.status_code == 429 and attempt < 2:
                time.sleep(2.0 * (attempt + 1))
                continue
            if resp.status_code != 200:
                logger.warning(f"[uw] {path} returned HTTP {resp.status_code} — "
                               f"no data for this call")
                return None
            data = resp.json()
            if cache_key and _CACHE_DIR:
                Path(_CACHE_DIR).mkdir(parents=True, exist_ok=True)
                (Path(_CACHE_DIR) / f"{cache_key}.json").write_text(json.dumps(data))
            return data
        except Exception as exc:
            if attempt < 2:
                time.sleep(1.0)
                continue
            # After three failures, None is indistinguishable from "the API
            # legitimately had nothing" — which is how a dead upstream becomes a
            # backtest that quietly measures no signal at all.
            logger.warning(f"[uw] {path} failed after 3 attempts "
                           f"({type(exc).__name__}: {exc}) — returning no data")
            return None
    return None


def _f(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def net_prem_daily(ticker: str, date: Optional[str] = None) -> Optional[Dict[str, float]]:
    """Sum the intraday net-prem ticks into a single day's directional signal.
    Returns {net_call_premium, net_put_premium, net_premium (call-put), net_call_volume,
    net_put_volume}. `date` = YYYY-MM-DD (defaults to latest). None on failure."""
    ck = f"netprem_{ticker}_{date or 'latest'}"
    raw = _get(f"/api/stock/{ticker}/net-prem-ticks", {"date": date}, cache_key=ck)
    if not raw:
        return None
    rows = raw.get("data") if isinstance(raw, dict) else raw
    if not isinstance(rows, list) or not rows:
        return None
    ncp = sum(_f(r.get("net_call_premium")) for r in rows)
    npp = sum(_f(r.get("net_put_premium")) for r in rows)
    ncv = sum(_f(r.get("net_call_volume")) for r in rows)
    npv = sum(_f(r.get("net_put_volume")) for r in rows)
    # ask-side = aggressive buyers lifting the offer; bid-side = hitting the bid.
    call_ask = sum(_f(r.get("call_volume_ask_side")) for r in rows)
    call_bid = sum(_f(r.get("call_volume_bid_side")) for r in rows)
    put_ask = sum(_f(r.get("put_volume_ask_side")) for r in rows)
    put_bid = sum(_f(r.get("put_volume_bid_side")) for r in rows)
    call_vol = sum(_f(r.get("call_volume")) for r in rows)
    put_vol = sum(_f(r.get("put_volume")) for r in rows)
    return {"net_call_premium": ncp, "net_put_premium": npp,
            "net_premium": ncp - npp, "net_call_volume": ncv, "net_put_volume": npv,
            "net_volume": ncv - npv,
            # aggression: net ask-side lifting on calls minus on puts (bullish urgency)
            "aggression": (call_ask - call_bid) - (put_ask - put_bid),
            "call_volume": call_vol, "put_volume": put_vol,
            # put/call ratio (contrarian when extreme): low pcr = greedy
            "pcr": put_vol / (call_vol + 1.0),
            "n_ticks": len(rows)}


def flow_alerts(ticker: str, limit: int = 50) -> List[Dict[str, Any]]:
    raw = _get(f"/api/stock/{ticker}/flow-alerts", {"limit": limit})
    if isinstance(raw, dict):
        d = raw.get("data")
        return d if isinstance(d, list) else []
    return raw if isinstance(raw, list) else []


def greek_daily(ticker: str) -> Dict[str, Dict[str, float]]:
    """Full daily dealer-greek history (~250d) in one call -> {date: {net_gamma, net_delta,
    net_charm, net_vanna}}. net = call + put (put legs are already signed negative). {} on fail."""
    raw = _get(f"/api/stock/{ticker}/greek-exposure", cache_key=f"gex_{ticker}")
    rows = raw.get("data") if isinstance(raw, dict) else raw
    out: Dict[str, Dict[str, float]] = {}
    for r in (rows or []):
        d = r.get("date")
        if not d:
            continue
        out[d] = {
            "net_gamma": _f(r.get("call_gamma")) + _f(r.get("put_gamma")),
            "net_delta": _f(r.get("call_delta")) + _f(r.get("put_delta")),
            "net_charm": _f(r.get("call_charm")) + _f(r.get("put_charm")),
            "net_vanna": _f(r.get("call_vanna")) + _f(r.get("put_vanna")),
        }
    return out


def has_key() -> bool:
    return bool(_key())


if __name__ == "__main__":
    print("UW key present:", has_key())
    np = net_prem_daily("AAPL")
    print("AAPL net premium (latest day):", np)
    fa = flow_alerts("NVDA", limit=3)
    print(f"NVDA flow alerts: {len(fa)} (sample type: {fa[0].get('type') if fa else None})")
