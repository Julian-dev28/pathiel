#!/usr/bin/env python3
"""Apply named config presets to .agent-config.json.

Presets encode opinionated defaults for different account sizes + styles.
See docs/CONFIG.md for the full key reference and what each knob does.

Usage:
    scripts/config_preset.py list
    scripts/config_preset.py show small_aggressive
    scripts/config_preset.py apply small_aggressive            # diff + confirm
    scripts/config_preset.py apply small_aggressive --yes      # skip confirm
    scripts/config_preset.py apply --account-size 250          # auto-pick by equity
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
CONFIG_FILE = _REPO / ".agent-config.json"


PRESETS: dict[str, dict] = {
    # ── Small accounts ($100-500) ──────────────────────────────────────────
    "small_aggressive": {
        "_doc": "$100-500. Max conviction, high leverage, tight daily loss cap. "
                 "For traders who want to be in every setup the bot finds.",
        "equity_fraction_per_trade": 0.10,
        "leverage": 40,
        "max_concurrent": 18,
        "max_trade_notional_usd": 100000,
        "max_total_notional_pct": 40.0,
        "max_daily_loss_usd": -30,
        "min_available_margin_pct": 0.10,
        "cooldown_min": 60,
        "min_ai_confidence": 0.30,
        "counter_regime_min_conf": 0.65,
        "max_crypto_long_correlated": 5,
        "min_market_volume_usd": 5000000,
        "min_hip3_volume_usd": 500000,
        "force_execute_composite": 40,
        "force_execute_slow_burn_count": 2,
        "conviction_sizing": True,
        "dsl_exit": {
            "max_loss_pct": 2.0,
            "max_loss_roe_pct": 30.0,
            "protect_pct": 0.5,
            "retrace_threshold": 0.30,
            "hard_timeout_minutes": 180.0,
        },
    },
    "small_conservative": {
        "_doc": "$100-500. Lower leverage, looser stops, longer holds. "
                 "For traders who want to learn the system without big day-to-day swings.",
        "equity_fraction_per_trade": 0.05,
        "leverage": 10,
        "max_concurrent": 8,
        "max_trade_notional_usd": 5000,
        "max_total_notional_pct": 5.0,
        "max_daily_loss_usd": -25,
        "min_available_margin_pct": 0.20,
        "cooldown_min": 120,
        "min_ai_confidence": 0.50,
        "counter_regime_min_conf": 0.75,
        "max_crypto_long_correlated": 2,
        "min_market_volume_usd": 10000000,
        "min_hip3_volume_usd": 1000000,
        "force_execute_composite": 999,  # disabled
        "force_execute_slow_burn_count": 99,
        "conviction_sizing": False,
        "dsl_exit": {
            "max_loss_pct": 3.0,
            "max_loss_roe_pct": 40.0,
            "protect_pct": 1.5,
            "retrace_threshold": 0.40,
            "hard_timeout_minutes": 360.0,
        },
    },

    # ── Medium accounts ($500-2000) ────────────────────────────────────────
    "medium_balanced": {
        "_doc": "$500-2000. Default-ish, balanced risk. Sane starting point if you don't know.",
        "equity_fraction_per_trade": 0.04,
        "leverage": 15,
        "max_concurrent": 12,
        "max_trade_notional_usd": 50000,
        "max_total_notional_pct": 15.0,
        "max_daily_loss_usd": -150,
        "min_available_margin_pct": 0.15,
        "cooldown_min": 60,
        "min_ai_confidence": 0.40,
        "counter_regime_min_conf": 0.70,
        "max_crypto_long_correlated": 3,
        "min_market_volume_usd": 5000000,
        "min_hip3_volume_usd": 500000,
        "force_execute_composite": 50,
        "force_execute_slow_burn_count": 2,
        "conviction_sizing": True,
        "dsl_exit": {
            "max_loss_pct": 2.5,
            "max_loss_roe_pct": 35.0,
            "protect_pct": 1.0,
            "retrace_threshold": 0.30,
            "hard_timeout_minutes": 180.0,
        },
    },

    # ── Large accounts ($2000+) ────────────────────────────────────────────
    "large_steady": {
        "_doc": "$2000+. Low leverage, tight per-trade size, looser caps. "
                 "Compound steadily without blowing up on a bad day.",
        "equity_fraction_per_trade": 0.02,
        "leverage": 5,
        "max_concurrent": 15,
        "max_trade_notional_usd": 25000,
        "max_total_notional_pct": 8.0,
        "max_daily_loss_usd": -500,
        "min_available_margin_pct": 0.20,
        "cooldown_min": 90,
        "min_ai_confidence": 0.45,
        "counter_regime_min_conf": 0.70,
        "max_crypto_long_correlated": 3,
        "min_market_volume_usd": 10000000,
        "min_hip3_volume_usd": 1000000,
        "force_execute_composite": 999,  # disabled — let AI decide on bigger accounts
        "force_execute_slow_burn_count": 99,
        "conviction_sizing": True,
        "dsl_exit": {
            "max_loss_pct": 2.0,
            "max_loss_roe_pct": 50.0,
            "protect_pct": 1.5,
            "retrace_threshold": 0.30,
            "hard_timeout_minutes": 240.0,
        },
    },

    # ── Asset-class overlays (apply after a sizing preset) ─────────────────
    "hip3_only": {
        "_doc": "Disable native crypto entirely. Focuses budget on tokenized equity / commodity perps. "
                 "Apply AFTER a sizing preset — only changes the two enable_ flags.",
        "enable_crypto": False,
        "enable_hip3": True,
    },
    "crypto_only": {
        "_doc": "Disable HIP-3. Pure native HL perps. "
                 "Apply AFTER a sizing preset.",
        "enable_crypto": True,
        "enable_hip3": False,
    },
    "both_classes": {
        "_doc": "Scan both native crypto and HIP-3. Apply after a sizing preset.",
        "enable_crypto": True,
        "enable_hip3": True,
    },
}


LEGACY_RISK_PRESETS = {
    "small_aggressive",
    "small_conservative",
    "medium_balanced",
    "large_steady",
}


def _load() -> dict:
    return json.loads(CONFIG_FILE.read_text())


def _save(cfg: dict) -> None:
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2) + "\n")


def _strip_doc(p: dict) -> dict:
    return {k: v for k, v in p.items() if not k.startswith("_")}


def _auto_pick(equity: float) -> str:
    if equity < 500: return "small_aggressive"
    if equity < 2000: return "medium_balanced"
    return "large_steady"


def cmd_list() -> int:
    print("Available presets (see docs/CONFIG.md for full reference):\n")
    for name, p in PRESETS.items():
        doc = p.get("_doc", "")
        print(f"  {name}")
        print(f"    {doc}\n")
    return 0


def cmd_show(name: str) -> int:
    if name not in PRESETS:
        print(f"unknown preset: {name}\nrun `list` for available presets")
        return 2
    p = PRESETS[name]
    print(f"# {name}")
    print(f"# {p.get('_doc','')}\n")
    print(json.dumps(_strip_doc(p), indent=2))
    return 0


def cmd_apply(
    name: str | None,
    account_size: float | None,
    yes: bool,
    allow_legacy_risk_preset: bool,
) -> int:
    if name is None:
        if account_size is None:
            print("provide --account-size or a preset name")
            return 2
        name = _auto_pick(account_size)
        print(f"auto-picked preset for ${account_size:.0f}: {name}\n")

    if name not in PRESETS:
        print(f"unknown preset: {name}")
        return 2

    if name in LEGACY_RISK_PRESETS and not allow_legacy_risk_preset:
        print(
            f"refusing to apply legacy risk preset `{name}`.\n"
            "These presets predate the current PnL audit and would overwrite "
            "audited live-risk gates (leverage, confidence floors, force "
            "thresholds, and notional caps). Update the preset from fresh "
            "evidence first, or rerun with --allow-legacy-risk-preset if you "
            "intentionally want this unsafe override."
        )
        return 2

    cur = _load()
    new_values = _strip_doc(PRESETS[name])
    # Deep merge: dsl_exit nested values overlay, not replace
    merged = dict(cur)
    for k, v in new_values.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = {**merged[k], **v}
        else:
            merged[k] = v

    # Show diff
    print(f"# Diff: applying preset `{name}`\n")
    changed = []
    for k, v in new_values.items():
        old = cur.get(k, "<unset>")
        if isinstance(v, dict):
            for nk, nv in v.items():
                old_nv = (cur.get(k) or {}).get(nk, "<unset>")
                if old_nv != nv:
                    changed.append((f"{k}.{nk}", old_nv, nv))
        elif old != v:
            changed.append((k, old, v))

    if not changed:
        print("(no changes; current config already matches preset)")
        return 0

    width = max(len(c[0]) for c in changed)
    for key, old, new in changed:
        print(f"  {key:<{width}}  {old}  →  {new}")
    print(f"\n{len(changed)} change(s)")

    if not yes:
        resp = input("\napply? [y/N] ").strip().lower()
        if resp != "y":
            print("aborted.")
            return 0

    _save(merged)
    print(f"\n✓ applied to {CONFIG_FILE}")
    print("Most keys hot-reload on the next trade. Restart the loop only if you changed:")
    print("  enable_crypto, enable_hip3   (universe is fetched at startup)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    p_show = sub.add_parser("show")
    p_show.add_argument("name")
    p_apply = sub.add_parser("apply")
    p_apply.add_argument("name", nargs="?")
    p_apply.add_argument("--account-size", type=float, default=None,
                        help="auto-pick preset based on equity")
    p_apply.add_argument("--yes", "-y", action="store_true", help="skip confirm")
    p_apply.add_argument("--allow-legacy-risk-preset", action="store_true",
                         help="permit old account-size presets that overwrite live risk gates")
    args = ap.parse_args()

    if args.cmd == "list":
        return cmd_list()
    if args.cmd == "show":
        return cmd_show(args.name)
    if args.cmd == "apply":
        return cmd_apply(args.name, args.account_size, args.yes, args.allow_legacy_risk_preset)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
