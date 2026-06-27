"""Tests for:
1. ClaimsRegistry — cross-book coin coordination (Task 1)
2. strategy_book_equity_frac sizing in executor.maybe_execute (Task 2)
"""
from __future__ import annotations

import json
import tempfile
import types
import unittest.mock as mock
from pathlib import Path

import pytest

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _pos(coin: str, szi: float):
    return {"position": {"coin": coin, "szi": szi}}


def _uni(coins):
    return [{"coin": c, "dayNtlVlm": 1e8, "type": "perp"} for c in coins]


# ═══════════════════════════════════════════════════════════════════════════════
# TASK 1: ClaimsRegistry unit tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestClaimsRegistryUnit:
    """Low-level ClaimsRegistry API tests."""

    def _make(self, tmp_path) -> "ClaimsRegistry":
        from hermes_trader.agents.rebalancer_owned import ClaimsRegistry
        return ClaimsRegistry(str(tmp_path / "claims.json")).load()

    def test_claim_succeeds_on_unclaimed_coin(self, tmp_path):
        cr = self._make(tmp_path)
        assert cr.claim("BTC", "xs_momentum") is True

    def test_claim_succeeds_when_same_book_reclaims(self, tmp_path):
        cr = self._make(tmp_path)
        cr.claim("BTC", "xs_momentum")
        assert cr.claim("BTC", "xs_momentum") is True

    def test_claim_denied_when_other_book_holds(self, tmp_path):
        cr = self._make(tmp_path)
        cr.claim("BTC", "xs_momentum")
        assert cr.claim("BTC", "other_book") is False

    def test_release_frees_claim(self, tmp_path):
        cr = self._make(tmp_path)
        cr.claim("ETH", "xs_momentum")
        cr.release("ETH", "xs_momentum")
        # Now another book can claim it
        assert cr.claim("ETH", "other_book") is True

    def test_release_noop_for_wrong_book(self, tmp_path):
        cr = self._make(tmp_path)
        cr.claim("SOL", "xs_momentum")
        cr.release("SOL", "other_book")   # wrong book — should not release
        assert cr.owner_of("SOL") == "xs_momentum"

    def test_release_all_drops_all_owned_by_book(self, tmp_path):
        cr = self._make(tmp_path)
        cr.claim("BTC", "xs_momentum")
        cr.claim("ETH", "xs_momentum")
        cr.claim("SOL", "other_book")
        cr.release_all("xs_momentum")
        assert cr.owner_of("BTC") is None
        assert cr.owner_of("ETH") is None
        assert cr.owner_of("SOL") == "other_book"   # untouched

    def test_claimed_by_others_excludes_own_coins(self, tmp_path):
        cr = self._make(tmp_path)
        cr.claim("BTC", "xs_momentum")
        cr.claim("ETH", "other_book")
        cr.claim("SOL", "xs_momentum")
        others = cr.claimed_by_others("xs_momentum")
        assert "ETH" in others          # other book's coin blocks xs_momentum
        assert "BTC" not in others      # xs_momentum's own coin → not blocked
        assert "SOL" not in others      # xs_momentum's own coin → not blocked

    def test_claimed_by_others_empty_when_nothing_else_claimed(self, tmp_path):
        cr = self._make(tmp_path)
        cr.claim("BTC", "xs_momentum")
        others = cr.claimed_by_others("xs_momentum")
        assert others == set()

    def test_prune_to_releases_stopped_coins(self, tmp_path):
        cr = self._make(tmp_path)
        cr.claim("BTC", "xs_momentum")
        cr.claim("ETH", "xs_momentum")
        # BTC was stopped out — live only has ETH
        cr.prune_to({"ETH"}, "xs_momentum")
        assert cr.owner_of("BTC") is None      # pruned
        assert cr.owner_of("ETH") == "xs_momentum"   # still live → kept

    def test_prune_to_only_affects_given_book(self, tmp_path):
        cr = self._make(tmp_path)
        cr.claim("BTC", "xs_momentum")
        cr.claim("ETH", "other_book")
        # Live coins are empty, but prune only touches xs_momentum
        cr.prune_to(set(), "xs_momentum")
        assert cr.owner_of("BTC") is None      # xs_momentum's coin → pruned
        assert cr.owner_of("ETH") == "other_book"   # different book → untouched

    def test_save_and_reload(self, tmp_path):
        path = str(tmp_path / "claims.json")
        from hermes_trader.agents.rebalancer_owned import ClaimsRegistry
        cr1 = ClaimsRegistry(path).load()
        cr1.claim("BTC", "xs_momentum")
        cr1.claim("ETH", "other_book")
        cr1.save()

        cr2 = ClaimsRegistry(path).load()
        assert cr2.owner_of("BTC") == "xs_momentum"
        assert cr2.owner_of("ETH") == "other_book"

    def test_load_missing_file_starts_empty(self, tmp_path):
        from hermes_trader.agents.rebalancer_owned import ClaimsRegistry
        cr = ClaimsRegistry(str(tmp_path / "nonexistent.json")).load()
        assert cr.claimed_by_others("any_book") == set()

    def test_load_corrupt_file_starts_empty(self, tmp_path):
        from hermes_trader.agents.rebalancer_owned import ClaimsRegistry
        p = tmp_path / "bad.json"
        p.write_text("NOT JSON {{{")
        cr = ClaimsRegistry(str(p)).load()
        assert cr.claimed_by_others("any_book") == set()

    def test_active_book_load_scrubs_stale_owners(self, tmp_path):
        from hermes_trader.agents.rebalancer_owned import ClaimsRegistry

        p = tmp_path / "claims.json"
        p.write_text(json.dumps({
            "claims": {
                "BTC": "vol_dispersion",
                "ETH": "xs_momentum",
                "SOL": "sortino_factor",
                "ALT": "rally_exhaustion",
            }
        }))

        cr = ClaimsRegistry(
            str(p),
            active_books={"xs_momentum", "rally_exhaustion"},
        ).load()

        assert cr.owner_of("BTC") is None
        assert cr.owner_of("SOL") is None
        assert cr.owner_of("ETH") == "xs_momentum"
        assert cr.owner_of("ALT") == "rally_exhaustion"
        saved = json.loads(p.read_text())
        assert saved["claims"] == {"ALT": "rally_exhaustion", "ETH": "xs_momentum"}

    def test_active_book_registry_denies_inactive_claim_owner(self, tmp_path):
        from hermes_trader.agents.rebalancer_owned import ClaimsRegistry

        cr = ClaimsRegistry(
            str(tmp_path / "claims.json"),
            active_books={"xs_momentum", "rally_exhaustion"},
        ).load()

        assert cr.claim("BTC", "sortino_factor") is False
        assert cr.owner_of("BTC") is None

    def test_claimed_by_others_self_heals_stale_in_memory_owner(self, tmp_path):
        from hermes_trader.agents.rebalancer_owned import ClaimsRegistry

        cr = ClaimsRegistry(
            str(tmp_path / "claims.json"),
            active_books={"xs_momentum", "rally_exhaustion"},
        ).load()
        cr.claim("ETH", "rally_exhaustion")
        cr._claims["BTC"] = "vol_dispersion"
        cr._claims["SOL"] = "sortino_factor"

        blocked = cr.claimed_by_others("xs_momentum")

        assert blocked == {"ETH"}
        assert cr.owner_of("BTC") is None
        assert cr.owner_of("SOL") is None
        saved = json.loads((tmp_path / "claims.json").read_text())
        assert saved["claims"] == {"ETH": "rally_exhaustion"}

    def test_prune_claims_to_live_releases_non_live_claims(self, tmp_path, monkeypatch):
        import hermes_trader.agents.rebalancer_owned as ro

        path = str(tmp_path / "claims.json")
        cr = ro.ClaimsRegistry(path, active_books=ro.active_claim_books()).load()
        cr.claim("A", "xs_momentum")
        cr.claim("B", "rally_exhaustion")
        cr.claim("C", "hail_mary_short")
        cr.save()
        monkeypatch.setattr(ro, "_claims_registry", cr)

        dropped = ro.prune_claims_to_live([_pos("B", -1.0)])

        assert dropped == {"A": "xs_momentum", "C": "hail_mary_short"}
        assert cr.claims() == {"B": "rally_exhaustion"}
        saved = json.loads((tmp_path / "claims.json").read_text())
        assert saved["claims"] == {"B": "rally_exhaustion"}

    def test_active_claim_books_include_live_claimants(self):
        from hermes_trader.agents.rebalancer_owned import active_claim_books
        import hermes_trader.agents.hail_mary_short_live as hms
        import hermes_trader.agents.xs_momentum_live as xl
        import hermes_trader.agents.rally_exhaustion_live as rel

        active = active_claim_books()
        assert hms._BOOK_NAME in active
        assert xl._BOOK_NAME in active
        assert rel._BOOK_NAME in active


# ─────────────────────────────────────────────────────────────────────────────
# Integration: exclusion propagates through xs_momentum_live target-book
# ─────────────────────────────────────────────────────────────────────────────

# Minimal fetch factory for xs_momentum (needs lb+1 bars with lb=1 → 2 bars)
def _fetch_factory(rets):
    def fetch(coin, interval, n):
        r = rets.get(coin, 0.0)
        return [{"t": 0, "o": 100, "h": 100, "l": 100, "c": 100, "v": 1},
                {"t": 1, "o": 100 * (1 + r), "h": 100 * (1 + r), "l": 100 * (1 + r),
                 "c": 100 * (1 + r), "v": 1}]
    return fetch


_XS_CFG = {"xs_momentum": {"enabled": True, "lookback_days": 1,
                            "hold_days": 10, "k_per_leg": 2, "universe_top_n": 50,
                            "min_volume_usd": 1e6, "vol_gate": False}}
_RETS = {"A": 0.50, "B": 0.20, "C": 0.05, "D": -0.05, "E": -0.20, "F": -0.40}


class TestCrossBookExclusion:
    """Verify that xs_momentum excludes coins claimed by another book."""

    def test_coin_claimed_by_other_book_is_not_opened(self, tmp_path, monkeypatch):
        """If another book already claimed 'A', xs_momentum must not open 'A'."""
        import hermes_trader.agents.xs_momentum_live as xl
        import hermes_trader.agents.rebalancer_owned as ro

        # Point claims registry to tmp path
        claims_path = str(tmp_path / ".rebalancer_claims.json")
        new_registry = ro.ClaimsRegistry(claims_path).load()
        new_registry.claim("A", "other_book")  # A is the best long normally
        new_registry.save()
        monkeypatch.setattr(ro, "_claims_registry", new_registry)

        monkeypatch.setattr(xl, "_OWNED_FILE", str(tmp_path / ".xs_momentum_positions.json"))
        monkeypatch.setattr(xl, "_owned", None)
        monkeypatch.setattr(xl, "_last_ts", lambda: 0.0)
        monkeypatch.setattr(xl, "_save_ts", lambda t: None)
        monkeypatch.setattr(xl, "log_event", lambda e: None)

        opened = []
        xl.maybe_rebalance(
            _XS_CFG, _uni(_RETS), [],
            _fetch_factory(_RETS),
            lambda a: opened.append(a["coin"]),
            lambda c: None,
        )

        # A was claimed by another book → xs_momentum must not open it
        assert "A" not in opened, f"xs_momentum opened A despite another book's claim: {opened}"
        # B (next best long) should be opened instead
        assert "B" in opened, f"Expected B opened as fallback; got: {opened}"

    def test_own_claimed_coins_are_still_opened(self, tmp_path, monkeypatch):
        """xs_momentum's OWN claimed coins (from a prior rebalance) don't block itself."""
        import hermes_trader.agents.xs_momentum_live as xl
        import hermes_trader.agents.rebalancer_owned as ro

        claims_path = str(tmp_path / ".rebalancer_claims.json")
        new_registry = ro.ClaimsRegistry(claims_path).load()
        new_registry.claim("A", "xs_momentum")  # xs_momentum already owns A
        new_registry.save()
        monkeypatch.setattr(ro, "_claims_registry", new_registry)

        monkeypatch.setattr(xl, "_OWNED_FILE", str(tmp_path / ".xs_momentum_positions.json"))
        monkeypatch.setattr(xl, "_owned", None)
        monkeypatch.setattr(xl, "_last_ts", lambda: 0.0)
        monkeypatch.setattr(xl, "_save_ts", lambda t: None)
        monkeypatch.setattr(xl, "log_event", lambda e: None)

        opened = []
        xl.maybe_rebalance(
            _XS_CFG, _uni(_RETS), [],
            _fetch_factory(_RETS),
            lambda a: opened.append(a["coin"]),
            lambda c: None,
        )

        # A is in the target and owned by this book → should still be opened
        assert "A" in opened, f"xs_momentum should open A (self-owned claim); got: {opened}"

    def test_claim_registered_on_open_live(self, tmp_path, monkeypatch):
        """After a LIVE open, the coin should appear in the claims file as owned by xs_momentum."""
        import hermes_trader.agents.xs_momentum_live as xl
        import hermes_trader.agents.rebalancer_owned as ro

        claims_path = str(tmp_path / ".rebalancer_claims.json")
        new_registry = ro.ClaimsRegistry(claims_path).load()
        monkeypatch.setattr(ro, "_claims_registry", new_registry)

        monkeypatch.setattr(xl, "_OWNED_FILE", str(tmp_path / ".xs_momentum_positions.json"))
        monkeypatch.setattr(xl, "_owned", None)
        monkeypatch.setattr(xl, "_last_ts", lambda: 0.0)
        monkeypatch.setattr(xl, "_save_ts", lambda t: None)
        monkeypatch.setattr(xl, "log_event", lambda e: None)

        xl.maybe_rebalance(
            _XS_CFG, _uni(_RETS), [],
            _fetch_factory(_RETS),
            lambda a: None,  # simulate execute succeeding
            lambda c: None,
        )

        # Read the saved claims file and verify xs_momentum claimed its opens
        with open(claims_path) as fh:
            saved = json.load(fh)
        claims = saved.get("claims", {})
        # At least one claim should be by xs_momentum
        assert any(v == "xs_momentum" for v in claims.values()), \
            f"No xs_momentum claims found after LIVE rebalance: {claims}"

    def test_claim_released_on_close_live(self, tmp_path, monkeypatch):
        """When xs_momentum closes a coin, the claim is released from the registry."""
        import hermes_trader.agents.xs_momentum_live as xl
        import hermes_trader.agents.rebalancer_owned as ro

        claims_path = str(tmp_path / ".rebalancer_claims.json")
        owned_path = str(tmp_path / ".xs_momentum_positions.json")

        # Pre-state: xs_momentum previously owned B (long), has a claim on it
        with open(owned_path, "w") as fh:
            json.dump({"longs": ["B"], "shorts": []}, fh)
        new_registry = ro.ClaimsRegistry(claims_path).load()
        new_registry.claim("B", "xs_momentum")
        new_registry.save()
        monkeypatch.setattr(ro, "_claims_registry", new_registry)

        monkeypatch.setattr(xl, "_OWNED_FILE", owned_path)
        monkeypatch.setattr(xl, "_owned", None)
        monkeypatch.setattr(xl, "_last_ts", lambda: 0.0)
        monkeypatch.setattr(xl, "_save_ts", lambda t: None)
        monkeypatch.setattr(xl, "log_event", lambda e: None)

        # Universe that forces B out (B not in rets → not in target)
        custom_rets = {"A": 0.50, "X": 0.40, "C": 0.05, "D": -0.05, "E": -0.20, "F": -0.40}
        positions = [_pos("B", 1.0)]  # B is currently open
        closed = []

        xl.maybe_rebalance(
            _XS_CFG, _uni(custom_rets), positions,
            _fetch_factory(custom_rets),
            lambda a: None,
            lambda c: closed.append(c),
        )

        assert "B" in closed, f"Expected B to be closed; got {closed}"
        # Claim on B should be released
        with open(claims_path) as fh:
            saved = json.load(fh)
        assert saved.get("claims", {}).get("B") is None, \
            f"Claim on B not released after close: {saved['claims']}"


# ═══════════════════════════════════════════════════════════════════════════════
# TASK 2: strategy_book_equity_frac sizing in maybe_execute
# ═══════════════════════════════════════════════════════════════════════════════

def _make_analysis(coin="ETH", side="long", book_name="xs_momentum", book_notional=None):
    import uuid, time
    a = {
        "id": str(uuid.uuid4()), "coin": coin, "verdict": "LONG" if side == "long" else "SHORT",
        "side": side, "confidence": 0.99, "entry_px": 0.0, "stop_px": 0.0, "tp_px": 0.0,
        "reasoning": "[test]", "news_risk": "none", "ai_down": False,
        "created_at": int(time.time() * 1000), "composite_score": 0.0,
        "strategy_book": book_name,
    }
    if book_notional is not None:
        a["strategy_book_notional"] = book_notional
    return a


class TestStrategyBookEquityFracSizing:
    """Test that strategy_book_equity_frac sizes positions correctly."""

    def _run_execute(self, config_overrides, analysis, monkeypatch):
        """Run maybe_execute with all network calls stubbed, return the result."""
        import hermes_trader.agents.executor as ex
        import hermes_trader.agents.config_store as cs

        # Build a config
        cfg = dict(cs.DEFAULT_CONFIG)
        cfg.update({
            "mode": "LIVE",
            "leverage": 12,
            "strategy_book_equity_frac": 0.1,
            "strategy_book_notional_usd": 0,
            "atr_risk_sizing": {"enabled": False},   # use legacy path so no ATR fetch
            "equity_fraction_per_trade": 0.2,
            "max_trade_notional_usd": 0,
            "min_available_margin_pct": 0.0,         # don't block on margin
            "max_total_notional_pct": 100.0,         # 10000% of equity = permissive
            "max_concurrent": 20,
            "max_daily_loss_usd": -10000,
            "daily_giveback_halt_pct": 0.0,
            "enable_crypto": True,
            "enable_hip3": False,
            "runner_entry_gate": {"enabled": False},
            "trend_filter_200ma": {"enabled": False},
            "loss_cooldown_min": 0,
            "reentry_cap": {"enabled": False},
            "capital_rotation": {"enabled": False},
            "ta_sidestep_force_execute": False,
        })
        cfg.update(config_overrides)

        monkeypatch.setattr(ex, "read_agent_config", lambda: cfg)

        # Stub all network / exchange calls
        monkeypatch.setattr(ex, "resolve_user_address", lambda: "0xtest")
        # Stub DSL backstop so prior-test registrations don't block re-entry
        monkeypatch.setattr(ex, "active_position_coins", lambda: {})
        monkeypatch.setattr(ex, "fetch_account_state", lambda user, include_hip3=False: {
            "equity": 60.0,    # aggregate equity (main dex only for this test)
            "available": 60.0,
            "asset_positions": [],
            "total_ntl": 0.0,
            "dex_equity": {"": 60.0},
            "dex_available": {"": 60.0},
        })
        monkeypatch.setattr(ex, "get_hl_price", lambda coin: 2000.0)
        monkeypatch.setattr(ex, "get_max_leverage", lambda coin: 50)
        monkeypatch.setattr(ex, "set_leverage", lambda coin, lev: None)
        monkeypatch.setattr(ex, "get_hl_atr", lambda interval, period, coin: 40.0)
        monkeypatch.setattr(ex, "entry_size_for_notional",
                            lambda coin, notional, px: round(notional / px, 4))
        monkeypatch.setattr(ex, "min_entry_notional_usd", lambda coin, px: 10.0)
        monkeypatch.setattr(ex, "place_hl_order",
                            lambda is_buy, sz, px, coin: {"ok": True, "order_id": "test-order",
                                                          "avg_px": px, "total_sz": sz})
        monkeypatch.setattr(ex, "place_hl_trigger_order",
                            lambda is_buy, sz, px, kind, coin: {"ok": True})
        monkeypatch.setattr(ex, "cancel_open_orders_for_coin", lambda coin: None)

        # Stub memory so it doesn't block
        from hermes_trader.agents.memory import memory
        monkeypatch.setattr(memory, "get_recent_trades", lambda n: [])
        monkeypatch.setattr(memory, "track_daily_pnl", lambda eq: None)
        monkeypatch.setattr(memory, "get_daily_pnl", lambda: 0.0)
        monkeypatch.setattr(memory, "peak_daily_pnl", lambda: 0.0)
        monkeypatch.setattr(memory, "loss_cooldown_remaining_min", lambda coin: 0)
        monkeypatch.setattr(memory, "count_entries_since", lambda coin, t: 0)
        monkeypatch.setattr(memory, "record_trade", lambda t: None)
        monkeypatch.setattr(memory, "record_entry_context", lambda *a, **kw: None)
        monkeypatch.setattr(memory, "latest_trade_ts_by_coin", lambda n: {})

        import os
        monkeypatch.setenv("HYPERLIQUID_PRIVATE_KEY", "0xdeadbeef")

        return ex.maybe_execute(analysis)

    def test_equity_frac_sizing_produces_correct_notional(self, tmp_path, monkeypatch):
        """$60 main equity × 0.1 frac × 12x lev = $72 notional."""
        a = _make_analysis()
        result = self._run_execute({}, a, monkeypatch)
        # Should execute (not be blocked by gates)
        assert result.get("executed") is True, f"Unexpected block: {result}"
        # Notional should be ~$72 ($60 × 0.1 × 12). Allow small rounding from entry_size_for_notional.
        assert 60 <= result["size_usd"] <= 80, \
            f"Expected size_usd ~$72 (0.1-frac), got ${result['size_usd']:.2f}"

    def test_equity_frac_larger_than_old_15_cap(self, tmp_path, monkeypatch):
        """With equity-frac, strategy_book notional should exceed the old $15 cap."""
        a = _make_analysis()
        result = self._run_execute({}, a, monkeypatch)
        assert result.get("executed") is True
        assert result["size_usd"] > 15, \
            f"Expected strategy_book size > $15 (old cap), got ${result['size_usd']:.2f}"

    def test_abs_cap_clips_equity_frac(self, tmp_path, monkeypatch):
        """strategy_book_notional_usd=30 clips the equity-frac result from $72 to $30."""
        a = _make_analysis()
        result = self._run_execute({"strategy_book_notional_usd": 30}, a, monkeypatch)
        assert result.get("executed") is True
        assert result["size_usd"] <= 32, \
            f"Expected abs cap of $30 to be enforced; got ${result['size_usd']:.2f}"

    def test_zero_frac_falls_back_to_notional_usd(self, tmp_path, monkeypatch):
        """When strategy_book_equity_frac=0, fall back to strategy_book_notional_usd=$15."""
        a = _make_analysis()
        result = self._run_execute({
            "strategy_book_equity_frac": 0,
            "strategy_book_notional_usd": 15,
        }, a, monkeypatch)
        assert result.get("executed") is True
        # Should be close to $15 (legacy path)
        assert result["size_usd"] <= 20, \
            f"Expected legacy $15 cap; got ${result['size_usd']:.2f}"

    def test_normal_trade_uses_equity_fraction_not_ext_frac(self, tmp_path, monkeypatch):
        """A non-strategy_book trade should NOT use strategy_book_equity_frac."""
        import uuid, time
        a = {
            "id": str(uuid.uuid4()), "coin": "ETH", "verdict": "LONG", "side": "long",
            "confidence": 0.99, "entry_px": 0.0, "stop_px": 0.0, "tp_px": 0.0,
            "reasoning": "[test normal]", "news_risk": "none", "ai_down": False,
            "created_at": int(time.time() * 1000), "composite_score": 0.0,
            # NO strategy_book key
        }
        result = self._run_execute({
            "equity_fraction_per_trade": 0.2,
            "strategy_book_equity_frac": 0.1,
        }, a, monkeypatch)
        assert result.get("executed") is True
        # Normal sizing: $60 × 0.2 × 12 = $144 (larger than strategy-book $72)
        assert result["size_usd"] > 100, \
            f"Expected normal sizing ~$144; got ${result['size_usd']:.2f}"

    def test_safety_gates_still_clamp_strategy_book(self, tmp_path, monkeypatch):
        """Verify that normal safety gates still apply to strategy_book positions.

        We test this by directly exercising the max_concurrent gate through risk_gates.py
        with a strategy_book analysis and confirming the gate blocks it when full.
        This proves the executor's gate path is NOT bypassed for strategy_book.
        """
        from hermes_trader.agents.risk_gates import max_concurrent_positions_gate, GateContext

        # Simulate 10 open positions (at the limit)
        full_positions = [{"coin": f"COIN{i}", "side": "long", "size_usd": 72.0} for i in range(10)]
        ctx = GateContext(
            confidence=0.99, current_positions=full_positions,
            trade_notional_usd=72.0, daily_pnl=0,
            market_volume_24h_usd=1e8, coin="ETH", trade_side="long",
            has_binary_news_risk=False, binary_news_match="",
            equity=60.0, total_open_notional=720.0,
            composite_score=0.0, momentum_burst_fired=False,
            slow_burn_fired=False, peak_daily_pnl=0.0,
        )
        result = max_concurrent_positions_gate(ctx, max_concurrent=10)
        assert result["pass"] is False, \
            f"max_concurrent gate should block at 10/10; got: {result}"
        assert "max positions reached" in result.get("reason", ""), \
            f"Expected 'max positions reached' in reason; got: {result}"

        # And when there IS room (9 positions, cap 10) it should pass
        fewer_positions = full_positions[:9]
        ctx2 = GateContext(
            confidence=0.99, current_positions=fewer_positions,
            trade_notional_usd=72.0, daily_pnl=0,
            market_volume_24h_usd=1e8, coin="ETH", trade_side="long",
            has_binary_news_risk=False, binary_news_match="",
            equity=60.0, total_open_notional=648.0,
            composite_score=0.0, momentum_burst_fired=False,
            slow_burn_fired=False, peak_daily_pnl=0.0,
        )
        result2 = max_concurrent_positions_gate(ctx2, max_concurrent=10)
        assert result2["pass"] is True, \
            f"max_concurrent gate should pass at 9/10; got: {result2}"
