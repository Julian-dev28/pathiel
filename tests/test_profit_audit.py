"""Unit + e2e tests for scripts/profit_audit.py.

Every pure computation is exercised on fixture data matching the real HL
/info shapes this script was built against (see docs/overhaul/PROFIT_AUDIT.md
for the live numbers these fixtures are modeled on). The e2e test runs the
full `run_audit` orchestration against a fixture state dir with every
fetcher injected — no network call anywhere in this file.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("profit_audit", ROOT / "scripts" / "profit_audit.py")
pa = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pa)

OUR_ADDR = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
OUR_ADDRESSES = {OUR_ADDR}


def _fill(coin, sz, px, fee=0.0, crossed=True, closed_pnl=0.0, liquidated=False, oid=1):
    f = {"coin": coin, "sz": str(sz), "px": str(px), "fee": str(fee),
        "crossed": crossed, "closedPnl": str(closed_pnl), "oid": oid, "side": "B"}
    if liquidated:
        f["liquidation"] = {"liquidatedUser": OUR_ADDR, "method": "market"}
    return f


# ── fee_summary ───────────────────────────────────────────────────────────

def test_fee_summary_splits_taker_maker_and_venue():
    fills = [
        _fill("BTC", 1, 100, fee=0.045, crossed=True),
        _fill("BTC", 1, 100, fee=0.015, crossed=False),
        _fill("xyz:CL", 1, 100, fee=0.009, crossed=True),
    ]
    s = pa.fee_summary(fills)
    assert s["n_fills"] == 3
    assert s["n_taker"] == 2 and s["n_maker"] == 1
    assert s["taker_pct"] == pytest.approx(200 / 3)
    assert s["fee_taker_usd"] == pytest.approx(0.054)
    assert s["fee_maker_usd"] == pytest.approx(0.015)
    assert s["fee_main_usd"] == pytest.approx(0.06)
    assert s["fee_xyz_usd"] == pytest.approx(0.009)
    assert s["n_main"] == 2 and s["n_xyz"] == 1


def test_fee_summary_empty_input():
    s = pa.fee_summary([])
    assert s["n_fills"] == 0 and s["taker_pct"] == 0.0


def test_fee_summary_all_taker_matches_live_evidence():
    """The account's actual 60d sample: 88/88 fills taker, $0 maker."""
    fills = [_fill("BTC", 1, 100, fee=0.045, crossed=True) for _ in range(88)]
    s = pa.fee_summary(fills)
    assert s["n_maker"] == 0
    assert s["taker_pct"] == 100.0
    assert s["fee_maker_usd"] == 0.0


# ── effective_fee_rate_by_venue / hip3_growth_mode_evidence ─────────────────

def test_effective_fee_rate_by_venue_matches_observed_hip3_discount():
    """Live evidence: main dex pays 0.045% taker, xyz HIP-3 pays 0.009% —
    a 5x discount ("growth mode")."""
    fills = [
        _fill("BTC", 1, 100, fee=0.045),       # 0.045% of $100
        _fill("xyz:CL", 1, 100, fee=0.009),    # 0.009% of $100
    ]
    rates = pa.effective_fee_rate_by_venue(fills)
    assert rates["main_dex_pct"] == pytest.approx(0.045)
    assert rates["xyz_hip3_pct"] == pytest.approx(0.009)


def test_effective_fee_rate_excludes_liquidation_fills():
    fills = [
        _fill("BTC", 1, 100, fee=0.045, liquidated=True),   # excluded
        _fill("BTC", 1, 100, fee=0.045),                     # included
    ]
    rates = pa.effective_fee_rate_by_venue(fills)
    assert rates["main_dex_notional_usd"] == pytest.approx(100.0)


def test_hip3_growth_mode_evidence_detects_discount():
    fills = [_fill("xyz:CL", 1, 100, fee=0.009)]
    ev = pa.hip3_growth_mode_evidence(fills, main_dex_taker_rate_pct=0.045)
    assert ev["is_discounted"] is True
    assert ev["discount_ratio"] == pytest.approx(0.2)


def test_hip3_growth_mode_evidence_no_discount_when_rates_match():
    fills = [_fill("xyz:CL", 1, 100, fee=0.045)]
    ev = pa.hip3_growth_mode_evidence(fills, main_dex_taker_rate_pct=0.045)
    assert ev["is_discounted"] is False


# ── taker_to_maker_savings (the maker_first business case) ──────────────────

def test_taker_to_maker_savings_ceiling():
    fills = [_fill("BTC", 1, 1000, crossed=True)]  # $1000 taker notional
    s = pa.taker_to_maker_savings(fills, taker_rate_pct=0.045, maker_rate_pct=0.015, window_days=30)
    # delta = 0.03% of $1000 = $0.30 over the window
    assert s["ceiling_savings_usd"] == pytest.approx(0.30)
    assert s["ceiling_savings_per_day_usd"] == pytest.approx(0.01)
    assert s["ceiling_savings_per_month_usd"] == pytest.approx(0.30)


def test_taker_to_maker_savings_ignores_maker_fills():
    fills = [_fill("BTC", 1, 1000, crossed=False)]
    s = pa.taker_to_maker_savings(fills, 0.045, 0.015, 30)
    assert s["taker_notional_usd"] == 0.0
    assert s["ceiling_savings_usd"] == 0.0


def test_maker_first_savings_report_splits_main_and_xyz_at_their_own_rates():
    """Live evidence: main pays 0.045% taker, xyz pays 0.009% (5x cheaper,
    growth mode). Blending them into one rate would overstate xyz savings —
    this must price each venue at its OWN observed taker rate."""
    fills = [
        _fill("BTC", 1, 1000, fee=0.45, crossed=True),        # main: $1000 notional
        _fill("xyz:CL", 1, 1000, fee=0.09, crossed=True),     # xyz: $1000 notional, 0.009% observed
    ]
    r = pa.maker_first_savings_report(fills, taker_rate_pct=0.045, maker_rate_pct=0.015, window_days=30)
    assert r["main_dex"]["taker_notional_usd"] == pytest.approx(1000.0)
    assert r["xyz_hip3"]["taker_notional_usd"] == pytest.approx(1000.0)
    # main delta = 0.045-0.015 = 0.03% of $1000 = $0.30
    assert r["main_dex"]["ceiling_savings_usd"] == pytest.approx(0.30)
    # xyz: observed taker 0.009%, assumed maker = 0.015% * (0.009/0.045) = 0.003%
    # delta = 0.006% of $1000 = $0.06
    assert r["xyz_maker_rate_assumed_pct"] == pytest.approx(0.003)
    assert r["xyz_hip3"]["ceiling_savings_usd"] == pytest.approx(0.06)
    assert r["combined_ceiling_savings_per_month_usd"] == pytest.approx(
        r["main_dex"]["ceiling_savings_per_month_usd"] + r["xyz_hip3"]["ceiling_savings_per_month_usd"])
    assert r["xyz_maker_rate_is_assumed"] is True


def test_maker_first_savings_report_main_only_matches_plain_taker_to_maker():
    """With no xyz fills, the report's main-dex leg must equal calling
    taker_to_maker_savings directly on the same fills/rates."""
    fills = [_fill("BTC", 1, 1000, fee=0.45, crossed=True)]
    r = pa.maker_first_savings_report(fills, 0.045, 0.015, 30)
    direct = pa.taker_to_maker_savings(fills, 0.045, 0.015, 30)
    assert r["main_dex"] == direct
    assert r["xyz_hip3"]["ceiling_savings_usd"] == 0.0


# ── liquidation_summary / realized_pnl_summary ───────────────────────────────

def test_liquidation_summary_matches_live_evidence_shape():
    """Live evidence: 8 of 88 fills over ~50.5d were liquidations, net ~-$17.36."""
    fills = [_fill("ZRO", 38.9, 0.85, fee=0.0149, closed_pnl=-2.76, liquidated=True),
            _fill("FARTCOIN", 227.6, 0.148, fee=0.0152, closed_pnl=-1.57, liquidated=True),
            _fill("BTC", 1, 100000, fee=0.01, closed_pnl=5.0, liquidated=False)]
    liq = pa.liquidation_summary(fills, OUR_ADDRESSES)
    assert liq["n_liquidations"] == 2
    assert liq["gross_pnl_usd"] == pytest.approx(-4.33)
    assert liq["coins"] == ["ZRO", "FARTCOIN"]


def test_liquidation_summary_ignores_other_accounts_liquidations():
    fills = [{"coin": "BTC", "fee": "0.01", "closedPnl": "-1.0",
             "liquidation": {"liquidatedUser": "0xSOMEONE_ELSE"}}]
    liq = pa.liquidation_summary(fills, OUR_ADDRESSES)
    assert liq["n_liquidations"] == 0


def test_realized_pnl_summary_computes_liquidation_share_of_loss():
    fills = [
        _fill("ZRO", 1, 1, fee=0.01, closed_pnl=-9.0, liquidated=True),
        _fill("BTC", 1, 1, fee=0.01, closed_pnl=-1.0, liquidated=False),
    ]
    s = pa.realized_pnl_summary(fills, OUR_ADDRESSES)
    assert s["net_pnl_usd"] == pytest.approx(-10.02)
    # liquidation net ~-9.01 of total net ~-10.02 -> ~90%
    assert s["liquidation_share_of_loss_pct"] == pytest.approx(89.9, abs=0.5)
    assert s["non_liquidation_net_pnl_usd"] == pytest.approx(-1.01)


def test_realized_pnl_summary_zero_loss_share_when_profitable():
    fills = [_fill("BTC", 1, 1, fee=0.01, closed_pnl=5.0)]
    s = pa.realized_pnl_summary(fills, OUR_ADDRESSES)
    assert s["liquidation_share_of_loss_pct"] == 0.0


# ── referral_discount_savings ────────────────────────────────────────────────

def test_referral_savings_when_not_applied():
    user_fees = {"activeReferralDiscount": "0.0", "feeSchedule": {"referralDiscount": "0.04"}}
    r = pa.referral_discount_savings(user_fees, monthly_fee_usd=10.0)
    assert r["already_applied"] is False
    assert r["savings_per_month_usd"] == pytest.approx(0.40)


def test_referral_savings_when_already_applied_reports_zero_further_savings():
    user_fees = {"activeReferralDiscount": "0.04", "feeSchedule": {"referralDiscount": "0.04"}}
    r = pa.referral_discount_savings(user_fees, monthly_fee_usd=10.0)
    assert r["already_applied"] is True
    assert r["savings_per_month_usd"] == 0.0


# ── staking_discount_tiers ───────────────────────────────────────────────────

def _user_fees_with_staking_tiers():
    return {
        "activeStakingDiscount": {"discount": "0.0"},
        "feeSchedule": {
            "stakingDiscountTiers": [
                {"bpsOfMaxSupply": "0.0", "discount": "0.0"},
                {"bpsOfMaxSupply": "0.0001", "discount": "0.05"},
                {"bpsOfMaxSupply": "0.001", "discount": "0.1"},
                {"bpsOfMaxSupply": "1.0", "discount": "0.3"},
                {"bpsOfMaxSupply": "5.0", "discount": "0.4"},
            ]
        },
    }


def test_staking_tiers_hype_amounts_match_known_thresholds():
    """At HYPE_MAX_SUPPLY=1e9, 0.0001 bps -> 10 HYPE, 0.001 bps -> 100 HYPE —
    the round numbers independently reported by HL docs / third-party guides."""
    tiers, active = pa.staking_discount_tiers(
        _user_fees_with_staking_tiers(), hype_price_usd=0.08, monthly_fee_usd=1.0)
    by_discount = {t["discount_frac"]: t for t in tiers}
    assert by_discount[0.05]["hype_needed"] == pytest.approx(10.0)
    assert by_discount[0.1]["hype_needed"] == pytest.approx(100.0)
    assert by_discount[0.3]["hype_needed"] == pytest.approx(100_000.0)
    assert active == 0.0


def test_staking_tiers_cost_and_savings_scale_correctly():
    tiers, _ = pa.staking_discount_tiers(
        _user_fees_with_staking_tiers(), hype_price_usd=2.0, monthly_fee_usd=100.0,
        equity_tiers=(1000.0,))
    tier_5pct = next(t for t in tiers if t["discount_frac"] == 0.05)
    assert tier_5pct["cost_usd"] == pytest.approx(20.0)          # 10 HYPE * $2
    assert tier_5pct["savings_per_month_usd_at_current_volume"] == pytest.approx(5.0)  # 5% of $100
    assert tier_5pct["pct_of_equity_locked"]["at_1000"] == pytest.approx(2.0)  # $20 / $1000


def test_staking_tiers_sorted_ascending_by_discount():
    tiers, _ = pa.staking_discount_tiers(_user_fees_with_staking_tiers(), 0.08, 1.0)
    assert [t["discount_frac"] for t in tiers] == sorted(t["discount_frac"] for t in tiers)


def test_staking_tiers_skips_zero_discount_row():
    tiers, _ = pa.staking_discount_tiers(_user_fees_with_staking_tiers(), 0.08, 1.0)
    assert all(t["discount_frac"] > 0 for t in tiers)


# ── small_account_friction ───────────────────────────────────────────────────

def test_small_account_friction_flags_below_floor():
    s = pa.small_account_friction(equity=15.18, min_tradable_equity_usd=26.0,
                                  min_order_usd=10.5, max_concurrent=3, daily_kill_usd=-3.15)
    assert s["below_tradable_floor"] is True
    assert s["shortfall_usd"] == pytest.approx(10.82)


def test_small_account_friction_not_flagged_when_funded():
    s = pa.small_account_friction(equity=100.0, min_tradable_equity_usd=26.0,
                                  min_order_usd=10.5, max_concurrent=3, daily_kill_usd=-3.15)
    assert s["below_tradable_floor"] is False
    assert s["shortfall_usd"] == 0.0


# ── position_liquidation_safety / liquidation_safety_check ──────────────────
# Reuses pathiel.agents.book_params.max_stop_pct_at_leverage for real — this
# is an integration point, not a reimplementation, so the test imports the
# real function rather than faking it.

def test_position_liquidation_safety_flags_stop_wider_than_liquidation_buffer():
    from pathiel.agents.book_params import max_stop_pct_at_leverage
    # ZRO-class coin: maxLeverage 5 -> maintenance 10%. At leverage 3 with
    # liq_safety_frac 0.85: widest = 0.85 * (1/3 - 0.10) = ~19.8%. A 25% stop
    # (e.g. a wide ATR stop on a volatile memecoin) sits OUTSIDE that.
    pos = {"coin": "ZRO", "leverage": 3, "stop_pct": 25.0}
    res = pa.position_liquidation_safety(
        pos, coin_max_leverage=5, max_frac_of_liq=0.60, liq_safety_frac=0.85,
        max_stop_pct_at_leverage_fn=max_stop_pct_at_leverage)
    assert res["safe"] is False
    assert res["widest_safe_stop_pct"] < 25.0


def test_position_liquidation_safety_passes_a_book_honored_stop():
    from pathiel.agents.book_params import max_stop_pct_at_leverage
    # xyz equities: maxLeverage 20 -> maintenance 2.5%. A 15% stop at 3x is
    # exactly the geometry the live books run (README: "$20/1x with a 15%
    # stop"; this checks the 3x ceiling case too).
    pos = {"coin": "xyz:NVDA", "leverage": 3, "stop_pct": 15.0}
    res = pa.position_liquidation_safety(
        pos, coin_max_leverage=20, max_frac_of_liq=0.60, liq_safety_frac=0.85,
        max_stop_pct_at_leverage_fn=max_stop_pct_at_leverage)
    assert res["safe"] is True


def test_liquidation_safety_check_returns_empty_when_all_safe():
    from pathiel.agents.book_params import max_stop_pct_at_leverage
    positions = [{"coin": "xyz:NVDA", "leverage": 3, "stop_pct": 15.0}]
    unsafe = pa.liquidation_safety_check(
        positions, {"xyz:NVDA": 20}, max_frac_of_liq=0.60, liq_safety_frac=0.85,
        max_stop_pct_at_leverage_fn=max_stop_pct_at_leverage)
    assert unsafe == []


def test_liquidation_safety_check_returns_empty_for_no_positions():
    from pathiel.agents.book_params import max_stop_pct_at_leverage
    assert pa.liquidation_safety_check(
        [], {}, max_frac_of_liq=0.60, liq_safety_frac=0.85,
        max_stop_pct_at_leverage_fn=max_stop_pct_at_leverage) == []


def test_liquidation_safety_check_flags_the_unsafe_one_only():
    from pathiel.agents.book_params import max_stop_pct_at_leverage
    positions = [
        {"coin": "xyz:NVDA", "leverage": 3, "stop_pct": 15.0},   # safe
        {"coin": "ZRO", "leverage": 3, "stop_pct": 25.0},        # unsafe
    ]
    unsafe = pa.liquidation_safety_check(
        positions, {"xyz:NVDA": 20, "ZRO": 5},
        max_frac_of_liq=0.60, liq_safety_frac=0.85,
        max_stop_pct_at_leverage_fn=max_stop_pct_at_leverage)
    assert [u["coin"] for u in unsafe] == ["ZRO"]


# ── funding_summary / cross_venue_funding_gap ────────────────────────────────

def test_funding_summary_matches_live_evidence_shape():
    """Live evidence: net POSITIVE funding, dominated by oil shorts/longs."""
    events = [
        {"delta": {"coin": "xyz:BRENTOIL", "usdc": "2.8048"}},
        {"delta": {"coin": "xyz:CL", "usdc": "0.5835"}},
        {"delta": {"coin": "xyz:HOOD", "usdc": "-0.0114"}},
    ]
    s = pa.funding_summary(events, window_days=60.0)
    assert s["total_usd"] == pytest.approx(3.3769)
    assert s["by_coin_usd"]["xyz:BRENTOIL"] == pytest.approx(2.8048)
    assert s["per_month_usd"] == pytest.approx(round(3.3769 / 60 * 30, 4))


def test_funding_summary_empty():
    s = pa.funding_summary([], window_days=60.0)
    assert s["total_usd"] == 0.0


def test_cross_venue_funding_gap_reports_hl_vs_others():
    predicted = [["BTC", [["HlPerp", {"fundingRate": "0.0000125"}],
                          ["BinPerp", {"fundingRate": "0.0001"}],
                          ["BybitPerp", {"fundingRate": "0.00005651"}]]]]
    rows = pa.cross_venue_funding_gap(predicted, ["BTC"])
    assert len(rows) == 1
    row = rows[0]
    assert row["coin"] == "BTC"
    assert row["hl_annualized_pct"] == pytest.approx(0.0000125 * 24 * 365 * 100)
    assert row["BinPerp_annualized_pct"] == pytest.approx(0.0001 * 24 * 365 * 100)


def test_cross_venue_funding_gap_skips_coins_with_no_data():
    assert pa.cross_venue_funding_gap([], ["BTC"]) == []


# ── book_roster_staleness ────────────────────────────────────────────────────

def test_book_roster_staleness_detects_missing_books():
    r = pa.book_roster_staleness(
        ("news_surge_short", "social_trending", "xs_reversal"),
        ("news_surge_short",),
    )
    assert r["is_stale"] is True
    assert r["missing_from_book_priority"] == ["social_trending", "xs_reversal"]


def test_book_roster_staleness_clean_when_fully_covered():
    r = pa.book_roster_staleness(("a", "b"), ("a", "b", "c"))
    assert r["is_stale"] is False
    assert r["missing_from_book_priority"] == []


def test_book_roster_staleness_against_real_pnl_by_book_script():
    """Regression pin: the audit found drawdown_ladder, social_trending and
    xs_reversal missing from pnl_by_book.py's BOOK_PRIORITY (2026-10-17).
    Fixed the same day; every live book must stay covered."""
    priority = pa.load_pnl_by_book_priority()
    r = pa.book_roster_staleness(pa.CURRENT_BOOK_ROSTER, priority)
    assert r["is_stale"] is False, r["missing_from_book_priority"]


# ── pair_trade_stats ──────────────────────────────────────────────────────

def test_pair_trade_stats_perfect_correlation_zero_pair_vol():
    a = [100, 101, 102, 101, 103, 104]
    b = [200, 202, 204, 202, 206, 208]   # exactly 2x a, perfectly correlated
    s = pa.pair_trade_stats(a, b)
    assert s["correlation"] == pytest.approx(1.0, abs=1e-6)
    assert s["pair_daily_stdev_pct"] == pytest.approx(0.0, abs=1e-6)


def test_pair_trade_stats_matches_live_evidence_shape():
    """Live evidence: ~0.95 correlation, pair vol well below either leg's."""
    import random
    random.seed(0)
    a = [100.0]
    b = [106.0]
    for _ in range(59):
        move = random.gauss(0, 0.02)
        a.append(a[-1] * (1 + move))
        b.append(b[-1] * (1 + move + random.gauss(0, 0.003)))
    s = pa.pair_trade_stats(a, b)
    assert s["correlation"] > 0.8
    assert s["vol_reduction_vs_leg_a_x"] > 1.5


def test_pair_trade_stats_insufficient_history():
    assert "error" in pa.pair_trade_stats([1, 2], [1, 2])


def test_pair_trade_stats_spread_z_score_zero_when_constant_spread():
    a = [100, 101, 102, 103]
    b = [106, 107, 108, 109]   # constant +6 spread
    s = pa.pair_trade_stats(a, b)
    assert s["spread_stdev"] == pytest.approx(0.0, abs=1e-9)
    assert s["spread_z_score"] == 0.0


# ── env helpers (never print a secret) ───────────────────────────────────────

def test_read_env_key_reads_only_the_named_key(tmp_path):
    env_file = tmp_path / ".env.local"
    env_file.write_text("HYPERLIQUID_PRIVATE_KEY=supersecret\nPATHIEL_STATE_DIR=/tmp/x\n")
    assert pa.read_env_key("PATHIEL_STATE_DIR", env_file) == "/tmp/x"
    # The function CAN read the private key if asked (it has no allowlist —
    # the discipline is in main() never asking for it), but it must not be
    # read incidentally while fetching an unrelated key.
    assert pa.read_env_key("PATHIEL_STATE_DIR", env_file) != "supersecret"


def test_read_env_key_missing_file_returns_empty(tmp_path):
    assert pa.read_env_key("ANYTHING", tmp_path / "does-not-exist.env") == ""


def test_resolve_account_addresses_prefers_master(tmp_path):
    env_file = tmp_path / ".env.local"
    env_file.write_text("HYPERLIQUID_MASTER_ADDRESS=0xMASTER\nHYPERLIQUID_WALLET_ADDRESS=0xWALLET\n")
    assert pa.primary_account_address(env_file) == "0xMASTER"


def test_resolve_account_addresses_falls_back_to_wallet(tmp_path):
    env_file = tmp_path / ".env.local"
    env_file.write_text("HYPERLIQUID_WALLET_ADDRESS=0xWALLET\n")
    assert pa.primary_account_address(env_file) == "0xWALLET"


# ── snapshot_state_dir: never reads the live dir directly ───────────────────

def test_snapshot_state_dir_copies_contents(tmp_path):
    src = tmp_path / "live"
    src.mkdir()
    (src / "state.json").write_text('{"a": 1}')
    copy_path = pa.snapshot_state_dir(str(src))
    assert copy_path != str(src)
    assert (Path(copy_path) / "state.json").read_text() == '{"a": 1}'


def test_snapshot_state_dir_handles_missing_source():
    copy_path = pa.snapshot_state_dir("/does/not/exist")
    assert Path(copy_path).is_dir()
    assert list(Path(copy_path).iterdir()) == []


# ═════════════════════════════════════════════════════════════════════════
# E2E: run_audit against a fixture state dir, every fetcher injected — the
# whole script, zero network, zero live-account access.
# ═════════════════════════════════════════════════════════════════════════

@pytest.fixture
def fixture_state_dir(tmp_path):
    state = tmp_path / "fixture_state"
    state.mkdir()
    (state / "shadow_ledger").mkdir()
    (state / "shadow_ledger" / "news_surge_short.jsonl").write_text(
        json.dumps({"book": "news_surge_short", "coin": "xyz:BIRD", "shadow": False}) + "\n")
    (state / "scan_integrity.json").write_text('{"ok": true}')
    return str(state)


@pytest.fixture
def fixture_fetchers():
    fills = [
        _fill("BTC", 0.001, 100000, fee=0.045, crossed=True),
        _fill("xyz:CL", 2.0, 100.0, fee=0.009, crossed=True, closed_pnl=3.0),
        _fill("ZRO", 10.0, 1.0, fee=0.01, crossed=True, closed_pnl=-9.0, liquidated=True),
    ]
    user_fees = {
        "userCrossRate": "0.00045", "userAddRate": "0.00015",
        "activeReferralDiscount": "0.0",
        "activeStakingDiscount": {"discount": "0.0"},
        "feeSchedule": {
            "referralDiscount": "0.04",
            "stakingDiscountTiers": [
                {"bpsOfMaxSupply": "0.0001", "discount": "0.05"},
                {"bpsOfMaxSupply": "0.001", "discount": "0.1"},
            ],
        },
    }
    funding = [{"delta": {"coin": "xyz:CL", "usdc": "0.5"}}]
    predicted = [["BTC", [["HlPerp", {"fundingRate": "0.0000125"}],
                         ["BinPerp", {"fundingRate": "0.0001"}]]]]
    ch_main = {"marginSummary": {"accountValue": "0.01"}, "assetPositions": []}
    ch_xyz = {"marginSummary": {"accountValue": "15.0"}, "assetPositions": []}
    spot_ctxs = [
        {"tokens": [{"index": 0, "name": "USDC"}, {"index": 150, "name": "HYPE"}],
         "universe": [{"name": "@107", "tokens": [150, 0]}]},
        [{"markPx": "0.08"}],
    ]
    candles_cl = [{"t": i, "c": str(100 + i * 0.1)} for i in range(10)]
    candles_bo = [{"t": i, "c": str(106 + i * 0.1)} for i in range(10)]
    return {
        "fills": lambda: fills,
        "user_fees": lambda: user_fees,
        "funding": lambda: funding,
        "predicted_fundings": lambda: predicted,
        "clearinghouse_main": lambda: ch_main,
        "clearinghouse_xyz": lambda: ch_xyz,
        "spot_meta_ctxs": lambda: spot_ctxs,
        "candles_cl": lambda: candles_cl,
        "candles_brentoil": lambda: candles_bo,
    }


def test_run_audit_e2e_against_fixture_state_dir(fixture_state_dir, fixture_fetchers):
    report = pa.run_audit(
        fixture_state_dir,
        config={"sl_atr_mult": 1.5, "max_concurrent": 3, "max_daily_loss_usd": -3.15},
        our_addresses=OUR_ADDRESSES,
        fetchers=fixture_fetchers,
        get_max_leverage_fn=lambda coin: 20,
        window_days=60.0,
        now_ms=1_760_000_000_000,
    )

    assert report["equity"]["total_usd"] == pytest.approx(15.01)
    assert report["fees"]["n_fills"] == 3
    assert report["liquidations"]["n_liquidations"] == 1
    assert report["liquidations"]["coins"] == ["ZRO"]
    assert report["hype_price_usd"] == pytest.approx(0.08)
    assert report["book_roster"]["is_stale"] is False  # fixed 2026-10-17
    assert "error" not in report["pair_trade_brent_wti"]
    assert report["liquidation_safety_open_positions"] == []   # no open positions in fixture

    # render + write, exactly what main() does, into a scratch out-dir
    md = pa.render_markdown(report)
    assert "Profit audit report" in md
    assert "liquidation" in md.lower()


def test_run_audit_e2e_writes_valid_json_and_md(fixture_state_dir, fixture_fetchers, tmp_path):
    report = pa.run_audit(
        fixture_state_dir, config={}, our_addresses=OUR_ADDRESSES,
        fetchers=fixture_fetchers, get_max_leverage_fn=lambda coin: 20,
        now_ms=1_760_000_000_000,
    )
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "report.json").write_text(json.dumps(report, indent=2, default=str))
    (out_dir / "report.md").write_text(pa.render_markdown(report))

    reloaded = json.loads((out_dir / "report.json").read_text())
    assert reloaded["equity"]["total_usd"] == pytest.approx(15.01)
    assert (out_dir / "report.md").read_text().startswith("# Profit audit report")


def test_run_audit_e2e_with_no_state_dir_still_runs(fixture_fetchers):
    """A None state_dir_src (e.g. PATHIEL_STATE_DIR unset) must not crash the
    audit — the state-dir reads are supplementary, not load-bearing for the
    fee/liquidation/staking computations."""
    report = pa.run_audit(
        None, config={}, our_addresses=OUR_ADDRESSES,
        fetchers=fixture_fetchers, get_max_leverage_fn=lambda coin: 20,
        now_ms=1_760_000_000_000,
    )
    assert report["fees"]["n_fills"] == 3


def test_run_audit_is_deterministic_given_fixed_input(fixture_state_dir, fixture_fetchers):
    """Same input, same output, by definition (CLAUDE.md's deterministic-space
    rule) — run it twice and diff."""
    kwargs = dict(config={"sl_atr_mult": 1.5}, our_addresses=OUR_ADDRESSES,
                  fetchers=fixture_fetchers, get_max_leverage_fn=lambda coin: 20,
                  now_ms=1_760_000_000_000)
    r1 = pa.run_audit(fixture_state_dir, **kwargs)
    r2 = pa.run_audit(fixture_state_dir, **kwargs)
    assert r1 == r2
