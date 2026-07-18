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


class _MakerStubApi(_StubApi):
    """Healthy exchange that also supports the maker-first (ALO) surface."""
    def __init__(self, mid=100.0, order_ok=True, sl_ok=True, maker_ok=True,
                 cancel_ok=True, status=None):
        super().__init__(mid=mid, order_ok=order_ok, sl_ok=sl_ok)
        self.maker_ok, self.cancel_ok = maker_ok, cancel_ok
        self.status = status or {"status": "open", "filled_sz": 0.0}
        self.maker_orders, self.status_calls, self.order_cancels = [], [], []

    def place_hl_maker_order(self, is_buy, size, mid_price, coin):
        self.maker_orders.append({"is_buy": is_buy, "size": size, "coin": coin})
        if not self.maker_ok:
            return {"ok": False, "error": "Post only order would have immediately matched"}
        px = mid_price * (0.999 if is_buy else 1.001)
        return {"ok": True, "order_id": "moid-1", "resting": True,
                "limit_px": px, "size": size}

    def order_fill_status(self, oid, coin):
        self.status_calls.append((oid, coin))
        return dict(self.status)

    def cancel_orders(self, oid, coin=None):
        self.order_cancels.append((oid, coin))
        return {"ok": self.cancel_ok}


class _ExplodingApi:
    """Any attribute access = an order-path touch. Shadow mode must never get here."""
    def __getattr__(self, name):
        raise AssertionError(f"order api touched in shadow mode: {name}")


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    """Fresh claims file + isolated DSL registry + shadow env by default."""
    monkeypatch.delenv("HERMES_V2_LIVE", raising=False)
    monkeypatch.setattr(ex, "_CLAIMS_PATH", str(tmp_path / "v2_claims.json"))
    monkeypatch.setattr(ex, "_PENDING_MAKERS_PATH", str(tmp_path / "pending.json"))
    ex.reset_claims_singleton()
    monkeypatch.setattr(ex, "_SL_RETRY_SLEEP_S", 0.0)
    monkeypatch.setattr(dsl, "load_state", lambda force=False: None)
    monkeypatch.setattr(dsl, "_save_state", lambda: None)
    dsl._active_positions.clear()
    yield
    dsl._active_positions.clear()
    ex.reset_claims_singleton()


@pytest.fixture()
def fills(monkeypatch):
    """Capture the self-grading meta_fill ledger rows instead of writing jsonl."""
    out = []
    monkeypatch.setattr(ex.v2_ledger, "record",
                        lambda book, **kw: out.append((book, kw)) or {})
    return out


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

    def test_pending_maker_sweep_refuses_in_shadow(self):
        """Even with a fabricated pending file, the sweep must not touch an api."""
        ex._save_pending({"AAA": {"book": "extreme_fade", "coin": "AAA",
                                  "side": "long", "oid": "moid-1", "size": 0.6,
                                  "limit_px": 99.9, "ref_px": 100.0,
                                  "signal_bar_t": 0, "stop_pct": 20.0,
                                  "hold_days": 3.0, "leverage": 12,
                                  "placed_ms": 0, "wait_min": 30.0}})
        assert ex.check_pending_makers({}, order_api=_ExplodingApi()) == []
        assert "AAA" in ex._load_pending()               # untouched, not dropped


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


# ── Maker-first entries (post-only at touch, IOC cross after the window) ──────

class TestMakerFirst:
    @pytest.fixture(autouse=True)
    def _arm(self, monkeypatch):
        monkeypatch.setenv("HERMES_V2_LIVE", "1")

    def test_entry_rests_alo_and_defers_execution(self, fills):
        api = _MakerStubApi(mid=100.0)
        res = _exec(_intent(), api)
        assert res["executed"] is False and res["reason"] == "maker_pending"
        assert res["pending_maker"] is True and res["order_id"] == "moid-1"
        assert len(api.maker_orders) == 1 and api.orders == []   # no IOC cross
        assert api.triggers == []                                # SL only on fill
        assert dsl._active_positions == {}                       # DSL only on fill
        assert ex.claims_registry().owner_of("AAA") == "extreme_fade"  # claim held
        p = ex._load_pending()["AAA"]
        assert p["oid"] == "moid-1" and p["limit_px"] == pytest.approx(99.9)
        assert p["wait_min"] == 30.0                             # default window
        assert fills == []                                       # nothing filled yet

    def test_resignal_while_pending_is_refused(self):
        api = _MakerStubApi()
        _exec(_intent(), api)
        res = _exec(_intent(), api)
        assert res["executed"] is False and res["reason"] == "maker_pending"
        assert len(api.maker_orders) == 1                        # no duplicate order
        assert ex.claims_registry().owner_of("AAA") == "extreme_fade"

    def test_filled_maker_finalizes_sl_dsl_and_meta(self, fills):
        api = _MakerStubApi(mid=100.0)
        _exec(_intent(stop=20.0, lev=12), api)
        api.status = {"status": "filled", "filled_sz": 0.6}
        out = ex.check_pending_makers(CFG, order_api=api)
        assert len(out) == 1 and out[0]["executed"] is True
        assert out[0]["fill_path"] == "maker"
        assert out[0]["entry_px"] == pytest.approx(99.9)         # filled AT the limit
        assert len(api.triggers) == 1                            # backup SL placed
        assert api.triggers[0]["px"] == pytest.approx(99.9 * 0.95)   # liq-capped 5%
        t = dsl._active_positions["AAA_long"]
        assert t.policy.max_loss_pct == 20.0 and t.policy.protect_pct == 9999.0
        assert ex._load_pending() == {}                          # pending consumed
        book, kw = fills[-1]
        assert book == "extreme_fade" and kw["side"] == "meta_fill"
        assert kw["horizon_days"] == 0.0                         # ungradeable by design
        m = kw["meta"]
        assert m["fill_path"] == "maker" and m["fill_px"] == pytest.approx(99.9)
        assert m["ref_px"] == pytest.approx(100.0)               # signal ref px
        assert m["slip_bps"] == pytest.approx(-10.0)             # filled BETTER than ref

    def test_window_expiry_cancels_and_crosses_ioc(self, fills):
        api = _MakerStubApi(mid=100.0)
        _exec(_intent(), api)
        placed = ex._load_pending()["AAA"]["placed_ms"]
        late = placed + int(30.5 * 60_000)
        out = ex.check_pending_makers(CFG, order_api=api, now_ms=late)
        assert api.order_cancels == [("moid-1", "AAA")]          # cancelled first
        assert len(api.orders) == 1                              # crossed via IOC path
        assert out[0]["executed"] is True and out[0]["fill_path"] == "taker"
        assert out[0]["entry_px"] == pytest.approx(100.0)        # IOC filled at mid
        assert "AAA_long" in dsl._active_positions
        assert ex._load_pending() == {}
        assert fills[-1][1]["meta"]["fill_path"] == "taker"
        assert fills[-1][1]["meta"]["slip_bps"] == pytest.approx(0.0)

    def test_within_window_stays_resting(self, fills):
        api = _MakerStubApi()
        _exec(_intent(), api)
        out = ex.check_pending_makers(CFG, order_api=api)        # now < window
        assert out == [] and api.order_cancels == [] and api.orders == []
        assert "AAA" in ex._load_pending()

    def test_partial_maker_fill_crosses_remainder_as_mixed(self, fills):
        api = _MakerStubApi(mid=100.0)
        _exec(_intent(), api)                                    # size 0.6
        api.status = {"status": "open", "filled_sz": 0.3}
        placed = ex._load_pending()["AAA"]["placed_ms"]
        out = ex.check_pending_makers(CFG, order_api=api,
                                      now_ms=placed + 31 * 60_000)
        assert out[0]["fill_path"] == "mixed"
        # 0.3 @ 99.9 (maker) + 0.3 @ 100.0 (IOC) = 99.95 blended
        assert out[0]["entry_px"] == pytest.approx(99.95)
        assert api.orders[0]["size"] == pytest.approx(0.3)       # only the remainder
        m = fills[-1][1]["meta"]
        assert m["maker_sz"] == pytest.approx(0.3) and m["taker_sz"] == pytest.approx(0.3)

    def test_dead_unfilled_releases_claim_and_records_none(self, fills):
        api = _MakerStubApi(order_ok=False)                      # IOC fallback fails too
        _exec(_intent(), api)
        api.status = {"status": "canceled", "filled_sz": 0.0}
        out = ex.check_pending_makers(CFG, order_api=api)
        assert out[0]["executed"] is False and out[0]["fill_path"] == "none"
        assert ex.claims_registry().claims() == {}               # claim released
        assert ex._load_pending() == {} and dsl._active_positions == {}
        assert fills[-1][1]["meta"]["fill_path"] == "none"

    def test_cancel_failure_never_crosses(self, fills):
        """The just-filled race: cancel fails -> do NOT cross; next sweep resolves."""
        api = _MakerStubApi(cancel_ok=False)
        _exec(_intent(), api)
        placed = ex._load_pending()["AAA"]["placed_ms"]
        out = ex.check_pending_makers(CFG, order_api=api,
                                      now_ms=placed + 31 * 60_000)
        assert out == [] and api.orders == []                    # no IOC fired
        assert "AAA" in ex._load_pending()                       # retried next sweep

    def test_maker_reject_falls_back_to_ioc_same_call(self, fills):
        api = _MakerStubApi(maker_ok=False)                      # ALO would cross
        res = _exec(_intent(), api)
        assert res["executed"] is True and res["fill_path"] == "taker"
        assert len(api.maker_orders) == 1 and len(api.orders) == 1
        assert fills[-1][1]["meta"]["fill_path"] == "taker"

    def test_maker_first_disabled_by_config(self, fills):
        api = _MakerStubApi()
        cfg = {**CFG, "entry": {"maker_first": False}}
        res = _exec(_intent(), api, cfg=cfg)
        assert res["executed"] is True and res["fill_path"] == "taker"
        assert api.maker_orders == []                            # ALO never attempted

    def test_taker_only_api_records_fill_meta_too(self, fills):
        """Plain IOC api (no ALO support): change still grades itself."""
        api = _StubApi(mid=100.0)
        intent = _intent()
        intent.entry_ref_px = 99.5
        res = _exec(intent, api)
        assert res["executed"] is True and res["fill_path"] == "taker"
        m = fills[-1][1]["meta"]
        assert m["fill_path"] == "taker"
        assert m["ref_px"] == pytest.approx(99.5)
        assert m["slip_bps"] == pytest.approx((100.0 - 99.5) / 99.5 * 1e4, rel=1e-3)

    def test_slippage_sign_convention(self):
        assert ex.slippage_bps("long", 100.0, 100.1) == pytest.approx(10.0)   # worse
        assert ex.slippage_bps("short", 100.0, 100.1) == pytest.approx(-10.0)  # better
        assert ex.slippage_bps("long", 0.0, 100.0) is None


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
