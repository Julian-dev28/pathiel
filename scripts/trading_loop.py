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
from hermes_trader.agents.executor import maybe_execute
from hermes_trader.agents.config import get_config
from hermes_trader.agents.memory import memory
from hermes_trader.client.universe import get_universe
from hermes_trader.client.hl_client import fetch_account_state, resolve_user_address
from hermes_trader.session_log import append as log_event

logger = logging.getLogger(__name__)

logger.info("=== HERMES TRADER - Starting Continuous Trading Loop ===")
logger.info(f"Mode: LIVE  env={_args.env}  daemon={_args.daemon}")

config = get_config()
universe = get_universe()
memory.load()  # hydrate from .agent-memory.json so cache + flush work.

# Scan cadence: env-overridable, default 60s. Keep it above the candle cache
# TTL (config.scan.cacheTtlMs) so every scan reads a fresh candle snapshot.
scan_interval = int(os.environ.get('HERMES_SCAN_INTERVAL', '60'))
min_score = config['scan']['minCompositeScore']

logger.info(f"Scan interval: {scan_interval}s, Min score: {min_score}")
log_event({"event": "loop_start", "scan_interval": scan_interval, "min_score": min_score})


def _burst_fired(perception):
    """True if the perception's momentumBurst trigger fired (a large fast move)."""
    return any(t.get("name") == "momentumBurst" and t.get("fired")
               for t in perception.get("triggers", []))


def _sync_account_state():
    """Pull live equity + positions from Hyperliquid, persist to .agent-memory.json.

    Runs once per cycle so `status.py` and the cron report always show fresh
    data — previously the on-disk memory only updated on a successful execute,
    so equity stayed at 0 between trades.

    Returns (equity, positions, available, spot_usdc) — all 0/[] if wallet not
    configured or the HL call raised, so a network blip never kills the loop.
    """
    user = resolve_user_address()
    if not user:
        return 0.0, [], 0.0, 0.0
    try:
        state = fetch_account_state(user)
    except Exception as e:
        logger.warning(f"[heartbeat] HL fetch_account_state failed: {e}")
        return 0.0, [], 0.0, 0.0

    equity = float(state.get("equity", 0) or 0)
    available = float(state.get("available", 0) or 0)
    spot_usdc = float(state.get("spot_usdc", 0) or 0)
    positions = state.get("asset_positions", []) or []

    memory.track_daily_pnl(equity)              # also sets _equity
    memory.update_open_positions(positions)
    memory.flush()                               # persist to disk
    return equity, positions, available, spot_usdc


while True:
    try:
        # ── Heartbeat: refresh equity / positions before scanning ──────────
        equity, positions, available, spot_usdc = _sync_account_state()
        daily_pnl = memory.get_daily_pnl()
        if equity <= 0 and spot_usdc > 0:
            logger.warning(
                f"[heartbeat] perp equity $0 but ${spot_usdc:.2f} USDC idle in "
                f"spot — transfer spot->perp to enable trading.")
        log_event({
            "event": "loop_heartbeat",
            "equity": round(equity, 4),
            "available": round(available, 4),
            "spot_usdc": round(spot_usdc, 4),
            "daily_pnl": round(daily_pnl, 4),
            "open_positions": len(positions),
        })

        logger.info("Scanning markets...")
        results = scan_once(universe=universe, min_score=min_score, config=config)
        logger.info(f"Scan found {len(results)} triggers")
        # Per-cycle heartbeat — proof of life even when nothing triggers.
        log_event({"event": "scan", "triggers": len(results),
                   "coins": [p['coin'] for p in results]})

        for perception in results:
            coin = perception['coin']
            score = perception.get('composite_score', 0)

            # TA filter — cheap statistical gate before the paid AI call.
            ta = analyze_perception(perception)
            if ta['signal'] != 'CONFIRMED' and not _burst_fired(perception):
                logger.info(f"{coin}: TA {ta['signal']} (score {ta['score']:.0f}) — skip AI research")
                log_event({"event": "ta_skip", "coin": coin, "signal": ta['signal']})
                continue
            gate = 'CONFIRMED' if ta['signal'] == 'CONFIRMED' else f"{ta['signal']}+burst"
            logger.info(f"Researching {coin} (trigger {score:.1f}, TA {gate})...")

            try:
                analysis = research(coin, perception)
                logger.info(f"Verdict: {analysis['verdict']}, Confidence: {analysis['confidence']}")
                log_event({"event": "research", "coin": coin,
                           "verdict": analysis['verdict'],
                           "confidence": round(float(analysis['confidence']), 2)})

                if analysis['verdict'] in ('LONG', 'SHORT'):
                    logger.info(f"Executing {analysis['side']} trade...")
                    result = maybe_execute(analysis)
                    logger.info(f"Trade result: {result}")
                    log_event({"event": "execute", "coin": coin,
                               "side": analysis['side'],
                               "executed": bool(result.get("executed")),
                               "detail": result.get("order_id")
                               or result.get("reason")
                               or result.get("blocked_by")})
            except Exception as e:
                logger.error(f"Error processing {coin}: {e}")
                log_event({"event": "error", "coin": coin, "error": str(e)})

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
