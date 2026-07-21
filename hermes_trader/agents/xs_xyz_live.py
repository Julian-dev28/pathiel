"""Live wiring for the xs_xyz_equities book (W-X2 cell A, VERDICT ROBUST).

AUTHORITATIVE SPEC: research/alpha_swarm/findings/W-X2_xs_xyz_equities.md —
net25 +0.65%/rebalance (p=0.0055, OOS +0.18/+1.12, n=34). Operator
pre-authorized LIVE wiring of the ROBUST cell.

Drives the pure engine (agents/xs_xyz.py) on a hold-days timer, exactly like
xs_momentum_live: build the residual-momentum target book from cached daily
candles, diff vs the book-owned live legs, execute the diff through the shared
executor (every SAFETY gate still applies). The rebalance timer is persisted so
a loop restart does not re-fire it.

Structure the spec fixes (do not "tune" these without a new validated cell):
  - 7d residual momentum vs xyz:XYZ100, OLS beta on 30 daily rets (1.0 if <8)
  - long top-5 / short bottom-5, equal weight, 5d non-overlapping hold
  - eligibility: >=61 completed daily bars + 30d mean notional >= $250k
  - e248c13-style wide-only exits: 20% disaster stop, hard_timeout =
    hold_days×1440, NO breakeven/trail/stale-flat/ATR — the rebalance owns
    exits on its own clock
  - min_short_volume_usd_override = 250k (the global $20M floor would block
    EVERY xyz short and run the market-neutral book net-long — the exact
    failure the crypto xs book had, audit 2026-07-09)

Pre-committed kills (constants re-exported from xs_xyz; grading =
scripts/shadow_status.py over the xs_xyz_equities ledger):
  - cumulative forward net25 < 0 after 12 rebalances → shadow_only same day
  - any single rebalance EV < −8% → shadow_only same day
  - semis-theme ablation check at rebalance 6 (halve size if forward no-semis
    EV ≈ 0 and semis dispersion compressed)

Wired as one self-gating call per loop cycle:
    maybe_rebalance(config, universe, positions, fetch_candles, execute_fn, close_fn)
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from hermes_trader.agents import shadow_ledger
from hermes_trader.agents.rebalancer_owned import (
    OwnedPositions, _live_coin_set, get_claims_registry, state_file,
)
from hermes_trader.agents.xs_momentum import TargetBook, is_empty_plan, rebalance_plan
from hermes_trader.agents.xs_xyz import (
    KILL_CUM_NET25_REBALANCES,          # noqa: F401  (re-export: kill spec lives with the book)
    KILL_SINGLE_REBALANCE_EV_PCT,       # noqa: F401
    SEMIS_ABLATION_CHECK_REBALANCE,     # noqa: F401
    bar_t, completed_bars, eligible_xyz_coins, filter_eligible, rank_xyz,
)
from hermes_trader.session_log import append as log_event

logger = logging.getLogger(__name__)

_BOOK_NAME = "xs_xyz_equities"
_TS_FILE = state_file(".xs_xyz_rebalance_ts")
_OWNED_FILE = state_file(".xs_xyz_positions.json")
_COUNT_FILE = state_file(".xs_xyz_rebalance_n")

_BETA_WINDOW = 30          # spec-fixed: OLS beta window (daily rets)
_NOTIONAL_WINDOW = 30      # spec-fixed: mean-daily-notional eligibility window

# Module-level singleton — loaded lazily on first maybe_rebalance call.
_owned: Optional[OwnedPositions] = None


def _get_owned() -> OwnedPositions:
    global _owned
    if _owned is None:
        _owned = OwnedPositions(_OWNED_FILE)
    return _owned.load()


def _last_ts() -> float:
    try:
        return float(open(_TS_FILE).read().strip())
    except Exception:
        return 0.0


def _save_ts(t: float) -> None:
    try:
        open(_TS_FILE, "w").write(str(t))
    except Exception:
        pass


def _rebalance_n() -> int:
    """Completed-rebalance counter (persisted): drives the pre-committed kill
    schedule (12-rebalance cumulative check, semis ablation at rebalance 6)."""
    try:
        return int(open(_COUNT_FILE).read().strip())
    except Exception:
        return 0


def _save_rebalance_n(n: int) -> None:
    try:
        open(_COUNT_FILE, "w").write(str(int(n)))
    except Exception:
        pass


def _analysis(coin: str, side: str, rank_score: float, hold_days: float = 5.0,
              short_floor_usd: float = 250_000.0,
              equity_frac: float = 0.0) -> Dict[str, Any]:
    """Synthetic analysis for the executor — the xs_momentum_live post-f0f8f72
    pattern. strategy_book bypasses the thought-engine ENTRY gates (this is a
    separately validated edge) while every SAFETY gate still applies.

    SIZING (equity_frac, 2026-07-20): this book deploys 10 legs at once, so
    the GLOBAL strategy_book_equity_frac put 12.0x gross on the xyz dex
    ($119/leg x 10 on $99.52 equity). Market-neutral gross is only safe while
    the hedge holds — and it does not hold through a momentum crash, which is
    momentum's documented failure mode: measured live 2026-07-20, the weakest
    prior-7d quintile (what this book SHORTS) bounced +1.43% while the
    strongest (what it LONGS) fell -0.34%, a -1.77pp hit to the spread. At
    12x that 1.77pp cost 10.6% of the dex; a 20pp crash — inside the
    historical range for momentum — would cost 120%, i.e. ruin. A per-book
    fraction bounds this without shrinking the crypto xs book, which is the
    only consistently profitable book we have (+$43 / 83% win over 30d).
    Falls back to the global path when unset.

    min_short_volume_usd_override is carried on EVERY analysis (the gate only
    consumes it for shorts): the global $20M floor would block every xyz short
    — the exact failure that ran the crypto xs book long-only (audit
    2026-07-09). The book's own $250k eligibility floor is the short floor."""
    return {
        "id": str(uuid.uuid4()), "coin": coin,
        "verdict": "LONG" if side == "long" else "SHORT", "side": side,
        "confidence": 0.99, "entry_px": 0.0, "stop_px": 0.0, "tp_px": 0.0,
        "reasoning": f"[{_BOOK_NAME}] {side} (resid7 {rank_score*100:+.1f}% vs xyz:XYZ100)",
        "news_risk": "none", "ai_down": False, "created_at": int(time.time() * 1000),
        "composite_score": 0.0, "strategy_book": _BOOK_NAME,
        # The 5-day rebalance OWNS exits (validated structure, e248c13 shape):
        # without this override the executor registers legs under the
        # MAIN-ENGINE DSL policy — 30h hard-timeout, 8h stale-flat, tight stop —
        # which would shred the hold (caught live on the crypto xs basket
        # 2026-07-19). Wide 20% disaster stop only; the rebalance replaces legs
        # on its own clock.
        "backup_sl_pct_override": 20.0,
        # No mid-hold profit banking: W-X2 measured hold EV as monotonically
        # INCREASING with hold length (H10 > H5 > H3), so every early clip
        # forfeits edge. The rebalance banks profits on its clock.
        "tp_scale_fraction_override": 0.0,
        "min_short_volume_usd_override": float(short_floor_usd),
        **({"strategy_book_equity_frac_override": float(equity_frac)}
           if equity_frac and equity_frac > 0 else {}),
        "dsl_exit_override": {
            "max_loss_pct": 20.0,
            "max_loss_roe_pct": 240.0,
            "protect_pct": 1000.0,
            "retrace_threshold": 1.0,
            "hard_timeout_minutes": float(hold_days) * 1440.0,
            "breakeven_trigger_pct": 0.0,
            "breakeven_lock_pct": 0.0,
            "stale_flat_timeout_minutes": 0.0,
            "consecutive_breaches_required": 1,
            "atr_stop": {"enabled": False},
            "noise_band": {"enabled": False},
        },
    }


def _execute_opened(result: Any) -> bool:
    """True when execute_fn actually opened risk (same contract as xs_momentum_live)."""
    if isinstance(result, dict):
        nested = result.get("result")
        if isinstance(nested, dict):
            return bool(nested.get("executed"))
        if "executed" in result:
            return bool(result.get("executed"))
        if "ok" in result:
            return bool(result.get("ok"))
    return result is None


def _execute_block_detail(result: Any) -> Any:
    if not isinstance(result, dict):
        return result
    return (
        result.get("reason")
        or result.get("error")
        or result.get("blocked_by")
        or result.get("gate_results")
        or result
    )


def _target_book(universe, cfg: Dict[str, Any], fetch_candles: Callable,
                 now_ms: int):
    """(TargetBook, candles_by_coin, error_or_None). Eligibility + ranking on
    COMPLETED daily bars. Coins claimed by other books are excluded before
    ranking (cross-book claim registry). A missing/short benchmark is an ERROR
    (can't residualize per spec) — the caller retries next cycle without
    arming the timer."""
    lb = int(cfg.get("lookback_days", 7))
    k = int(cfg.get("k_per_leg", 5))
    max_pos = int(cfg.get("max_book_positions", 10))
    if max_pos > 0:
        k = min(k, max_pos // 2)
    min_hist = int(cfg.get("history_bars", 60))
    min_vol = float(cfg.get("min_volume_usd", 250_000))
    bench_coin = str(cfg.get("benchmark", "xyz:XYZ100"))
    nbars = max(min_hist + 5, _BETA_WINDOW + 5, lb + 10)

    try:
        bench = completed_bars(fetch_candles(bench_coin, "1d", nbars), now_ms)
    except Exception:
        bench = []
    if not bench or len(bench) < lb + 1:
        return TargetBook([], [], {}), {}, f"benchmark {bench_coin} bars unavailable"

    blocked = get_claims_registry().claimed_by_others(_BOOK_NAME)
    cbc: Dict[str, List[Any]] = {}
    for coin in eligible_xyz_coins(universe, benchmark=bench_coin):
        if coin in blocked:
            logger.debug(f"[xs-xyz] skipping {coin} — claimed by another book")
            continue
        try:
            bars = completed_bars(fetch_candles(coin, "1d", nbars), now_ms)
        except Exception:
            bars = []
        if bars:
            cbc[coin] = bars
    cbc = filter_eligible(cbc, min_hist, min_vol, _NOTIONAL_WINDOW)
    return rank_xyz(cbc, bench, lb, k, _BETA_WINDOW), cbc, None


def _realized_vol_pct(bars, window: int = 14) -> float:
    """14d realized vol (pct) of daily closes — the leg's own volatility.

    Tagged into each ledger row (metadata only, never gates) so the forward
    grader can settle the 2026-07-21 finding that momentum continuation is far
    stronger for high-vol thematic xyz names (corr +0.45) than low-vol
    mega-caps like AAPL (+0.22, 1.4% vol, the book's one red long): split with
    `shadow_status.py --book xs_xyz_equities --meta vol_bucket=high`. Distinct
    from the REFUTED vol-scaling/idio-vol overlays — this only asks whether a
    vol FLOOR on entry (drop AAPL-class names) helps, on live evidence. 0.0 if
    too few bars."""
    try:
        cl = [float(b.get("c") if isinstance(b, dict) else getattr(b, "c", 0))
              for b in bars[-(window + 1):]]
        if len(cl) < window + 1:
            return 0.0
        rets = [cl[i] / cl[i - 1] - 1 for i in range(1, len(cl)) if cl[i - 1] > 0]
        if len(rets) < 2:
            return 0.0
        import statistics
        return round(statistics.pstdev(rets) * 100, 3)
    except Exception:
        return 0.0


def maybe_rebalance(config: Dict[str, Any], universe, positions,
                    fetch_candles: Callable, execute_fn: Callable,
                    close_fn: Callable) -> Optional[Dict]:
    """Self-gating rebalance: fires at most once per hold-days. Returns the plan
    (or None when disabled / not time / no book). Live mode executes the diff
    (close drops first, then open adds); shadow_only records + logs only.
    Every rebalance writes the full target basket to the shadow ledger so
    forward grading (and the pre-committed kills) start on day one."""
    cfg = config.get(_BOOK_NAME) or {}
    if not bool(cfg.get("enabled", False)):
        return None
    hold_days = float(cfg.get("hold_days", 5))
    now = time.time()
    if now - _last_ts() < hold_days * 86400:
        return None                                            # not time yet
    now_ms = int(now * 1000)
    shadow = bool(cfg.get("shadow_only", False))

    book, cbc, err = _target_book(universe, cfg, fetch_candles, now_ms)
    if err:
        logger.warning(f"[xs-xyz] {err} — skip rebalance (retry next cycle)")
        return None
    if not book.longs or not book.shorts:
        logger.info("[xs-xyz] no target book (too few eligible xyz equities) — skip rebalance")
        return None

    # ── Ownership-scoped current book ─────────────────────────────────────────
    # cur_long/cur_short ONLY contain coins this rebalancer opened (intersected
    # with live positions) — never foreign positions.
    owned = _get_owned()
    live = _live_coin_set(positions)
    owned.prune(live)
    get_claims_registry().prune_to(live, _BOOK_NAME)
    cur_long, cur_short = owned.filter_to_owned(positions)

    plan = rebalance_plan(book, cur_long, cur_short)
    _save_ts(now)                                              # arm the timer before live execution
    n = _rebalance_n() + 1
    _save_rebalance_n(n)

    # Every rebalance's FULL target basket goes to the ledger (one row per leg)
    # so scripts/shadow_status.py forward-grades the book from day one — the
    # 12-rebalance cumulative and single-rebalance −8% kills are graded there.
    def _leg_row(coin: str, side: str) -> Dict[str, Any]:
        bars = cbc.get(coin) or []
        _vol = _realized_vol_pct(bars)
        return {
            "coin": coin, "side": side,
            "signal_bar_t": bar_t(bars[-1]) if bars else 0,
            "entry_ref_px": float((bars[-1].get("c") if isinstance(bars[-1], dict)
                                   else getattr(bars[-1], "c", 0)) or 0) if bars else 0.0,
            "horizon_days": hold_days,
            "stop_pct": 20.0,
            "ts": now_ms,
            "meta": {"shadow": shadow, "rebalance_n": n,
                     "score": round(float(book.scores.get(coin, 0.0)), 6),
                     "vol_14d_pct": _vol,
                     # Fixed 3.0% threshold (the 2026-07-21 universe median),
                     # NOT a per-rebalance median — so the same coin gets a
                     # stable label across rebalances and the forward split is
                     # comparable. AAPL-class (~1.4%) reads "low".
                     "vol_bucket": "high" if _vol >= 3.0 else "low"},
        }
    shadow_ledger.record_many(_BOOK_NAME, [_leg_row(c, "long") for c in book.longs]
                              + [_leg_row(c, "short") for c in book.shorts])

    # Session event. LIVE events carry NO shadow key (pnl_by_book attribution
    # reads `not e.get("shadow", False)` — the xs_rebalance lesson, W-X2 audit
    # 2026-07-20); shadow_only runs tag shadow=true explicitly.
    evt = {"event": "xs_xyz_rebalance", "rebalance_n": n,
           "longs": book.longs, "shorts": book.shorts,
           "open_long": plan["open_long"], "open_short": plan["open_short"],
           "close": plan["close_long"] + plan["close_short"]}
    if shadow:
        evt["shadow"] = True
    log_event(evt)
    logger.info(f"[xs-xyz] {'SHADOW' if shadow else 'LIVE'} rebalance #{n} — "
                f"target {len(book.longs)}L/{len(book.shorts)}S; "
                f"open {len(plan['open_long'])}L+{len(plan['open_short'])}S, "
                f"close {len(plan['close_long']) + len(plan['close_short'])}")

    if shadow or is_empty_plan(plan):
        return plan

    # LIVE: close drops first (free capital), then open adds — both legs.
    claims = get_claims_registry()
    short_floor = float(cfg.get("min_volume_usd", 250_000))
    # Per-book sizing bound (see _analysis docstring): 10 simultaneous legs off
    # the GLOBAL fraction put 12x gross on the xyz dex, where a 20pp momentum
    # crash is a wipeout. 0 = fall back to the global path.
    book_frac = float(cfg.get("equity_frac", 0) or 0)
    for coin in plan["close_long"] + plan["close_short"]:
        try:
            close_fn(coin)
            owned.remove(coin)
            claims.release(coin, _BOOK_NAME)
        except Exception as e:
            logger.warning(f"[xs-xyz] close {coin} failed: {e}")
    for side, coins in (("long", plan["open_long"]), ("short", plan["open_short"])):
        for coin in coins:
            try:
                if not claims.claim(coin, _BOOK_NAME):
                    logger.warning(f"[xs-xyz] open {side} {coin} skipped — "
                                   f"claimed by {claims.owner_of(coin)}")
                    continue
                a = _analysis(coin, side, book.scores.get(coin, 0.0),
                              hold_days=hold_days, short_floor_usd=short_floor,
                              equity_frac=book_frac)
                result = execute_fn(a)
                if _execute_opened(result):
                    owned.add(coin, side)
                    log_event({"event": "book_open", "book": _BOOK_NAME,
                               "coin": coin, "side": side, "rebalance_n": n,
                               "score": round(float(book.scores.get(coin, 0.0)), 6)})
                    logger.info(f"[xs-xyz] LIVE opened {side} {coin} "
                                f"(resid7 {book.scores.get(coin, 0.0)*100:+.1f}%)")
                else:
                    claims.release(coin, _BOOK_NAME)
                    reason = _execute_block_detail(result)
                    logger.warning(
                        f"[xs-xyz] open {side} {coin} not recorded — executor did not open"
                        + (f": {reason}" if reason else "")
                    )
            except Exception as e:
                claims.release(coin, _BOOK_NAME)
                logger.warning(f"[xs-xyz] open {side} {coin} failed: {e}")
    claims.save()
    owned.save()
    return plan
