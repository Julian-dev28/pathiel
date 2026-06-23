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
        # audit-driven upgrades: rank on the BTC-neutral RESIDUAL (stronger + smoother), and GATE
        # on BTC vol (momentum lives in low-vol; go flat in high-vol = the dead regime).
        "residual": True,
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
    # Vol-dispersion edge (W1 — long high-idio-vol / short low, BETA-NEUTRAL via within-β-tercile).
    # Pure engine: hermes_trader/agents/vol_dispersion.py
    # Live wiring: hermes_trader/agents/vol_dispersion_live.py (shadow_mode=true default).
    # Research agent still tuning best idio_vol_window — parameterized, not hardcoded.
    "vol_dispersion": {
        "enabled": False,        # master gate — loop hook is a no-op while False
        "shadow_mode": True,     # always log before risking capital
        "idio_vol_window": 30,   # trailing days for per-coin idiosyncratic vol (BTC-residual stdev)
        "k_per_tercile": 3,      # names per leg per beta-tercile (3 terciles × 3 = 9-name legs)
        "hold_days": 10,         # rebalance interval
        "universe_top_n": 50,    # liquid-universe cap
        "min_volume_usd": 5_000_000,
    },

    # ── NEW EDGES (wired here, all OFF + shadow by default) ──────────────────────

    # Sortino factor (V2 — +3.66%/rebal, beta-neutral, regime-stable).
    # Scores coins by mean(daily return)/downside-deviation over `window` days within each BTC-beta
    # tercile. More regime-stable than idio-vol (holds in down-regime: +2.24%). Corr +0.07 to
    # momentum / +0.37 to vol-dispersion → partially orthogonal.
    # Pure engine: vol_dispersion.py with score_fn="sortino"
    # Live wiring: hermes_trader/agents/sortino_live.py (shadow_mode=True default).
    # Small k=1-2 for a ~$60 main-perp account (6 total names per side at k=2).
    "sortino_factor": {
        "enabled": False,        # master gate — loop hook is a no-op while False
        "shadow_mode": True,     # log before risking capital
        "window": 60,            # trailing days for Sortino scoring (validated ~60d)
        "k_per_tercile": 2,      # names per leg per beta-tercile (k=2 → 6-name legs)
        "hold_days": 10,         # rebalance interval (matches vol_dispersion)
        "universe_top_n": 50,
        "min_volume_usd": 5_000_000,
    },

    # Amihud illiquidity factor (W6 — BORDERLINE: +2.33%/rebal but lumpy, 2/4 quarters negative).
    # Scores coins by mean(|daily ret|/daily $volume) over `window` days. LONG illiquid/SHORT liquid
    # within each BTC-beta tercile. Use minimal k=1 and validate forward before increasing.
    # Pure engine: vol_dispersion.py with score_fn="amihud"
    # Live wiring: hermes_trader/agents/amihud_live.py (shadow_mode=True default).
    "amihud_factor": {
        "enabled": False,        # BORDERLINE — keep off until multi-quarter live validation
        "shadow_mode": True,
        "window": 30,            # trailing days for Amihud ratio (validated ~30d)
        "k_per_tercile": 1,      # MINIMAL — lumpy edge, small allocation only
        "hold_days": 10,
        "universe_top_n": 50,
        "min_volume_usd": 5_000_000,
    },

    # Kurtosis factor (V2 — +1.71%/rebal, beta-neutral, within-β-tercile, HIGH kurtosis = LONG).
    # Scores coins by excess kurtosis of daily returns over ~60 days. Fat-tailed coins (high
    # kurtosis) are longed; thin-tailed coins (low kurtosis) are shorted — within each BTC-beta
    # tercile. MODEST edge — bleeds in sustained down-regimes. Use k=1 + shadow first.
    # Pure engine: vol_dispersion.py with score_fn="kurtosis"
    # Live wiring: hermes_trader/agents/kurtosis_live.py (shadow_mode=True default).
    "kurtosis_factor": {
        "enabled": False,        # MODEST edge — keep off until forward validation complete
        "shadow_mode": True,     # log before risking capital; bleeds in down-regime
        "window": 60,            # trailing days for excess-kurtosis scoring (~60d validated V2)
        "k_per_tercile": 1,      # minimal — modest edge, small allocation only
        "hold_days": 10,         # rebalance interval (matches other factor rebalancers)
        "universe_top_n": 50,
        "min_volume_usd": 5_000_000,
    },

    # Pairs stat-arb (validated +1.08%/trade, V4: entry_z=2.5, exit_z=0.5, corr>0.6).
    # Market-neutral mean-reversion of log-spread between co-moving coins.
    # ORTHOGONAL to momentum (profits from reversion) → stacking diversifies.
    # Pure engine: hermes_trader/agents/pairs_engine.py
    # Live wiring: hermes_trader/agents/pairs_live.py (shadow_mode=True default).
    # For a ~$60 main-perp account: max_open_pairs=2 (each pair = 2 legs = 4 positions).
    "pairs_statarb": {
        "enabled": False,        # master gate
        "shadow_mode": True,
        "entry_z": 2.5,          # validated V4 (up from 2.0 → +1.98%/trade at 2.5)
        "exit_z": 0.5,           # reversion exit threshold (validated 0.5)
        "min_corr": 0.6,         # min trailing return correlation to form a pair
        "window": 30,            # trailing window days for z-score and correlation
        "scan_interval_hours": 6, # re-scan every 6h (pairs change slowly on daily bars)
        "max_open_pairs": 2,     # SMALL account: cap at 2 pairs (4 legs total)
        "universe_top_n": 40,    # fewer coins → fewer but higher-quality pairs
        "min_volume_usd": 5_000_000,
    },

    # Correlation-regime sizing gate (V3 — validated by edge_regime_timing.py).
    # Scales momentum exposure UP in low-corr periods (more cross-sectional dispersion)
    # and vol-dispersion exposure UP in high-corr periods.
    # Validated: momentum Sharpe 4.95→8.36 (low-corr) / vol-disp 9.06→13.27 (high-corr).
    # Pure engine: hermes_trader/agents/corr_gate.py
    # Wired into xs_momentum_live.py and vol_dispersion_live.py (reads this config key).
    "correlation_gate": {
        "enabled": False,        # master gate — no scaling while disabled (scalar=1.0)
        "window": 14,            # rolling pairwise-correlation window (validated 14d)
        "low_corr_scalar": 1.2,  # multiply momentum exposure in low-corr regime
        "high_corr_scalar": 1.2, # multiply vol-dispersion exposure in high-corr regime
        "cap": 1.5,              # maximum scalar (never more than 1.5x from this gate alone)
    },

    # Day-of-week tilt (calendar edge; MULTIPLE-TESTING CAVEAT — treat as tilt not standalone).
    # Monday +0.78% (OOS robust) → long-bias (scalar >1); Thursday −1.64% → reduce (scalar <1).
    # Orthogonal to momentum + pairs (different mechanism). Apply as a small sizing scalar overlay.
    # Pure engine: hermes_trader/agents/day_of_week_tilt.py
    "day_of_week_tilt": {
        "enabled": False,
        "monday_scalar": 1.15,   # increase size 15% on Monday (positive bias)
        "thursday_scalar": 0.75, # reduce size 25% on Thursday (negative bias)
    },

    # Extreme-fade overlay (MARGINAL: +0.23–0.59% per trade net of 10bps).
    # After |daily return| > threshold_pct, fade it the next day (short big-up, long big-down).
    # Not a primary edge — small overlay. Runs inside scanner loop (not a rebalancer).
    # Pure engine: hermes_trader/agents/extreme_fade.py
    "extreme_fade": {
        "enabled": False,
        "shadow_mode": True,
        "threshold_pct": 12.0,   # validated range 12–18%; 12% has most trades, 18% highest EV
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
