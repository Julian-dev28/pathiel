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

2026-08-30 — IT NOW TRADES. Operator directive: "nothing should be a recorder". A book
that can validate and still has no capital path is dead weight, and this one VALIDATED on
its own forward ledger (n=185, EV@6bps +1.08%, EV@25bps +0.89%, halves +0.54/+1.50,
same-coin random-time null p=0.0005) while having no way to act on that. The live arm
below is bounded and defaults to shadow_only=true, so it earns capital through
scripts/autonomous_cycle.py like everything else rather than by being switched on here.

Best-effort throughout: a failed fetch or parse never raises into the loop.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.request
from typing import Any, Callable, Dict, List, Optional

import uuid

from hermes_trader.agents import shadow_ledger
from hermes_trader.agents.book_helpers import bounded_exit_override
from hermes_trader.agents.book_helpers import execute_opened as _execute_opened
from hermes_trader.agents.book_helpers import load_state, save_state
from hermes_trader.agents.book_helpers import safe_float as _num
from hermes_trader.agents.rebalancer_owned import get_claims_registry, state_file
from hermes_trader.agents.rebalancer_owned import held_coins_with_dsl as _held_coins
from hermes_trader.models.types import BookAnalysis
from hermes_trader.session_log import append as log_event

logger = logging.getLogger(__name__)

_BOOK = "social_trending"
_HOUR_MS = 3_600_000
_STATE_FILE = state_file(".social_trending_state.json")
_TRENDING_URL = "https://api.coingecko.com/api/v3/search/trending"


def _load_state() -> Dict[str, Any]:
    return load_state(_STATE_FILE)


def _save_state(state: Dict[str, Any]) -> None:
    save_state(_STATE_FILE, state)


def fetch_trending(timeout: float = 15.0) -> List[Dict[str, Any]]:
    """Return CoinGecko trending coins as [{symbol,name,rank,score,price_btc}]. []
    on any failure (never raises)."""
    try:
        req = urllib.request.Request(_TRENDING_URL, headers={"User-Agent": "hermes-trader"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
    except Exception as exc:  # noqa: BLE001
        # This book now trades (2026-08-30, see module docstring) — a
        # persistent fetch failure means every pass silently records
        # nothing, indistinguishable from "nothing trending today."
        logger.warning(f"[social-trending] fetch failed (non-fatal): {exc}")
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


def _analysis(coin: str, row: Dict[str, Any], cfg: Dict[str, Any]) -> BookAnalysis:
    """The graded geometry, verbatim: LONG, 1-day horizon, hard stop, no trail.

    The ledger recorded side=long with horizon_days=1 and that is what graded
    VALIDATED, so the live order must match it exactly. A live policy that
    differs from the graded policy is an ungraded book wearing a validated
    book's verdict.
    """
    stop_pct = float(cfg.get("stop_pct", 15.0))
    leverage = max(1, int(cfg.get("leverage", 1)))
    notional = float(cfg.get("notional_usd", 20.0))
    hold_days = float(cfg.get("horizon_days", 1.0))
    return {
        "id": str(uuid.uuid4()), "coin": coin,
        "verdict": "LONG", "side": "long",
        "confidence": 0.99, "entry_px": 0.0, "stop_px": 0.0, "tp_px": 0.0,
        "reasoning": (f"[{_BOOK}] CoinGecko trending rank {row.get('rank')} "
                      f"(score {row.get('score')}) — attention spike, "
                      f"forward-validated n=185 p=0.0005"),
        "news_risk": "none", "ai_down": False, "created_at": int(time.time() * 1000),
        "composite_score": 0.0, "strategy_book": _BOOK,
        "strategy_book_notional": notional,
        "leverage_override": leverage,
        "backup_sl_pct_override": stop_pct,
        "tp_scale_fraction_override": 0.0,
        "dsl_exit_override": bounded_exit_override(stop_pct, leverage, hold_days * 1440.0),
    }


def maybe_record(universe: Optional[List[Dict[str, Any]]],
                 config: Dict[str, Any],
                 positions: Optional[List[Dict[str, Any]]] = None,
                 execute_fn: Optional[Callable] = None) -> int:
    """Call once per scan. Throttled to poll_hours; records each trending coin at most
    once per dedup_hours. Returns number of episodes recorded this call.

    Records ALWAYS. Trades only when shadow_only=false AND an execute_fn is
    supplied, so the forward ledger keeps accruing under the same policy whether
    or not capital is attached — which is what keeps the grade honest."""
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
    fresh: List[Dict[str, Any]] = []      # rows recorded THIS call
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
        fresh.append(row)
        n += 1

    # prune last_seen entries older than the dedup window so state stays small
    state["last_seen"] = {k: v for k, v in last_seen.items()
                          if now_ms - int(v) < dedup_ms}
    _save_state(state)

    # ── live arm ────────────────────────────────────────────────────────────
    # Bounded and shadow by default. It opens at most max_new_per_cycle, only on
    # coins Hyperliquid actually lists, and takes a cross-book claim so it cannot
    # collide with another book on the same coin.
    if bool(cfg.get("shadow_only", True)) or execute_fn is None or not fresh:
        return n

    held = _held_coins(positions)
    claims = get_claims_registry()
    claims.prune_to(held, _BOOK)
    blocked = claims.claimed_by_others(_BOOK)
    max_new = int(cfg.get("max_new_per_cycle", 1))
    opened = 0
    # Best rank first: rank 1 is the strongest attention spike in the payload.
    for row in sorted(fresh, key=lambda r: r.get("rank", 999)):
        if opened >= max_new:
            break
        coin = row["symbol"]
        # Only coins the exchange lists. A trending coin with no HL market has
        # no capital path at all, and pretending otherwise burns a claim.
        if coin not in mids or coin in held or coin in blocked:
            continue
        if not claims.claim(coin, _BOOK):
            continue
        try:
            if _execute_opened(execute_fn(_analysis(coin, row, cfg))):
                opened += 1
                log_event({"event": "book_open", "book": _BOOK, "coin": coin,
                           "side": "long", "sig_t": now_ms})
                logger.info(f"[social-trending] LIVE opened long {coin} "
                            f"(cg rank {row.get('rank')})")
            else:
                claims.release(coin, _BOOK)
        except Exception as exc:
            claims.release(coin, _BOOK)
            logger.warning(f"[social-trending] open {coin} failed: {exc}")
    if opened:
        claims.save()
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
