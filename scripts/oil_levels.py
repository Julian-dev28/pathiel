"""Deterministic levels for the Claude-run oil loop (.claude/skills/oil-tick).

Reads Hyperliquid's public candles and prints the numbers a tick needs, so
the model never eyeballs ranges or does R arithmetic in its head:

  python3 scripts/oil_levels.py                       # xyz:BRENTOIL
  python3 scripts/oil_levels.py --coin xyz:CL
  python3 scripts/oil_levels.py --check 107.20 106.70 108.45   # entry stop tp -> R
  python3 scripts/oil_levels.py --coin xyz:GOLD --next long --thesis-entry 4100 --max-lev 25

`--next SIDE` prints the live entry rule's answer at the mid
(services/strategies/next_trade.next_trade: the 24h-range rule, leverage
fit, and the breakout entry where a coin has one) plus, when it trades now, the exact
`place_bracket` arguments: `tp_px` is the STRUCTURAL target, because
place_bracket applies the 0.5% offset itself.

Output is one JSON object: mid, the last CLOSED 1h candle, 8h/24h/48h
range, ATR14 on 1h, where the mid sits in the 24h range, and the last four
closed 15m candles' range. Read-only; no key, no account.

This is a thin CLI: the actual math (closed_only/atr/summarize/check) lives
in services/strategies/levels.py, which the dashboard's
GET /api/desks/oil/levels route also calls, so the two never drift apart.
Only the network leg stays in this file: a plain urllib POST. The system
python3 the desk-tick agent runs has no CA bundle of its own (python.org's
macOS build), so _ssl_context() loads certifi's, or the OS bundle when
certifi is not installed. It used to shell out to curl for the same reason;
the plugin directory reads a curl call as download-and-run, and urllib
needs no second program.
"""
from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import time
import urllib.request
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from contracts import runtime  # noqa: E402
from services.strategies.levels import HOUR_MS, check, summarize  # noqa: E402
from services.strategies.next_trade import (breakout_for, leverage_fit_for, next_trade,  # noqa: E402
                                            stop_buffer_for)

# The resolved network's /info URL (contracts/runtime). Unset: mainnet, as before.
INFO_URL = runtime.info_url()


# OS CA bundles, for a python3 without certifi: macOS and the BSDs, Debian and
# Ubuntu, Fedora and RHEL.
_SYSTEM_CA_FILES = ("/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt",
                    "/etc/pki/tls/certs/ca-bundle.crt")


def _ssl_context() -> ssl.SSLContext:
    """The default context plus a CA bundle that exists: certifi's when it is
    importable, else the first OS bundle on disk. Verification stays on."""
    ctx = ssl.create_default_context()
    try:
        import certifi
        ctx.load_verify_locations(certifi.where())
        return ctx
    except ImportError:
        pass
    for path in _SYSTEM_CA_FILES:
        if os.path.isfile(path):
            ctx.load_verify_locations(path)
            break
    return ctx


def _post(body: Dict[str, Any]) -> Any:
    req = urllib.request.Request(INFO_URL, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10, context=_ssl_context()) as resp:
        return json.loads(resp.read())


def fetch_candles(coin: str, interval: str, hours: int, now_ms: Optional[int] = None) -> List[Dict[str, Any]]:
    now_ms = now_ms or int(time.time() * 1000)
    return _post({"type": "candleSnapshot", "req": {
        "coin": coin, "interval": interval, "startTime": now_ms - hours * HOUR_MS, "endTime": now_ms}})


def fetch_mid(coin: str) -> float:
    dex = coin.split(":")[0] if ":" in coin else ""
    body: Dict[str, Any] = {"type": "allMids"}
    if dex:
        body["dex"] = dex
    return float(_post(body)[coin])


def next_order(coin: str, side: str, levels: Dict[str, Any], thesis_entry: Optional[float],
               leverage: float, max_leverage: float, thesis_age_min: Optional[float] = None,
               entry_delay_min: Optional[float] = None) -> Dict[str, Any]:
    """next_trade() for `coin` off a summarize() answer, plus `place_bracket`
    args when it trades now (market: no entry_px; tp_px structural). Pure."""
    r24 = levels.get("range_24h") or {}
    mid = levels.get("mid")
    if mid is None or r24.get("low") is None or r24.get("high") is None:
        return {"ok": False, "reason": "no mid or 24h range", "coin": coin}
    buf = stop_buffer_for(coin, levels.get("atr14_1h"), mid)
    if buf is None:
        return {"ok": False, "reason": "no ATR for the stop buffer", "coin": coin}
    out = next_trade(side, float(mid), float(r24["low"]), float(r24["high"]), thesis_entry=thesis_entry,
                     leverage=leverage, coin_max_leverage=max_leverage, stop_buffer=buf,
                     leverage_fit=leverage_fit_for(coin),
                     breakout=levels.get("breakout_24h") if breakout_for(coin) else None,
                     thesis_age_min=thesis_age_min, entry_delay_min=entry_delay_min)
    out["coin"] = coin
    if out.get("ok"):
        out["place_bracket"] = {"coin": coin, "stop_px": out["stop_px"], "tp_px": out["structure_tp_px"]}
    return out


# ------------------------------------------------------------------ CLI
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--coin", default="xyz:BRENTOIL")
    ap.add_argument("--check", nargs=3, type=float, metavar=("ENTRY", "STOP", "TP"))
    ap.add_argument("--lev", type=float, help="with --check: leverage to judge (e.g. 20)")
    ap.add_argument("--max-lev", type=float, default=20.0, help="venue max leverage for the coin (Brent/WTI 20)")
    ap.add_argument("--next", choices=("long", "short"), help="the live entry rule's answer for this side")
    ap.add_argument("--thesis-entry", type=float, help="with --next: the thesis entry (never-chase line)")
    ap.add_argument("--risk-lev", type=float, default=20.0, help="with --next: the risk profile's leverage (20)")
    ap.add_argument("--thesis-age-min", type=float, help="with --next: minutes since the thesis was written "
                    "(the entry delay reads it; off while the config has no oil_entry_delay_min)")
    a = ap.parse_args(argv)
    if a.check:
        print(json.dumps(check(*a.check, leverage=a.lev, max_leverage=a.max_lev)))
        return 0
    now_ms = int(time.time() * 1000)
    out = summarize(fetch_candles(a.coin, "1h", 50, now_ms), fetch_candles(a.coin, "15m", 3, now_ms),
                    fetch_mid(a.coin), now_ms)
    out["coin"] = a.coin
    if a.next:
        from services.strategies.next_trade import entry_delay_for
        out = next_order(a.coin, a.next, out, a.thesis_entry, a.risk_lev, a.max_lev,
                         thesis_age_min=a.thesis_age_min, entry_delay_min=entry_delay_for(a.coin))
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
