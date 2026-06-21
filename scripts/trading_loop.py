#!/usr/bin/env python3
"""Continuous trading loop for hermes-trader.

Per cycle: scan -> TA filter -> AI research -> execute. The TA filter
(`analyze_perception`, zero AI cost) gates the paid LLM call — only CONFIRMED
perceptions reach research. A perception whose `momentumBurst` trigger fired
bypasses the gate: a large fast move is always worth researching.

Every cycle and decision is appended to the session log (`session_log`), so
`status.py` and the hourly cron report show a live activity feed.

Flags (tolerant — unknown flags are ignored so legacy callers keep working):
  --env {prod,dev}  Currently informational; loaded from .env.local in CWD.
  --daemon          Currently informational; the loop already daemonizes via
                    `nohup ... &` / Hermes background. Kept for skill scripts.
"""
import argparse
import math
import os
import sys
import threading
import time
import logging

# Load .env.local (CWD-relative, matches skill restart command).
env_path = '.env.local'
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, _, val = line.partition('=')
                os.environ[key.strip()] = val.strip()

# Tolerant argparse — `--env prod --daemon` were silently dropped before.
# Now they're parsed (and ignored) instead of raising on stray flags some
# future callers might add.
_parser = argparse.ArgumentParser(add_help=False)
_parser.add_argument("--env", default="prod")
_parser.add_argument("--daemon", action="store_true")
_args, _unknown = _parser.parse_known_args()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s:%(name)s:%(message)s'
)

from hermes_trader.agents.perception import scan_once
from hermes_trader.agents.ta_filter import analyze_perception
from hermes_trader.agents.research import research
from hermes_trader.agents.executor import (
    _runner_entry_block_reason,
    close_position_market,
    maybe_execute,
    monitor_exits,
    record_external_position_close,
    route_verdict,
)
from hermes_trader.agents.dsl_exit import active_position_coins, rehydrate_from_exchange
from hermes_trader.agents.config import get_config
from hermes_trader.agents.config_store import read_agent_config
from hermes_trader.agents.memory import memory
from hermes_trader.client.exchange import get_all_hl_mids, prewarm_meta_cache
from hermes_trader.client.universe import get_universe
from hermes_trader.client.hl_client import fetch_account_state, fetch_aggregate_contributions_since, resolve_user_address
from hermes_trader.positions_snapshot import write_snapshot
from hermes_trader.session_log import append as log_event

# Last wall-clock time the (heavy) external-alpha poll ran — throttles it off the
# 60s scan cadence so it can't add ~40s of latency to every exit-monitor cycle.
_last_external_alpha_ts = 0.0


def _run_external_alpha(config) -> None:
    """Validated external-alpha edges (smart_money copy + basis_gap) run alongside the
    AI scan. Each source is independently shadow/live via its own `shadow_mode`. A LIVE
    signal becomes a synthetic analysis routed through route_verdict -> maybe_execute, so
    EVERY safety gate (kill-switch, caps, margin, liquidation stop, sizing) still applies;
    only the candle-impulse runner gate is bypassed (different alpha source). Sized small
    via external_alpha_notional_usd. Wrapped by the caller so an outage can't break scan."""
    sm = bool((config.get("smart_money") or {}).get("enabled", False))
    bg = bool((config.get("basis_gap") or {}).get("enabled", False))
    if not (sm or bg):
        return
    # Throttle: the poll (30 traders' fills + stock feeds) takes ~40s — far too heavy to
    # run every 60s scan and starve monitor_exits. Signals stay fresh for ~30min, so a
    # ~5min cadence loses nothing. Gated by external_alpha_interval_min.
    global _last_external_alpha_ts
    interval_s = float(config.get("external_alpha_interval_min", 5)) * 60
    _now = time.time()
    if _now - _last_external_alpha_ts < interval_s:
        return
    _last_external_alpha_ts = _now
    import uuid as _uuid
    from hermes_trader.agents.external_alpha import external_alpha_signals
    # 0 (or unset) = FULL sizing (use the normal max_trade_notional_usd); a positive
    # value caps external-alpha trades smaller than normal.
    ext_notional = float(config.get("external_alpha_notional_usd", 0) or 0)
    held = set(memory.open_position_coins())
    for s in external_alpha_signals(config):
        shadow = bool((config.get(s["source"]) or {}).get("shadow_mode", True))
        log_event({"event": "external_alpha", "coin": s["coin"], "side": s["side"],
                   "source": s["source"], "reason": s["reason"],
                   "strength": round(float(s.get("strength", 0)), 2), "shadow": shadow})
        if shadow:
            logger.info(f"[external-alpha] SHADOW would-trade {s['coin']} {s['side']} "
                        f"via {s['source']} — {s['reason']}")
            continue
        if s["coin"] in held:
            logger.info(f"[external-alpha] {s['coin']} already held — skip {s['source']}")
            continue
        # Synthetic analysis: marked external_alpha so the runner gate bypasses it; all
        # downstream safety gates + sizing run normally. stop/tp left 0 -> executor's
        # ATR backup-SL (clamped to liq buffer) places the protective bracket.
        analysis = {
            "id": str(_uuid.uuid4()), "coin": s["coin"],
            "verdict": "LONG" if s["side"] == "long" else "SHORT", "side": s["side"],
            "confidence": 0.80, "entry_px": 0.0, "stop_px": 0.0, "tp_px": 0.0,
            "reasoning": f"[{s['source']}] {s['reason']}", "news_risk": "none",
            "ai_down": False, "created_at": int(time.time() * 1000),
            "composite_score": 0.0, "external_alpha": s["source"],
            "external_alpha_notional": ext_notional,
        }
        logger.info(f"[external-alpha] LIVE {s['coin']} {s['side']} via {s['source']} "
                    f"(${ext_notional:.0f}) — {s['reason']}")
        try:
            routed = route_verdict(analysis)
            log_event({"event": "external_alpha_exec", "coin": s["coin"],
                       "source": s["source"], "action": routed.get("action"),
                       "executed": bool((routed.get("result") or {}).get("executed")),
                       "detail": (routed.get("result") or {}).get("reason")
                       or (routed.get("result") or {}).get("order_id")})
        except Exception as _xe:
            logger.warning(f"[external-alpha] execute failed for {s['coin']}: {_xe}")

logger = logging.getLogger(__name__)


def _remaining_minutes(ms_remaining: float) -> int:
    """Human log label for a positive millisecond cooldown."""
    return max(1, int(math.ceil(max(0.0, ms_remaining) / 60_000)))

# ── Self-healing watchdog (armed FIRST, before any network I/O) ─────────────
# No external supervisor exists (restart.sh just launches). A local DNS/network
# outage froze the loop twice — once mid-scan, once during STARTUP (universe
# load / prewarm) where the watchdog wasn't armed yet, so it stayed hung ~58min.
# Arm it before any network call so BOTH a startup hang and a mid-scan hang
# self-heal via re-exec. `_last_progress_ts` is bumped after each completed scan
# cycle; if it goes stale > HERMES_WATCHDOG_TIMEOUT_S (default 600s, generous so
# a slow-but-progressing scan isn't killed) the process re-execs (startup
# rehydrates trackers from disk; the stacking backstop prevents a re-entry
# pyramid). A persistent DNS outage just re-execs every ~600s until it clears.
_last_progress_ts = time.time()
_watchdog_timeout_s = int(os.environ.get('HERMES_WATCHDOG_TIMEOUT_S', '600'))


def _watchdog() -> None:
    while True:
        time.sleep(60)
        if _watchdog_timeout_s <= 0:
            continue
        stalled = time.time() - _last_progress_ts
        if stalled >= _watchdog_timeout_s:
            logger.error(
                f"[watchdog] no progress for {stalled:.0f}s "
                f"(> {_watchdog_timeout_s}s) — HUNG (startup or scan); re-execing to self-heal")
            try:
                log_event({"event": "error", "scope": "watchdog",
                           "error": f"hung {stalled:.0f}s — re-exec"})
            except Exception:
                pass
            os.execv(sys.executable, [sys.executable] + sys.argv)


threading.Thread(target=_watchdog, name="hermes-watchdog", daemon=True).start()
logger.info(f"[watchdog] armed pre-startup: re-exec if no progress for {_watchdog_timeout_s}s")

logger.info("=== HERMES TRADER - Starting Continuous Trading Loop ===")

config = get_config()
startup_agent_config = read_agent_config()
startup_mode = str(startup_agent_config.get("mode", "OFF")).upper()
logger.info(f"Mode: {startup_mode}  env={_args.env}  daemon={_args.daemon}")
# HIP-3 toggle: read once at startup so the prefetched universe includes
# tokenized-equity / commodity perps if enabled. The agent config is
# hot-reloaded per cycle inside the executor / perception layer for other
# fields; the universe itself is fetched once at startup, so flipping
# enable_hip3 mid-run requires a loop restart to pick up new markets.
try:
    _enable_hip3 = bool(startup_agent_config.get("enable_hip3", False))
except Exception:
    _enable_hip3 = False
universe = get_universe(include_hip3=_enable_hip3)
logger.info(
    f"Universe loaded: {len(universe)} markets"
    + (f" (HIP-3 enabled — {sum(1 for m in universe if m.get('dex'))} tokenized markets)" if _enable_hip3 else "")
)
# Warm the per-dex meta cache BEFORE the first scan/execute so the restart-time
# 429 storm can't make coin resolution fall through to "Unknown coin" (which
# kills the HIP-3 backup stop-loss) or blank candle fetches. Bound it: the SDK
# meta call has hung during startup, which left the bot neither scanning nor
# monitoring exits until an external restart.
def _prewarm_meta_cache_bounded(timeout_s: float) -> None:
    state = {"done": False, "error": None}

    def _run() -> None:
        try:
            prewarm_meta_cache()
        except Exception as e:
            state["error"] = e
        finally:
            state["done"] = True

    t = threading.Thread(target=_run, name="hermes-meta-prewarm", daemon=True)
    t.start()
    t.join(timeout_s)
    if t.is_alive():
        logger.warning(
            f"[startup] meta prewarm exceeded {timeout_s:.0f}s — continuing; "
            "coin metadata will warm lazily")
    elif state["error"] is not None:
        logger.warning(f"[startup] meta prewarm failed (will warm lazily): {state['error']}")


_prewarm_meta_cache_bounded(float(os.environ.get('HERMES_META_PREWARM_TIMEOUT_S', '3')))
# The universe carries prevDayPx / dayNtlVlm / funding which DRIFT over the
# day; fetched once here they'd freeze at loop-start for the whole process,
# so mover-selection + volume-ranking would rank stale 24h windows (a coin
# ripping now would never enter the movers slot). Re-fetch on a TTL so those
# fields track the live market. metaAndAssetCtxs is ~20 weight (+~8 POSTs for
# HIP-3) — trivial against HL's 1200 weight/min. Env-overridable; 0 disables.
universe_refresh_s = int(os.environ.get('HERMES_UNIVERSE_REFRESH_S', '1800'))
_last_universe_refresh = time.time()
memory.load()  # hydrate from .agent-memory.json so cache + flush work.

# Startup grace: the prewarm burst above + the cold-cache first scan (every
# coin's candles fetched fresh) + any tail from the just-killed process all hit
# the SAME per-IP HL budget at once → the restart 429-storm (observed 2026-06-15:
# ~30% scan data-gaps for ~2min, loop stalled). Pause so the rate-limiter bucket
# refills before the first scan fires its full candle burst. Env-overridable;
# 0 disables. Cheap one-time cost; steady-state scans are unaffected.
_startup_grace_s = float(os.environ.get('HERMES_STARTUP_GRACE_S', '12'))
if _startup_grace_s > 0:
    logger.info(f"[startup] grace delay {_startup_grace_s:.0f}s — letting HL rate budget refill before the first cold scan")
    time.sleep(_startup_grace_s)

# Scan cadence: env-overridable, default 60s. Keep it above the candle cache
# TTL (config.scan.cacheTtlMs) so every scan reads a fresh candle snapshot.
scan_interval = int(os.environ.get('HERMES_SCAN_INTERVAL', '60'))
min_score = config['scan']['minCompositeScore']

logger.info(f"Scan interval: {scan_interval}s, Min score: {min_score}")
log_event({
    "event": "loop_start",
    "scan_interval": scan_interval,
    "min_score": min_score,
    # Full config snapshot at startup so the feed shows exactly what the bot
    # is configured to do — useful for postmortems ("what was the cap when
    # this trade happened?") and for the operator UI to surface drift.
    "config": startup_agent_config,
})


def _burst_fired(perception):
    """True if the perception's momentumBurst trigger fired (a large fast move)."""
    return any(t.get("name") == "momentumBurst" and t.get("fired")
               for t in perception.get("triggers", []))


def _trigger_fired(perception, name: str) -> bool:
    return any(t.get("name") == name and t.get("fired")
               for t in perception.get("triggers", []))


def _slow_burn_count(perception) -> int:
    return sum(
        1 for t in perception.get("triggers", [])
        if t.get("name") in ("volumeBuildup1h", "trendFlip1h", "higherLows1h")
        and t.get("fired")
    )


def _pre_research_runner_block_reason(perception, config):
    """Return the entry gate reason for non-held candidates before paid AI.

    The AI can still research held positions for CLOSE decisions. For fresh
    entries, avoid paying the LLM for a candidate the live runner gate would
    deterministically reject after research anyway. This intentionally only
    pre-blocks when shorts are disabled; if shorts are enabled, the AI may still
    need to choose direction on downtrend candidates.
    """
    gate = config.get("runner_entry_gate") or {}
    if not bool(gate.get("enabled", False)):
        return ""
    if bool(gate.get("allow_shorts", False)):
        return ""
    min_conf = float(gate.get("min_confidence", config.get("min_ai_confidence", 0.70)))
    analysis_stub = {
        "coin": perception.get("coin"),
        "side": "long",
        "confidence": min_conf,
        "composite_score": float(perception.get("composite_score", 0) or 0),
        "volume_spike_fired": _trigger_fired(perception, "volumeSpike"),
        "breakout_fired": _trigger_fired(perception, "breakout"),
        "momentum_burst_fired": _trigger_fired(perception, "momentumBurst"),
        "daily_mover_fired": _trigger_fired(perception, "dailyMover"),
        "uptrend_momentum_fired": _trigger_fired(perception, "uptrendMomentum"),
        "downtrend_momentum_fired": _trigger_fired(perception, "downtrendMomentum"),
        "slow_burn_count": _slow_burn_count(perception),
    }
    return _runner_entry_block_reason(analysis_stub, config)


def _capital_rotation_live(config) -> bool:
    rot = config.get("capital_rotation") or {}
    return bool(rot.get("enabled", False)) and not bool(rot.get("shadow_mode", True))


def _rotation_preflight_eval(coin, perception, positions, config):
    """Would capital-rotation free room for a margin-blocked fresh `coin`? Returns the
    RotationDecision (or None). The pre-research margin preflight is UPSTREAM of the
    executor-stage rotation, so a strong margin-blocked mover (the missed-mover case)
    dies before rotation ever sees it. This mirrors the executor's rotation eval so the
    preflight can (a) let it through when rotation is LIVE, or (b) shadow-log it for
    forward validation. Fully guarded — never raises into the preflight; on any error
    returns None so the normal block stands."""
    try:
        rot = config.get("capital_rotation") or {}
        if not bool(rot.get("enabled", False)):
            return None
        from hermes_trader.agents.rotation import decide_rotation
        from hermes_trader.agents import dsl_exit as _dsl
        now = time.time()
        opos = []
        for p in (positions or []):
            pp = p.get("position", p) if isinstance(p, dict) else {}
            c = pp.get("coin")
            if not c or c == coin:
                continue
            tr = (_dsl._active_positions.get(f"{c}_long")
                  or _dsl._active_positions.get(f"{c}_short"))
            age = (now - tr.entry_time) / 60.0 if tr is not None else 0.0
            opos.append({"coin": c,
                         "roe_pct": float(pp.get("returnOnEquity", 0) or 0) * 100.0,
                         "age_minutes": age})
        return decide_rotation(
            candidate_coin=coin,
            candidate_composite=float((perception or {}).get("composite_score", 0) or 0),
            blocked_reasons=["total notional would exceed"],   # margin saturation == capital block
            open_positions=opos,
            min_candidate_composite=float(rot.get("min_candidate_composite", 40.0)),
            min_hold_minutes=float(rot.get("min_hold_minutes", 30.0)),
            protect_winner_roe_pct=float(rot.get("protect_winner_roe_pct", 3.0)),
        )
    except Exception:
        return None


def _position_value_usd(row) -> float:
    pos = (row or {}).get("position", row or {})
    try:
        val = float(pos.get("positionValue", 0) or 0)
        if val > 0:
            return abs(val)
    except (TypeError, ValueError):
        pass
    try:
        return abs(float(pos.get("szi", 0) or 0)) * float(pos.get("entryPx", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def _fresh_entry_preblock_reason(coin, perception, config, equity, available,
                                 positions, state, daily_pnl):
    """Cheap deterministic gates before paid AI research on fresh entries."""
    is_hip3 = ":" in (coin or "")
    mode = str(config.get("mode", "OFF")).upper()
    if mode == "LIVE" and not os.environ.get("HYPERLIQUID_PRIVATE_KEY"):
        return "private_key_missing (fresh entries cannot execute)"
    if is_hip3 and not bool(config.get("enable_hip3", False)):
        return "hip3_disabled"
    if (not is_hip3) and not bool(config.get("enable_crypto", True)):
        return "crypto_disabled"

    blocklist = set(config.get("coin_blocklist", []) or [])
    allowlist = set(config.get("coin_allowlist", []) or [])
    if coin in blocklist:
        return f"{coin} is on the coin blocklist"
    if allowlist and coin not in allowlist:
        return f"{coin} not on the coin allowlist"

    loss_remaining = memory.loss_cooldown_remaining_min(coin)
    if loss_remaining > 0:
        return f"loss_cooldown ({loss_remaining:.0f}min remaining)"

    try:
        max_daily_loss = float(config.get("max_daily_loss_usd", -100) or -100)
    except (TypeError, ValueError):
        max_daily_loss = -100.0
    if daily_pnl <= max_daily_loss:
        return f"daily_loss_gate (PnL ${daily_pnl:.2f} <= ${max_daily_loss:.0f})"

    try:
        halt_pct = float(config.get("daily_giveback_halt_pct", 0.0) or 0.0)
        min_peak = float(config.get("daily_giveback_min_peak_usd", 20.0) or 0.0)
        peak = float(memory.peak_daily_pnl())
    except (TypeError, ValueError):
        halt_pct, min_peak, peak = 0.0, 0.0, 0.0
    if halt_pct > 0 and peak >= min_peak:
        floor = peak * (1.0 - halt_pct)
        if daily_pnl <= floor:
            return (f"daily_giveback_gate (PnL ${daily_pnl:.2f} <= "
                    f"${floor:.2f} floor from ${peak:.2f} peak)")

    if equity <= 0:
        return "account_state_unavailable (equity<=0)"

    rotation_live = _capital_rotation_live(config)
    try:
        max_concurrent = int(config.get("max_concurrent", 3) or 3)
    except (TypeError, ValueError):
        max_concurrent = 3
    if max_concurrent > 0 and len(positions or []) >= max_concurrent and not rotation_live:
        return f"max_positions_reached ({len(positions or [])}/{max_concurrent})"

    total_ntl = 0.0
    try:
        total_ntl = float((state or {}).get("total_ntl", 0) or 0)
    except (TypeError, ValueError):
        total_ntl = sum(_position_value_usd(p) for p in (positions or []))
    try:
        max_total_pct = float(config.get("max_total_notional_pct", 0) or 0)
    except (TypeError, ValueError):
        max_total_pct = 0.0
    if max_total_pct > 0 and total_ntl >= equity * max_total_pct and not rotation_live:
        return (f"notional_room_full (${total_ntl:.0f} >= "
                f"${equity * max_total_pct:.0f} cap)")

    try:
        min_avail_pct = float(config.get("min_available_margin_pct", 0.10) or 0.0)
    except (TypeError, ValueError):
        min_avail_pct = 0.10
    dex = coin.split(":", 1)[0] if is_hip3 else ""
    dex_equity = (state or {}).get("dex_equity") or {}
    dex_available = (state or {}).get("dex_available") or {}
    try:
        target_equity = float(dex_equity.get(dex, equity) or 0)
        target_available = float(dex_available.get(dex, available) or 0)
    except (TypeError, ValueError):
        target_equity, target_available = equity, available
    if target_equity > 0 and min_avail_pct > 0:
        avail_pct = target_available / target_equity
        if avail_pct < min_avail_pct:
            label = dex or "main"
            # Upstream capital-rotation hook — mirrors the concurrency/notional bypass at
            # max_concurrent/notional above. A strong margin-blocked mover is the exact
            # missed-mover case rotation exists for, but it dies HERE (pre-research),
            # before the executor-stage rotation can see it. When rotation is LIVE and it
            # would free room, fall through (let it reach research + executor rotation);
            # in shadow, log what it WOULD do so the margin case can be validated forward.
            _rd = _rotation_preflight_eval(coin, perception, positions, config)
            _can_rotate = _rd is not None and _rd.should_rotate
            if not (rotation_live and _can_rotate):
                if _can_rotate:
                    logger.warning(
                        f"[rotation-preflight][SHADOW] {coin} "
                        f"(comp {float((perception or {}).get('composite_score', 0) or 0):.0f}) "
                        f"margin-blocked ({label} {avail_pct*100:.1f}%<{min_avail_pct*100:.0f}%); "
                        f"rotation WOULD {_rd.reason}")
                return (f"insufficient_free_margin_preflight ({label}: "
                        f"{avail_pct*100:.1f}% < {min_avail_pct*100:.0f}%)")
            # rotation_live and would free room → fall through to remaining preflight gates

    try:
        vol = float(perception.get("daily_volume_usd", 0) or 0)
        vol_floor = float(config.get("min_hip3_volume_usd" if is_hip3 else "min_market_volume_usd",
                                     0) or 0)
    except (TypeError, ValueError):
        vol, vol_floor = 0.0, 0.0
    if vol_floor > 0 and 0 < vol < vol_floor:
        return f"liquidity_floor_preflight (${vol/1e6:.2f}M < ${vol_floor/1e6:.2f}M)"

    return ""


def _sync_account_state():
    """Pull live aggregated equity + positions from HL, persist to memory.

    Returns (equity, positions, available, spot_usdc, queried_dexes, state).
    `state` is the full dict so callers can grab per-dex breakdowns
    (`dex_equity`, `dex_available`) without re-fetching.
    """
    user = resolve_user_address()
    if not user:
        # No user → no authoritative position view. Return an EMPTY queried-dexes
        # set (not {""}) so the DSL reconcile preserves existing trackers instead
        # of dropping them as "stale".
        return 0.0, [], 0.0, 0.0, set(), {}
    try:
        state = fetch_account_state(user, include_hip3=True)
    except Exception as e:
        # Fetch FAILED (e.g. API timeout storm). We did NOT successfully query any
        # dex, so report queried_dexes=set() — NOT {""}. Reporting the main dex as
        # "queried" while holding no position data caused live main-dex trackers
        # (e.g. NIL) to be falsely dropped and then re-synthesized with a looser
        # default stop. Empty set => rehydrate preserves every tracker this tick.
        logger.warning(f"[heartbeat] HL fetch_account_state failed: {e}")
        return 0.0, [], 0.0, 0.0, set(), {}

    equity = float(state.get("equity", 0) or 0)
    if equity <= 0:
        # A 'successful' fetch returning $0 equity while positions are open is a
        # degraded/empty API response (timeout-storm), not reality. Don't poison
        # memory — writing it would record a false equity=0 and dailyPnl=-SOD (which
        # also drags the daily-loss kill toward a false trip). Preserve last-known-good
        # by skipping the memory update this tick; queried_dexes=set() keeps DSL
        # trackers intact, and maybe_execute already refuses to size on equity<=0.
        logger.warning("[heartbeat] fetch returned equity<=0 (degraded API) — skipping memory update, preserving last-known-good")
        return 0.0, [], 0.0, 0.0, set(), {}
    # Heartbeat shows total-across-dexes free margin (what the operator
    # actually has trade-ready) — not the main-only number used internally
    # by the executor for native-crypto sizing.
    available = float(state.get("available_aggregated", state.get("available", 0)) or 0)
    spot_usdc = float(state.get("spot_usdc", 0) or 0)
    positions = state.get("asset_positions", []) or []
    queried_dexes = state.get("queried_dexes") or {""}
    live_position_coins = {
        (p.get("position") or {}).get("coin")
        for p in positions
        if (p.get("position") or {}).get("coin")
    }

    # PARTIAL-DEX degraded-read guard: a 'successful' fetch where equity>0 (main
    # dex fine) but a HIP-3 dex we HOLD a position on failed to respond drops that
    # dex's equity from the aggregate — e.g. on 2026-06-03 a missing xyz dex made
    # equity read $56.65 instead of $187.42 (a phantom -$128/-69%). The equity<=0
    # guard above can't catch it (main was funded). Left unguarded it poisons
    # memory equity/dailyPnl AND can FALSE-TRIP the daily-loss kill switch.
    # Detect it: if any dex backing an open DSL tracker isn't in queried_dexes,
    # the aggregate is incomplete → preserve last-known-good (skip memory update,
    # queried_dexes=set() keeps trackers), same as the equity<=0 path.
    held_dexes = {(c.split(":", 1)[0] if ":" in c else "") for c in active_position_coins()}
    missing_dexes = held_dexes - set(queried_dexes)
    if missing_dexes:
        logger.warning(
            f"[heartbeat] partial-dex degraded read: held dex(es) {missing_dexes} "
            f"missing from queried {set(queried_dexes)} (equity read ${equity:.2f} is "
            f"incomplete) — skipping memory update, preserving last-known-good")
        return 0.0, [], 0.0, 0.0, set(), {}

    vanished_tracked = {
        c for c in active_position_coins()
        if ((c.split(":", 1)[0] if ":" in c else "") in set(queried_dexes)
            and c not in live_position_coins)
    }

    # Subtract net USDC contributions so transfers/deposits don't show
    # up as trading PnL in the equity-diff calculation.
    sod_ts_ms = memory.get_day_start_ts() * 1000
    contributions = 0.0
    if sod_ts_ms > 0:
        try:
            contributions = fetch_aggregate_contributions_since(user, sod_ts_ms)
        except Exception as e:
            logger.warning(f"[heartbeat] contribution fetch failed: {e}")

    if vanished_tracked:
        logger.error(
            f"[heartbeat] tracked position(s) vanished from live account after "
            f"successful dex query: {sorted(vanished_tracked)} — accepting equity "
            f"move as real for daily PnL/kill-switch")
    memory.track_daily_pnl(equity, contributions, force_accept=bool(vanished_tracked))
    memory.update_open_positions(positions)
    memory.flush()
    return equity, positions, available, spot_usdc, queried_dexes, state


# When we last paid for AI research on each coin (this process). Throttles the
# AI close-check on coins we already hold so we don't research a "hold" every
# scan. Resets on restart (a fresh close-check on startup is harmless/useful).
_last_research_by_coin: dict = {}


while True:
    try:
        # ── Heartbeat: refresh equity / positions before scanning ──────────
        equity, positions, available, spot_usdc, queried_dexes, state = _sync_account_state()
        daily_pnl = memory.get_daily_pnl()
        if equity <= 0 and spot_usdc > 0:
            logger.warning(
                f"[heartbeat] perp equity $0 but ${spot_usdc:.2f} USDC idle in "
                f"spot — transfer spot->perp to enable trading.")
        # Compact config snapshot for the heartbeat line — surfaces what the
        # bot is currently tuned to do without forcing the watcher to pop
        # open `.agent-config.json`. Read fresh each tick so a hot-reloaded
        # config is reflected in the next heartbeat.
        _cfg = read_agent_config()
        # Per-dex breakdown so the dashboard can show where USDC + free
        # margin actually sits (main vs xyz vs km, etc).
        dex_equity = {k: round(float(v), 2) for k, v in (state.get("dex_equity") or {}).items()}
        dex_available = {k: round(float(v), 2) for k, v in (state.get("dex_available") or {}).items()}
        log_event({
            "event": "loop_heartbeat",
            "equity": round(equity, 4),
            "available": round(available, 4),
            "dex_equity": dex_equity,
            "dex_available": dex_available,
            "spot_usdc": round(spot_usdc, 4),
            "daily_pnl": round(daily_pnl, 4),
            "open_positions": len(positions),
            "config": {
                "mode": _cfg.get("mode"),
                "frac": _cfg.get("equity_fraction_per_trade"),
                "lev": _cfg.get("leverage"),
                "max_conc": _cfg.get("max_concurrent"),
                "notional_cap": _cfg.get("max_total_notional_pct"),
                "cool_min": _cfg.get("cooldown_min"),
                "min_conf": _cfg.get("min_ai_confidence"),
                "kill": _cfg.get("max_daily_loss_usd"),
                "crypto": bool(_cfg.get("enable_crypto", True)),
                "hip3": bool(_cfg.get("enable_hip3", False)),
            },
        })
        # Publish the position list so the dashboard can render the table
        # without its own fetch_account_state call (which, sharing this IP,
        # was doubling HL load and tripping per-IP rate limits).
        write_snapshot(positions)

        # ── HARD daily-loss kill-switch ─────────────────────────────────────
        # The daily_loss GATE (risk_gates) only blocks NEW entries — it can't
        # close what's already open, so a losing book OVERSHOOTS the limit as
        # positions keep bleeding to their DSL stops (2026-06-09: hit -$35 vs a
        # -$30 cap). Make the floor HARD: once the day's loss breaches the limit,
        # FLATTEN every open position so the loss can't run further. The gate then
        # keeps re-entry blocked until the UTC roll. Guarded by equity>0: every
        # degraded/partial-read path in _sync_account_state returns equity=0 (and
        # preserves last-known-good daily_pnl), so a bad read can NEVER trigger a
        # flatten. Idempotent: after flattening, the next tick's positions are
        # empty so it won't re-fire.
        _max_daily_loss = float(_cfg.get("max_daily_loss_usd", -100) or -100)
        if equity > 0 and positions and daily_pnl <= _max_daily_loss:
            logger.warning(
                f"[killswitch] HARD daily-loss floor breached: PnL ${daily_pnl:.2f} "
                f"<= ${_max_daily_loss:.0f} — flattening {len(positions)} open "
                f"position(s) to cap the loss")
            for _p in positions:
                _coin = (_p.get("position") or {}).get("coin")
                if not _coin:
                    continue
                try:
                    _res = close_position_market(_coin)
                    logger.warning(f"[killswitch] flattened {_coin}: ok={_res.get('ok')}")
                except Exception as _e:
                    logger.error(f"[killswitch] failed to flatten {_coin}: {_e}")
            log_event({"event": "hard_killswitch", "daily_pnl": round(daily_pnl, 2),
                       "limit": _max_daily_loss, "flattened": len(positions)})

        # ── DSL exit pass ───────────────────────────────────────────────────
        # Reconcile trackers with live exchange positions (handles restarts,
        # manual closes, externally-filled SLs), then market-close anything
        # whose dynamic floor was breached.
        try:
            stale_trackers = rehydrate_from_exchange(
                positions,
                default_leverage=int(_cfg.get("leverage", 1) or 1),
                queried_dexes=queried_dexes,
            )
            for _stale in stale_trackers:
                try:
                    record_external_position_close(_stale, user=resolve_user_address())
                except Exception as _e:
                    logger.error(f"[outcome-store] vanished tracker record failed "
                                 f"for {_stale.get('coin')}: {_e}")
            # include_hip3=True so xyz:MU / vntl:* etc. get fresh mids each
            # cycle — without them, monitor_exits has no price for HIP-3
            # trackers and their peak/floor never advance (dashboard shows
            # "no DSL" indefinitely and DSL stop never fires on HIP-3).
            mids = get_all_hl_mids(include_hip3=True)
            exits = monitor_exits(mids)
            # Forward-shadow the wider VOL ATR-stop on the same marks (no live effect).
            try:
                from hermes_trader.agents.volstop_shadow import update_and_log as _vs_update
                _vs_update(mids, read_agent_config())
            except Exception as _vse:
                logger.debug(f"[volstop-shadow] cycle hook failed: {_vse}")
            for ex in exits:
                coin = ex["coin"]
                lev = ex.get("leverage", 1)
                lpct = ex.get("leveraged_pct", ex["unrealized_pct"] * lev)
                logger.info(f"[dsl] Closing {coin} {ex.get('side','?')} ({lev}x): "
                            f"{ex['reason']} (margin {lpct:+.2f}% · spot {ex['unrealized_pct']:+.2f}%)")
                # Tag the shadow with the live exit ROE for side-by-side comparison.
                try:
                    from hermes_trader.agents.volstop_shadow import record_live_exit as _vs_exit
                    _vs_exit(coin, ex.get("side"), lpct)
                except Exception:
                    pass
                res = close_position_market(coin)
                # The close response carries authoritative realized PnL when
                # the order filled with a parseable avgPx — prefer it over the
                # tick-time estimate, which is gross of fees and off by the
                # fill slippage.
                evt = {
                    "event": "dsl_exit",
                    "coin": coin,
                    "side": ex.get("side"),
                    "leverage": lev,
                    "reason": ex["reason"],
                    "unrealized_pct": round(ex["unrealized_pct"], 4),
                    "leveraged_pct": round(lpct, 4),
                    "executed": bool(res.get("ok")),
                    "detail": res.get("order_id") or res.get("noop") or res.get("error"),
                }
                if res.get("realized_pnl_pct") is not None:
                    evt["fill_px"] = res.get("fill_px")
                    evt["entry_px"] = res.get("entry_px")
                    evt["realized_spot_pct"] = res.get("spot_pct")
                    evt["realized_pnl_pct"] = res.get("realized_pnl_pct")
                    evt["fees_pct"] = res.get("fees_pct")
                log_event(evt)
        except Exception as e:
            logger.error(f"[dsl] monitor pass failed: {e}")
            log_event({"event": "error", "scope": "dsl_monitor", "error": str(e)})

        if str(_cfg.get("mode", "OFF")).upper() == "OFF":
            logger.info("[mode] OFF — skipping scan/research/execution; exits still monitored")
            _last_progress_ts = time.time()
            logger.info(f"Sleeping {scan_interval}s until next scan...")
            time.sleep(scan_interval)
            continue

        # Refresh the universe on a TTL so prevDayPx / dayNtlVlm / funding track
        # the live market instead of freezing at loop-start (stale fields make
        # the scanner rank yesterday's movers — see HERMES_UNIVERSE_REFRESH_S).
        if universe_refresh_s > 0 and (time.time() - _last_universe_refresh) >= universe_refresh_s:
            try:
                universe = get_universe(force_refresh=True, include_hip3=_enable_hip3)
                _last_universe_refresh = time.time()
                logger.info(f"Universe refreshed: {len(universe)} markets")
            except Exception as e:
                logger.warning(f"[universe] periodic refresh failed, keeping prior snapshot: {e}")

        # OI time-series logger — self-collect open interest forward (HL exposes no OI
        # history) so the OI/price four-quadrant positioning filter can be backtested
        # later. Piggybacks the universe already in hand (no extra API call), throttled +
        # size-capped, wrapped so it can never break the scan.
        try:
            from hermes_trader.agents.oi_logger import append_oi
            append_oi(universe)
        except Exception as _oie:
            logger.debug(f"[oi-logger] append failed (non-fatal): {_oie}")

        logger.info("Scanning markets...")
        results = scan_once(universe=universe, min_score=min_score, config=config)
        logger.info(f"Scan found {len(results)} triggers")
        # Per-cycle heartbeat — proof of life even when nothing triggers.
        # `coin_scores` carries the composite score for each trigger so the
        # feed can show *why* a coin was picked, not just that it was.
        log_event({"event": "scan", "triggers": len(results),
                   "coins": [p['coin'] for p in results],
                   "coin_scores": [{"coin": p['coin'],
                                    "score": round(p.get('composite_score', 0), 1),
                                    "triggers": [t['name'] for t in p.get('triggers', []) if t.get('fired')]}
                                   for p in results]})

        # External-alpha edges (smart_money copy + basis_gap) — validated OOS, run beside
        # the AI scan. MUST use the AGENT config (.agent-config.json), NOT the loop's
        # scanner `config` (get_config()) which lacks the smart_money/basis_gap keys — a
        # mismatch silently early-returned the whole hook. read_agent_config() is hot-read
        # so enable/shadow/sizing changes take effect with no restart.
        try:
            _run_external_alpha(read_agent_config())
        except Exception as _eae:
            logger.warning(f"[external-alpha] cycle failed: {_eae}")

        # Pre-research dedupe cache: coin → last research timestamp this run.
        # Prevents burning AI tokens on a setup that's still in cooldown from a
        # prior cycle. The execute-time `cooldown_gate` is still in place as the
        # authoritative backstop; this just stops the paid LLM call early.
        _cfg_cd = read_agent_config()
        cooldown_min = float(_cfg_cd.get("cooldown_min", 60))
        cooldown_ms = cooldown_min * 60_000
        # How often a HELD coin is re-researched for a possible AI CLOSE. We
        # don't pay for a "hold" PASS every scan — the DSL engine handles fast
        # exits in real time; the AI close-check is the slower structural-flip
        # judgment and only needs an occasional refresh.
        held_research_ms = float(_cfg_cd.get("held_research_interval_min", 10)) * 60_000
        # Re-research throttle for NON-held, non-traded coins: a coin that keeps
        # triggering but keeps PASSing (or whose trade gets gate/margin-rejected)
        # used to be researched EVERY scan — burning LLM tokens/credits on a setup
        # that won't meaningfully change in 60s (e.g. XLM PASS'd every cycle). Skip
        # re-research for this window regardless of verdict. The scan still re-detects
        # it; we just don't re-pay the LLM until the cooldown lapses.
        research_cooldown_ms = float(_cfg_cd.get("research_cooldown_min", 15)) * 60_000
        # Newest trade timestamp per coin (NOT oldest — see the method docstring;
        # the prior inline `setdefault` kept the oldest, so a coin traded twice
        # in the window paid for redundant LLM research every cycle).
        recent_trades_by_coin = memory.latest_trade_ts_by_coin(20)
        held_coins = memory.open_position_coins()
        now_ms = int(time.time() * 1000)

        for perception in results:
            coin = perception['coin']
            score = perception.get('composite_score', 0)

            # Persist perceptions so memory/dashboard track real signal volume.
            try:
                memory.record_perception(perception)
            except Exception:
                pass

            if coin in held_coins:
                # Held position: research only every held_research_interval_min
                # so the AI can still issue a CLOSE without paying for a "hold"
                # PASS on every scan. (A re-entry is gate-blocked anyway.)
                last_research = _last_research_by_coin.get(coin, 0)
                if (now_ms - last_research) < held_research_ms:
                    remaining_min = _remaining_minutes(held_research_ms - (now_ms - last_research))
                    logger.info(f"{coin}: held — next AI close-check in {remaining_min}min — skip")
                    log_event({"event": "ta_skip", "coin": coin,
                               "signal": "HELD_THROTTLE",
                               "score": round(float(score), 1),
                               "trigger_score": round(float(score), 1)})
                    continue
                # Infancy hold: skip the AI close-check while the position is
                # younger than min_ai_close_hold_min (0=off). Measured churn
                # 2026-06-11/12: the FIRST 10-min close-check reversed the AI's
                # own fresh entry 3x (TON 2x, ZEC 1x, each ~-1% ROE incl. fees) —
                # flip-flopping on entry noise. DSL stop + backup SL still
                # protect an infant position; only the AI's second-guess waits.
                min_hold_min = float(_cfg_cd.get("min_ai_close_hold_min", 0) or 0)
                if min_hold_min > 0:
                    from hermes_trader.agents import dsl_exit as _dsl
                    _tr = (_dsl._active_positions.get(f"{coin}_long")
                           or _dsl._active_positions.get(f"{coin}_short"))
                    if _tr is not None:
                        age_min = (time.time() - _tr.entry_time) / 60
                        if age_min < min_hold_min:
                            logger.info(f"{coin}: held {age_min:.0f}min < min_hold "
                                        f"{min_hold_min:.0f}min — infancy, skip close-check")
                            continue
            else:
                # Fresh entry preflight: skip paid research when deterministic
                # downstream gates already prove this entry cannot execute.
                # Held positions took the branch above and still get their
                # periodic AI close-check.
                entry_preblock = _fresh_entry_preblock_reason(
                    coin, perception, _cfg_cd, equity, available,
                    positions, state, daily_pnl,
                )
                if entry_preblock:
                    logger.info(f"{coin}: pre-research {entry_preblock} — skip AI research")
                    log_event({"event": "ta_skip", "coin": coin,
                               "signal": "ENTRY_PREFLIGHT",
                               "score": round(float(score), 1),
                               "trigger_score": round(float(score), 1),
                               "reason": entry_preblock})
                    continue
                # Not held but executed within cooldown_min → re-entry would be
                # gate-blocked, so skip the paid AI call.
                last_ms = recent_trades_by_coin.get(coin)
                if last_ms and (now_ms - last_ms) < cooldown_ms:
                    remaining_min = _remaining_minutes(cooldown_ms - (now_ms - last_ms))
                    logger.info(f"{coin}: pre-research cooldown ({remaining_min}min remaining) — skip")
                    log_event({"event": "ta_skip", "coin": coin,
                               "signal": "COOLDOWN",
                               "score": round(float(score), 1),
                               "trigger_score": round(float(score), 1)})
                    continue
                # Re-research throttle: already researched recently (any verdict) →
                # don't re-pay the LLM until research_cooldown_min lapses.
                last_research = _last_research_by_coin.get(coin, 0)
                if (now_ms - last_research) < research_cooldown_ms:
                    remaining_min = _remaining_minutes(research_cooldown_ms - (now_ms - last_research))
                    logger.info(f"{coin}: re-research throttle ({remaining_min}min remaining) — skip")
                    log_event({"event": "ta_skip", "coin": coin,
                               "signal": "RESEARCH_THROTTLE",
                               "score": round(float(score), 1),
                               "trigger_score": round(float(score), 1)})
                    continue

            # TA filter — cheap statistical gate before the paid AI call.
            ta = analyze_perception(perception)
            if ta['signal'] != 'CONFIRMED' and not _burst_fired(perception):
                logger.info(f"{coin}: TA {ta['signal']} (score {ta['score']:.0f}) — skip AI research")
                log_event({"event": "ta_skip", "coin": coin,
                           "signal": ta['signal'],
                           "score": round(float(ta.get('score', 0)), 1),
                           "trigger_score": round(float(score), 1)})
                continue
            if coin not in held_coins:
                runner_preblock = _pre_research_runner_block_reason(perception, _cfg_cd)
                if runner_preblock:
                    logger.info(f"{coin}: pre-research {runner_preblock} — skip AI research")
                    log_event({"event": "ta_skip", "coin": coin,
                               "signal": "PRE_RESEARCH_RUNNER_GATE",
                               "score": round(float(ta.get('score', 0)), 1),
                               "trigger_score": round(float(score), 1),
                               "reason": runner_preblock})
                    continue
            gate = 'CONFIRMED' if ta['signal'] == 'CONFIRMED' else f"{ta['signal']}+burst"
            logger.info(f"Researching {coin} (trigger {score:.1f}, TA {gate})...")
            # Record the paid-research time so the held-coin throttle above can
            # pace the next AI close-check on this position.
            _last_research_by_coin[coin] = now_ms

            try:
                analysis = research(coin, perception)
                logger.info(f"Verdict: {analysis['verdict']}, Confidence: {analysis['confidence']}")
                # Store the full LLM reasoning verbatim — no character cap.
                # The feed shows the complete rationale.
                _r = (analysis.get('reasoning') or '').strip()
                log_event({"event": "research", "coin": coin,
                           "verdict": analysis['verdict'],
                           "confidence": round(float(analysis['confidence']), 2),
                           "reasoning": _r,
                           "news_risk": analysis.get('news_risk'),
                           "entry_px": analysis.get('entry_px'),
                           "stop_px": analysis.get('stop_px'),
                           "tp_px": analysis.get('tp_px')})

                # All verdict→action routing lives in executor.route_verdict
                # (unit-tested) so no verdict can be silently dropped again.
                # Capture the AI's own verdict BEFORE route_verdict/maybe_execute can
                # mutate it (a TA-sidestep override rewrites PASS→LONG in place) so the
                # execute event can show WHY a PASS still fired.
                _ai_verdict = (analysis.get("verdict") or "").upper()
                routed = route_verdict(analysis)
                action = routed["action"]
                result = routed["result"] or {}
                if action == "execute":
                    logger.info(f"Trade result: {result}")
                    executed = bool(result.get("executed"))
                    # Surface the regime decision so the log answers "why did a
                    # counter-regime trade fire?" — via is one of aligned /
                    # neutral / confidence / composite / trigger:<name> / blocked.
                    mr = (result.get("gate_results") or {}).get("market_regime") or {}
                    log_event({"event": "execute", "coin": coin,
                               "side": analysis['side'],
                               "executed": executed,
                               # WHY it fired: the AI's own verdict + the entry path, so a
                               # PASS that still executes (TA-sidestep override on a strong
                               # composite) is explicit in the feed instead of looking like
                               # a contradiction.
                               "ai_verdict": _ai_verdict,
                               "entry_via": ("ta_sidestep" if analysis.get("sidestep_override")
                                             else "override" if _ai_verdict not in ("LONG", "SHORT")
                                             else "ai"),
                               "detail": result.get("order_id")
                               or result.get("reason")
                               or result.get("blocked_by"),
                               "blocked_by": result.get("blocked_by") if not executed else None,
                               "size_usd": result.get("size_usd"),
                               "entry_px": result.get("entry_px"),
                               "stop_px": result.get("stop_px"),
                               "tp_px": result.get("tp_px"),
                               "regime": mr.get("regime"),
                               "funding_regime": mr.get("funding"),
                               "regime_via": mr.get("via"),
                               "counter_regime": mr.get("counter_trend") or mr.get("against_funding")})
                elif action == "close":
                    logger.info(f"Closed {coin} per AI CLOSE verdict: {result}")
                    log_event({"event": "ai_close", "coin": coin,
                               "executed": bool(result.get("ok")),
                               "detail": result.get("order_id")
                               or result.get("noop")
                               or result.get("error"),
                               "reasoning": (analysis.get("reasoning") or "")})
                elif action == "unknown":
                    log_event({"event": "error", "coin": coin,
                               "error": f"unhandled verdict {routed['verdict']!r}"})
            except Exception as e:
                # repr(e) not str(e): a bare exception (e.g. some httpx errors)
                # stringifies to "" and produced blank "Error processing X:" lines.
                detail = repr(e) if str(e) == "" else str(e)
                logger.error(f"Error processing {coin}: {type(e).__name__}: {detail}")
                log_event({"event": "error", "coin": coin,
                           "error": f"{type(e).__name__}: {detail}"})

        _last_progress_ts = time.time()  # watchdog: a full cycle completed
        logger.info(f"Sleeping {scan_interval}s until next scan...")
        time.sleep(scan_interval)

    except KeyboardInterrupt:
        logger.info("Trading loop stopped by user")
        log_event({"event": "loop_stop"})
        break
    except Exception as e:
        logger.error(f"Trading loop error: {e}")
        log_event({"event": "error", "error": str(e)})
        logger.info("Sleeping 60s before retry...")
        time.sleep(60)
