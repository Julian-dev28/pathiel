"""Zero-capital social-attention recorder — CoinGecko trending (W-SOC lane, 2026-07-23).

WHY THIS EXISTS: every free social-attention source with HISTORY is dead (fxtwitter has
no search/timeline; CoinGecko `/coins/{id}/history` reddit fields return 0.0; LunarCrush
v4 is 401/paid). So a Twitter/social signal CANNOT be backtested from cached data — the
data does not exist for free. The only free path is to RECORD it forward and build our own
history, exactly like `news_catalyst` / `unlock_recorder`. CoinGecko `/search/trending` is
free, no key: the top search-trending coins right now, with a CG-internal score.

HYPOTHESIS being accrued: a coin entering CoinGecko's trending list is an attention spike;
does it lead a forward move? Recorded as a LONG (W-SOC1 showed attention-LONG is refuted on
NEWS surges — crypto news = exit liquidity — so trending-search may well be another fade;
the ledger + a future W-SOC2 backtest adjudicate, EV25 both halves, no prior baked in).

Nothing here trades. Promotion runs through scripts/shadow_status.py like every recorder.
Best-effort throughout: a failed fetch or parse never raises into the loop.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from typing import Any, Dict, List, Optional

from hermes_trader.agents import shadow_ledger
from hermes_trader.agents.rebalancer_owned import state_file

logger = logging.getLogger(__name__)

_BOOK = "social_trending"
_HOUR_MS = 3_600_000
_STATE_FILE = state_file(".social_trending_state.json")
_TRENDING_URL = "https://api.coingecko.com/api/v3/search/trending"


def _num(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _load_state() -> Dict[str, Any]:
    try:
        raw = json.load(open(_STATE_FILE))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _save_state(state: Dict[str, Any]) -> None:
    try:
        with open(_STATE_FILE, "w") as fh:
            json.dump(state, fh)
    except Exception:
        pass


def fetch_trending(timeout: float = 15.0) -> List[Dict[str, Any]]:
    """Return CoinGecko trending coins as [{symbol,name,rank,score,price_btc}]. []
    on any failure (never raises)."""
    try:
        req = urllib.request.Request(_TRENDING_URL, headers={"User-Agent": "hermes-trader"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"[social-trending] fetch failed (non-fatal): {exc}")
        return []
    out = []
    for entry in (data.get("coins") or []):
        it = entry.get("item") or {}
        sym = str(it.get("symbol") or "").upper()
        if not sym:
            continue
        out.append({
            "symbol": sym,
            "name": it.get("name"),
            "rank": it.get("market_cap_rank"),
            "score": int(it.get("score") or 0),  # 0 = most-trending
            "price_btc": _num((it.get("data") or {}).get("price_btc")
                              if isinstance(it.get("data"), dict) else it.get("price_btc")),
        })
    return out


def _universe_mids(universe: Optional[List[Dict[str, Any]]]) -> Dict[str, float]:
    mids: Dict[str, float] = {}
    for m in universe or []:
        coin = str(m.get("coin") or "")
        if not coin or ":" in coin:  # skip xyz-equity dex names
            continue
        px = _num(m.get("midPx") or m.get("markPx"))
        if px > 0:
            # index by the coin AND a k-prefix-stripped alias (kPEPE -> PEPE)
            mids[coin.upper()] = px
            if coin.upper().startswith("K") and len(coin) > 1:
                mids.setdefault(coin[1:].upper(), px)
    return mids


def maybe_record(universe: Optional[List[Dict[str, Any]]],
                 config: Dict[str, Any]) -> int:
    """Call once per scan. Throttled to poll_hours; records each trending coin at most
    once per dedup_hours. Returns number of episodes recorded this call."""
    cfg = (config.get(_BOOK) or {})
    if not bool(cfg.get("enabled", True)):
        return 0
    now_ms = int(time.time() * 1000)
    state = _load_state()
    poll_ms = float(cfg.get("poll_hours", 1.0)) * _HOUR_MS
    if now_ms - int(state.get("last_poll_ms", 0)) < poll_ms:
        return 0

    trending = fetch_trending()
    # advance the poll clock even on empty fetch so a persistent outage doesn't hot-loop
    state["last_poll_ms"] = now_ms
    if not trending:
        _save_state(state)
        return 0

    mids = _universe_mids(universe)
    dedup_ms = float(cfg.get("dedup_hours", 24.0)) * _HOUR_MS
    min_score = int(cfg.get("min_score", 999))  # score<=min_score kept; 999 = keep all
    horizon = float(cfg.get("horizon_days", 1.0))
    last_seen: Dict[str, int] = dict(state.get("last_seen") or {})

    n = 0
    for row in trending:
        if row["score"] > min_score:
            continue
        sym = row["symbol"]
        if now_ms - int(last_seen.get(sym, 0)) < dedup_ms:
            continue  # already recorded this coin inside the dedup window
        on_hl = sym in mids
        shadow_ledger.record(
            _BOOK, coin=sym, side="long", signal_bar_t=now_ms, ts=now_ms,
            entry_ref_px=mids.get(sym, 0.0), horizon_days=horizon,
            meta={"source": "coingecko_trending", "cg_rank": row["rank"],
                  "trending_score": row["score"], "name": row["name"],
                  "on_hl": on_hl, "shadow": True},
        )
        last_seen[sym] = now_ms
        n += 1

    # prune last_seen entries older than the dedup window so state stays small
    state["last_seen"] = {k: v for k, v in last_seen.items()
                          if now_ms - int(v) < dedup_ms}
    _save_state(state)
    if n:
        logger.info(f"[social-trending] recorded {n} trending coins "
                    f"({sum(1 for r in trending if r['symbol'] in mids)}/{len(trending)} on HL)")
    return n


if __name__ == "__main__":
    # standalone poll — usable from cron independent of the trading loop
    logging.basicConfig(level=logging.INFO)
    rows = fetch_trending()
    print(f"CoinGecko trending: {len(rows)} coins")
    for r in rows:
        print(f"  score={r['score']:>2} {r['symbol']:<10} rank={r['rank']} {r['name']}")
