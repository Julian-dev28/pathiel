"""The 3 surviving signal books — and only these (MINIMAL_SYSTEM.md §3).

Each book emits the same Intent shape and returns EVERY signal as a shadow-ledger
row whether or not capital deploys (grading never stops when capital goes on —
the funding_spike_short_live pattern).

PORTS, NOT REWRITES. The signal math is imported from the already-live v1
modules so it cannot drift:
  - extreme_fade      : agents/extreme_fade.compute_signals + the completed-bar /
                        entry-window / crash-bar-dedup / skew-arm path of
                        agents/extreme_fade_live (imported helpers).
  - funding_spike_short: agents/funding_spike_short_live.funding_z (the W-F2A
                        z-episode math) + the seen-map episode dedup.
  - xs_momentum       : agents/xs_momentum.rank_universe / rebalance_plan,
                        equity-gated OFF below the spec's arming floor.

Books gate themselves on bar completion and their own persisted timers/dedup —
the loop has ONE cadence. State files carry a .v2_ prefix so Phase-2 shadow runs
never touch v1 book state; they route through rebalancer_owned.state_file so
HERMES_STATE_DIR redirects them in tests.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

from hermes_trader.agents.extreme_fade import compute_signals as _fade_compute_signals
from hermes_trader.agents.extreme_fade_live import (
    _bar_t as bar_t,
    _completed_bars,
    _last_close,
    _market_skew,
)
from hermes_trader.agents.funding_spike_short_live import funding_z
from hermes_trader.agents.rebalancer_owned import OwnedPositions, state_file
from hermes_trader.agents.xs_momentum import (
    TargetBook,
    is_empty_plan,
    rank_universe,
    rebalance_plan,
)
from hermes_trader.v2.risk import GROSS_CAP_PCT, MIN_ORDER_USD

logger = logging.getLogger(__name__)

DAY_MS = 86_400_000

# v2-prefixed state files (never shared with v1 books).
_EF_STATE_FILE = state_file(".v2_extreme_fade_state.json")     # coin -> faded crash-bar t
_FS_SEEN_FILE = state_file(".v2_funding_spike_seen.json")      # coin -> episode entry-day ms
_XS_TS_FILE = state_file(".v2_xs_rebalance_ts")                # last rebalance epoch-s
_XS_OWNED_FILE = state_file(".v2_xs_positions.json")           # OwnedPositions


@dataclass
class Intent:
    """One executable entry candidate. Same shape for every book (spec §3)."""
    book: str
    coin: str
    side: str                 # "long" | "short"
    notional_usd: float
    stop_pct: float
    hold_days: float
    leverage: int = 1
    entry_ref_px: float = 0.0
    signal_bar_t: int = 0
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BookResult:
    """One book's output for a signal cycle."""
    book: str
    records: List[Dict[str, Any]] = field(default_factory=list)  # ledger rows (EVERY signal)
    intents: List[Intent] = field(default_factory=list)          # executable entries
    closes: List[str] = field(default_factory=list)              # coins to close (xs drops)
    info: Dict[str, Any] = field(default_factory=dict)


# ── Shared sizing + universe helpers ──────────────────────────────────────────

def intent_notional(frac: float, equity: float, book_cap_usd: float = 0.0,
                    min_order_usd: float = MIN_ORDER_USD) -> float:
    """Spec §3 sizing: notional = clamp(frac × equity, MIN_ORDER_USD, book_cap).

    At $19 everything pins to the $10.50 exchange minimum — that is the real
    floor of the whole design.
    """
    n = max(0.0, float(frac)) * max(0.0, float(equity))
    if book_cap_usd and book_cap_usd > 0:
        n = min(n, float(book_cap_usd))
    return max(n, float(min_order_usd))


def completed_bars(bars: Optional[List[Any]], now_ms: int) -> List[Any]:
    """COMPLETED daily bars only — v2 LAW #2 (contract-tested, not a convention).

    Verbatim port (by import) of extreme_fade_live._completed_bars: a daily bar
    is forming iff it STARTED < 24h ago; a bar that started exactly 24h ago just
    closed and is kept.
    """
    return list(_completed_bars(bars, now_ms) or [])


def _val(m: Any, key: str) -> float:
    try:
        return float(m.get(key) if isinstance(m, dict) else getattr(m, key))
    except Exception:
        return 0.0


def tradeable_perps(universe: Optional[List[Dict[str, Any]]], min_volume_usd: float,
                    cap: int) -> List[str]:
    """Top-`cap` liquid native perps by 24h volume (no HIP-3 `:`, no `@` spot/index)."""
    rows = []
    for m in universe or []:
        coin = m.get("coin") or ""
        if not coin or coin.startswith("@") or ":" in coin or m.get("type") == "spot":
            continue
        dvol = _val(m, "dayNtlVlm")
        if dvol >= min_volume_usd:
            rows.append((dvol, coin))
    rows.sort(reverse=True)
    return [c for _, c in rows[: max(0, int(cap))]]


def _load_json(path: str) -> Dict[str, int]:
    try:
        with open(path) as fh:
            d = json.load(fh)
        return {str(k): int(v) for k, v in d.items()} if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_json(path: str, data: Dict[str, int]) -> None:
    try:
        with open(path, "w") as fh:
            json.dump(data, fh, sort_keys=True)
    except Exception:
        pass


# ── Book 1: extreme_fade (long a completed −12% daily crash) ──────────────────

def extreme_fade_intents(cfg: Dict[str, Any], universe: Optional[List[Dict[str, Any]]],
                         held_coins: Set[str], fetch_candles: Callable,
                         equity: float, now_ms: Optional[int] = None) -> BookResult:
    """extreme_fade: 20% stop, 3d hold, deep tier ≤ −20% at 1.5× size.

    Evidence: findings/extreme_surface.md live cell +4.2%/ep @12bps, n=193.
    W-B2 skew-arm ported as live-configured (enforce=true, DEMOLITION_MANIFEST:
    ROBUST): disarmed cycles still record every signal, they just open nothing.
    """
    res = BookResult(book="extreme_fade")
    if not bool(cfg.get("enabled", True)):
        res.info["disabled"] = True
        return res
    now_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    entry_window_ms = float(cfg.get("entry_window_hours", 6.0)) * 3_600_000

    skew_cfg = cfg.get("skew_arm") or {}
    skew_enabled = bool(skew_cfg.get("enabled", True))
    skew_window = int(skew_cfg.get("window", 20))
    n_bars = max(6, skew_window + 8) if skew_enabled else 6

    cbc: Dict[str, List[Any]] = {}
    crash_bar_t: Dict[str, int] = {}
    for coin in tradeable_perps(universe, float(cfg.get("min_volume_usd", 5_000_000.0)),
                                int(cfg.get("max_scan_coins", 40))):
        try:
            bars = fetch_candles(coin, "1d", n_bars)
        except Exception:
            bars = None
        bars = completed_bars(bars, now_ms)
        if bars and len(bars) >= 2:
            cbc[coin] = bars
            crash_bar_t[coin] = bar_t(bars[-1])

    skew = _market_skew(cbc, skew_window) if skew_enabled else None
    armed = (skew is None) or (skew < float(skew_cfg.get("threshold", 0.0)))
    enforce = bool(skew_cfg.get("enforce", True))

    signals = _fade_compute_signals(cbc, {"extreme_fade": {**cfg, "enabled": True}})
    stop_pct = float(cfg.get("stop_pct", 20.0))
    hold_days = float(cfg.get("hold_days", 3.0))
    leverage = max(1, int(cfg.get("leverage", 1)))

    res.records = [{
        "coin": s.coin, "side": "long",
        "signal_bar_t": crash_bar_t.get(s.coin, 0),
        "entry_ref_px": _last_close(cbc.get(s.coin)),
        "horizon_days": hold_days, "stop_pct": stop_pct, "ts": now_ms,
        "meta": {"prior_ret_pct": round(s.prior_daily_ret * 100, 2),
                 "skew": round(skew, 4) if skew is not None else None,
                 "armed": armed, "enforced": enforce},
    } for s in signals]
    res.info = {"signals": len(signals), "skew": skew, "armed": armed}

    if enforce and not armed:
        res.info["disarmed"] = True
        return res

    faded = _load_json(_EF_STATE_FILE)
    deep = cfg.get("deep_tier") or {}
    base_frac = float(cfg.get("equity_fraction", 0.40) or 0.40)
    max_new = int(cfg.get("max_new_per_cycle", 2))
    for s in signals:
        if len(res.intents) >= max_new:
            break
        if s.coin in held_coins:
            continue                            # no stacking
        bt = crash_bar_t.get(s.coin, 0)
        if bt and faded.get(s.coin) == bt:
            continue                            # already faded THIS crash bar
        if bt and (now_ms - (bt + DAY_MS)) > entry_window_ms:
            continue                            # stale: don't chase the bounce
        frac = base_frac
        deep_hit = bool(deep) and s.prior_daily_ret <= float(deep.get("crash_pct", -0.20))
        if deep_hit:
            frac = float(deep.get("equity_fraction", frac) or frac)
        res.intents.append(Intent(
            book="extreme_fade", coin=s.coin, side="long",
            notional_usd=intent_notional(frac, equity, float(cfg.get("book_cap_usd", 0.0) or 0.0)),
            stop_pct=stop_pct, hold_days=hold_days, leverage=leverage,
            entry_ref_px=_last_close(cbc.get(s.coin)), signal_bar_t=bt,
            meta={"prior_ret_pct": round(s.prior_daily_ret * 100, 2),
                  "deep_tier": deep_hit, "skew": skew},
        ))
    return res


def extreme_fade_mark_opened(coin: str, signal_bar_t: int) -> None:
    """Persist crash-bar dedup after a successful LIVE open (restart-safe)."""
    faded = _load_json(_EF_STATE_FILE)
    faded[str(coin)] = int(signal_bar_t)
    _save_json(_EF_STATE_FILE, faded)


# ── Book 2: funding_spike_short (crowded-long funding-spike fade, W-F2A) ──────

def funding_spike_intents(cfg: Dict[str, Any], universe: Optional[List[Dict[str, Any]]],
                          held_coins: Set[str], coin_funding_z: Callable[[str, int, int], Optional[float]],
                          equity: float, now_ms: Optional[int] = None) -> BookResult:
    """funding_spike_short: 24h funding z ≥ 2 vs own 30d → short, 5d hold, 15% stop.

    Episode dedup: once a coin fires, NO re-entry until its z falls below exit_z
    (1.0). `coin_funding_z(coin, now_ms, lookback_days)` is injected — the live
    deps wrap fetch_funding_history through funding_z (the imported W-F2A math);
    tests inject a table. Pre-committed kill: forward EV25 < 0 over 15 episodes.
    """
    res = BookResult(book="funding_spike_short")
    if not bool(cfg.get("enabled", True)):
        res.info["disabled"] = True
        return res
    now_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    entry_z = float(cfg.get("entry_z", 2.0))
    exit_z = float(cfg.get("exit_z", 1.0))
    lookback = int(cfg.get("lookback_days", 30))
    stop_pct = float(cfg.get("stop_pct", 15.0))
    hold_days = float(cfg.get("hold_days", 5.0))
    leverage = max(1, int(cfg.get("leverage", 1)))
    sig_bar_t = (now_ms // DAY_MS) * DAY_MS

    mids = {m.get("coin"): (_val(m, "midPx") or _val(m, "markPx"))
            for m in universe or [] if m.get("coin")}
    seen = _load_json(_FS_SEEN_FILE)
    scanned = 0
    signals: List[Dict[str, Any]] = []
    for coin in tradeable_perps(universe, float(cfg.get("min_volume_usd", 20_000_000.0)),
                                int(cfg.get("max_scan_coins", 40))):
        z = coin_funding_z(coin, now_ms, lookback)
        scanned += 1
        if z is None:
            continue
        if coin in seen:
            if z < exit_z:
                seen.pop(coin, None)            # episode over — coin re-armable
            continue
        if z >= entry_z:
            signals.append({"coin": coin, "z": round(z, 3),
                            "entry_ref_px": round(mids.get(coin, 0.0) or 0.0, 8)})
    _save_json(_FS_SEEN_FILE, seen)             # persist episode clears

    res.records = [{
        "coin": s["coin"], "side": "short",
        "signal_bar_t": sig_bar_t, "entry_ref_px": s["entry_ref_px"],
        "horizon_days": hold_days, "stop_pct": stop_pct, "ts": now_ms,
        "meta": {"funding_z": s["z"]},
    } for s in signals]
    res.info = {"scanned": scanned, "signals": len(signals)}

    max_new = int(cfg.get("max_new_per_cycle", 1))
    for s in signals:
        if len(res.intents) >= max_new:
            break
        if s["coin"] in held_coins:
            continue
        res.intents.append(Intent(
            book="funding_spike_short", coin=s["coin"], side="short",
            notional_usd=intent_notional(float(cfg.get("equity_fraction", 0.25) or 0.25),
                                         equity, float(cfg.get("book_cap_usd", 0.0) or 0.0)),
            stop_pct=stop_pct, hold_days=hold_days, leverage=leverage,
            entry_ref_px=s["entry_ref_px"], signal_bar_t=sig_bar_t,
            meta={"funding_z": s["z"]},
        ))
    return res


def funding_spike_mark_episode(coin: str, signal_bar_t: int) -> None:
    """Arm episode dedup (on LIVE open, or for every emitted shadow signal — the
    v1 shadow behavior, so a persistent spike is ONE episode, not one per scan)."""
    seen = _load_json(_FS_SEEN_FILE)
    seen[str(coin)] = int(signal_bar_t)
    _save_json(_FS_SEEN_FILE, seen)


def live_coin_funding_z(coin: str, now_ms: int, lookback_days: int) -> Optional[float]:
    """Live z fetch (lazy client import keeps books importable without touching
    the network layer; the imported funding_z IS the validated W-F2A math)."""
    try:
        from hermes_trader.client.hl_client import fetch_funding_history
        rows = fetch_funding_history(coin, now_ms - (lookback_days + 2) * DAY_MS, now_ms)
        return funding_z(rows, now_ms, lookback_days)
    except Exception:
        return None


# ── Book 3: xs_momentum (LB7 residual rank, top-4/bottom-4, 5d, equity-gated) ─

def xs_armed(equity: float, cfg: Dict[str, Any],
             gross_cap_pct: float = GROSS_CAP_PCT) -> bool:
    """Spec §3: OFF until 0.10 × equity ≥ $10.50 per leg AND the full 2k-leg book
    fits inside the gross cap (~the $100 mark; $84 gross = 440% of a $19 account).
    """
    frac = float(cfg.get("equity_frac_per_leg", 0.10) or 0.10)
    k = int(cfg.get("k_per_leg", 4))
    per_leg = frac * max(0.0, float(equity))
    if per_leg < MIN_ORDER_USD:
        return False
    return (2 * k * per_leg) <= gross_cap_pct * float(equity)


def xs_intents(cfg: Dict[str, Any], universe: Optional[List[Dict[str, Any]]],
               positions: Optional[List[Dict[str, Any]]], fetch_candles: Callable,
               equity: float, now_s: Optional[float] = None,
               blocked_coins: Optional[Set[str]] = None,
               gross_cap_pct: float = GROSS_CAP_PCT) -> BookResult:
    """xs_momentum: market-neutral top-k/bottom-k on the imported v1 ranker.

    Self-gates on the 5d rebalance timer AND the equity arming floor. Exit is
    the rebalance itself; the DSL stop/timeout on each leg is only a backstop.
    """
    res = BookResult(book="xs_momentum")
    if not bool(cfg.get("enabled", True)):
        res.info["disabled"] = True
        return res
    if not xs_armed(equity, cfg, gross_cap_pct):
        res.info["armed"] = False
        return res
    now_s = float(now_s if now_s is not None else time.time())
    hold_days = float(cfg.get("hold_days", 5.0))
    try:
        last = float(open(_XS_TS_FILE).read().strip())
    except Exception:
        last = 0.0
    if now_s - last < hold_days * 86_400:
        res.info["armed"] = True
        res.info["waiting"] = True
        return res

    lb = int(cfg.get("lookback_days", 7))
    k = int(cfg.get("k_per_leg", 4))
    beta_window = int(cfg.get("beta_window", 30))
    ranking = str(cfg.get("ranking", "raw"))
    zext_window = int(cfg.get("zext_window", 14))
    nbars = max(lb + 10, beta_window + 5, zext_window + 5, 40)
    blocked = blocked_coins or set()

    cbc: Dict[str, List[Any]] = {}
    for coin in tradeable_perps(universe, float(cfg.get("min_volume_usd", 5_000_000.0)),
                                int(cfg.get("universe_top_n", 50))):
        if coin in blocked:
            continue
        try:
            bars = fetch_candles(coin, "1d", nbars)
        except Exception:
            bars = None
        if bars and len(bars) >= lb + 1:
            cbc[coin] = bars
    bench = None
    if bool(cfg.get("residual", True)):
        try:
            bench = fetch_candles("BTC", "1d", nbars)
        except Exception:
            bench = None
    book = rank_universe(cbc, lb, k, bench_bars=bench, beta_window=beta_window,
                         ranking=ranking, zext_window=zext_window)
    if not book.longs or not book.shorts:
        res.info["armed"] = True
        res.info["empty_book"] = True
        return res

    owned = OwnedPositions(_XS_OWNED_FILE).load()
    live: Set[str] = set()
    for p in positions or []:
        pos = p.get("position", p) if isinstance(p, dict) else {}
        coin = pos.get("coin")
        try:
            szi = float(pos.get("szi", 0) or 0)
        except (TypeError, ValueError):
            szi = 0.0
        if coin and szi != 0:
            live.add(coin)
    owned.prune(live)
    cur_long, cur_short = owned.filter_to_owned(positions)
    plan = rebalance_plan(book, cur_long, cur_short)
    try:
        open(_XS_TS_FILE, "w").write(str(now_s))   # arm the timer at decision time (v1 port)
    except Exception:
        pass

    stop_pct = float(cfg.get("stop_pct", 25.0))
    leverage = max(1, int(cfg.get("leverage", 1)))
    frac = float(cfg.get("equity_frac_per_leg", 0.10) or 0.10)
    now_ms = int(now_s * 1000)
    sig_bar_t = (now_ms // DAY_MS) * DAY_MS
    for side, coins in (("long", plan["open_long"]), ("short", plan["open_short"])):
        for coin in coins:
            res.intents.append(Intent(
                book="xs_momentum", coin=coin, side=side,
                notional_usd=intent_notional(frac, equity,
                                             float(cfg.get("book_cap_usd", 0.0) or 0.0)),
                stop_pct=stop_pct, hold_days=hold_days, leverage=leverage,
                signal_bar_t=sig_bar_t,
                meta={"rank_score": book.scores.get(coin, 0.0)},
            ))
    res.closes = plan["close_long"] + plan["close_short"]
    # Record BOTH target legs (not just the diff) — the spread is the graded object.
    res.records = [{
        "coin": coin, "side": side, "signal_bar_t": sig_bar_t,
        "entry_ref_px": 0.0,   # filled by loop when mids are known; 0 = ungradeable row
        "horizon_days": hold_days, "stop_pct": stop_pct, "ts": now_ms,
        "meta": {"rank_score": round(book.scores.get(coin, 0.0), 6)},
    } for side, leg in (("long", book.longs), ("short", book.shorts)) for coin in leg]
    res.info = {"armed": True, "plan": plan,
                "longs": book.longs, "shorts": book.shorts}
    return res


def xs_mark_opened(coin: str, side: str) -> None:
    """Record an xs leg as owned (mirrors v1 OwnedPositions bookkeeping)."""
    owned = OwnedPositions(_XS_OWNED_FILE).load()
    owned.add(coin, side)
    owned.save()


def xs_mark_closed(coin: str) -> None:
    owned = OwnedPositions(_XS_OWNED_FILE).load()
    owned.remove(coin)
    owned.save()
