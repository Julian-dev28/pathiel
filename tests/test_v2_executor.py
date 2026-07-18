"""v2 gate tests — executor: shadow-mode-cannot-place-orders, claims exclusivity,
backup-SL cap arithmetic, live path end-to-end with a stub order api.
"""
from __future__ import annotations

import time

import pytest

import hermes_trader.agents.dsl_exit as dsl
import hermes_trader.v2.books as books
import hermes_trader.v2.executor as ex
from hermes_trader.v2.books import Intent

CFG = {"risk": {"max_daily_loss_pct": 0.15, "max_daily_loss_usd": -100},
       "backup_sl_max_frac_of_liq": 0.60}


def _intent(coin="AAA", side="long", notional=60.0, stop=20.0, hold=3.0, lev=12,
            book="extreme_fade"):
    return Intent(book=book, coin=coin, side=side, notional_usd=notional,
                  stop_pct=stop, hold_days=hold, leverage=lev)


class _StubApi:
    """Order-api stub recording every call; behaves like a healthy exchange."""
    def __init__(self, mid=100.0, order_ok=True, sl_ok=True):
        self.mid, self.order_ok, self.sl_ok = mid, order_ok, sl_ok
        self.orders, self.triggers, self.cancels, self.leverage_calls = [], [], [], []

    def get_hl_price(self, coin):
        return self.mid

    def min_entry_notional_usd(self, coin, mid):
        return 10.5

    def entry_size_for_notional(self, coin, notional, mid):
        return round(notional / mid, 6)

    def get_max_leverage(self, coin):
        return 50

    def set_leverage(self, coin, lev):
        self.leverage_calls.append((coin, lev))

    def place_hl_order(self, is_buy, size, mid_price, coin, reduce_only=False):
        self.orders.append({"is_buy": is_buy, "size": size, "coin": coin,
                            "reduce_only": reduce_only})
        if not self.order_ok:
            return {"ok": False, "error": "stub_reject"}
        return {"ok": True, "order_id": "oid-1", "avg_px": mid_price, "total_sz": size}

    def place_hl_trigger_order(self, is_buy, size, px, kind, coin):
        self.triggers.append({"kind": kind, "px": px, "coin": coin, "size": size})
        return {"ok": self.sl_ok}

    def cancel_open_orders_for_coin(self, coin):
        self.cancels.append(coin)
        return 0


class _ExplodingApi:
    """Any attribute access = an order-path touch. Shadow mode must never get here."""
    def __getattr__(self, name):
        raise AssertionError(f"order api touched in shadow mode: {name}")


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    """Fresh claims file + isolated DSL registry + shadow env by default."""
    monkeypatch.delenv("HERMES_V2_LIVE", raising=False)
    monkeypatch.setattr(ex, "_CLAIMS_PATH", str(tmp_path / "v2_claims.json"))
    ex.reset_claims_singleton()
    monkeypatch.setattr(ex, "_SL_RETRY_SLEEP_S", 0.0)
    monkeypatch.setattr(dsl, "load_state", lambda force=False: None)
    monkeypatch.setattr(dsl, "_save_state", lambda: None)
    dsl._active_positions.clear()
    yield
    dsl._active_positions.clear()
    ex.reset_claims_singleton()


def _exec(intent, api, **over):
    kw = dict(cfg=CFG, equity=150.0, available=150.0, held_coins=set(),
              total_open_notional=0.0, day_volume_usd=1e8, daily_pnl=0.0,
              order_api=api)
    kw.update(over)
    return ex.execute_intent(intent, **kw)


# ── THE shadow wall ───────────────────────────────────────────────────────────

class TestShadowCannotPlaceOrders:
    def test_execute_refuses_before_touching_any_api(self):
        res = _exec(_intent(), _ExplodingApi())
        assert res == {"executed": False, "reason": "shadow_mode_order_blocked",
                       "book": "extreme_fade", "coin": "AAA"}

    def test_order_layer_import_is_gated(self):
        with pytest.raises(ex.ShadowModeViolation):
            ex._order_api()

    def test_close_refuses_in_shadow(self):
        res = ex.close_coin("AAA", order_api=_ExplodingApi())
        assert res["ok"] is False and res["reason"] == "shadow_mode_order_blocked"

    def test_flatten_refuses_in_shadow(self):
        res = ex.flatten_all({"asset_positions": [{"position": {"coin": "AAA", "szi": 1}}]},
                             order_api=_ExplodingApi())
        assert res == [{"ok": False, "reason": "shadow_mode_order_blocked"}]

    def test_shadow_leaves_no_claim_and_no_tracker(self):
        _exec(_intent(), _ExplodingApi())
        assert ex.claims_registry().claims() == {}
        assert dsl._active_positions == {}

    def test_env_must_be_exactly_1(self, monkeypatch):
        for bad in ("true", "yes", "0", ""):
            monkeypatch.setenv("HERMES_V2_LIVE", bad)
            assert not ex.live_enabled()
        monkeypatch.setenv("HERMES_V2_LIVE", "1")
        assert ex.live_enabled()


# ── Live path (env armed, stub api) ───────────────────────────────────────────

class TestLivePath:
    @pytest.fixture(autouse=True)
    def _arm(self, monkeypatch):
        monkeypatch.setenv("HERMES_V2_LIVE", "1")

    def test_full_open_places_order_sl_and_registers_dsl(self):
        api = _StubApi(mid=100.0)
        res = _exec(_intent(stop=20.0, lev=12), api)
        assert res["executed"] is True
        assert len(api.orders) == 1 and api.orders[0]["is_buy"] is True
        # Backup SL: 20% intent stop capped at 0.60/12 = 5% of entry → 95.0
        assert len(api.triggers) == 1
        assert api.triggers[0]["px"] == pytest.approx(95.0)
        assert res["backup_sl_capped"] is True
        t = dsl._active_positions["AAA_long"]
        assert t.policy.max_loss_pct == 20.0
        assert t.policy.protect_pct == 9999.0                  # stop-or-horizon: no trail
        assert t.policy.hard_timeout_minutes == 3.0 * 1440.0
        assert ex.claims_registry().owner_of("AAA") == "extreme_fade"

    def test_claims_exclusivity_second_book_denied(self):
        api = _StubApi()
        assert _exec(_intent(book="extreme_fade"), api)["executed"] is True
        res = _exec(_intent(book="funding_spike_short", side="short"), api,
                    held_coins=set())
        assert res["executed"] is False and "claimed_by_extreme_fade" in res["reason"]
        assert len(api.orders) == 1                            # no second order

    def test_inactive_book_denied_by_registry(self):
        res = _exec(_intent(book="premium_fade"), _StubApi())
        assert res["executed"] is False and "claimed_by" in res["reason"]
        assert ex.claims_registry().claims() == {}

    def test_failed_order_releases_claim(self):
        api = _StubApi(order_ok=False)
        res = _exec(_intent(), api)
        assert res["executed"] is False and "order_failed" in res["reason"]
        assert ex.claims_registry().claims() == {}             # claim released
        assert dsl._active_positions == {}                     # no phantom tracker

    def test_sl_failure_retries_once_and_flags(self):
        api = _StubApi(sl_ok=False)
        res = _exec(_intent(), api)
        assert res["executed"] is True and res["sl_missing"] is True
        assert len(api.triggers) == 2                          # one retry

    def test_risk_gates_run_before_order(self):
        api = _StubApi()
        res = _exec(_intent(side="short"), api, day_volume_usd=5e6)   # thin short
        assert res["executed"] is False and res["reason"] == "risk_gates"
        assert any("liquidity_floor" in r for r in res["blocked_by"])
        assert api.orders == []

    def test_held_coin_refused(self):
        api = _StubApi()
        res = _exec(_intent(), api, held_coins={"AAA"})
        assert res["executed"] is False and res["reason"] == "already_held"
        assert api.orders == []

    def test_close_is_reduce_only_and_cleans_up(self):
        api = _StubApi()
        assert _exec(_intent(), api)["executed"] is True
        state = {"asset_positions": [{"position": {"coin": "AAA", "szi": "0.6",
                                                   "entryPx": "100.0"}}]}
        res = ex.close_coin("AAA", book="extreme_fade", order_api=api,
                            account_state=state)
        assert res["ok"] is True
        assert api.orders[-1]["reduce_only"] is True           # can never flip
        assert api.orders[-1]["is_buy"] is False               # closing a long = sell
        assert "AAA" in api.cancels                            # SL bracket cancelled
        assert dsl._active_positions == {}
        assert ex.claims_registry().claims() == {}


# ── Backup-SL cap arithmetic (pure) ───────────────────────────────────────────

class TestBackupSlPrice:
    def test_uncapped_at_1x(self):
        px, capped = ex.backup_sl_price(100.0, 20.0, True, 1)
        assert px == pytest.approx(80.0) and not capped

    def test_liq_cap_at_12x(self):
        """The audit case: a 20% stop at 12x becomes 0.60/12 = 5% on-exchange."""
        px, capped = ex.backup_sl_price(100.0, 20.0, True, 12)
        assert px == pytest.approx(95.0) and capped

    def test_short_side_mirrors(self):
        px, capped = ex.backup_sl_price(100.0, 15.0, False, 1)
        assert px == pytest.approx(115.0) and not capped
        px, capped = ex.backup_sl_price(100.0, 15.0, False, 12)
        assert px == pytest.approx(105.0) and capped

    def test_book_exit_policy_shape(self):
        p = ex.book_exit_policy(15.0, 5.0, leverage=12)
        assert p.max_loss_pct == 15.0
        assert p.max_loss_roe_pct == 180.0                     # spot cap binds: 180/12=15
        assert p.protect_pct == 9999.0
        assert p.hard_timeout_minutes == 7200.0
        assert p.atr_stop_enabled is False and p.noise_band_enabled is False
