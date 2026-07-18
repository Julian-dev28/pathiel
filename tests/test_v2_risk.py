"""v2 gate tests — risk: kill-switch SOD math + UTC-date persistence, gross cap,
liquidity floors, margin preflight, composed entry gates.
"""
from __future__ import annotations

import json

import pytest

import hermes_trader.v2.risk as risk

# 2026-07-18 12:00:00 UTC and a same-day later instant + the next UTC day.
T_NOON = 1_784_376_000.0
T_LATER = T_NOON + 6 * 3600
T_NEXT_DAY = T_NOON + 24 * 3600
RISK_CFG = {"max_daily_loss_pct": 0.15, "max_daily_loss_usd": -100}


class TestStartOfDayPersistence:
    def test_first_sight_baselines_and_persists(self, tmp_path):
        p = str(tmp_path / "sod.json")
        assert risk.start_of_day_equity(20.0, T_NOON, p) == 20.0
        saved = json.loads(open(p).read())
        assert saved["equity"] == 20.0 and saved["date"] == risk.utc_date(T_NOON)

    def test_midday_restart_cannot_rebaseline(self, tmp_path):
        """THE SOD-reset laundering fix: equity dropped to 17, process restarted —
        the baseline must stay 20 so the -15% kill floor still sees the -$3 day."""
        p = str(tmp_path / "sod.json")
        risk.start_of_day_equity(20.0, T_NOON, p)
        assert risk.start_of_day_equity(17.0, T_LATER, p) == 20.0   # NOT 17

    def test_utc_roll_rebaselines(self, tmp_path):
        p = str(tmp_path / "sod.json")
        risk.start_of_day_equity(20.0, T_NOON, p)
        assert risk.start_of_day_equity(17.0, T_NEXT_DAY, p) == 17.0

    def test_degraded_read_never_clobbers(self, tmp_path):
        p = str(tmp_path / "sod.json")
        risk.start_of_day_equity(20.0, T_NOON, p)
        assert risk.start_of_day_equity(0.0, T_LATER, p) == 20.0
        assert risk.start_of_day_equity(0.0, T_NEXT_DAY, p) == 20.0   # keep old baseline
        assert json.loads(open(p).read())["equity"] == 20.0

    def test_no_file_and_degraded_read_is_unusable(self, tmp_path):
        assert risk.start_of_day_equity(0.0, T_NOON, str(tmp_path / "none.json")) == 0.0


class TestKillSwitch:
    def test_breach_at_exactly_minus_15pct_of_sod(self):
        ks = risk.kill_switch(RISK_CFG, equity=17.0, sod_equity=20.0)
        assert ks["limit_usd"] == pytest.approx(-3.0)          # 15% of $20 SOD
        assert ks["daily_pnl"] == pytest.approx(-3.0)
        assert ks["breached"] is True

    def test_no_breach_just_above_the_floor(self):
        ks = risk.kill_switch(RISK_CFG, equity=17.1, sod_equity=20.0)
        assert ks["breached"] is False

    def test_scales_with_account_both_directions(self):
        big = risk.kill_switch(RISK_CFG, equity=200.0, sod_equity=260.0)
        assert big["limit_usd"] == pytest.approx(-39.0)        # 15% of $260
        assert big["breached"] is True                         # -$60 <= -$39
        tiny = risk.kill_switch(RISK_CFG, equity=18.0, sod_equity=19.0)
        assert tiny["limit_usd"] == pytest.approx(-2.85)
        assert tiny["breached"] is False                       # -$1 > -$2.85

    def test_pct_zero_falls_back_to_usd(self):
        ks = risk.kill_switch({"max_daily_loss_pct": 0.0, "max_daily_loss_usd": -5.0},
                              equity=13.0, sod_equity=19.0)
        assert ks["limit_usd"] == -5.0 and ks["breached"] is True

    def test_degraded_read_is_not_a_crash(self):
        """project_partial_dex_degraded_read: equity 0 must not read as breach."""
        ks = risk.kill_switch(RISK_CFG, equity=0.0, sod_equity=20.0)
        assert ks["breached"] is False and ks["degraded"] is True


class TestPureGates:
    def test_margin_floor(self):
        assert risk.margin_ok(100.0, 10.0) is True             # exactly 10%
        assert risk.margin_ok(100.0, 9.9) is False
        assert risk.margin_ok(0.0, 100.0) is False

    def test_gross_cap_300pct(self):
        assert risk.gross_cap_ok(40.0, 17.0, 19.0) is True     # $57 = 300% of $19
        assert risk.gross_cap_ok(40.0, 17.1, 19.0) is False
        assert risk.gross_cap_ok(0.0, 10.0, 0.0) is False

    def test_liquidity_floors(self):
        assert risk.liquidity_floor_ok("long", 5e6) is True
        assert risk.liquidity_floor_ok("long", 4.9e6) is False
        assert risk.liquidity_floor_ok("short", 20e6) is True
        assert risk.liquidity_floor_ok("short", 19e6) is False # $20M short floor

    def test_entry_gates_pass_clean(self):
        assert risk.entry_gates(side="long", notional_usd=10.5, day_volume_usd=1e8,
                                equity=19.0, available=19.0, total_open_notional=0.0,
                                daily_pnl=0.0, risk_cfg=RISK_CFG) == []

    def test_entry_gates_collect_every_failure(self):
        reasons = risk.entry_gates(side="short", notional_usd=8.0, day_volume_usd=1e6,
                                   equity=19.0, available=0.5, total_open_notional=60.0,
                                   daily_pnl=-4.0, risk_cfg=RISK_CFG)
        text = " ".join(reasons)
        for tag in ("daily_loss_kill_switch", "insufficient_free_margin",
                    "gross_notional_cap", "liquidity_floor", "below_min_order"):
            assert tag in text, f"missing gate: {tag} in {reasons}"

    def test_min_order_parity_with_exchange_layer(self):
        from hermes_trader.client.exchange import MIN_ORDER_USD as REAL
        assert risk.MIN_ORDER_USD == REAL
