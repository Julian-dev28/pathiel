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
        "shadow_mode": True,
        "min_candidate_composite": 40.0,
        "min_hold_minutes": 30,
        "protect_winner_roe_pct": 3.0,
    },
    "gex_signal": {
        "enabled": True,
        "shadow_mode": False,
        "caution_near_wall_pct": 15.0,
    },
    "shadow_signals": {
        "enabled": True,
        "gex": True,
        "short_volume": True,
        "crypto_whale": True,
        "news": True,
        "whale_window_min": 15,
    },
    "signal_enforcement": {
        "enabled": True,
        "veto": True,
        "boost": True,
        "gex_veto": True,
        "boost_bar_delta": 4,
        "whale_window_min": 15,
        "whale_veto_min_usd": 250_000,
        "whale_boost_min_usd": 250_000,
    },
    "runner_mover_surface": {
        "enabled": True,
        "min_crypto_24h_pct": 10.0,
        "min_hip3_24h_pct": 8.0,
        "min_volume_usd": 5_000_000,
    },
    # Cross-sectional momentum rebalancer (validated +EV edge, ALPHA-PLAN.md). Market-neutral:
    # long top-K / short bottom-K by trailing return, rebalanced every hold_days. shadow_mode=True
    # logs the target book WITHOUT placing orders — forward-validate before going live.
    "xs_momentum": {
        "enabled": True,
        "shadow_mode": True,
        "lookback_days": 7,
        "hold_days": 10,
        "k_per_leg": 8,
        "universe_top_n": 50,
        "min_volume_usd": 5_000_000,
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
