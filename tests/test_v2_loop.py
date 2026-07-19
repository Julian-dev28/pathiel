"""v2 gate tests — loop: one cadence, shadow purity end-to-end, kill-switch wiring,
degraded-read guard, exit sub-cycle behavior in both modes.
"""
from __future__ import annotations

import time

import pytest

import hermes_trader.agents.dsl_exit as dsl
import hermes_trader.v2.books as books
import hermes_trader.v2.executor as ex
import hermes_trader.v2.loop as loop
import hermes_trader.v2.risk as risk

NOW_S = 1_784_336_400.0            # 2026-07-18 01:00 UTC (inside the 6h fade entry window)
NOW_MS = int(NOW_S * 1000)
DAY_MS = books.DAY_MS


def _deps(mids=None, account=None, universe=None, candles=None, zfn=None):
    return loop.Deps(
        fetch_mids=lambda: dict(mids or {}),
        fetch_account=lambda: dict(account or {}),
        fetch_universe=lambda: list(universe or []),
        fetch_candles=candles or (lambda c, i, n: []),
        coin_funding_z=zfn or (lambda c, t, lb: None),
        now=lambda: NOW_S,
    )


def _account(equity=150.0, positions=(), available=None, total_ntl=0.0):
    return {"equity": equity, "available": available if available is not None else equity,
            "asset_positions": list(positions), "total_ntl": total_ntl,
            "queried_dexes": {""}}


def _crash_universe():
    return [{"coin": "AAA", "dayNtlVlm": 1e8, "type": "perp", "midPx": 87.0}]


def _crash_candles(coin, interval, n):
    end = NOW_MS - (NOW_MS % DAY_MS) - DAY_MS      # completed crash bar
    closes = [100, 100, 100, 100, 100, 87]
    return [{"t": end - (len(closes) - 1 - i) * DAY_MS, "o": c, "h": c, "l": c,
             "c": c, "v": 1.0} for i, c in enumerate(closes)]


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.delenv("HERMES_V2_LIVE", raising=False)
    monkeypatch.setattr(books, "_EF_STATE_FILE", str(tmp_path / "ef.json"))
    monkeypatch.setattr(books, "_FS_SEEN_FILE", str(tmp_path / "fs.json"))
    monkeypatch.setattr(books, "_XS_TS_FILE", str(tmp_path / "xs_ts"))
    monkeypatch.setattr(books, "_XS_OWNED_FILE", str(tmp_path / "xs_owned.json"))
    monkeypatch.setattr(ex, "_CLAIMS_PATH", str(tmp_path / "claims.json"))
    ex.reset_claims_singleton()
    monkeypatch.setattr(risk, "_SOD_FILE", str(tmp_path / "sod.json"))
    monkeypatch.setattr(dsl, "_save_state", lambda: None)
    dsl._active_positions.clear()
    yield
    dsl._active_positions.clear()
    ex.reset_claims_singleton()


def _cfg(**over):
    cfg = loop._deep_merge(loop.V2_DEFAULTS, {
        "extreme_fade": {"skew_arm": {"enabled": False}},
        "recorder": {"enabled": False},
    })
    return loop._deep_merge(cfg, over)


class TestShadowSignalCycle:
    def test_records_to_v2_ledger_and_places_zero_orders(self, monkeypatch):
        """The Phase-2 contract in one test: full signal cycle, intents in the
        v2_ ledger, and the executor is never even called in shadow mode."""
        import hermes_trader.v2.ledger as ledger

        def _boom(*a, **kw):
            raise AssertionError("executor touched in shadow mode")
        monkeypatch.setattr(ex, "execute_intent", _boom)
        monkeypatch.setattr(ex, "close_coin", _boom)
        monkeypatch.setattr(ex, "flatten_all", _boom)

        out = loop.signal_cycle(_cfg(), "SHADOW",
                                _deps(account=_account(), universe=_crash_universe(),
                                      candles=_crash_candles))
        assert out["books"]["extreme_fade"]["records"] == 1
        assert out["books"]["extreme_fade"]["opened"] == 0
        rows = ledger.load("extreme_fade")
        assert rows and rows[-1]["coin"] == "AAA"
        assert rows[-1]["entry_ref_px"] == pytest.approx(87.0)   # gradeable row

    def test_degraded_equity_read_skips_cycle(self):
        out = loop.signal_cycle(_cfg(), "SHADOW", _deps(account=_account(equity=0.0)))
        assert out["skipped"] == "degraded_equity_read"

    def test_kill_switch_breach_blocks_entries_no_flatten_in_shadow(self, monkeypatch):
        risk.start_of_day_equity(200.0, NOW_S, risk._SOD_FILE)   # SOD $200, now $150
        flattened = []
        monkeypatch.setattr(ex, "flatten_all", lambda *a, **kw: flattened.append(1))
        out = loop.signal_cycle(_cfg(), "SHADOW",
                                _deps(account=_account(equity=150.0),
                                      universe=_crash_universe(), candles=_crash_candles))
        assert out["kill_switch"]["breached"] is True
        assert "books" not in out and flattened == []            # shadow: no orders

    def test_kill_switch_breach_flattens_in_live(self, monkeypatch):
        monkeypatch.setenv("HERMES_V2_LIVE", "1")
        risk.start_of_day_equity(200.0, NOW_S, risk._SOD_FILE)
        flattened = []
        monkeypatch.setattr(ex, "flatten_all", lambda acct, **kw: flattened.append(acct))
        out = loop.signal_cycle(_cfg(), "LIVE",
                                _deps(account=_account(equity=150.0)))
        assert out["kill_switch"]["breached"] is True and len(flattened) == 1

    def test_live_cycle_executes_and_marks_dedup(self, monkeypatch):
        monkeypatch.setenv("HERMES_V2_LIVE", "1")
        calls = []

        def _fake_exec(intent, **kw):
            calls.append(intent)
            return {"executed": True, "size_usd": intent.notional_usd}
        monkeypatch.setattr(ex, "execute_intent", _fake_exec)
        out = loop.signal_cycle(_cfg(), "LIVE",
                                _deps(account=_account(), universe=_crash_universe(),
                                      candles=_crash_candles))
        assert out["books"]["extreme_fade"]["opened"] == 1
        assert calls[0].coin == "AAA" and calls[0].book == "extreme_fade"
        # dedup persisted: the same crash bar cannot open twice
        out2 = loop.signal_cycle(_cfg(), "LIVE",
                                 _deps(account=_account(), universe=_crash_universe(),
                                       candles=_crash_candles))
        assert out2["books"]["extreme_fade"]["opened"] == 0


class TestExitCycle:
    def _register_breached_long(self):
        pol = ex.book_exit_policy(20.0, 3.0)
        t = dsl.DSLTracker("AAA", "long", 100.0, time.time(), pol, leverage=1)
        dsl._active_positions["AAA_long"] = t

    def test_shadow_computes_verdict_but_never_closes(self, monkeypatch):
        self._register_breached_long()
        monkeypatch.setattr(loop, "load_state", lambda force=False: None)
        monkeypatch.setattr(ex, "close_coin",
                            lambda *a, **kw: (_ for _ in ()).throw(AssertionError("closed!")))
        out = loop.exit_cycle(_cfg(), "SHADOW", _deps(mids={"AAA": 79.0}))
        assert len(out["exits"]) == 1
        assert out["exits"][0]["close"] == {"ok": False, "reason": "shadow_mode"}

    def test_live_closes_on_breach(self, monkeypatch):
        monkeypatch.setenv("HERMES_V2_LIVE", "1")
        self._register_breached_long()
        closed = []
        monkeypatch.setattr(ex, "close_coin",
                            lambda coin, **kw: closed.append(coin) or {"ok": True})
        out = loop.exit_cycle(_cfg(), "LIVE", _deps(mids={"AAA": 79.0}))
        assert closed == ["AAA"] and out["exits"][0]["close"] == {"ok": True}

    def test_no_breach_no_exits(self, monkeypatch):
        self._register_breached_long()
        monkeypatch.setattr(loop, "load_state", lambda force=False: None)
        out = loop.exit_cycle(_cfg(), "SHADOW", _deps(mids={"AAA": 99.0}))
        assert out["exits"] == []


class TestModeWiring:
    def test_defaults_are_shadow(self):
        assert loop.V2_DEFAULTS["mode"] == "SHADOW"
        assert loop.load_config()["mode"] == "SHADOW"

    def test_live_mode_without_env_is_not_live(self):
        assert loop._is_live("LIVE") is False        # env unset → still no orders

    def test_shadow_invariant_rejects_armed_process(self, monkeypatch):
        monkeypatch.setenv("HERMES_V2_LIVE", "1")
        with pytest.raises(ex.ShadowModeViolation):
            loop.assert_shadow_invariant("SHADOW")
        loop.assert_shadow_invariant("LIVE")         # legal combination

    def test_run_forever_one_cycle_smoke(self, monkeypatch):
        slept = []
        deps = _deps(mids={}, account=_account(), universe=[], candles=lambda c, i, n: [])
        monkeypatch.setattr(loop, "load_state", lambda force=False: None)
        loop.run_forever(mode="SHADOW", cfg=_cfg(), deps=deps, max_cycles=1,
                         sleep_fn=lambda s: slept.append(s))
        assert slept == []                            # --once semantics: no trailing sleep


def test_parity_diff_flags_policy_drift(monkeypatch):
    """Phase-2 differ: a v1 tracker whose policy diverges from v2's own
    resolution for the same book/position must be flagged; an identical
    resolution must count as matched. This is the check whose ABSENCE let
    48h of shadow read as 'zero divergences' (2026-07-20)."""
    import hermes_trader.v2.loop as L
    import hermes_trader.agents.dsl_exit as dsl
    from hermes_trader.v2 import executor as vexec

    cfg = L.load_config()
    ef = cfg["extreme_fade"]
    good_pol = vexec.book_exit_policy(stop_pct=float(ef.get("stop_pct", 20.0)),
                                      hold_days=float(ef.get("hold_days", 3.0)),
                                      leverage=1)
    t_good = dsl.DSLTracker("GOODCOIN", "long", 100.0, 0.0, good_pol, leverage=1)
    bad_pol = dsl.ExitPolicy(max_loss_pct=2.5, protect_pct=1.25)   # main-engine-style drift
    t_bad = dsl.DSLTracker("BADCOIN", "long", 100.0, 0.0, bad_pol, leverage=1)

    monkeypatch.setattr(dsl, "_active_positions",
                        {"GOODCOIN_long": t_good, "BADCOIN_long": t_bad})
    monkeypatch.setattr(L, "_book_of", lambda coin, cfg: "extreme_fade")
    out = L._parity_diff({"GOODCOIN": 99.0, "BADCOIN": 99.0})
    assert out["matched"] == 1
    assert out["divergent"] == 1


def test_parity_diff_unmapped_books_not_divergent(monkeypatch):
    import hermes_trader.v2.loop as L
    import hermes_trader.agents.dsl_exit as dsl
    t = dsl.DSLTracker("HYPE", "short", 59.0, 0.0, dsl.ExitPolicy(), leverage=10)
    monkeypatch.setattr(dsl, "_active_positions", {"HYPE_short": t})
    monkeypatch.setattr(L, "_book_of", lambda coin, cfg: "crash_continue_div_short")
    out = L._parity_diff({"HYPE": 58.0})
    assert out == {"matched": 0, "divergent": 0, "unmapped": 1}
