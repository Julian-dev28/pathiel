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
from hermes_trader.agents.executor import close_position_market, maybe_execute, monitor_exits, route_verdict
from hermes_trader.agents.dsl_exit import active_position_coins, rehydrate_from_exchange
from hermes_trader.agents.config import get_config
from hermes_trader.agents.memory import memory
from hermes_trader.client.exchange import get_all_hl_mids, prewarm_meta_cache
from hermes_trader.client.universe import get_universe
from hermes_trader.client.hl_client import fetch_account_state, fetch_aggregate_contributions_since, resolve_user_address
from hermes_trader.positions_snapshot import write_snapshot
from hermes_trader.session_log import append as log_event

logger = logging.getLogger(__name__)

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
logger.info(f"Mode: LIVE  env={_args.env}  daemon={_args.daemon}")

config = get_config()
# HIP-3 toggle: read once at startup so the prefetched universe includes
# tokenized-equity / commodity perps if enabled. The agent config is
# hot-reloaded per cycle inside the executor / perception layer for other
# fields; the universe itself is fetched once at startup, so flipping
# enable_hip3 mid-run requires a loop restart to pick up new markets.
try:
    from hermes_trader.agents.config_store import read_agent_config
    _enable_hip3 = bool(read_agent_config().get("enable_hip3", False))
except Exception:
    _enable_hip3 = False
universe = get_universe(include_hip3=_enable_hip3)
logger.info(
    f"Universe loaded: {len(universe)} markets"
    + (f" (HIP-3 enabled — {sum(1 for m in universe if m.get('dex'))} tokenized markets)" if _enable_hip3 else "")
)
# Warm the per-dex meta cache BEFORE the first scan/execute so the restart-time
# 429 storm can't make coin resolution fall through to "Unknown coin" (which
# kills the HIP-3 backup stop-loss) or blank candle fetches.
try:
    prewarm_meta_cache()
except Exception as e:
    logger.warning(f"[startup] meta prewarm failed (will warm lazily): {e}")
# The universe carries prevDayPx / dayNtlVlm / funding which DRIFT over the
# day; fetched once here they'd freeze at loop-start for the whole process,
# so mover-selection + volume-ranking would rank stale 24h windows (a coin
# ripping now would never enter the movers slot). Re-fetch on a TTL so those
# fields track the live market. metaAndAssetCtxs is ~20 weight (+~8 POSTs for
# HIP-3) — trivial against HL's 1200 weight/min. Env-overridable; 0 disables.
universe_refresh_s = int(os.environ.get('HERMES_UNIVERSE_REFRESH_S', '1800'))
_last_universe_refresh = time.time()
memory.load()  # hydrate from .agent-memory.json so cache + flush work.

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
    "config": read_agent_config(),
})


def _burst_fired(perception):
    """True if the perception's momentumBurst trigger fired (a large fast move)."""
    return any(t.get("name") == "momentumBurst" and t.get("fired")
               for t in perception.get("triggers", []))


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

    # Subtract net USDC contributions so transfers/deposits don't show
    # up as trading PnL in the equity-diff calculation.
    sod_ts_ms = memory.get_day_start_ts() * 1000
    contributions = 0.0
    if sod_ts_ms > 0:
        try:
            contributions = fetch_aggregate_contributions_since(user, sod_ts_ms)
        except Exception as e:
            logger.warning(f"[heartbeat] contribution fetch failed: {e}")

    memory.track_daily_pnl(equity, contributions)
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
            rehydrate_from_exchange(positions,
                                    default_leverage=int(config.get("leverage", 1) or 1),
                                    queried_dexes=queried_dexes)
            # include_hip3=True so xyz:MU / vntl:* etc. get fresh mids each
            # cycle — without them, monitor_exits has no price for HIP-3
            # trackers and their peak/floor never advance (dashboard shows
            # "no DSL" indefinitely and DSL stop never fires on HIP-3).
            mids = get_all_hl_mids(include_hip3=True)
            exits = monitor_exits(mids)
            for ex in exits:
                coin = ex["coin"]
                lev = ex.get("leverage", 1)
                lpct = ex.get("leveraged_pct", ex["unrealized_pct"] * lev)
                logger.info(f"[dsl] Closing {coin} {ex.get('side','?')} ({lev}x): "
                            f"{ex['reason']} (margin {lpct:+.2f}% · spot {ex['unrealized_pct']:+.2f}%)")
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
        # Blocklisted coins can never execute (coin_filter gate blocks them), so
        # we skip the paid LLM research for any we don't hold — see the else
        # branch below. Held blocklisted coins are exempt (AI must keep the
        # ability to CLOSE). Read once per scan from the hot-reloaded config.
        _blocklist = set(_cfg_cd.get("coin_blocklist", []) or [])
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
                    remaining_min = int((held_research_ms - (now_ms - last_research)) / 60_000)
                    logger.info(f"{coin}: held — next AI close-check in {remaining_min}min — skip")
                    log_event({"event": "ta_skip", "coin": coin,
                               "signal": "HELD_THROTTLE",
                               "score": round(float(score), 1),
                               "trigger_score": round(float(score), 1)})
                    continue
            else:
                # Blocklisted + not held → coin_filter will reject any entry, so
                # skip the paid LLM research entirely (this coin keeps triggering
                # every scan otherwise). Held blocklisted coins took the held
                # branch above and still get their AI close-check.
                if coin in _blocklist:
                    logger.info(f"{coin}: on coin blocklist — skip research")
                    log_event({"event": "ta_skip", "coin": coin,
                               "signal": "BLOCKLISTED",
                               "score": round(float(score), 1),
                               "trigger_score": round(float(score), 1)})
                    continue
                # Not held but executed within cooldown_min → re-entry would be
                # gate-blocked, so skip the paid AI call.
                last_ms = recent_trades_by_coin.get(coin)
                if last_ms and (now_ms - last_ms) < cooldown_ms:
                    remaining_min = int((cooldown_ms - (now_ms - last_ms)) / 60_000)
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
                    remaining_min = int((research_cooldown_ms - (now_ms - last_research)) / 60_000)
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
