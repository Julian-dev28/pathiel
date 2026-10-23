#!/usr/bin/env python3
"""profit_audit.py — deterministic, reproducible profitability audit.

Reads a COPY of live state (never the live dir itself) plus public HL /info
and config, computes a ranked set of profitability findings with evidence,
and writes /tmp/profit_audit/report.json and report.md.

Every number-producing function below is a PURE function: fixed input,
fixed output, no network, no filesystem, no clock reads of its own. The
only I/O lives in the `fetch_*` functions and `main()`, which is how
`tests/test_profit_audit.py` exercises every computation on fixture data
with zero network calls, and how the e2e test runs the whole script against
a fixture state dir with injected fetchers.

Read-only, always: this script places no order, cancels nothing, and never
calls `write_agent_config`. It only ever reads `.agent-config.json` via
`pathiel.agents.config_store.read_agent_config()`.

USAGE
-----
    python scripts/profit_audit.py                      # live run
    python scripts/profit_audit.py --state-dir /tmp/x    # explicit state-dir copy
    python scripts/profit_audit.py --out-dir /tmp/profit_audit

The live run NEVER reads PATHIEL_STATE_DIR directly: it copies it to a fresh
temp dir first (see `snapshot_state_dir`) and reads only the copy.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

REPO = Path(__file__).resolve().parents[1]
ENV_FILE = REPO / ".env.local"
HL_API = "https://api.hyperliquid.xyz"

# Repo-wide convention (every other scripts/*.py that imports `pathiel` does
# this): running this file directly puts scripts/ on sys.path[0], not the
# repo root, so the `pathiel` package next to it wouldn't resolve otherwise.
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

# ── evidence-sourced constants ───────────────────────────────────────────────
# HYPE max supply: cross-checked 2026-10-17 against the account's own live
# userFees.feeSchedule.stakingDiscountTiers (bpsOfMaxSupply 0.0001/0.001/0.01/
# 0.1/1.0/5.0 -> discount 5/10/15/20/30/40%), which implies round HYPE amounts
# (10/100/1,000/10,000/100,000/500,000) only at this supply. Hyperliquid docs
# and third-party guides (intercom.help, medium.com/@eccervse, Oct 2026) state
# those same round amounts directly. If HL ever changes the max supply this
# constant needs updating — it is the one external fact this file assumes.
HYPE_MAX_SUPPLY = 1_000_000_000

EQUITY_SCALE_TIERS = (15.0, 1_000.0, 10_000.0)


# ── env helpers (never print a secret) ───────────────────────────────────────

def read_env_key(key: str, env_file: Path = ENV_FILE) -> str:
    """One key's value out of a dotenv-style file, without touching or
    printing any OTHER key in it. Used for PATHIEL_STATE_DIR and the two
    public wallet ADDRESSES (not secrets) — never for a private key."""
    if not env_file.exists():
        return ""
    prefix = f"{key}="
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line.startswith(prefix):
            return line[len(prefix):].strip().strip('"').strip("'")
    return ""


def resolve_account_addresses(env_file: Path = ENV_FILE) -> Tuple[str, str]:
    """(master, wallet) public addresses. Mirrors
    pathiel.client.exchange's HL_ACCOUNT precedence without importing that
    module (which requires a private key to construct its Exchange
    singleton) — this script only ever needs the address, never signs."""
    return (read_env_key("HYPERLIQUID_MASTER_ADDRESS", env_file),
            read_env_key("HYPERLIQUID_WALLET_ADDRESS", env_file))


def primary_account_address(env_file: Path = ENV_FILE) -> str:
    master, wallet = resolve_account_addresses(env_file)
    return master or wallet


# ── state-dir snapshot (never read the live dir directly) ──────────────────

def snapshot_state_dir(src: str) -> str:
    """Copy `src` into a fresh temp dir and return its path. Every state-dir
    read in this file goes through the returned copy — never `src` itself."""
    dst = tempfile.mkdtemp(prefix="profit_audit_state_")
    if src and os.path.isdir(src):
        shutil.copytree(src, dst, dirs_exist_ok=True)
    return dst


def read_json_file(path: str) -> Any:
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def read_jsonl_file(path: str) -> List[dict]:
    out = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except FileNotFoundError:
        pass
    return out


# ═════════════════════════════════════════════════════════════════════════
# PURE COMPUTATIONS — every one of these is unit-tested on fixture data.
# ═════════════════════════════════════════════════════════════════════════

# ── fees / maker-taker ───────────────────────────────────────────────────────

def _is_hip3(coin: str) -> bool:
    return ":" in (coin or "")


def fee_summary(fills: Sequence[dict]) -> Dict[str, Any]:
    """Taker/maker split and $ fee cost, overall and by venue (main vs HIP-3).

    `crossed` is HL's own per-fill flag: True = this fill took liquidity
    (taker, paid the cross rate), False = it was resting (maker, paid the
    add rate — usually 1/3 of taker). Dust-conversion fills (sz*px == 0) are
    excluded from counts but their (zero) fee still nets out harmlessly.
    """
    n_taker = n_maker = 0
    fee_taker = fee_maker = 0.0
    fee_main = fee_xyz = 0.0
    n_main = n_xyz = 0
    for f in fills:
        fee = abs(float(f.get("fee") or 0))
        crossed = bool(f.get("crossed"))
        if crossed:
            n_taker += 1
            fee_taker += fee
        else:
            n_maker += 1
            fee_maker += fee
        if _is_hip3(f.get("coin", "")):
            n_xyz += 1
            fee_xyz += fee
        else:
            n_main += 1
            fee_main += fee
    n_total = n_taker + n_maker
    return {
        "n_fills": n_total,
        "n_taker": n_taker, "n_maker": n_maker,
        "taker_pct": (100.0 * n_taker / n_total) if n_total else 0.0,
        "fee_taker_usd": round(fee_taker, 6),
        "fee_maker_usd": round(fee_maker, 6),
        "fee_total_usd": round(fee_taker + fee_maker, 6),
        "fee_main_usd": round(fee_main, 6),
        "fee_xyz_usd": round(fee_xyz, 6),
        "n_main": n_main, "n_xyz": n_xyz,
    }


def effective_fee_rate_by_venue(fills: Sequence[dict]) -> Dict[str, float]:
    """Observed fee/notional (%) for main-dex vs xyz-HIP3 fills, computed
    directly from what the account actually paid — not assumed from the
    account-wide userFees rate card, which is blended and can miss a
    per-market deployer fee schedule (HIP-3 "growth mode").

    Liquidation-close fills are excluded: HL's liquidation fee handling
    differs from a normal fill's, so mixing them in would understate the
    rate real discretionary order flow pays.
    """
    main_fee = main_notional = xyz_fee = xyz_notional = 0.0
    for f in fills:
        if "liquidation" in f:
            continue
        try:
            sz, px, fee = abs(float(f.get("sz") or 0)), float(f.get("px") or 0), abs(float(f.get("fee") or 0))
        except (TypeError, ValueError):
            continue
        notional = sz * px
        if notional <= 0:
            continue
        if _is_hip3(f.get("coin", "")):
            xyz_fee += fee
            xyz_notional += notional
        else:
            main_fee += fee
            main_notional += notional
    return {
        "main_dex_pct": (100.0 * main_fee / main_notional) if main_notional else 0.0,
        "xyz_hip3_pct": (100.0 * xyz_fee / xyz_notional) if xyz_notional else 0.0,
        "main_dex_notional_usd": round(main_notional, 2),
        "xyz_hip3_notional_usd": round(xyz_notional, 2),
    }


def taker_to_maker_savings(fills: Sequence[dict], taker_rate_pct: float,
                           maker_rate_pct: float, window_days: float) -> Dict[str, Any]:
    """What converting every TAKER fill in `fills` to `maker_rate_pct` would
    have saved, extrapolated to $/day and $/month. This is the maker_first
    ceiling for whatever subset of fills the caller passes in — not a guess
    at how often a post-only order would actually fill (that depends on the
    market; this is the saving if every maker_first attempt filled
    passively). Pass only one venue's fills in for a venue-specific rate;
    see `maker_first_savings_report` for the main+xyz blend this account
    actually needs (main and xyz pay different taker AND maker rates).
    """
    taker_notional = 0.0
    for f in fills:
        if not bool(f.get("crossed")):
            continue
        try:
            sz, px = abs(float(f.get("sz") or 0)), float(f.get("px") or 0)
        except (TypeError, ValueError):
            continue
        taker_notional += sz * px
    rate_delta = max(0.0, taker_rate_pct - maker_rate_pct) / 100.0
    total_savings = taker_notional * rate_delta
    per_day = total_savings / window_days if window_days > 0 else 0.0
    return {
        "taker_notional_usd": round(taker_notional, 2),
        "rate_delta_pct": round(taker_rate_pct - maker_rate_pct, 6),
        "ceiling_savings_usd": round(total_savings, 4),
        "ceiling_savings_per_day_usd": round(per_day, 4),
        "ceiling_savings_per_month_usd": round(per_day * 30, 4),
    }


def maker_first_savings_report(fills: Sequence[dict], taker_rate_pct: float,
                               maker_rate_pct: float, window_days: float) -> Dict[str, Any]:
    """The REAL maker_first business case, split main-dex vs xyz-HIP3,
    because this account's own fills prove the two pay different rates
    (main 0.045/0.015 observed in userFees; xyz 0.009 observed in fills,
    growth mode). Main-dex uses the account's OWN add rate (directly
    observed via userFees.userAddRate — fully factual). xyz has placed ZERO
    maker fills in the sample, so there is nothing to observe directly for
    its add rate; this ASSUMES xyz's maker rate is discounted by the same
    ratio as its observed taker rate (xyz_taker / main_taker) applied to the
    main add rate. That assumption is flagged explicitly in the result so it
    is never read as a measured fact.
    """
    main_fills = [f for f in fills if not _is_hip3(f.get("coin", ""))]
    xyz_fills = [f for f in fills if _is_hip3(f.get("coin", ""))]
    rates = effective_fee_rate_by_venue(fills)
    xyz_taker_observed = rates["xyz_hip3_pct"]
    discount_ratio = (xyz_taker_observed / taker_rate_pct) if taker_rate_pct > 0 else 1.0
    xyz_maker_assumed = maker_rate_pct * discount_ratio

    main = taker_to_maker_savings(main_fills, taker_rate_pct, maker_rate_pct, window_days)
    xyz = taker_to_maker_savings(xyz_fills, xyz_taker_observed, xyz_maker_assumed, window_days)
    return {
        "main_dex": main,
        "xyz_hip3": xyz,
        "xyz_maker_rate_is_assumed": True,
        "xyz_maker_rate_assumed_pct": round(xyz_maker_assumed, 6),
        "combined_ceiling_savings_per_month_usd": round(
            main["ceiling_savings_per_month_usd"] + xyz["ceiling_savings_per_month_usd"], 4),
    }


# ── liquidations / realized PnL ──────────────────────────────────────────────

def is_own_liquidation(fill: dict, our_addresses: Set[str]) -> bool:
    liq = fill.get("liquidation")
    if not isinstance(liq, dict):
        return False
    who = str(liq.get("liquidatedUser") or "").lower()
    return who in our_addresses


def liquidation_summary(fills: Sequence[dict], our_addresses: Set[str]) -> Dict[str, Any]:
    """Count and $ cost of OUR OWN account's forced liquidations, separated
    from ordinary realized PnL — a liquidation means the exchange closed the
    position at the maintenance-margin bound, not our own stop, which is a
    structurally different (and avoidable) loss."""
    our_addresses = {a.lower() for a in our_addresses if a}
    n = 0
    gross = 0.0
    fee = 0.0
    coins = []
    for f in fills:
        if not is_own_liquidation(f, our_addresses):
            continue
        n += 1
        gross += float(f.get("closedPnl") or 0)
        fee += abs(float(f.get("fee") or 0))
        coins.append(f.get("coin"))
    return {
        "n_liquidations": n,
        "gross_pnl_usd": round(gross, 4),
        "fee_usd": round(fee, 4),
        "net_pnl_usd": round(gross - fee, 4),
        "coins": coins,
    }


def realized_pnl_summary(fills: Sequence[dict], our_addresses: Set[str]) -> Dict[str, Any]:
    gross = sum(float(f.get("closedPnl") or 0) for f in fills)
    fee = sum(abs(float(f.get("fee") or 0)) for f in fills)
    liq = liquidation_summary(fills, our_addresses)
    non_liq_gross = gross - liq["gross_pnl_usd"]
    non_liq_fee = fee - liq["fee_usd"]
    return {
        "gross_pnl_usd": round(gross, 4),
        "fee_usd": round(fee, 4),
        "net_pnl_usd": round(gross - fee, 4),
        "liquidation_share_of_loss_pct": (
            round(100.0 * abs(liq["net_pnl_usd"]) / abs(gross - fee), 1)
            if (gross - fee) < 0 and liq["net_pnl_usd"] < 0 else 0.0
        ),
        "non_liquidation_gross_pnl_usd": round(non_liq_gross, 4),
        "non_liquidation_fee_usd": round(non_liq_fee, 4),
        "non_liquidation_net_pnl_usd": round(non_liq_gross - non_liq_fee, 4),
    }


# ── referral / staking ────────────────────────────────────────────────────────

def referral_discount_savings(user_fees: dict, monthly_fee_usd: float) -> Dict[str, Any]:
    """$/month saved by applying a referral code, from the account's own LIVE
    fee schedule (feeSchedule.referralDiscount is a fraction OFF the fee, e.g.
    0.04 = 4% cheaper fees, not 4 percentage points off the rate)."""
    schedule = (user_fees or {}).get("feeSchedule") or {}
    discount_frac = float(schedule.get("referralDiscount") or 0)
    active = float((user_fees or {}).get("activeReferralDiscount") or 0)
    savings = monthly_fee_usd * discount_frac
    return {
        "available_discount_frac": discount_frac,
        "currently_active_frac": active,
        "already_applied": active > 0,
        "savings_per_month_usd": round(savings, 4) if active <= 0 else 0.0,
    }


def staking_discount_tiers(user_fees: dict, hype_price_usd: float,
                           monthly_fee_usd: float,
                           equity_tiers: Sequence[float] = EQUITY_SCALE_TIERS,
                           hype_max_supply: float = HYPE_MAX_SUPPLY,
                           ) -> Tuple[List[Dict[str, Any]], float]:
    """For each live stakingDiscountTiers entry: HYPE needed, its $ cost at
    the current HYPE price, the monthly fee $ saved at the CURRENT fee
    volume, and — because staking is a CAPITAL commitment, not a per-trade
    cost — how much of each equity tier that stake would lock up."""
    schedule = (user_fees or {}).get("feeSchedule") or {}
    tiers = schedule.get("stakingDiscountTiers") or []
    active = float(((user_fees or {}).get("activeStakingDiscount") or {}).get("discount") or 0)
    out = []
    for t in tiers:
        bps = float(t.get("bpsOfMaxSupply") or 0)
        discount = float(t.get("discount") or 0)
        if discount <= 0:
            continue
        hype_needed = (bps / 10_000.0) * hype_max_supply
        cost_usd = hype_needed * hype_price_usd
        savings_per_month = monthly_fee_usd * discount
        row = {
            "discount_frac": discount,
            "hype_needed": round(hype_needed, 2),
            "cost_usd": round(cost_usd, 2),
            "savings_per_month_usd_at_current_volume": round(savings_per_month, 4),
            "pct_of_equity_locked": {
                f"at_{eq:g}": round(100.0 * cost_usd / eq, 1) if eq > 0 else None
                for eq in equity_tiers
            },
        }
        out.append(row)
    out.sort(key=lambda r: r["discount_frac"])
    return out, active


# ── small-account friction ───────────────────────────────────────────────────

def small_account_friction(equity: float, min_tradable_equity_usd: float,
                           min_order_usd: float, max_concurrent: int,
                           daily_kill_usd: float) -> Dict[str, Any]:
    """Whether the account is CURRENTLY below its own tradable floor, and the
    other hard friction points sizing has to clear. `min_tradable_equity_usd`
    and `max_concurrent` should come from the SAME resolver the executor uses
    (`pathiel.agents.executor.min_tradable_equity` /
    `read_agent_config()['max_concurrent']`) so this never drifts from what
    is actually enforced live."""
    below_floor = equity < min_tradable_equity_usd
    return {
        "equity_usd": round(equity, 4),
        "min_tradable_equity_usd": round(min_tradable_equity_usd, 2),
        "below_tradable_floor": below_floor,
        "shortfall_usd": round(max(0.0, min_tradable_equity_usd - equity), 2),
        "min_order_usd": min_order_usd,
        "max_concurrent": max_concurrent,
        "daily_kill_usd": daily_kill_usd,
    }


# ── forward-looking liquidation safety on OPEN positions ────────────────────

def position_liquidation_safety(
    position: Dict[str, Any],
    coin_max_leverage: int,
    *,
    max_frac_of_liq: float,
    liq_safety_frac: float,
    max_stop_pct_at_leverage_fn: Callable[..., float],
) -> Dict[str, Any]:
    """Is ONE currently-held position's configured stop still inside the
    exchange's liquidation buffer at its ACTUAL leverage? Reuses
    `pathiel.agents.book_params.max_stop_pct_at_leverage` (pass it in as
    `max_stop_pct_at_leverage_fn`) rather than recomputing the maintenance-
    margin formula a second time and risking drift from the one the executor
    already enforces at entry — this check is the entry-time guarantee's
    ONLY gap: it is never re-verified for a position already open when
    config changes later.
    """
    leverage = float(position.get("leverage") or 0)
    stop_pct = float(position.get("stop_pct") or 0)
    coin = position.get("coin", "")
    if leverage <= 0:
        return {"coin": coin, "safe": True, "reason": "no leverage info", "widest_safe_stop_pct": None}
    widest = max_stop_pct_at_leverage_fn(
        int(round(leverage)), max_frac_of_liq=max_frac_of_liq,
        coin_max_leverage=coin_max_leverage, liq_safety_frac=liq_safety_frac,
    )
    safe = (stop_pct <= 0) or stop_pct <= widest
    return {
        "coin": coin, "leverage": leverage, "stop_pct": stop_pct,
        "widest_safe_stop_pct": None if widest == float("inf") else round(widest, 4),
        "safe": safe,
    }


def liquidation_safety_check(
    positions: Sequence[Dict[str, Any]],
    coin_max_leverage: Dict[str, int],
    *,
    max_frac_of_liq: float,
    liq_safety_frac: float,
    max_stop_pct_at_leverage_fn: Callable[..., float],
) -> List[Dict[str, Any]]:
    """Every CURRENTLY OPEN position that is NOT safe by
    `position_liquidation_safety` — empty when every open position (or there
    are none) is fine. This is the one check in this file that is about the
    future, not the past 60 days."""
    unsafe = []
    for p in positions:
        res = position_liquidation_safety(
            p, coin_max_leverage.get(p.get("coin", ""), 0),
            max_frac_of_liq=max_frac_of_liq, liq_safety_frac=liq_safety_frac,
            max_stop_pct_at_leverage_fn=max_stop_pct_at_leverage_fn,
        )
        if not res["safe"]:
            unsafe.append(res)
    return unsafe


# ── funding ──────────────────────────────────────────────────────────────────

def funding_summary(funding_events: Sequence[dict], window_days: float) -> Dict[str, Any]:
    """Net funding paid/received over the window, by coin and extrapolated
    to $/month. HL convention: a positive `delta.usdc` is funding the account
    RECEIVED (it was on the side the funding rate pays)."""
    by_coin: Dict[str, float] = {}
    total = 0.0
    for e in funding_events:
        d = e.get("delta") or {}
        try:
            usdc = float(d.get("usdc") or 0)
        except (TypeError, ValueError):
            continue
        coin = d.get("coin", "?")
        by_coin[coin] = by_coin.get(coin, 0.0) + usdc
        total += usdc
    per_day = total / window_days if window_days > 0 else 0.0
    return {
        "total_usd": round(total, 4),
        "per_day_usd": round(per_day, 4),
        "per_month_usd": round(per_day * 30, 4),
        "by_coin_usd": {c: round(v, 4) for c, v in sorted(by_coin.items(), key=lambda kv: kv[1])},
    }


def cross_venue_funding_gap(predicted_fundings: Sequence[Sequence[Any]],
                            coins: Sequence[str],
                            hl_venue_name: str = "HlPerp") -> List[Dict[str, Any]]:
    """Informational only — HL's own predicted-funding rate per coin vs the
    same coin on Binance/Bybit, annualized. No execution exists or is built
    against this; it is context for whether HL is cheap or expensive to hold
    a funding-paying side on, for the coins this account actually trades."""
    by_coin = {row[0]: dict(row[1]) for row in predicted_fundings if len(row) == 2}
    out = []
    for coin in coins:
        venues = by_coin.get(coin)
        if not venues:
            continue
        try:
            hl_rate = float(venues.get(hl_venue_name, {}).get("fundingRate") or 0)
        except (TypeError, ValueError):
            continue
        others = {}
        for venue, venue_info in venues.items():
            if venue == hl_venue_name:
                continue
            try:
                others[venue] = float(venue_info.get("fundingRate") or 0)
            except (TypeError, ValueError):
                continue
        if not others:
            continue
        row = {"coin": coin, "hl_hourly_rate": hl_rate,
               "hl_annualized_pct": round(hl_rate * 24 * 365 * 100, 2)}
        for venue, rate in others.items():
            row[f"{venue}_hourly_rate"] = rate
            row[f"{venue}_annualized_pct"] = round(rate * 24 * 365 * 100, 2)
        out.append(row)
    return out


# ── HIP-3 growth-mode evidence ───────────────────────────────────────────────

def hip3_growth_mode_evidence(fills: Sequence[dict], main_dex_taker_rate_pct: float) -> Dict[str, Any]:
    """Whether xyz-HIP3 fills are ACTUALLY paying a discounted taker rate vs
    the main dex, straight from fee/notional on real fills — not assumed
    from a press report about "growth mode." If the xyz rate matches the
    main-dex rate, growth mode is not (or no longer) active for this dex and
    this returns is_discounted=False."""
    rates = effective_fee_rate_by_venue(fills)
    xyz_rate = rates["xyz_hip3_pct"]
    discount_ratio = (xyz_rate / main_dex_taker_rate_pct) if main_dex_taker_rate_pct > 0 else None
    return {
        "xyz_observed_taker_pct": round(xyz_rate, 5),
        "main_dex_taker_rate_pct": main_dex_taker_rate_pct,
        "discount_ratio": round(discount_ratio, 3) if discount_ratio is not None else None,
        "is_discounted": bool(discount_ratio is not None and discount_ratio < 0.9),
    }


# ── pair-trade stats (Brent / WTI) ──────────────────────────────────────────

def pair_trade_stats(closes_a: Sequence[float], closes_b: Sequence[float]) -> Dict[str, Any]:
    """Correlation and relative volatility between two daily close series of
    equal length, aligned index-for-index by the caller. Built for the
    Brent/WTI HIP-3 pair but generic to any two series."""
    n = min(len(closes_a), len(closes_b))
    if n < 3:
        return {"n": n, "error": "insufficient history"}
    a, b = list(closes_a[:n]), list(closes_b[:n])
    rets_a = [a[i] / a[i - 1] - 1 for i in range(1, n) if a[i - 1]]
    rets_b = [b[i] / b[i - 1] - 1 for i in range(1, n) if b[i - 1]]
    m = min(len(rets_a), len(rets_b))
    rets_a, rets_b = rets_a[:m], rets_b[:m]
    if m < 2:
        return {"n": n, "error": "insufficient returns"}
    mean_a, mean_b = statistics.mean(rets_a), statistics.mean(rets_b)
    sd_a, sd_b = statistics.pstdev(rets_a), statistics.pstdev(rets_b)
    cov = sum((rets_a[i] - mean_a) * (rets_b[i] - mean_b) for i in range(m)) / m
    corr = cov / (sd_a * sd_b) if sd_a > 0 and sd_b > 0 else 0.0
    pair_rets = [rets_b[i] - rets_a[i] for i in range(m)]
    sd_pair = statistics.pstdev(pair_rets)
    spreads = [b[i] - a[i] for i in range(n)]
    return {
        "n_days": n,
        "correlation": round(corr, 4),
        "leg_a_daily_stdev_pct": round(sd_a * 100, 4),
        "leg_b_daily_stdev_pct": round(sd_b * 100, 4),
        "pair_daily_stdev_pct": round(sd_pair * 100, 4),
        "vol_reduction_vs_leg_a_x": round(sd_a / sd_pair, 2) if sd_pair > 0 else None,
        "spread_mean": round(statistics.mean(spreads), 4),
        "spread_stdev": round(statistics.pstdev(spreads), 4),
        "spread_latest": round(spreads[-1], 4),
        "spread_z_score": (
            round((spreads[-1] - statistics.mean(spreads)) / statistics.pstdev(spreads), 2)
            if statistics.pstdev(spreads) > 0 else 0.0
        ),
    }


# ═════════════════════════════════════════════════════════════════════════
# I/O — the only functions that touch the network or the state-dir copy.
# ═════════════════════════════════════════════════════════════════════════

def _http_post(path: str, payload: dict) -> Any:
    import requests
    r = requests.post(f"{HL_API}{path}", json=payload, timeout=15)
    r.raise_for_status()
    return r.json()


def default_fetchers(user: str) -> Dict[str, Callable[[], Any]]:
    """The real, network-hitting fetcher for every raw input this script
    needs, keyed by the same names `run_audit` looks them up by. Tests pass
    their own dict of fixtures instead of this."""
    now_ms = int(time.time() * 1000)
    since_60d = now_ms - 60 * 86400 * 1000
    return {
        "fills": lambda: _http_post("/info", {"type": "userFillsByTime", "user": user, "startTime": since_60d}) or [],
        "user_fees": lambda: _http_post("/info", {"type": "userFees", "user": user}) or {},
        "funding": lambda: _http_post("/info", {"type": "userFunding", "user": user, "startTime": since_60d}) or [],
        "predicted_fundings": lambda: _http_post("/info", {"type": "predictedFundings"}) or [],
        "clearinghouse_main": lambda: _http_post("/info", {"type": "clearinghouseState", "user": user}) or {},
        "clearinghouse_xyz": lambda: _http_post("/info", {"type": "clearinghouseState", "user": user, "dex": "xyz"}) or {},
        "spot_meta_ctxs": lambda: _http_post("/info", {"type": "spotMetaAndAssetCtxs"}) or [{}, []],
        "candles_cl": lambda: _http_post("/info", {"type": "candleSnapshot", "req": {
            "coin": "xyz:CL", "interval": "1d", "startTime": since_60d, "endTime": now_ms}}) or [],
        "candles_brentoil": lambda: _http_post("/info", {"type": "candleSnapshot", "req": {
            "coin": "xyz:BRENTOIL", "interval": "1d", "startTime": since_60d, "endTime": now_ms}}) or [],
    }


def hype_usd_price(spot_meta_ctxs: Any) -> float:
    """HYPE/USDC mark price from spotMetaAndAssetCtxs — the most liquid HYPE
    quote pair, used as the USD reference for staking-tier cost math."""
    try:
        meta, ctxs = spot_meta_ctxs[0], spot_meta_ctxs[1]
        tokens = {t["index"]: t["name"] for t in meta.get("tokens", [])}
        for u, ctx in zip(meta.get("universe", []), ctxs):
            names = [tokens.get(i) for i in u.get("tokens", [])]
            if "HYPE" in names and "USDC" in names:
                return float(ctx.get("markPx") or 0)
    except (KeyError, IndexError, TypeError, ValueError):
        pass
    return 0.0


def open_positions_from_state(clearinghouse_states: Sequence[dict],
                              sl_atr_mult: float) -> List[Dict[str, Any]]:
    """Normalize HL clearinghouseState(s) asset_positions into the
    {coin, leverage, stop_pct} shape `liquidation_safety_check` wants.
    `stop_pct` is approximated from the account's own `sl_atr_mult` backup-SL
    multiplier against the position's entry (ATR itself is not in the
    position payload, so this is a label on how the backup SL is SIZED, not
    its exact live distance) when no better figure is available — it is the
    same fallback executor.py uses when a book sets no explicit override.
    """
    out = []
    for state in clearinghouse_states:
        for p in (state or {}).get("assetPositions", []):
            pos = p.get("position", {})
            coin = pos.get("coin")
            if not coin:
                continue
            try:
                szi = float(pos.get("szi") or 0)
            except (TypeError, ValueError):
                szi = 0.0
            if szi == 0:
                continue
            try:
                lev = float((pos.get("leverage") or {}).get("value") or 0)
            except (TypeError, ValueError):
                lev = 0.0
            out.append({"coin": coin, "leverage": lev, "stop_pct": 0.0,
                       "_sl_atr_mult": sl_atr_mult})
    return out


# ═════════════════════════════════════════════════════════════════════════
# Orchestration
# ═════════════════════════════════════════════════════════════════════════

def run_audit(
    state_dir_src: Optional[str],
    *,
    config: Dict[str, Any],
    our_addresses: Set[str],
    fetchers: Dict[str, Callable[[], Any]],
    get_max_leverage_fn: Callable[[str], int],
    window_days: float = 60.0,
    now_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """Run every computation above against live (or injected) data and
    return the full report dict. `fetchers` and `get_max_leverage_fn` are
    the ONLY seams tests need to fill to run this with zero network."""
    from pathiel.agents.book_params import (
        BACKUP_SL_MAX_FRAC_OF_LIQ, LIQ_SAFETY_FRAC, max_stop_pct_at_leverage)

    state_copy = snapshot_state_dir(state_dir_src) if state_dir_src else None

    fills = fetchers["fills"]()
    user_fees = fetchers["user_fees"]()
    funding = fetchers["funding"]()
    predicted = fetchers["predicted_fundings"]()
    ch_main = fetchers["clearinghouse_main"]()
    ch_xyz = fetchers["clearinghouse_xyz"]()
    spot_ctxs = fetchers["spot_meta_ctxs"]()
    candles_cl = [float(c["c"]) for c in fetchers["candles_cl"]()]
    candles_bo = [float(c["c"]) for c in fetchers["candles_brentoil"]()]

    equity_main = float((ch_main.get("marginSummary") or {}).get("accountValue") or 0)
    equity_xyz = float((ch_xyz.get("marginSummary") or {}).get("accountValue") or 0)
    total_equity = equity_main + equity_xyz

    taker_rate_pct = float((user_fees.get("userCrossRate") or 0)) * 100
    maker_rate_pct = float((user_fees.get("userAddRate") or 0)) * 100
    fees = fee_summary(fills)
    monthly_fee_usd = (fees["fee_total_usd"] / window_days) * 30 if window_days > 0 else 0.0

    hype_price = hype_usd_price(spot_ctxs)
    staking_tiers, active_staking = staking_discount_tiers(user_fees, hype_price, monthly_fee_usd)

    sl_atr_mult = float(config.get("sl_atr_mult", 1.5) or 1.5)
    positions = open_positions_from_state([ch_main, ch_xyz], sl_atr_mult)
    coin_max_lev = {p["coin"]: get_max_leverage_fn(p["coin"]) for p in positions}

    try:
        from pathiel.agents.executor import min_tradable_equity
        min_eq = min_tradable_equity(config)
    except Exception:
        min_eq = 25.0

    coins_traded = sorted({f.get("coin") for f in fills if f.get("coin")})

    report = {
        "generated_at_ms": now_ms or int(time.time() * 1000),
        "window_days": window_days,
        "equity": {"main_usd": round(equity_main, 4), "xyz_usd": round(equity_xyz, 4),
                   "total_usd": round(total_equity, 4)},
        "fees": fees,
        "fee_rates": {"taker_rate_pct": taker_rate_pct, "maker_rate_pct": maker_rate_pct},
        "effective_fee_rate_by_venue": effective_fee_rate_by_venue(fills),
        "maker_first_ceiling_savings": maker_first_savings_report(
            fills, taker_rate_pct, maker_rate_pct, window_days),
        "hip3_growth_mode": hip3_growth_mode_evidence(fills, taker_rate_pct),
        "realized_pnl": realized_pnl_summary(fills, our_addresses),
        "liquidations": liquidation_summary(fills, our_addresses),
        "referral": referral_discount_savings(user_fees, monthly_fee_usd),
        "staking_tiers": staking_tiers,
        "staking_active_discount": active_staking,
        "hype_price_usd": hype_price,
        "small_account_friction": small_account_friction(
            total_equity, min_eq,
            10.5, int(config.get("max_concurrent", 3) or 3),
            float(config.get("max_daily_loss_usd", -100) or -100),
        ),
        "liquidation_safety_open_positions": liquidation_safety_check(
            positions, coin_max_lev,
            max_frac_of_liq=float(config.get("backup_sl_max_frac_of_liq", BACKUP_SL_MAX_FRAC_OF_LIQ) or BACKUP_SL_MAX_FRAC_OF_LIQ),
            liq_safety_frac=float(config.get("liq_safety_frac", LIQ_SAFETY_FRAC) or LIQ_SAFETY_FRAC),
            max_stop_pct_at_leverage_fn=max_stop_pct_at_leverage,
        ),
        "funding": funding_summary(funding, window_days),
        "cross_venue_funding_gap": cross_venue_funding_gap(predicted, coins_traded),
        "pair_trade_brent_wti": pair_trade_stats(candles_cl, candles_bo),
        "monthly_fee_usd_at_current_volume": round(monthly_fee_usd, 4),
    }

    if state_copy:
        shutil.rmtree(state_copy, ignore_errors=True)
    return report


# ── report rendering ─────────────────────────────────────────────────────────

def render_markdown(report: Dict[str, Any]) -> str:
    lines = ["# Profit audit report", ""]
    lines.append(f"Generated: {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(report['generated_at_ms'] / 1000))}")
    lines.append(f"Window: {report['window_days']:.0f} days")
    eq = report["equity"]
    lines.append(f"Equity: ${eq['total_usd']:.2f} (main ${eq['main_usd']:.2f}, xyz ${eq['xyz_usd']:.2f})")
    lines.append("")

    lines.append("## Fees")
    f = report["fees"]
    lines.append(f"- {f['n_fills']} fills: {f['n_taker']} taker ({f['taker_pct']:.1f}%), {f['n_maker']} maker")
    lines.append(f"- Fee paid: ${f['fee_total_usd']:.4f} (taker ${f['fee_taker_usd']:.4f}, maker ${f['fee_maker_usd']:.4f})")
    r = report["effective_fee_rate_by_venue"]
    lines.append(f"- Observed rate: main-dex {r['main_dex_pct']:.4f}%, xyz-HIP3 {r['xyz_hip3_pct']:.4f}%")
    g = report["hip3_growth_mode"]
    lines.append(f"- HIP-3 growth-mode discount confirmed from fills: {g['is_discounted']} (ratio {g['discount_ratio']})")
    m = report["maker_first_ceiling_savings"]
    lines.append(f"- maker_first ceiling savings: ${m['combined_ceiling_savings_per_month_usd']:.2f}/mo "
                f"(main ${m['main_dex']['ceiling_savings_per_month_usd']:.2f} + "
                f"xyz ${m['xyz_hip3']['ceiling_savings_per_month_usd']:.2f}, "
                f"xyz maker rate ASSUMED at {m['xyz_maker_rate_assumed_pct']:.4f}% — no maker fills "
                f"exist yet to observe it directly)")
    lines.append("")

    lines.append("## Realized PnL / liquidations")
    p = report["realized_pnl"]
    lines.append(f"- Net realized PnL: ${p['net_pnl_usd']:.2f} (gross ${p['gross_pnl_usd']:.2f}, fees ${p['fee_usd']:.2f})")
    liq = report["liquidations"]
    lines.append(f"- Of which, OWN-ACCOUNT liquidations: {liq['n_liquidations']} events, net ${liq['net_pnl_usd']:.2f} "
                f"({p['liquidation_share_of_loss_pct']:.0f}% of net loss)")
    lines.append(f"- Non-liquidation net: ${p['non_liquidation_net_pnl_usd']:.2f}")
    lines.append("")

    lines.append("## Referral / staking")
    ref = report["referral"]
    lines.append(f"- Referral discount available: {ref['available_discount_frac']*100:.0f}%, "
                f"active: {ref['already_applied']}, would save ${ref['savings_per_month_usd']:.2f}/mo")
    lines.append(f"- HYPE price used: ${report['hype_price_usd']:.6f}")
    for t in report["staking_tiers"]:
        lines.append(f"  - {t['discount_frac']*100:.0f}% discount: {t['hype_needed']:.0f} HYPE "
                    f"(${t['cost_usd']:.2f}), saves ${t['savings_per_month_usd_at_current_volume']:.4f}/mo at current volume")
    lines.append("")

    lines.append("## Small-account friction")
    s = report["small_account_friction"]
    lines.append(f"- Equity ${s['equity_usd']:.2f} vs tradable floor ${s['min_tradable_equity_usd']:.2f} "
                f"— below floor: {s['below_tradable_floor']}")
    lines.append("")

    lines.append("## Liquidation safety (open positions, forward-looking)")
    unsafe = report["liquidation_safety_open_positions"]
    if not unsafe:
        lines.append("- No open positions are unsafe (or none open).")
    else:
        for u in unsafe:
            lines.append(f"- UNSAFE: {u['coin']} stop {u['stop_pct']}% > widest safe {u['widest_safe_stop_pct']}% at {u['leverage']}x")
    lines.append("")

    lines.append("## Funding")
    fn = report["funding"]
    lines.append(f"- Net funding: ${fn['total_usd']:.2f} over the window (${fn['per_month_usd']:.2f}/mo)")
    lines.append("")

    lines.append("## Brent/WTI pair stats (xyz:CL vs xyz:BRENTOIL)")
    pt = report["pair_trade_brent_wti"]
    if "error" not in pt:
        lines.append(f"- correlation {pt['correlation']}, pair daily stdev {pt['pair_daily_stdev_pct']:.3f}% "
                    f"vs leg stdev ~{pt['leg_a_daily_stdev_pct']:.2f}% ({pt['vol_reduction_vs_leg_a_x']}x lower)")
        lines.append(f"- spread now {pt['spread_latest']:.2f} (mean {pt['spread_mean']:.2f}, z={pt['spread_z_score']})")
    lines.append("")

    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--state-dir", default=None,
                    help="live state dir to snapshot (default: PATHIEL_STATE_DIR from --env-file)")
    ap.add_argument("--out-dir", default="/tmp/profit_audit")
    ap.add_argument("--env-file", default=str(ENV_FILE),
                    help="path to the .env.local to read PATHIEL_STATE_DIR / the two public "
                         "wallet addresses from (never the private key). Defaults to the repo "
                         "this script lives in; a worktree without its own .env.local should "
                         "point this at the main checkout's.")
    args = ap.parse_args()
    env_file = Path(args.env_file)

    from pathiel.agents.config_store import read_agent_config
    from pathiel.client.exchange import get_max_leverage

    state_dir_src = args.state_dir or read_env_key("PATHIEL_STATE_DIR", env_file)
    master, wallet = resolve_account_addresses(env_file)
    user = master or wallet
    if not user:
        raise SystemExit("No HYPERLIQUID_MASTER_ADDRESS/HYPERLIQUID_WALLET_ADDRESS in .env.local")

    config = read_agent_config()
    report = run_audit(
        state_dir_src,
        config=config,
        our_addresses={master, wallet},
        fetchers=default_fetchers(user),
        get_max_leverage_fn=get_max_leverage,
    )

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(report, indent=2, default=str))
    (out_dir / "report.md").write_text(render_markdown(report))
    print(f"wrote {out_dir / 'report.json'}")
    print(f"wrote {out_dir / 'report.md'}")


if __name__ == "__main__":
    main()
