"""v2 loop — ONE process, ONE cadence (MINIMAL_SYSTEM.md §3).

    60s exit sub-cycle : mids (weight 2) → DSL floor check on every open
                         position → reduce-only close on breach (LIVE only;
                         SHADOW computes + logs the verdicts, closes nothing).
    30-min signal cycle: account state → universe (cached) → recorder snapshot →
                         each book's intents → ledger (ALWAYS, v2_ prefix) →
                         executor (LIVE only, and the executor itself refuses
                         without HERMES_V2_LIVE=1 — a double wall).

Books that are internally slower (funding z on daily rows, xs 5-day rebalance,
extreme_fade on completed daily bars) simply return nothing most cycles — the
cadence is one; the books gate themselves on bar completion and persisted
timers, never on private scan intervals.

Every network dependency is injected via `Deps` so the whole loop is gate-testable
offline; `default_deps()` lazily imports the client layer (AFTER the entrypoint
has set the rate-budget env vars).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Set

from hermes_trader.session_log import append as log_event
from hermes_trader.v2 import books, executor, ledger, recorder, risk
from hermes_trader.v2.dsl_exit import (
    active_position_coins,
    check_all_positions,
    load_state,
    rehydrate_from_exchange,
)

logger = logging.getLogger(__name__)

SIGNAL_INTERVAL_MIN = 30.0
EXIT_INTERVAL_S = 60.0

V2_DEFAULTS: Dict[str, Any] = {
    "mode": "SHADOW",                       # LIVE requires HERMES_V2_LIVE=1 on top
    "signal_interval_min": SIGNAL_INTERVAL_MIN,
    "exit_interval_s": EXIT_INTERVAL_S,
    "extreme_fade": {
        "enabled": True, "crash_pct": -0.12, "stop_pct": 20.0, "hold_days": 3.0,
        "equity_fraction": 0.40, "deep_tier": {"crash_pct": -0.20, "equity_fraction": 0.60},
        "entry_window_hours": 6.0, "max_new_per_cycle": 2,
        "min_volume_usd": 5_000_000.0, "max_scan_coins": 40, "leverage": 12,
        "skew_arm": {"enabled": True, "window": 20, "threshold": 0.0, "enforce": True},
    },
    "funding_spike_short": {
        "enabled": True, "entry_z": 2.0, "exit_z": 1.0, "lookback_days": 30,
        "stop_pct": 15.0, "hold_days": 5.0, "equity_fraction": 0.25,
        "min_volume_usd": 20_000_000.0, "max_scan_coins": 40,
        "max_new_per_cycle": 1, "leverage": 12,
    },
    "xs_momentum": {
        # Spec §3: LB7 residual rank, top-4/bottom-4, 5d rebalance, equity-gated.
        "enabled": True, "lookback_days": 7, "k_per_leg": 4, "hold_days": 5.0,
        "equity_frac_per_leg": 0.10, "min_volume_usd": 5_000_000.0,
        "universe_top_n": 50, "residual": True, "ranking": "raw",
        "beta_window": 30, "zext_window": 14, "stop_pct": 25.0, "leverage": 12,
    },
    "risk": {
        "max_daily_loss_pct": risk.MAX_DAILY_LOSS_PCT,
        "max_daily_loss_usd": -100,
        "gross_cap_pct": risk.GROSS_CAP_PCT,
        "min_available_margin_pct": risk.MIN_AVAILABLE_MARGIN_PCT,
        "long_liquidity_floor_usd": risk.LONG_LIQUIDITY_FLOOR_USD,
        "short_liquidity_floor_usd": risk.SHORT_LIQUIDITY_FLOOR_USD,
    },
    "backup_sl_max_frac_of_liq": executor.BACKUP_SL_MAX_FRAC_OF_LIQ,
    "recorder": {"enabled": True, "interval_hours": 1.0},
}


def _deep_merge(base: Dict[str, Any], override: Any) -> Dict[str, Any]:
    merged = dict(base or {})
    if not isinstance(override, dict):
        return merged
    for key, val in override.items():
        old = merged.get(key)
        merged[key] = _deep_merge(old, val) if isinstance(old, dict) and isinstance(val, dict) else val
    return merged


def load_config() -> Dict[str, Any]:
    """V2_DEFAULTS deep-merged under the operator's `.agent-config.json` "v2" block."""
    try:
        from hermes_trader.agents.config_store import read_agent_config
        return _deep_merge(V2_DEFAULTS, (read_agent_config().get("v2") or {}))
    except Exception:
        return dict(V2_DEFAULTS)


# ── Injected dependencies ─────────────────────────────────────────────────────

@dataclass
class Deps:
    fetch_mids: Callable[[], Dict[str, float]]
    fetch_account: Callable[[], Dict[str, Any]]
    fetch_universe: Callable[[], List[Dict[str, Any]]]
    fetch_candles: Callable[[str, str, int], List[Any]]
    coin_funding_z: Callable[[str, int, int], Optional[float]]
    now: Callable[[], float] = time.time


def default_deps() -> Deps:
    """Live deps. Client imports are LAZY — the entrypoint sets the rate-budget
    env vars before this runs, so HL_LIMITER is built with the shadow budget."""
    from hermes_trader.client.exchange import get_all_hl_mids
    from hermes_trader.client.hl_client import (
        fetch_account_state,
        fetch_hl_candles,
        resolve_user_address,
    )
    from hermes_trader.client.universe import get_universe

    def _account() -> Dict[str, Any]:
        return fetch_account_state(resolve_user_address(), include_hip3=True) or {}

    return Deps(
        fetch_mids=lambda: get_all_hl_mids(include_hip3=True),
        fetch_account=_account,
        fetch_universe=lambda: get_universe() or [],
        fetch_candles=lambda coin, interval, n: fetch_hl_candles(coin, interval, n),
        coin_funding_z=books.live_coin_funding_z,
    )


def _held_coins(positions: Optional[List[Dict[str, Any]]]) -> Set[str]:
    held: Set[str] = set()
    for p in positions or []:
        pos = p.get("position", p) if isinstance(p, dict) else {}
        coin = pos.get("coin")
        try:
            szi = float(pos.get("szi", 0) or 0)
        except (TypeError, ValueError):
            szi = 0.0
        if coin and szi != 0:
            held.add(coin)
    # DSL-registry backstop: a flaky account read must not un-hold a tracked coin.
    try:
        held.update(active_position_coins().keys())
    except Exception:
        pass
    return held


def _is_live(mode: str) -> bool:
    """LIVE needs BOTH the config/arg mode AND the env opt-in (double wall)."""
    return str(mode).upper() == "LIVE" and executor.live_enabled()


def assert_shadow_invariant(mode: str) -> None:
    """Refuse to run a SHADOW loop in a process armed for live orders."""
    if str(mode).upper() != "LIVE" and executor.live_enabled():
        raise executor.ShadowModeViolation(
            "SHADOW loop started with HERMES_V2_LIVE=1 — unset it or run mode=LIVE")


# ── 60s exit sub-cycle ────────────────────────────────────────────────────────

def exit_cycle(cfg: Dict[str, Any], mode: str, deps: Deps) -> Dict[str, Any]:
    """Protection, not trading: DSL floors on every open position."""
    live = _is_live(mode)
    if not live:
        # Pick up the (possibly foreign, v1) loop's tracker writes each pass.
        # The entrypoint sets HERMES_STATE_READONLY=1 in shadow so check() can
        # never clobber the live loop's .dsl-state.json.
        load_state(force=True)
    mids = deps.fetch_mids() or {}
    verdicts = check_all_positions(mids)
    out: Dict[str, Any] = {"mode": mode, "checked": len(mids), "exits": []}
    for v in verdicts:
        entry = {"coin": v.coin, "side": v.position_side, "reason": v.reason,
                 "phase": v.phase, "unrealized_pct": round(v.unrealized_pct, 3)}
        if live:
            entry["close"] = executor.close_coin(v.coin)
        else:
            entry["close"] = {"ok": False, "reason": "shadow_mode"}
        out["exits"].append(entry)
        log_event({"event": "v2_dsl_exit", "mode": mode, **entry})
        logger.info(f"[v2-loop] DSL exit {v.coin} {v.position_side}: {v.reason}"
                    + ("" if live else " (shadow: not closed)"))
    return out


# ── 30-min signal cycle ───────────────────────────────────────────────────────

def signal_cycle(cfg: Dict[str, Any], mode: str, deps: Deps) -> Dict[str, Any]:
    live = _is_live(mode)
    now_s = deps.now()
    now_ms = int(now_s * 1000)

    account = deps.fetch_account() or {}
    equity = float(account.get("equity") or 0.0)
    if equity <= 0:
        logger.warning("[v2-loop] degraded equity read — skipping signal cycle")
        return {"mode": mode, "skipped": "degraded_equity_read"}
    positions = account.get("asset_positions") or []
    available = float(account.get("available") or 0.0)
    total_ntl = float(account.get("total_ntl") or 0.0)

    sod = risk.start_of_day_equity(equity, now_s)
    ks = risk.kill_switch(cfg.get("risk") or {}, equity, sod)
    if ks["breached"]:
        log_event({"event": "v2_kill_switch", "mode": mode, **ks})
        logger.error(f"[v2-loop] KILL SWITCH: daily PnL ${ks['daily_pnl']} <= "
                     f"${ks['limit_usd']} — flatten + no entries")
        if live:
            executor.flatten_all(account)
        return {"mode": mode, "kill_switch": ks}

    if live:
        qd = account.get("queried_dexes")
        rehydrate_from_exchange(positions, queried_dexes=set(qd) if qd else None)

    universe = deps.fetch_universe() or []
    recorded = recorder.maybe_record(cfg.get("recorder") or {}, universe, now_s=now_s)

    held = _held_coins(positions)
    mids_from_universe = {m.get("coin"): (float(m.get("midPx") or m.get("markPx") or 0.0))
                          for m in universe if m.get("coin")}

    xs_blocked: Set[str] = set()
    if live:
        xs_blocked = executor.claims_registry().claimed_by_others("xs_momentum")

    results: List[books.BookResult] = [
        books.extreme_fade_intents(cfg.get("extreme_fade") or {}, universe, held,
                                   deps.fetch_candles, equity, now_ms),
        books.funding_spike_intents(cfg.get("funding_spike_short") or {}, universe, held,
                                    deps.coin_funding_z, equity, now_ms),
        books.xs_intents(cfg.get("xs_momentum") or {}, universe, positions,
                         deps.fetch_candles, equity, now_s, blocked_coins=xs_blocked,
                         gross_cap_pct=float((cfg.get("risk") or {}).get(
                             "gross_cap_pct", risk.GROSS_CAP_PCT))),
    ]

    summary: Dict[str, Any] = {"mode": mode, "equity": equity, "sod": sod,
                               "recorder_rows": recorded, "books": {}}
    for r in results:
        # Ledger FIRST, always, both modes — grading never stops when capital goes on.
        for rec in r.records:
            if not rec.get("entry_ref_px"):
                rec["entry_ref_px"] = round(mids_from_universe.get(rec["coin"], 0.0) or 0.0, 8)
        ledger.record_many(r.book, r.records)

        opened, closed = 0, 0
        if live:
            for coin in r.closes:
                res = executor.close_coin(coin, book=r.book)
                if res.get("ok"):
                    closed += 1
                    if r.book == "xs_momentum":
                        books.xs_mark_closed(coin)
            for intent in r.intents:
                res = executor.execute_intent(
                    intent, cfg=cfg, equity=equity, available=available,
                    held_coins=held, total_open_notional=total_ntl,
                    day_volume_usd=float(next(
                        (books._val(m, "dayNtlVlm") for m in universe
                         if m.get("coin") == intent.coin), 0.0)),
                    daily_pnl=ks["daily_pnl"],
                )
                log_event({"event": "v2_execute", "book": r.book, "coin": intent.coin,
                           "side": intent.side, "result": {k: res.get(k) for k in
                           ("executed", "reason", "blocked_by", "entry_px", "size_usd")}})
                if res.get("executed"):
                    opened += 1
                    held.add(intent.coin)
                    total_ntl += float(res.get("size_usd") or intent.notional_usd)
                    if r.book == "extreme_fade":
                        books.extreme_fade_mark_opened(intent.coin, intent.signal_bar_t)
                    elif r.book == "funding_spike_short":
                        books.funding_spike_mark_episode(intent.coin, intent.signal_bar_t)
                    elif r.book == "xs_momentum":
                        books.xs_mark_opened(intent.coin, intent.side)
        else:
            # Shadow parity with v1: a persistent funding spike is ONE episode.
            for intent in r.intents:
                if r.book == "funding_spike_short":
                    books.funding_spike_mark_episode(intent.coin, intent.signal_bar_t)
        summary["books"][r.book] = {"records": len(r.records), "intents": len(r.intents),
                                    "closes": len(r.closes), "opened": opened,
                                    "closed": closed, **r.info}
    log_event({"event": "v2_signal_cycle", **{k: v for k, v in summary.items() if k != "mode"},
               "mode": mode})
    return summary


# ── Runner ────────────────────────────────────────────────────────────────────

def run_forever(mode: Optional[str] = None, cfg: Optional[Dict[str, Any]] = None,
                deps: Optional[Deps] = None, max_cycles: Optional[int] = None,
                sleep_fn: Callable[[float], None] = time.sleep) -> None:
    cfg = cfg or load_config()
    mode = str(mode or cfg.get("mode", "SHADOW")).upper()
    assert_shadow_invariant(mode)
    deps = deps or default_deps()
    exit_s = float(cfg.get("exit_interval_s", EXIT_INTERVAL_S))
    signal_s = float(cfg.get("signal_interval_min", SIGNAL_INTERVAL_MIN)) * 60.0
    logger.info(f"[v2-loop] starting mode={mode} exit={exit_s:.0f}s signal={signal_s:.0f}s "
                f"live_enabled={executor.live_enabled()}")
    last_signal = 0.0
    n = 0
    while max_cycles is None or n < max_cycles:
        n += 1
        try:
            exit_cycle(cfg, mode, deps)
        except Exception as exc:
            logger.warning(f"[v2-loop] exit cycle failed (non-fatal): {exc}")
        now = deps.now()
        if now - last_signal >= signal_s:
            last_signal = now
            try:
                signal_cycle(cfg, mode, deps)
            except Exception as exc:
                logger.warning(f"[v2-loop] signal cycle failed (non-fatal): {exc}")
        if max_cycles is not None and n >= max_cycles:
            break
        sleep_fn(exit_s)
