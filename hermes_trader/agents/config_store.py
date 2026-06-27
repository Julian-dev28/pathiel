"""Read/write the agent config at .agent-config.json.

The checked-in config is explicit, but operator/API updates can be partial.
Normalize partial objects onto the tuned defaults here so missing keys do not
fall through to older scattered `.get(..., default)` values in execution code.
Missing or invalid files still fail safe with `mode: OFF`.
"""

from __future__ import annotations

import copy
import json
import logging
import os
from typing import Any, Dict

logger = logging.getLogger(__name__)

# Use absolute path based on this file's location (hermes-trader project root)
# __file__ = .../hermes-trader/hermes_trader/agents/config_store.py
# Go up 3 levels: agents/ → hermes_trader/ → hermes-trader/
# Override with HERMES_AGENT_CONFIG_FILE when deploying behind a mounted volume.
_CONFIG_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_PATH = os.environ.get(
    "HERMES_AGENT_CONFIG_FILE",
    os.path.join(_CONFIG_DIR, ".agent-config.json"),
)

DEFAULT_CONFIG: Dict[str, Any] = {
    "mode": "OFF",
    "ai_brain": {
        "provider": "openrouter",
        "timeout_s": 120,
        "claude_cli": {
            "command": "claude",
            "max_turns": 1,
        },
        "codex_cli": {
            "command": "codex",
        },
    },
    "enable_crypto": True,
    "enable_hip3": True,
    "equity_fraction_per_trade": 0.2,
    "leverage": 12,
    "max_trade_notional_usd": 350,
    "asset_notional_multiplier": {
        "crypto": 1.0,
        "hip3": 1.0,
    },
    "tp_scale_fraction": 0.5,
    "max_concurrent": 10,
    "max_total_notional_pct": 10.0,
    "max_daily_loss_usd": -100,
    "daily_giveback_halt_pct": 0.35,
    "daily_giveback_min_peak_usd": 25.0,
    "crowded_with_min_conf": 0.8,
    "min_available_margin_pct": 0.1,
    "cooldown_min": 30,
    "held_research_interval_min": 10,
    "min_ai_confidence": 0.7,
    "counter_regime_min_conf": 0.8,
    "max_crypto_long_correlated": 3,
    "min_market_volume_usd": 5_000_000,
    "min_hip3_volume_usd": 5_000_000,
    "min_short_volume_usd": 50_000_000,
    "coin_allowlist": [],
    "coin_blocklist": ["TON", "TRX"],
    "hip3_dex_allowlist": ["xyz"],
    "hip3_dex_blocklist": [],
    "dsl_exit": {
        "max_loss_pct": 0.4,
        "max_loss_roe_pct": 3.0,
        "protect_pct": 1.25,
        "retrace_threshold": 0.2,
        "hard_timeout_minutes": 1800.0,
        "breakeven_trigger_pct": 0.0,
        "breakeven_lock_pct": 0.0,
        "phase2_tiers": [
            {"pct_above_entry": 8.0, "retrace_threshold": 0.35},
            {"pct_above_entry": 15.0, "retrace_threshold": 0.4},
        ],
        "stale_flat_timeout_minutes": 480,
    },
    "ta_sidestep_force_execute": True,
    "override_max_daily_extension_pct": 30.0,
    "block_counter_trend_bypass": True,
    "trend_surface_enabled": True,
    "runner_entry_gate": {
        "enabled": True,
        "allow_shorts": False,
        "require_daily_mover_longs": False,
        "min_confidence": 0.7,
        "min_composite": 30.0,
        "min_crypto_composite": 20.0,
        "min_hip3_composite": 45.0,
        "min_short_confidence": 0.72,
        "min_short_composite": 25.0,
        "mover_min_confidence": 0.72,
        "mover_min_composite": 40.0,
    },
    "loss_cooldown_min": 180,
    "min_ai_close_hold_min": 25,
    "sl_atr_mult": 1.5,
    "backup_sl_max_frac_of_liq": 0.6,
    "atr_risk_sizing": {
        "enabled": True,
        "risk_per_trade_pct": 0.02,
        "sizing_basis": "primary_stop",
    },
    "capital_rotation": {
        "enabled": True,
        "min_candidate_composite": 40.0,
        "min_hold_minutes": 30,
        "protect_winner_roe_pct": 3.0,
    },
    "gex_signal": {
        "enabled": True,
        "caution_near_wall_pct": 15.0,
    },
    "runner_mover_surface": {
        "enabled": True,
        "min_crypto_24h_pct": 10.0,
        "min_hip3_24h_pct": 8.0,
        "min_volume_usd": 5_000_000,
    },
    # Strategy-book (rebalancer) position sizing.
    # strategy_book_equity_frac: fraction of the FUNDING account's equity × leverage used to size
    # each rebalancer trade. Default 0.1 → notional = size_equity × 0.1 × leverage (e.g. $60 main,
    # 12x lev → $72/trade). This replaces the old flat $15 cap with a properly equity-scaled size
    # while keeping the same safety gates (margin floor, max_total_notional_pct, per-name cap).
    # strategy_book_notional_usd: optional ABSOLUTE ceiling in USD (0 = inactive). When >0 the
    # equity-fraction result is min()'d with this cap so the operator can hard-bound per-trade size.
    # NOTE: if strategy_book_equity_frac=0 (disabled), the old fallback to strategy_book_notional_usd
    # is used directly (backward-compatible); set notional_usd=0 too for a full no-op until enabled.
    "strategy_book_equity_frac": 0.1,
    "strategy_book_notional_usd": 0,

    # Cross-sectional momentum rebalancer (validated +EV edge). Market-neutral:
    # long top-K / short bottom-K by trailing return, rebalanced every hold_days.
    "xs_momentum": {
        "enabled": True,
        "lookback_days": 7,
        "hold_days": 10,
        "k_per_leg": 8,
        "universe_top_n": 50,
        "min_volume_usd": 5_000_000,
        # audit-driven upgrades: rank on the BTC-neutral RESIDUAL (stronger + smoother), and GATE
        # on BTC vol (momentum lives in low-vol; go flat in high-vol = the dead regime).
        "residual": True,
        "ranking": "pct_k",       # Codex audit: cleaner robust expression than z_ext; do not stack both
        "zext_window": 14,        # shared channel window for pct_k / z_ext rankers
        "beta_window": 30,
        "vol_gate": True,
        "vol_short": 14,
        "vol_long": 90,
        # Vol-managed sizing (W6, Moreira-Muir): scale exposure by target_vol/realized_vol,
        # clamped to [0.3, cap]. Realized vol = pstdev of last ~20 rebalance-period returns,
        # persisted to .xs_volmgd_history. OFF by default — enable after history accumulates.
        "vol_managed": {
            "enabled": False,
            "target_vol": 0.02,   # per-period return vol target (rebalance-period units)
            "cap": 2.0,           # max exposure scalar (2x = never more than double notional)
        },
    },
    # Extreme-fade overlay: validated long-after-crash only.
    # Rally-exhaustion shorts use their own gated module and config.
    # After a completed daily crash, fade it near the next daily open.
    # Not a primary edge — small overlay. Runs inside scanner loop (not a rebalancer).
    # Pure engine: hermes_trader/agents/extreme_fade.py
    "extreme_fade": {
        "enabled": False,
        "crash_pct": -0.12,      # negative threshold for long leg (e.g. -0.12 = prior day ≤ -12%)
        "scan_interval_min": 30,
    },
    # Codex-discovered rally-exhaustion short. Live wiring uses tiny notional,
    # low leverage, a wide strategy-specific stop, held/claim/dedup preflight,
    # and still routes through maybe_execute for margin/liquidity/news/concurrency/order gates.
    "rally_exhaustion": {
        "enabled": False,
        "scan_interval_hours": 6,
        "entry_window_hours": 8,
        "lookback_days": 2,
        "threshold_pct": 12.0,
        "btc_window": 20,
        "min_volume_usd": 20_000_000,
        "executor_short_volume_floor_usd": 20_000_000,
        "volume_window": 30,
        "hold_days": 5,
        "stop_pct": 25.0,
        "notional_usd": 20.0,
        "leverage": 1,
        "tp_scale_fraction": 0.0,
        "max_new_per_cycle": 1,
        "history_bars": 40,
    },
    # AI/semis HIP-3 short basket. This is watchlist-driven but trigger-gated:
    # basket breadth and proxy trend must be bearish before any fresh daily
    # breakdown can trade. Keep shadow_only=True until backtest/forward logs are
    # strong enough to promote.
    "hail_mary_short": {
        "enabled": False,
        "shadow_only": True,
        "names": [
            "NVDA", "SMCI", "AVGO", "AMD", "TSM", "ASML", "ARM", "MSFT", "AMZN", "GOOGL",
            "META", "PLTR", "CRM", "ADBE", "NOW", "WDAY", "PATH", "AI", "SOUN", "UPST",
            "TSLA", "VRT", "MU", "CRWD", "SNOW", "DDOG", "HUBS", "ZS", "NET", "ARKK",
            "SOXX", "SMH", "OPENAI", "ANTHROPIC",
        ],
        "dex_allowlist": ["xyz", "vntl"],
        "proxy_coins": ["xyz:SMH", "xyz:SP500", "xyz:XYZ100"],
        "require_proxy_down": True,
        "scan_interval_hours": 6,
        "entry_window_hours": 10,
        "min_volume_usd": 20_000_000,
        "executor_short_volume_floor_usd": 20_000_000,
        "min_breadth_bearish_pct": 0.55,
        "breakdown_lookback_days": 20,
        "breakdown_buffer_pct": 0.0,
        "ema_fast": 8,
        "ema_slow": 21,
        "ema_trend": 50,
        "min_history_bars": 24,
        "history_bars": 90,
        "drawdown_lookback_days": 20,
        "min_basket_drawdown_pct": 6.0,
        "recent_drop_days": 5,
        "min_recent_drop_pct": 6.0,
        "hold_days": 10,
        "stop_pct": 12.0,
        "notional_usd": 20.0,
        "leverage": 1,
        "tp_scale_fraction": 0.0,
        "max_new_per_cycle": 1,
        "max_attempts_per_cycle": 1,
    },
}


def read_agent_config() -> Dict[str, Any]:
    """Read the agent config from .agent-config.json."""
    try:
        with open(CONFIG_PATH, "r") as f:
            cfg = json.load(f)
    except FileNotFoundError:
        return copy.deepcopy(DEFAULT_CONFIG)
    except json.JSONDecodeError as e:
        logger.error(f"[config] invalid JSON in {CONFIG_PATH}: {e}; using tuned OFF defaults")
        return copy.deepcopy(DEFAULT_CONFIG)
    if not isinstance(cfg, dict):
        logger.error(f"[config] expected object in {CONFIG_PATH}, got {type(cfg).__name__}; using tuned OFF defaults")
        return copy.deepcopy(DEFAULT_CONFIG)
    return merge_agent_config(DEFAULT_CONFIG, cfg)


def write_agent_config(cfg: Dict[str, Any]) -> None:
    """Write the agent config to .agent-config.json (atomic replace)."""
    directory = os.path.dirname(CONFIG_PATH)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, CONFIG_PATH)
    logger.info(f"[config] written {len(cfg)} keys to {CONFIG_PATH}")


def merge_agent_config(base: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
    """Deep-merge config dictionaries without mutating either input.

    Partial API updates frequently target nested blocks like `dsl_exit` or
    `runner_entry_gate`. A shallow merge would replace the whole nested block
    and silently drop safety keys.
    """
    merged: Dict[str, Any] = copy.deepcopy(base)
    for key, val in updates.items():
        old = merged.get(key)
        if isinstance(old, dict):
            if isinstance(val, dict):
                merged[key] = merge_agent_config(old, val)
            else:
                logger.warning(
                    f"[config] ignoring non-object value for nested config '{key}'"
                )
        else:
            merged[key] = copy.deepcopy(val)
    return merged
