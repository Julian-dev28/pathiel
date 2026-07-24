"""Polymarket judgment-edge SHADOW scout — core logic.

Thesis (W-Z1, GO-IF): our unfair edge is a tireless LLM that synthesizes news
into a probability. That edge is worthless in latency arb (the gabagool pair-arb
is 0/60 tradeable on resting books) but pays where being RIGHT beats being FAST:
mid-tail markets (geopolitics/world/culture) resolving in 3-21 days, priced by
retail/partisan money, not superforecasters or latency bots.

This module does ZERO trading. It reads public Polymarket data (keyless), asks an
injected forecaster for a YES probability, records the divergence as a PAPER trade
filled at the TOUCH (the ask, never the mid), and — once the market resolves —
grades net PnL + Brier(LLM) vs Brier(market). Capital only after the pre-registered
gate in README.md clears. Everything is pure + injectable so it tests offline.
"""
from __future__ import annotations

import calendar
import json
import os
import subprocess
import time
from typing import Any, Callable, Dict, List, Optional, Protocol, Sequence, Tuple

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
# Gamma's hard page cap. Ask for more and it returns 100 anyway, so every pager
# here steps the offset by this, never by the requested limit.
PAGE_MAX = 100
_DAY_MS = 86_400_000
# Measured taker-fee drag per side (W-Z1: fees rolled out 2026; world-events tier
# lower). Conservative: 1% per fill, applied to the paper PnL both ways.
FEE_PER_FILL = 0.01
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


# ── HTTP (injectable; curl-based default so it works behind the sandbox's SSL) ──
def _curl_get(url: str, timeout: float = 15.0) -> Any:
    """GET url -> parsed JSON, or None. Uses curl (the sandbox blocks urllib's UA
    and fails its cert store; curl works). Swappable for requests in prod."""
    try:
        out = subprocess.run(["curl", "-s", "--max-time", str(int(timeout)), "-A", _UA, url],
                             capture_output=True, text=True, timeout=timeout + 5)
        return json.loads(out.stdout)
    except Exception:
        return None


class Forecaster(Protocol):
    def forecast(self, question: str, description: str) -> Optional[Tuple[float, str]]:
        """Return (yes_probability in [0,1], reasoning) or None if it declines."""
        ...


# ── market read ────────────────────────────────────────────────────────────────
class PolymarketClient:
    def __init__(self, http_get: Callable[[str], Any] = _curl_get):
        self._get = http_get

    def open_markets(self, limit: int = 100, pages: int = 30) -> List[Dict[str, Any]]:
        # Gamma silently caps a page at PAGE_MAX rows (measured: limit=250 -> 100).
        # The offset MUST step by the effective page size, not by the requested
        # limit, or the pager strides over the rows it never received.
        step = min(int(limit), PAGE_MAX)
        out: List[Dict[str, Any]] = []
        for off in range(0, pages * step, step):
            batch = self._get(f"{GAMMA}/markets?closed=false&limit={step}&offset={off}")
            if not isinstance(batch, list):
                continue
            out.extend(m for m in batch if isinstance(m, dict))
            if len(batch) < step:
                break                       # short page = end of the result set
        return out

    def open_events(self, limit: int = 100, pages: int = 3,
                    exclude_tag_ids: Sequence[str] = (),
                    tag_ids: Sequence[str] = ()) -> List[Dict[str, Any]]:
        """Open EVENTS ordered by 24h volume — polymarket.com's own front-page
        ordering. Events (not markets) because only the event payload carries
        `tags`, and tags are how we drop (or select) the sports/esports lane
        server-side (`exclude_tag_id` / `tag_id`, both repeatable).

        `tag_ids=("1",)` is polymarket.com/sports; the same call carries the
        `live`, `score`, `teams` and `startTime` fields the /sports/live board
        renders."""
        excl = ("".join(f"&exclude_tag_id={t}" for t in exclude_tag_ids)
                + "".join(f"&tag_id={t}" for t in tag_ids))
        step = min(int(limit), PAGE_MAX)
        out: List[Dict[str, Any]] = []
        for off in range(0, pages * step, step):
            batch = self._get(f"{GAMMA}/events?closed=false&limit={step}&offset={off}"
                              f"&order=volume24hr&ascending=false{excl}")
            if not isinstance(batch, list):
                continue
            out.extend(e for e in batch if isinstance(e, dict))
            if len(batch) < step:
                break
        return out

    def best_ask(self, token_id: str) -> Optional[Tuple[float, float]]:
        """(price, size) of the lowest resting ask, or None if no book."""
        b = self._get(f"{CLOB}/book?token_id={token_id}")
        if not isinstance(b, dict) or b.get("error"):
            return None
        asks = b.get("asks") or []
        if not asks:
            return None
        best = min(asks, key=lambda a: float(a["price"]))
        return float(best["price"]), float(best["size"])

    def market_by_id(self, market_id: str) -> Optional[Dict[str, Any]]:
        m = self._get(f"{GAMMA}/markets/{market_id}")
        return m if isinstance(m, dict) else None

    def resolved_markets(self, limit: int = 100, pages: int = 10,
                         min_volume: float = 0.0) -> List[Dict[str, Any]]:
        """Closed markets, most-traded first — the backtest sample. Ordered by
        `volumeNum` (the numeric column; plain `order=volume` sorts the string
        field and returns junk), liquid first because a market with no volume has
        no price series to learn from."""
        step = min(int(limit), PAGE_MAX)
        vmin = f"&volume_num_min={min_volume:.0f}" if min_volume > 0 else ""
        out: List[Dict[str, Any]] = []
        for off in range(0, pages * step, step):
            batch = self._get(f"{GAMMA}/markets?closed=true&limit={step}&offset={off}"
                              f"&order=volumeNum&ascending=false{vmin}")
            if not isinstance(batch, list):
                continue
            out.extend(m for m in batch if isinstance(m, dict))
            if len(batch) < step:
                break
        return out

    def price_history(self, token_id: str, interval: str = "max",
                      fidelity: int = 60) -> List[Dict[str, float]]:
        """CLOB price series for one outcome token: [{t: epoch_s, p: 0..1}, ...].
        `fidelity` is the bar width in minutes (60 = hourly)."""
        raw = self._get(f"{CLOB}/prices-history?market={token_id}"
                        f"&interval={interval}&fidelity={fidelity}")
        if isinstance(raw, dict):
            h = raw.get("history")
            return h if isinstance(h, list) else []
        return raw if isinstance(raw, list) else []


def resolve_yes_won(m: Optional[Dict[str, Any]]) -> Optional[bool]:
    """True if YES won, False if NO won, None if not resolved yet. A resolved
    binary market is CLOSED with its YES price pinned to ~1 or ~0."""
    if not m or not m.get("closed"):
        return None
    p = market_yes_prob(m)
    if p is None:
        return None
    if p >= 0.99:
        return True
    if p <= 0.01:
        return False
    return None                         # closed but ambiguous/void — don't grade


def make_gamma_resolver(client: "PolymarketClient") -> Callable[[str], Optional[bool]]:
    """Live resolver for ledger.grade: market_id -> (YES won?), cached per run so
    a market shared by multiple signals is fetched once."""
    cache: Dict[str, Optional[bool]] = {}

    def resolve(market_id: str) -> Optional[bool]:
        if market_id not in cache:
            cache[market_id] = resolve_yes_won(client.market_by_id(market_id))
        return cache[market_id]

    return resolve


# ── pure filtering + edge math ───────────────────────────────────────────────
_CRYPTO_UPDOWN = ("up or down", "higher or lower", "up/down")
_CRYPTO_ASSETS = ("bitcoin", "btc", "ethereum", "eth", "solana", "sol", "xrp", "dogecoin")


def is_latency_market(m: Dict[str, Any]) -> bool:
    """Crypto up/down (and other sub-hour price windows) — the latency-arb trap we
    explicitly avoid; our edge is judgment, not speed."""
    q = (m.get("question") or "").lower()
    return any(k in q for k in _CRYPTO_UPDOWN) and any(c in q for c in _CRYPTO_ASSETS)


def _parse_tokens(m: Dict[str, Any]) -> List[str]:
    try:
        toks = json.loads(m.get("clobTokenIds") or "[]")
        return [str(t) for t in toks] if isinstance(toks, list) else []
    except Exception:
        return []


def market_yes_prob(m: Dict[str, Any]) -> Optional[float]:
    """Market-implied YES probability from Gamma outcomePrices ([YES, NO])."""
    op = m.get("outcomePrices")
    if isinstance(op, str):
        try:
            op = json.loads(op)
        except Exception:
            op = None
    if isinstance(op, list) and op:
        try:
            return float(op[0])
        except (TypeError, ValueError):
            return None
    return None


def is_judgment_market(m: Dict[str, Any], now_ms: int, cfg: Dict[str, Any]) -> bool:
    """Keep only the mid-tail judgment markets our LLM edge fits:
    order book on, live, liquid, resolving in [min,max] days, priced in the
    mid-range (not a near-settled 0.02/0.98 where there is no edge to find),
    and NOT a latency/crypto-updown market."""
    if not m.get("enableOrderBook") or m.get("closed") or not m.get("active", True):
        return False
    if is_latency_market(m):
        return False
    try:
        liq = float(m.get("liquidity") or 0)
    except (TypeError, ValueError):
        liq = 0.0
    if liq < float(cfg.get("min_liquidity", 500.0)):
        return False
    end = m.get("endDate") or ""
    try:                              # ISO UTC (drop the Z); timegm treats it as UTC
        end_ms = int(calendar.timegm(time.strptime(end[:19], "%Y-%m-%dT%H:%M:%S")) * 1000)
    except Exception:
        return False
    days = (end_ms - now_ms) / _DAY_MS
    if not (float(cfg.get("min_days", 3.0)) <= days <= float(cfg.get("max_days", 21.0))):
        return False
    p = market_yes_prob(m)
    if p is None:
        return False
    lo, hi = float(cfg.get("prob_floor", 0.10)), float(cfg.get("prob_ceiling", 0.90))
    return lo <= p <= hi and len(_parse_tokens(m)) == 2


def signed_edge(llm_yes: float, mkt_yes: float) -> float:
    """+ = LLM thinks YES is underpriced (buy YES); - = buy NO."""
    return llm_yes - mkt_yes


def decide_side(edge: float, threshold: float) -> Optional[str]:
    if edge >= threshold:
        return "YES"
    if edge <= -threshold:
        return "NO"
    return None                       # inside the band — no divergence, no paper trade


def paper_pnl(side_won: bool, fill_px: float) -> float:
    """Net paper PnL per $1 stake, filled at the TOUCH, net of the two-sided fee
    proxy: win pays (1 - fill), lose costs (-fill); a fill is charged FEE_PER_FILL
    plus the redemption fee on a win."""
    gross = (1.0 - fill_px) if side_won else (-fill_px)
    fees = FEE_PER_FILL + (FEE_PER_FILL if side_won else 0.0)
    return gross - fees


def brier(prob_yes: float, yes_won: bool) -> float:
    return (prob_yes - (1.0 if yes_won else 0.0)) ** 2
