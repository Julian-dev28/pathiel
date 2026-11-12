#!/usr/bin/env python3
"""Config presets, two generations.

PRIMARY (config v2, docs/overhaul/PROFIT_AUDIT.md / services/risk/): a
RiskProfile — preset name + a handful of overrides — DERIVES every number a
book needs (notional, max positions, daily loss limit) from current equity.
This is the one to reach for:

    scripts/config_preset.py derive --equity 15.19                 # this install's own config/profile
    scripts/config_preset.py derive --equity 15.19 --profile starter
    scripts/config_preset.py derive --equity 500 --profile small --json

`derive` NEVER writes `.agent-config.json` — read-only, prints the derived
numbers for a hypothetical equity/profile. To actually SAVE a RiskProfile,
use `PUT /api/risk/profile` (services/risk/api.py) or edit the "risk_profile"
key by hand.

LEGACY (pre-v2, kept for existing installs that haven't migrated): named
dollar-bucket presets that overwrite ~15 scattered keys at once. Guarded
behind --allow-legacy-risk-preset since 2026-09-07 — they predate the
profit audit and will overwrite audited live-risk gates. See
docs/CONFIG.md for the full key reference.

    scripts/config_preset.py list
    scripts/config_preset.py show small_aggressive
    scripts/config_preset.py apply small_aggressive            # diff + confirm
    scripts/config_preset.py apply small_aggressive --yes      # skip confirm
    scripts/config_preset.py apply --account-size 250          # auto-pick by equity

PUBLIC INSTALL (pathiel 0.4.0 plugin): seed a fresh config from the tracked
template config/public/agent-config.json (3x, 1% a trade, one position).
scripts/_plugin_env.sh runs it when the config file is missing:

    scripts/config_preset.py seed-public           # writes $PATHIEL_AGENT_CONFIG_FILE once
    scripts/config_preset.py seed-public --json

It never overwrites a file, never writes the repo's .agent-config.json, and
refuses a template whose risk_profile drifted from
services.risk.presets.PUBLIC_DEFAULT_PROFILE.

The config file the other subcommands use is $PATHIEL_AGENT_CONFIG_FILE when
set, else the default pathiel.agents.config_store uses: the repo file on the
operator's install, ~/.pathiel/.agent-config.json on a public one.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]

# Running this file directly (not `python -m`) puts scripts/ on sys.path[0],
# not the repo root — the `derive` subcommand needs `contracts`/`pathiel`/
# `services` importable (see scripts/profit_audit.py for the same fix).
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import contracts.runtime as _runtime  # noqa: E402  (stdlib only; needs the sys.path line above)

REPO_CONFIG_FILE = _REPO / ".agent-config.json"
PUBLIC_TEMPLATE = _REPO / "config" / "public" / "agent-config.json"


def _config_file() -> Path:
    explicit = os.environ.get("PATHIEL_AGENT_CONFIG_FILE")
    if explicit:
        return Path(explicit).expanduser()
    if _runtime.is_public():
        home = os.environ.get("PATHIEL_HOME") or os.path.join(os.path.expanduser("~"), ".pathiel")
        return Path(home) / ".agent-config.json"
    return REPO_CONFIG_FILE


CONFIG_FILE = _config_file()


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


def cmd_derive(equity: float, profile_name: str | None, as_json: bool) -> int:
    """Print DerivedLimits (services/risk/derive.py) for `equity`. READ-ONLY:
    never writes .agent-config.json. Uses this install's OWN book roster
    (services.risk.books.all_book_specs) so the numbers reflect what is
    actually configured, not a generic example.

    `profile_name` overrides the preset for a "what if I switched tiers"
    preview; omit it to use the config's own saved RiskProfile (or a
    migration of its legacy keys, when no "risk_profile" key exists yet —
    see pathiel.agents.config_store.read_risk_profile)."""
    from contracts.risk.models import RiskProfile
    from pathiel.agents.config_store import read_agent_config, read_risk_profile
    from pathiel.client.exchange import MIN_ORDER_USD
    from services.risk import books as risk_books
    from services.risk.derive import VenueRules, derive_limits

    cfg = read_agent_config()
    profile = RiskProfile(preset=profile_name) if profile_name else read_risk_profile(cfg)
    limits = derive_limits(equity, profile, risk_books.all_book_specs(cfg),
                           VenueRules(min_order_notional_usd=MIN_ORDER_USD))

    if as_json:
        print(limits.model_dump_json(indent=2))
        return 0

    print(f"# ${equity:,.2f} equity, {limits.preset_applied} preset\n")
    print(limits.headline)
    print(f"\nRisk per trade   ${limits.risk_per_trade_usd:,.2f}")
    print(f"Daily loss limit ${limits.daily_loss_limit_usd:,.2f}")
    print(f"Max positions    {limits.max_open_positions}")
    print(f"Min to trade     ${limits.min_equity_to_trade_usd:,.2f}\n")
    width = max((len(b.book) for b in limits.books), default=4)
    for b in limits.books:
        mark = "OK  " if b.can_trade else "--  "
        print(f"  {mark}{b.book:<{width}}  {b.reason}")
    for note in limits.notes:
        print(f"\n  note: {note}")
    return 0


def _seed_report(status: str, path: str, message: str, as_json: bool) -> None:
    if as_json:
        print(json.dumps({"status": status, "path": path, "message": message}))
    else:
        print(f"pathiel: config {status}: {message}" + (f" ({path})" if path else ""))


def _template_drift(template: object) -> str | None:
    """None when the template's risk_profile equals PUBLIC_DEFAULT_PROFILE
    field for field, else one sentence naming the difference."""
    from pydantic import ValidationError

    from contracts.risk.models import RiskProfile
    from services.risk.presets import PUBLIC_DEFAULT_PROFILE

    raw = template.get("risk_profile") if isinstance(template, dict) else None
    if not isinstance(raw, dict):
        return "the template has no risk_profile object"
    try:
        parsed = RiskProfile.model_validate(raw)
    except ValidationError as e:
        return f"the template's risk_profile does not validate: {e.errors()[0].get('msg')}"
    want = PUBLIC_DEFAULT_PROFILE.model_dump()
    if parsed.model_dump() != want or set(raw) != set(want):
        diff = sorted(k for k in set(raw) | set(want)
                      if raw.get(k, "<unset>") != want.get(k, "<unset>"))
        return ("the template's risk_profile differs from "
                f"services.risk.presets.PUBLIC_DEFAULT_PROFILE on {', '.join(diff) or 'its keys'}")
    return None


def cmd_seed_public(as_json: bool = False) -> int:
    """Copy config/public/agent-config.json to $PATHIEL_AGENT_CONFIG_FILE,
    mode 0600, only when that file does not exist.

    0  written, or kept (a file is there; it is never touched)
    2  refused, nothing written: the variable is unset, it resolves to the
       repo's .agent-config.json (the operator's live config), or the
       template drifted from PUBLIC_DEFAULT_PROFILE
    1  the write itself failed (permissions, disk)
    """
    raw_target = os.environ.get("PATHIEL_AGENT_CONFIG_FILE", "").strip()
    if not raw_target:
        _seed_report("refused", "", "PATHIEL_AGENT_CONFIG_FILE is not set; "
                     "seed-public writes only to an explicit target", as_json)
        return 2
    target = Path(raw_target).expanduser()
    if not target.is_absolute():
        target = Path.cwd() / target
    if os.path.realpath(target) == os.path.realpath(REPO_CONFIG_FILE):
        _seed_report("refused", str(target), "the target is the repo's .agent-config.json; "
                     "seed-public never writes it", as_json)
        return 2
    try:
        template_bytes = PUBLIC_TEMPLATE.read_bytes()
        template = json.loads(template_bytes)
    except (OSError, ValueError) as e:
        _seed_report("refused", str(target), f"cannot read {PUBLIC_TEMPLATE.name}: {e}", as_json)
        return 2
    drift = _template_drift(template)
    if drift:
        _seed_report("refused", str(target), "config/public/agent-config.json does not match "
                     f"services.risk.presets.PUBLIC_DEFAULT_PROFILE ({drift}); nothing written",
                     as_json)
        return 2
    if os.path.lexists(target):
        _seed_report("kept", str(target), "a config file already exists; left as it is", as_json)
        return 0
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        # O_EXCL: never replaces a file that appeared since the check above,
        # and never follows a symlink planted at the target.
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        _seed_report("kept", str(target), "a config file already exists; left as it is", as_json)
        return 0
    except OSError as e:
        _seed_report("failed", str(target), f"cannot create the file: {e}", as_json)
        return 1
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(template_bytes)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(target, 0o600)
    except OSError as e:
        _seed_report("failed", str(target), f"cannot write the file: {e}", as_json)
        return 1
    _seed_report("written", str(target),
                 "public default risk profile: 3x max leverage, 1% a trade, one position",
                 as_json)
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
    p_derive = sub.add_parser("derive", help="config v2: print derived limits for an equity/profile (read-only)")
    p_derive.add_argument("--equity", type=float, required=True)
    p_derive.add_argument("--profile", default=None,
                         choices=["auto", "starter", "small", "standard", "pro", "custom"],
                         help="override the preset; omit to use the config's own saved/migrated profile")
    p_derive.add_argument("--json", action="store_true", dest="as_json")
    p_seed = sub.add_parser("seed-public",
                            help="write the public default config to $PATHIEL_AGENT_CONFIG_FILE "
                                 "when that file does not exist")
    p_seed.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args()

    if args.cmd == "list":
        return cmd_list()
    if args.cmd == "show":
        return cmd_show(args.name)
    if args.cmd == "apply":
        return cmd_apply(args.name, args.account_size, args.yes, args.allow_legacy_risk_preset)
    if args.cmd == "derive":
        return cmd_derive(args.equity, args.profile, args.as_json)
    if args.cmd == "seed-public":
        return cmd_seed_public(args.as_json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
