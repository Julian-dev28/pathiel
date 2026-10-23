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
import threading
from typing import Any, Dict, List, Optional

from pydantic import ValidationError

from contracts.risk.models import RiskProfile
from pathiel.agents import universe as _universe

logger = logging.getLogger(__name__)

# Use absolute path based on this file's location (pathiel project root)
# __file__ = .../pathiel/pathiel/agents/config_store.py
# Go up 3 levels: agents/ → pathiel/ → pathiel/
# Override with PATHIEL_AGENT_CONFIG_FILE when deploying behind a mounted volume.
_CONFIG_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_PATH = os.environ.get(
    "PATHIEL_AGENT_CONFIG_FILE",
    os.path.join(_CONFIG_DIR, ".agent-config.json"),
)

DEFAULT_CONFIG: Dict[str, Any] = {
    "mode": "OFF",
    # Config v2 (docs/overhaul/PROFIT_AUDIT.md section 1 / services/risk/):
    # DELIBERATELY ABSENT here. "risk_profile" is written only by an explicit
    # PUT /api/risk/profile or a hand edit — read_risk_profile() treats its
    # ABSENCE as "migrate the legacy keys instead" (services.risk.migrate).
    # merge_agent_config layers `updates` onto THIS dict, so if a template
    # value lived here, every config read through read_agent_config() would
    # carry a syntactically-valid "risk_profile" forever, even when the file
    # on disk never set one — permanently defeating migration for every
    # config that predates config v2, which is every real one today. See
    # read_risk_profile's docstring for the precedence this preserves.
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
    # 2026-08-29: restricted to majors. Bare tickers — the HIP-3 dex prefix is
    # stripped when matching, so "GOLD" covers xyz:GOLD and km:GOLD. Empty list
    # restores the unrestricted behaviour. See pathiel/agents/universe.py
    # for why this is a capacity decision and not an edge claim.
    "coin_allowlist": list(_universe.MAJORS),
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
    "runner_mover_surface": {
        "enabled": True,
        "min_crypto_24h_pct": 10.0,
        "min_hip3_24h_pct": 8.0,
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


_UPDATE_LOCK = threading.Lock()


def read_agent_config_file() -> Dict[str, Any]:
    """The config file exactly as saved, WITHOUT the built-in defaults merged in.

    Writers must start from this, not read_agent_config(): writing the merged
    view back copies every DEFAULT_CONFIG value into the file, so a one-field
    change silently pins dozens of defaults (2026-10-23: a risk_profile write
    materialized per-book notional/leverage/stop into four books).

    Missing file -> {}. Unreadable file -> raises ValueError, because writing
    {} + updates over a corrupt file would erase every operator setting.
    """
    try:
        with open(CONFIG_PATH, "r") as f:
            cfg = json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as e:
        raise ValueError(f"refusing to update: invalid JSON in {CONFIG_PATH}: {e}") from e
    if not isinstance(cfg, dict):
        raise ValueError(f"refusing to update: {CONFIG_PATH} is not a JSON object")
    return cfg


def update_agent_config(updates: Dict[str, Any]) -> Dict[str, Any]:
    """Deep-merge `updates` into the saved file and write it. Returns the
    effective config (defaults merged) after the write. Serialized by a lock so
    two concurrent updates cannot drop each other's change."""
    with _UPDATE_LOCK:
        write_agent_config(merge_agent_config(read_agent_config_file(), updates))
    return read_agent_config()


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


# Old scattered sizing keys that migrate_legacy() turns into a profile.
LEGACY_SIZING_KEYS = ("equity_fraction_per_trade", "leverage", "min_order_bump_max_mult",
                      "max_concurrent", "max_daily_loss_pct", "max_daily_loss_usd",
                      "min_tradable_equity_usd", "default_stop_pct")
_MISSING = object()


def _has_legacy_sizing(cfg: Dict[str, Any]) -> bool:
    """True when the config carries a sizing key the USER set: present and
    different from the built-in default. read_agent_config() merges defaults
    in, so presence alone cannot tell a user's choice from a default."""
    for k in LEGACY_SIZING_KEYS:
        if k in cfg and cfg[k] != DEFAULT_CONFIG.get(k, _MISSING):
            return True
    return False


def read_risk_profile(config: Optional[Dict[str, Any]] = None) -> RiskProfile:
    """The resolved config-v2 RiskProfile for `config` (reads the live file
    via read_agent_config() when `config` is omitted).

    PRECEDENCE (see services/risk/migrate.py's module docstring for the full
    rationale):
      1. a valid "risk_profile" key in the config — used as-is.
      2. services.risk.migrate.migrate_legacy(config) — a best-effort
         profile built from the old scattered keys, when (1) is absent or
         fails to validate.
      3. RiskProfile() (preset="auto") — migrate_legacy's own fallback when
         the config carries none of the old keys either.

    Never raises: a malformed "risk_profile" value (e.g. a hand-edit that
    breaks the schema) degrades to migration rather than taking the
    executor down — the same fail-safe rule read_agent_config() already
    follows for the config file itself.
    """
    cfg = config if config is not None else read_agent_config()
    raw = cfg.get("risk_profile")
    if not isinstance(raw, dict) and not _has_legacy_sizing(cfg):
        # A fresh install has no old settings: the auto preset, not a
        # "custom" profile reverse-engineered from built-in defaults (live
        # e2e 2026-10-17: a brand-new user saw "Custom (from your old
        # settings)" with a 25% bump cap).
        return RiskProfile()
    if isinstance(raw, dict):
        try:
            return RiskProfile.model_validate(raw)
        except ValidationError as e:
            logger.warning(f"[config] risk_profile failed to validate ({e}); "
                           "migrating legacy keys instead")
    from services.risk.migrate import migrate_legacy
    profile, _report = migrate_legacy(cfg)
    return profile


def migration_report(config: Optional[Dict[str, Any]] = None) -> List[str]:
    """The plain-English mapping services.risk.migrate.migrate_legacy would
    make for this config, regardless of whether a "risk_profile" key is
    already present. Lets an API/CLI show "here is what migrating would
    do" even after a v2 profile has already been saved, without that
    display path silently becoming the one read_risk_profile() uses."""
    from services.risk.migrate import migrate_legacy
    _profile, report = migrate_legacy(config if config is not None else read_agent_config())
    return report


def write_risk_profile(profile: RiskProfile) -> None:
    """Persist `profile` into the agent config's "risk_profile" key,
    atomically (write_agent_config does the fsync + os.replace).

    Never called implicitly — only an explicit PUT /api/risk/profile
    (services/risk/api.py) or a deliberate script calls this. Writes
    whatever CONFIG_PATH / PATHIEL_AGENT_CONFIG_FILE currently points at;
    tests and dev servers must export that to a temp dir first (see
    docs/overhaul/SWARM_RULES.md section 1) — this function has no opinion
    about live vs. temp, same as write_agent_config.
    """
    # Replace the whole key (not deep-merge): a field set back to None must clear.
    with _UPDATE_LOCK:
        cfg = dict(read_agent_config_file())
        cfg["risk_profile"] = profile.model_dump()
        write_agent_config(cfg)


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
