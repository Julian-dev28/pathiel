"""FREE crypto whale-flow signal — our own build of the Unusual-Whales / Whale
Alert "large trade" workflow, with NO paid feed and NO API key.

Truly keyless on-chain wallet tracking needs a paid/keyed explorer, so the
genuinely-free real-time whale read is ORDER-FLOW whales: Binance's public
aggTrades endpoint (no auth) streams every executed trade with side, so we can
isolate the LARGE aggressive prints and net their pressure.

    https://api.binance.com/api/v3/aggTrades?symbol=BTCUSDT&limit=1000

Each aggTrade has price, qty, timestamp, and `m` = isBuyerMaker:
  m=True  -> buyer was the maker -> the taker SOLD  -> aggressive SELL
  m=False -> buyer was the taker -> aggressive BUY

What it produces, for any crypto coin (skips xyz: equity perps):
  - whale buy/sell $ volume = sum of aggressive prints >= a USD threshold,
  - net flow + a bias: heavy net aggressive BUYING by size = bullish whale
    pressure (and vice versa) — the same read as "big market buyer stepping in".

PURE compute (testable) + thin cached fetch. Nothing here trades; it's the signal
product. Wiring into perception/override is a separate, gated step.
"""

from __future__ import annotations

import json
import logging
import ssl
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    import certifi
    _SSL = ssl.create_default_context(cafile=certifi.where())
except Exception:                     # pragma: no cover
    _SSL = ssl._create_unverified_context()

_AGG = "https://api.binance.com/api/v3/aggTrades"

# HL coin -> Binance spot symbol. Default = {COIN}USDT; the k-prefixed HL meme
# tickers (kPEPE, kSHIB, kBONK …) are 1000x-scaled on HL but plain on Binance.
_SYMBOL_OVERRIDE = {
    "kPEPE": "PEPEUSDT", "kSHIB": "SHIBUSDT", "kBONK": "BONKUSDT",
    "kFLOKI": "FLOKIUSDT", "kLUNC": "LUNCUSDT", "kDOGS": "DOGSUSDT",
}


def binance_symbol(coin: str) -> Optional[str]:
    """HL crypto coin -> Binance USDT symbol. Returns None for xyz: equities."""
    if ":" in (coin or ""):
        return None
    return _SYMBOL_OVERRIDE.get(coin, f"{coin.upper()}USDT")


@dataclass(frozen=True)
class Print:
    price: float
    qty: float
    ts: int          # ms
    is_buy: bool     # aggressive taker BUY

    @property
    def usd(self) -> float:
        return self.price * self.qty


@dataclass(frozen=True)
class WhaleReport:
    symbol: str
    window_n: int             # total aggTrades scanned
    whale_n: int              # prints >= threshold
    buy_usd: float            # aggressive whale buying
    sell_usd: float           # aggressive whale selling
    net_usd: float            # buy - sell
    bias: str                 # "whale_buying" | "whale_selling" | "balanced"
    min_usd: float
    window_minutes: float = 0.0
    note: str = ""


def parse_aggtrades(payload: list) -> List[Print]:
    out: List[Print] = []
    for t in payload or []:
        try:
            out.append(Print(
                price=float(t["p"]), qty=float(t["q"]), ts=int(t["T"]),
                is_buy=not bool(t["m"]),       # m=True => buyer is maker => taker SOLD
            ))
        except (KeyError, ValueError, TypeError):
            continue
    return out


def compute_whale_flow(prints: List[Print], min_usd: float = 100_000.0,
                       symbol: str = "") -> WhaleReport:
    """Net aggressive whale flow from large prints (>= min_usd)."""
    buy = sell = 0.0
    whales = 0
    for p in prints:
        if p.usd < min_usd:
            continue
        whales += 1
        if p.is_buy:
            buy += p.usd
        else:
            sell += p.usd
    net = buy - sell
    total = buy + sell
    # bias needs a meaningful imbalance (>20% of whale $ on one side)
    if total > 0 and abs(net) / total >= 0.20:
        bias = "whale_buying" if net > 0 else "whale_selling"
    else:
        bias = "balanced"
    return WhaleReport(
        symbol=symbol, window_n=len(prints), whale_n=whales,
        buy_usd=round(buy, 2), sell_usd=round(sell, 2), net_usd=round(net, 2),
        bias=bias, min_usd=min_usd,
        note=("large aggressive buyers stepping in" if bias == "whale_buying"
              else "large aggressive sellers hitting bids" if bias == "whale_selling" else ""),
    )


# ── thin cached fetch ────────────────────────────────────────────────────────
_CACHE_TTL_S = 120.0
_cache: Dict[str, tuple] = {}
_lock = threading.Lock()


def _get_json(url: str, timeout: float = 10.0):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None


def fetch_aggtrades_window(symbol: str, window_minutes: float = 15.0,
                           max_pages: int = 6, page_limit: int = 1000) -> List[Print]:
    """Pull ALL aggTrades over the last `window_minutes` by forward-paginating from
    startTime (fromId), so the read covers real minutes — not the ~seconds that a
    single latest-1000 batch spans on a liquid pair. Bounded by max_pages."""
    sym = urllib.parse.quote(symbol)
    start_ms = int((time.time() - window_minutes * 60) * 1000)
    payload = _get_json(f"{_AGG}?symbol={sym}&startTime={start_ms}&limit={page_limit}")
    if not isinstance(payload, list):
        return []
    prints = parse_aggtrades(payload)
    pages = 1
    while payload and len(payload) >= page_limit and pages < max_pages:
        last_id = payload[-1].get("a")
        if last_id is None:
            break
        payload = _get_json(f"{_AGG}?symbol={sym}&fromId={int(last_id) + 1}&limit={page_limit}")
        if not isinstance(payload, list):
            break
        prints.extend(parse_aggtrades(payload))
        pages += 1
    return prints


def crypto_whale_signal(coin: str, min_usd: float = 100_000.0,
                        window_minutes: float = 15.0, max_pages: int = 6,
                        ttl: float = _CACHE_TTL_S,
                        allow_fetch: bool = True) -> Optional[WhaleReport]:
    """Free whale-flow report for a crypto coin via Binance public aggTrades over a
    rolling time WINDOW. Returns None for xyz: equities or on fetch failure.

    allow_fetch=False = CACHE-ONLY (return last cached value or None, no network)."""
    sym = binance_symbol(coin)
    if not sym:
        return None
    now = time.time()
    key = f"{sym}::{int(min_usd)}::{window_minutes}"
    with _lock:
        hit = _cache.get(key)
        if hit and (now - hit[0]) < ttl:
            return hit[1]
    if not allow_fetch:
        return hit[1] if hit else None
    prints = fetch_aggtrades_window(sym, window_minutes=window_minutes, max_pages=max_pages)
    rep = compute_whale_flow(prints, min_usd=min_usd, symbol=sym) if prints else None
    if rep is not None:
        rep = WhaleReport(**{**rep.__dict__, "window_minutes": window_minutes})
    with _lock:
        _cache[key] = (now, rep)
    return rep
