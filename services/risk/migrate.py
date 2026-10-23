"""migrate_legacy: turn a pre-v2 `.agent-config.json` into a starting RiskProfile.

PRECEDENCE (the config v2 rule; also documented on
`pathiel.agents.config_store.read_risk_profile`, which is what actually
enforces it on every read):

  1. an explicit, valid `"risk_profile"` key in the agent config — the user
     has already moved to v2, migration never runs, this wins outright.
  2. migrate_legacy(config) below — runs only when (1) is absent or fails to
     validate, and produces a best-effort RiskProfile from the OLD scattered
     keys plus a plain-English report of every mapping it made.
  3. RiskProfile() (preset="auto", nothing else set) — migrate_legacy itself
     never fails; a config with none of the old keys just gets presets.py's
     defaults, which is the right answer for a config that is already empty.

This function is PURE (no file I/O, no network, no clock) and never writes
anything — callers (the API, a one-off migration script) decide whether and
where to persist the result. It never touches the live `.agent-config.json`.

WHY A $-TO-% CONVERSION IS SOMETIMES LEFT UNDONE
`max_daily_loss_usd` is a fixed dollar amount; v2's `daily_loss_limit_pct` is
a percent of CURRENT equity. Converting one to the other needs an equity
snapshot this function does not take (it only ever sees `config`, never an
account balance) — manufacturing one would be a silent, unverifiable guess
dressed up as a fact. When the legacy config already carries the percent-
based `max_daily_loss_pct` (risk_gates.py's `effective_daily_loss_limit`
already prefers this over the dollar figure), that value is carried over
directly; otherwise `daily_loss_limit_pct` is left unset and the resolved
preset's own number applies, and the report says exactly why.
"""
from __future__ import annotations

import statistics
from typing import Any, Dict, List, Optional, Tuple

from contracts.risk.models import RiskProfile
from pathiel.agents.book_params import book_params as _book_params
from services.risk.books import RISK_SCALED_BOOK_NAMES, config_key_for

CONTRACT_VERSION = "1.0.0"

# Mirrors the fallbacks already load-bearing elsewhere, so a migrated profile
# reproduces the EXACT $ sizing the legacy config was already producing:
#   pathiel/agents/config_store.py DEFAULT_CONFIG: equity_fraction_per_trade=0.2, leverage=12
#   pathiel/agents/executor.py: config.get("min_order_bump_max_mult", 2.0)
#   pathiel/agents/book_params.py: FLOOR_STOP_PCT = 15.0
_DEFAULT_EQUITY_FRACTION = 0.2
_DEFAULT_LEVERAGE = 12.0
_DEFAULT_BUMP_MULT = 2.0
_DEFAULT_STOP_PCT = 15.0
_DEFAULT_DAILY_LOSS_USD = -100.0


def _safe_float(v: Any, default: Optional[float]) -> Optional[float]:
    if v is None:
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _representative_stop_pct(cfg: Dict[str, Any]) -> float:
    """Median stop_pct, FULLY RESOLVED through book_params()'s existing
    precedence (book override > top-level default > floor), across every
    book in `RISK_SCALED_BOOK_NAMES` that is currently enabled and not
    shadow-demoted.

    `RISK_SCALED_BOOK_NAMES` is permanently empty since the headless-desk
    cleanup (2026-10-23: the seven autonomous strategy books are retired),
    so this loop never finds a candidate any more and always falls through
    to `cfg.get("default_stop_pct")` / book_params.py's own FLOOR_STOP_PCT
    (15.0) — which is also the correct answer now, since there is no book
    config left to average. The book-aware path stays rather than being
    deleted: a legacy config from before this cleanup can still carry old
    per-book blocks on disk, and if `RISK_SCALED_BOOK_NAMES` is ever
    repopulated this resumes working without further changes here.
    """
    vals = []
    for name in RISK_SCALED_BOOK_NAMES:
        book_cfg = cfg.get(config_key_for(name))
        if not isinstance(book_cfg, dict):
            continue
        if not book_cfg.get("enabled", False) or book_cfg.get("shadow_only", False):
            continue
        sp = _safe_float(_book_params(cfg, config_key_for(name)).stop_pct, None)
        if sp is not None and sp > 0:
            vals.append(sp)
    if vals:
        return float(statistics.median(vals))
    return _safe_float(cfg.get("default_stop_pct"), _DEFAULT_STOP_PCT) or _DEFAULT_STOP_PCT


def migrate_legacy(config: Dict[str, Any]) -> Tuple[RiskProfile, List[str]]:
    """Map a legacy agent config onto a starting RiskProfile. Never raises on
    a malformed or empty config — degrades to presets.py's own defaults,
    same as the rest of this codebase's "fail safe" config reads."""
    cfg = config or {}
    report: List[str] = [
        "Precedence: this mapping only applies when no valid \"risk_profile\" "
        "key exists yet. Once you save a RiskProfile (PUT /api/risk/profile "
        "or editing the config directly), it is used as-is and this function "
        "is not consulted again.",
    ]

    leverage = _safe_float(cfg.get("leverage"), _DEFAULT_LEVERAGE) or _DEFAULT_LEVERAGE
    equity_frac = _safe_float(cfg.get("equity_fraction_per_trade"), _DEFAULT_EQUITY_FRACTION) \
        or _DEFAULT_EQUITY_FRACTION
    stop_pct = _representative_stop_pct(cfg)
    # equity x equity_fraction_per_trade x leverage is the legacy NOTIONAL fraction of
    # equity; x stop_pct turns that notional into the risk (dollars lost if the stop
    # hits) as a percent of equity — the same arithmetic test_live_config_coherence.py
    # already checks this config against (`_notional_at`, `test_the_book_fits_in_margin...`).
    implied_risk_pct = _clamp(equity_frac * leverage * (stop_pct / 100.0) * 100.0, 0.01, 25.0)
    report.append(
        f"risk_per_trade_pct <- equity_fraction_per_trade({equity_frac:g}) x leverage({leverage:g}) "
        f"x representative stop({stop_pct:g}%) = {implied_risk_pct:.2f}% of equity at risk per trade "
        "(reproduces the dollar risk the legacy fixed sizing was already taking; "
        "clamped to the contract's 0-25% bound)."
    )

    bump_mult = _safe_float(cfg.get("min_order_bump_max_mult"), _DEFAULT_BUMP_MULT) or 0.0
    allow_bump = bump_mult > 0
    report.append(
        f"allow_min_order_bump <- min_order_bump_max_mult({bump_mult:g}) > 0 -> {allow_bump} "
        "(the legacy default is 2.0x, i.e. bumping a too-small order up to the exchange "
        "minimum was already on unless this was explicitly set to 0)."
    )
    max_bumped_risk_pct: Optional[float] = None
    if allow_bump:
        max_bumped_risk_pct = _clamp(implied_risk_pct * max(bump_mult, 1.0), implied_risk_pct, 25.0)
        report.append(
            f"max_bumped_risk_pct <- risk_per_trade_pct({implied_risk_pct:.2f}%) x "
            f"min_order_bump_max_mult({bump_mult:g}) = {max_bumped_risk_pct:.2f}% "
            "(clamped to the contract's 25% bound)."
        )

    max_leverage = _clamp(leverage, 1.0, 50.0)
    if max_leverage == leverage:
        report.append(f"max_leverage <- leverage({leverage:g}).")
    else:
        report.append(f"max_leverage <- leverage({leverage:g}), clamped to {max_leverage:g} "
                      "(contract bound 1-50).")

    max_open_positions: Optional[int] = None
    raw_mc = cfg.get("max_concurrent")
    if raw_mc is not None:
        mc_f = _safe_float(raw_mc, None)
        if mc_f is not None and 1 <= mc_f <= 50:
            max_open_positions = int(mc_f)
            report.append(
                f"max_open_positions <- max_concurrent({max_open_positions}) carried over "
                "directly as an explicit override; v2 will not derive a different number "
                "from equity unless you clear this field."
            )
    if max_open_positions is None:
        report.append(
            "max_open_positions <- left unset (max_concurrent was missing or outside the "
            "1-50 contract bound); it will be derived from equity, margin and the daily "
            "loss limit instead (services/risk/derive.py)."
        )

    daily_loss_limit_pct: Optional[float] = None
    legacy_pct = _safe_float(cfg.get("max_daily_loss_pct"), 0.0) or 0.0
    if legacy_pct > 0:
        # effective_daily_loss_limit (risk_gates.py) stores this as a FRACTION
        # (0.10 = 10%); the contract's daily_loss_limit_pct is a PERCENT (10.0 = 10%).
        daily_loss_limit_pct = _clamp(legacy_pct * 100.0, 0.01, 50.0)
        report.append(
            f"daily_loss_limit_pct <- max_daily_loss_pct({legacy_pct:g}) converted to a "
            f"percent = {daily_loss_limit_pct:.2f}%."
        )
    else:
        legacy_usd = _safe_float(cfg.get("max_daily_loss_usd"), _DEFAULT_DAILY_LOSS_USD) \
            or _DEFAULT_DAILY_LOSS_USD
        report.append(
            f"daily_loss_limit_pct <- left unset. Legacy max_daily_loss_usd=${legacy_usd:g} is "
            "a FIXED dollar kill-switch; v2's daily loss limit is a percent of CURRENT "
            "equity, and this function has no equity snapshot to convert against. The "
            "resolved preset's own daily_loss_limit_pct applies until you set one "
            "explicitly (services/risk/presets.py)."
        )

    legacy_floor = _safe_float(cfg.get("min_tradable_equity_usd"), None)
    if legacy_floor is not None:
        report.append(
            f"min_tradable_equity_usd(${legacy_floor:g}) is no longer a global trading "
            "block. Per-trade feasibility (services/risk/derive.py BookFeasibility) "
            "replaces it: each book now has its own equity threshold instead of one "
            "number gating every book at once — see docs/overhaul/PROFIT_AUDIT.md section 1."
        )

    profile = RiskProfile(
        preset="auto",
        risk_per_trade_pct=implied_risk_pct,
        daily_loss_limit_pct=daily_loss_limit_pct,
        max_leverage=max_leverage,
        max_open_positions=max_open_positions,
        allow_min_order_bump=allow_bump,
        max_bumped_risk_pct=max_bumped_risk_pct,
    )
    return profile, report
