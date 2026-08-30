"""Live wiring for the news-coverage-surge SHORT edge (inverse of the
refuted news_catalyst LONG book).

Origin (2026-07-20, operator epiphany: "reverse anything REFUTED"):
`research/alpha_swarm/findings/reverse_refuted_direction_audit.md` graded the
exact opposite side of every refuted directional shadow book through the
production grader plus a matched same-coin random-time null. Blanket reversal
failed everywhere except one cell: SHORTING a breaking Google-News coverage
surge on xyz TOKENIZED EQUITIES, +13.82%/sig (n=7, excess +11.69% over the
null, mc_p 0.0005; ex-CASHCAT-outlier +7.52%/sig on 6 pure-equity episodes,
mc_p 0.004). The non-breaking control was ALSO positive-excess — read the
finding's "attention-fade" synthesis before extending this beyond equities.

news_catalyst_live (the LONG book this inverts) was demolished 2026-07-18
(commit 8ca189f) after its own REFUTED verdict; this module restores its
coin_catalyst() read but flips the trade and narrows the live scope to what
the evidence actually supports:

- RECORD every scan candidate (crypto AND xyz equities, breaking and
  non-breaking) to the unified shadow ledger under a NEW book name
  (`news_surge_short` — never conflated with the old LONG `news_catalyst`
  ledger, which stays historical) so the broader hypothesis keeps accruing
  forward evidence even where we do not yet trade it.
- TRADE (real capital) only breaking reads on xyz: coins. The n=7 sample was
  6/7 equities and 1 crypto outlier (CASHCAT +51.7%, a single wild episode) —
  crypto surge-shorts stay record-only until they earn their own n>=8 forward
  read. This is the operator's "small positions" order implemented BOUNDED:
  the live geometry (15% stop, 1-day hard timeout, no trail) is the EXACT
  shape that was graded, so the ledger keeps grading the trade it is taking.

Operator flip 2026-07-20: $20 notional, 10x leverage (both explicit operator
numbers, not the crash_continue/engulf-family default of 12x). Mandatory
review at 8 resolved forward episodes (the grader's own min_n) — REFUTED or
EV25<0 at that bar flips shadow_only=true, no debate, same as every other
thin-evidence live flip in this file.
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Set

from hermes_trader.agents import shadow_ledger
from hermes_trader.agents.book_helpers import bounded_exit_override
from hermes_trader.agents.book_helpers import execute_block_detail as _execute_block_detail
from hermes_trader.agents.book_helpers import execute_opened as _execute_opened
from hermes_trader.agents.book_helpers import last_pass_ms, load_seen, mark_pass, save_seen
from hermes_trader.agents.news_catalyst import CatalystReport, coin_catalyst
from hermes_trader.agents.rebalancer_owned import get_claims_registry, state_file
from hermes_trader.agents.rebalancer_owned import held_coins_with_dsl as _held_coins
from hermes_trader.models.types import BookAnalysis
from hermes_trader.session_log import append as log_event

logger = logging.getLogger(__name__)

_BOOK_NAME = "news_surge_short"
_HOUR_MS = 3_600_000
_DAY_MS = 86_400_000
_TS_FILE = state_file(".news_surge_short_live_ts.json")
_SEEN_FILE = state_file(".news_surge_short_live_seen.json")
_MAX_COINS_PER_PASS = 8  # keep the RSS pass bounded even on wild scan days


def _is_xyz_equity(coin: str) -> bool:
    """Live-trade scope: the evidence is equities-only (6/7 episodes, the
    lone crypto read was a single outlier). Recording stays universe-wide;
    only the trade gate checks this."""
    return ":" in str(coin or "")


def _last_pass_ms() -> int:
    return last_pass_ms(_TS_FILE)


def _mark_pass(now_ms: int) -> None:
    mark_pass(_TS_FILE, now_ms)


def _macro_regime(coin: str) -> Optional[str]:
    """Cheap macro-regime tag (up/down/neutral) for a coin's asset class — BTC
    for crypto, the xyz equity index for tokenized equities.

    Metadata ONLY: it never gates a trade. It exists so the forward grader can
    split this book by regime and settle on live evidence whether a regime tilt
    helps. Never raises.

    Moved here 2026-08-30 from mover_recorders, which was deleted while this
    module still imported it — see the import-check test that now guards that.
    """
    try:
        from hermes_trader.agents.market_regime import (
            CRYPTO_PROXY, EQUITY_PROXY, classify_asset, detect_regime,
        )
        proxy = EQUITY_PROXY if classify_asset(coin) == "equity" else CRYPTO_PROXY
        return str(detect_regime(proxy))
    except Exception:
        return None


def _load_seen() -> Dict[str, int]:
    return load_seen(_SEEN_FILE)


def _save_seen(seen: Dict[str, int]) -> None:
    save_seen(_SEEN_FILE, seen)


def _analysis(coin: str, rep: CatalystReport, cfg: Dict[str, Any]) -> BookAnalysis:
    # Asset-aware sizing. Equity arm = the original reverse-refuted geometry.
    # Crypto arm (W-SOC1, 2026-07-23): crypto coverage-surge SHORT graded net25
    # +4.30%/24h, BOTH halves positive (+4.52/+5.08), same-coin random-time null
    # p=0.0005, n=219 — clears the module's own "n>=8 forward" crypto-promotion bar.
    # Sized $20/1x (deliberately conservative: the n=219 is one 6-day pump-dump tape).
    if _is_xyz_equity(coin):
        stop_pct = float(cfg.get("stop_pct", 15.0))
        leverage = max(1, int(cfg.get("leverage", 10)))
        notional = float(cfg.get("notional_usd", 20.0))
    else:
        stop_pct = float(cfg.get("crypto_stop_pct", 15.0))
        leverage = max(1, int(cfg.get("crypto_leverage", 1)))
        notional = float(cfg.get("crypto_notional_usd", 20.0))
    hold_days = float(cfg.get("hold_days", 1.0))
    top = rep.headlines[0].title if rep.headlines else ""
    return {
        "id": str(uuid.uuid4()), "coin": coin,
        "verdict": "SHORT", "side": "short",
        "confidence": 0.99, "entry_px": 0.0, "stop_px": 0.0, "tp_px": 0.0,
        "reasoning": (f"[{_BOOK_NAME}] BREAKING coverage surge {rep.surge_x:.1f}x "
                      f"(n={rep.n_recent}), inverse-of-refuted news_catalyst: {top[:120]}"),
        "news_risk": "none", "ai_down": False, "created_at": int(time.time() * 1000),
        "composite_score": 0.0, "strategy_book": _BOOK_NAME,
        "strategy_book_notional": notional,
        "leverage_override": leverage,
        "backup_sl_pct_override": stop_pct,
        "tp_scale_fraction_override": 0.0,
        "min_short_volume_usd_override": float(cfg.get("min_volume_usd", 250_000.0)),
        # Exact graded geometry: 15% stop or 1d horizon close, no trail.
        "dsl_exit_override": bounded_exit_override(stop_pct, leverage, hold_days * 1440.0),
    }


def maybe_run(config: Dict[str, Any],
              perceptions: Optional[List[Dict[str, Any]]],
              positions: Optional[List[Dict[str, Any]]] = None,
              execute_fn: Optional[Callable] = None) -> int:
    """Call once per scan with the cycle's perceptions. Throttled to one pass
    per scan_interval_min. Records every read (crypto + equities, breaking and
    non-breaking) to the ledger; opens bounded SHORTs only on breaking xyz
    equity reads when shadow_only=false and an execute_fn is provided.
    Returns rows recorded."""
    cfg = (config.get("news_surge_short") or {})
    if not bool(cfg.get("enabled", True)):
        return 0
    now_ms = int(time.time() * 1000)
    interval_min = float(cfg.get("scan_interval_min", 30.0))
    if now_ms - _last_pass_ms() < interval_min * 60_000:
        return 0
    _mark_pass(now_ms)  # mark first: a failing RSS pass must not retry-storm

    rows: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for p in perceptions or []:
        coin = str(p.get("coin") or "")
        if not coin or coin in seen:
            continue
        seen.add(coin)
        if len(seen) > _MAX_COINS_PER_PASS:
            break
        try:
            mid = float(p.get("mid") or 0.0)
        except (TypeError, ValueError):
            mid = 0.0
        if mid <= 0:
            continue
        try:
            rep = coin_catalyst(coin)
        except Exception as exc:
            # LIVE book — a persistent per-coin read failure silently drops
            # that coin from this pass's evidence every cycle, indistinguishable
            # from "no catalyst." Needs to be visible, not debug-only.
            logger.warning(f"[news-surge-short-live] {coin}: read failed ({exc})")
            continue
        shadow_only = bool(cfg.get("shadow_only", False))
        rows.append({
            "coin": coin, "side": "short",
            "signal_bar_t": (now_ms // _HOUR_MS) * _HOUR_MS,
            "entry_ref_px": mid, "horizon_days": float(cfg.get("hold_days", 1.0)),
            "stop_pct": float(cfg.get("stop_pct", 15.0)),
            "meta": {
                "n_recent": rep.n_recent,
                "surge_x": rep.surge_x,
                "breaking": bool(rep.breaking),
                "equity": _is_xyz_equity(coin),
                "macro_regime": _macro_regime(coin),
                "top3_titles": [a.title for a in (rep.headlines or [])[:3]],
                "top3_urls": [a.url for a in (rep.headlines or [])[:3]],
                "top3_ages_h": [
                    (round((time.time() - a.seen.timestamp()) / 3600, 1)
                     if a.seen else None)
                    for a in (rep.headlines or [])[:3]
                ],
                "shadow": shadow_only,
                "_rep": rep,   # stripped before recording; live-open handle
            },
        })
    # Recording never carries the live handle.
    n = shadow_ledger.record_many(_BOOK_NAME, [
        {**r, "meta": {k: v for k, v in r["meta"].items() if k != "_rep"}}
        for r in rows
    ])
    if n:
        n_breaking = sum(1 for r in rows if r["meta"]["breaking"])
        logger.info(f"[news-surge-short-live] recorded {n} read(s), {n_breaking} breaking")

    if bool(cfg.get("shadow_only", False)) or execute_fn is None:
        return n
    # Trade scope, per asset class (each independently gated):
    #  - equity arm: OFF by default (equity_live) — shadowed 2026-07-23, the xyz-equity
    #    short overlay bled into the semis rally.
    #  - crypto arm: ON (crypto_live) — W-SOC1 validated crypto surge-short (n=219, both
    #    halves +, null p=0.0005). $20/1x, kill: crypto EV25<0 over 15 fwd episodes.
    equity_live = bool(cfg.get("equity_live", False))
    crypto_live = bool(cfg.get("crypto_live", False))
    breaking_rows = [
        r for r in rows if r["meta"]["breaking"] and (
            (r["meta"]["equity"] and equity_live)
            or (not r["meta"]["equity"] and crypto_live))
    ]
    if not breaking_rows:
        return n
    opened_seen = _load_seen()
    held = _held_coins(positions)
    claims = get_claims_registry()
    claims.prune_to(held, _BOOK_NAME)
    blocked_by_claim = claims.claimed_by_others(_BOOK_NAME)
    max_new = int(cfg.get("max_new_per_cycle", 1))
    opened = 0
    for r in sorted(breaking_rows, key=lambda x: -x["meta"]["surge_x"]):
        if opened >= max_new:
            break
        coin = r["coin"]
        day_key = f"{coin}:{now_ms // _DAY_MS}"
        if day_key in opened_seen or coin in held or coin in blocked_by_claim:
            continue
        if not claims.claim(coin, _BOOK_NAME):
            continue
        try:
            result = execute_fn(_analysis(coin, r["meta"]["_rep"], cfg))
            if _execute_opened(result):
                opened += 1
                opened_seen[day_key] = now_ms
                log_event({"event": "book_open", "book": _BOOK_NAME, "coin": coin,
                           "side": "short", "sig_t": r.get("signal_bar_t")})
                logger.info(f"[news-surge-short-live] LIVE opened short {coin} "
                            f"(surge {r['meta']['surge_x']:.1f}x)")
            else:
                claims.release(coin, _BOOK_NAME)
                why = _execute_block_detail(result)
                logger.warning(f"[news-surge-short-live] {coin} not opened: {why}")
        except Exception as exc:
            claims.release(coin, _BOOK_NAME)
            logger.warning(f"[news-surge-short-live] open {coin} failed: {exc}")
    if opened:
        # prune day keys older than 60d
        cutoff = (now_ms - 60 * _DAY_MS) // _DAY_MS
        _save_seen({k: v for k, v in opened_seen.items()
                    if int(k.rsplit(":", 1)[1]) >= cutoff})
        claims.save()
    return n
