"""Books-only mode (rebuild 2026-07-18): main-engine AI-verdict ENTRIES are
gated off via main_engine.entries_enabled=false. Forensics on 2,721 fills:
sub-2h AI-engine churn -$385.59 net vs >=2h holds +$134.96 — entries from
the thought-engine were the #1 measured loss source. Strategy books (tagged
strategy_book) pass; absent config defaults to enabled (old behavior)."""
import pytest

from pathia.agents import executor as ex


def _analysis(**kw):
    d = {"id": "t1", "coin": "BTC", "side": "long", "confidence": 0.9,
         "verdict": "LONG", "entry_px": 100.0}
    d.update(kw)
    return d


def _cfg(entries_enabled):
    return {"mode": "LIVE", "main_engine": {"entries_enabled": entries_enabled}}


def _stub_network(monkeypatch):
    """maybe_execute() reaches several real-network calls past the books-only
    gate (fetch_account_state, get_max_leverage, get_hl_price, ... all real
    HTTP POSTs to api.hyperliquid.xyz). Unpatched, any test whose analysis
    clears the gate (strategy_book set, or entries_enabled defaulting True)
    hits the real network — flaky (ConnectionError) and slow (real
    retry/backoff, seconds per call) offline / on CI. Same full stub set as
    test_claims_registry_and_sizing.py's `_execute` helper."""
    monkeypatch.setattr(ex, "resolve_user_address", lambda: "0xtest")
    monkeypatch.setattr(ex, "fetch_account_state", lambda user, include_hip3=False: {
        "equity": 60.0,
        "available": 60.0,
        "asset_positions": [],
        "total_ntl": 0.0,
        "dex_equity": {"": 60.0},
        "dex_available": {"": 60.0},
    })
    monkeypatch.setattr(ex, "get_hl_price", lambda coin: 100.0)
    monkeypatch.setattr(ex, "get_max_leverage", lambda coin: 20)
    monkeypatch.setattr(ex, "set_leverage", lambda coin, lev: None)
    monkeypatch.setattr(ex, "get_hl_atr", lambda interval, period, coin: 1.0)
    monkeypatch.setattr(ex, "entry_size_for_notional",
                        lambda coin, notional, px: round(notional / px, 4))
    monkeypatch.setattr(ex, "min_entry_notional_usd", lambda coin, px: 10.0)
    monkeypatch.setattr(ex, "place_hl_order", lambda *a, **kw: {
        "status": "ok", "filled": True, "avg_px": 100.0, "order_id": "test-order",
    })


def test_main_engine_entry_blocked_when_disabled(monkeypatch):
    _stub_network(monkeypatch)
    monkeypatch.setattr(ex, "read_agent_config", lambda: _cfg(False))
    r = ex.maybe_execute(_analysis())
    assert r["executed"] is False
    assert r["reason"] == "main_engine_entries_disabled"


def test_book_entry_passes_the_gate(monkeypatch):
    _stub_network(monkeypatch)
    monkeypatch.setattr(ex, "read_agent_config", lambda: _cfg(False))
    r = ex.maybe_execute(_analysis(strategy_book="extreme_fade"))
    # must NOT be blocked by the books-only gate — downstream gates may still
    # refuse (no live account in tests), but the reason must differ
    assert r.get("reason") != "main_engine_entries_disabled"


def test_absent_config_defaults_to_enabled(monkeypatch):
    _stub_network(monkeypatch)
    monkeypatch.setattr(ex, "read_agent_config", lambda: {"mode": "LIVE"})
    r = ex.maybe_execute(_analysis())
    assert r.get("reason") != "main_engine_entries_disabled"


# ── kill-switch rescale (rebuild step 4) ─────────────────────────────────────

from pathia.agents.risk_gates import effective_daily_loss_limit


def test_pct_limit_scales_with_sod_equity():
    # $18.06 equity, -$0.5 on the day -> SOD 18.56 -> floor -15% = -$2.78
    lim = effective_daily_loss_limit({"max_daily_loss_pct": 0.15,
                                      "max_daily_loss_usd": -100}, 18.06, -0.5)
    assert lim == pytest.approx(-2.784, abs=0.01)


def test_pct_zero_falls_back_to_usd():
    assert effective_daily_loss_limit({"max_daily_loss_usd": -12}, 18.0, 0.0) == -12


def test_degraded_zero_equity_never_yields_zero_floor():
    # equity read 0 (degraded tick): pct path disabled, usd fallback holds
    lim = effective_daily_loss_limit({"max_daily_loss_pct": 0.15,
                                      "max_daily_loss_usd": -100}, 0.0, -5.0)
    assert lim == -100


def test_garbage_config_defaults():
    assert effective_daily_loss_limit({"max_daily_loss_pct": "x",
                                       "max_daily_loss_usd": None}, 20.0, 0.0) == -100


# ── deposit-race peak guard (2026-07-18 incident) ────────────────────────────

def test_deposit_race_does_not_poison_peak_daily_pnl(monkeypatch):
    """The tick where a deposit lands can compute daily_pnl before the
    contributions fetch reflects it — 2026-07-18: +$132 deposit read as
    +$131 daily PnL for one tick, peakDailyPnl froze at 128.28 on a -$4 day
    and the give-back gate blocked ALL entries (books included) until UTC
    roll. A single-tick jump > max($10, 30% of equity) must freeze the peak
    high-water for that tick; the corrected next tick proceeds normally."""
    from pathia.agents.memory import AgentMemory
    m = AgentMemory.__new__(AgentMemory)
    m._start_of_day_equity = 18.0
    m._day_start_ts = 2**63 - 1          # force the "same day" branch
    m._daily_pnl = -0.5
    m._peak_daily_pnl = 0.0
    m._equity = 17.5
    m._last_eq_reading = 0.0   # 0 disables the fast-swing guard's compare
    m._last_eq_reading_ts = 0.0
    import datetime as _dt
    # same-day branch requires day_start >= today's midnight: pin it
    m._day_start_ts = int(_dt.datetime.now(_dt.timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0).timestamp())

    # tick 1: deposit landed ($150 equity) but contributions still report 0
    m.track_daily_pnl(150.0, net_contributions=0.0)
    assert m._peak_daily_pnl == 0.0, "transfer-race tick must not move the peak"

    # tick 2: contributions caught up — honest daily pnl, peak tracks again
    m.track_daily_pnl(150.0, net_contributions=132.0)
    assert m._peak_daily_pnl == pytest.approx(0.0, abs=0.01)
    assert m._daily_pnl == pytest.approx(0.0, abs=0.01)


# ── xs basket exit ownership (2026-07-19 incident) ───────────────────────────

def test_book_owned_holds_skip_ai_close_check():
    """Book-claimed coins are exempt from the AI close-check (their books own
    exits); the loop consults the claims registry before researching a held
    coin. Text-level assertion — trading_loop must never be imported."""
    import os
    src = open(os.path.join(os.path.dirname(__file__), "..",
                            "scripts", "trading_loop.py")).read()
    i = src.index("if coin in held_coins:")
    block = src[i:i + 2000]
    assert "owner_of(coin)" in block
    assert "BOOK_OWNED_HOLD" in block
    assert block.index("owner_of(coin)") < block.index("(now_ms - last_research)")




def test_disabled_markets_are_dropped_before_anything_can_spend_on_them():
    """enable_crypto=false was enforced only at executor.maybe_execute — the very
    end, after the books had already paid for the candidate.

    Measured 2026-09-06 with crypto disabled: 559 of 839 markets were unusable
    and still scanned every cycle, and news_surge_short calls coin_catalyst()
    per coin, which is one Google News fetch each. The refusal was correct and
    far too late.

    Filtering in get_universe means no caller can forget: a book cannot spend on
    a market it was never handed.
    """
    from pathia.client import universe as U

    meta = {"BTC": {"type": "perp", "dex": None, "maxLeverage": 40, "szDecimals": 5},
            "xyz:BE": {"type": "perp", "dex": "xyz", "maxLeverage": 10, "szDecimals": 2}}
    ctx = {"BTC": {"dayNtlVlm": "1e9"}, "xyz:BE": {"dayNtlVlm": "1e7"}}

    import unittest.mock as mock
    with mock.patch.object(U, "_fetch_perp_meta", return_value=({}, {})), \
         mock.patch.object(U, "_fetch_spot_meta", return_value=({}, {})), \
         mock.patch.object(U, "_fetch_hip3_meta", return_value=(meta, ctx)), \
         mock.patch.object(U, "list_hip3_dexes", return_value=["xyz"]):
        both = {m["coin"] for m in U.get_universe(include_hip3=True, include_crypto=True)}
        assert {"BTC", "xyz:BE"} <= both

        hip3_only = {m["coin"] for m in
                     U.get_universe(include_hip3=True, include_crypto=False)}
        assert "xyz:BE" in hip3_only, "HIP-3 markets must survive"
        assert "BTC" not in hip3_only, "a disabled native market reached the books"


def test_the_loop_passes_the_crypto_toggle_to_every_universe_build():
    """Two call sites build the universe — startup and the periodic refresh. A
    filter applied to only one of them silently reintroduces the spend on the
    refresh path, hours later, where nobody is looking."""
    import pathlib
    import re
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "scripts" / "trading_loop.py").read_text()
    calls = re.findall(r"get_universe\((?:[^()]|\([^()]*\))*\)", src)
    builds = [c for c in calls if "include_hip3" in c]
    assert builds, "no universe build found — did the call shape change?"
    for c in builds:
        assert "include_crypto" in c, f"universe built without the crypto toggle: {c}"


def test_turning_a_market_back_on_does_not_need_a_restart():
    """The toggles gate SPEND now, not just execution: a disabled market is
    never scanned and never news-fetched. So an operator flipping crypto back on
    expects the scanner to follow, and reading the toggle once at startup would
    mean it silently did not until the next restart.

    Pinned structurally: the refresh path must re-read the config rather than
    reuse the startup values.
    """
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "scripts" / "trading_loop.py").read_text()
    refresh = src[src.index("if universe_refresh_s > 0"):]
    refresh = refresh[:refresh.index("get_universe(force_refresh=True")]
    assert "read_agent_config()" in refresh, (
        "the universe refresh reuses startup toggles — flipping a market on "
        "would not take effect until the loop restarts")
    assert "enable_crypto" in refresh and "enable_hip3" in refresh


def test_every_sizing_gate_admits_the_book_the_config_asks_for():
    """A small account is not a big account with smaller numbers: it is an
    account where a dozen independently-set ceilings, each sane on its own, can
    conspire to refuse every trade. Three did in one session — the equity-fraction
    sizing put trades under HL's minimum, the xyz concentration cap was smaller
    than one position, and max_daily_loss_pct silently overrode the USD kill the
    book had been sized against.

    So this asserts COHERENCE rather than any single value: every gate must
    admit the book the config asks for, and the worst case must still fit the
    kill. Change any number and this says whether the set still agrees.
    """
    import json
    import pathlib
    from pathia.client.exchange import MIN_ORDER_USD
    root = pathlib.Path(__file__).resolve().parents[1]
    cfg = json.loads((root / ".agent-config.json").read_text())

    equity = float(cfg["min_tradable_equity_usd"])   # the floor it must work at
    lev = int(cfg["leverage"])
    conc = int(cfg["max_concurrent"])
    from pathia.agents.book_params import book_params
    _bp = book_params(cfg, "xs_reversal")
    stop = float(_bp.stop_pct) / 100.0
    # notional_usd 0 means equity-FRACTION sizing (strategy_book_equity_frac),
    # so the per-position size is derived from equity rather than fixed. Sized
    # at the floor, where the fraction produces its smallest order.
    n = float(_bp.notional_usd)
    if n <= 0:
        n = equity * float(cfg["strategy_book_equity_frac"]) * lev
    book = conc * n

    # max_daily_loss_pct WINS over max_daily_loss_usd (effective_daily_loss_limit).
    # Sizing against the USD value while a pct is set is how the book ends up
    # larger than the kill it was supposed to respect.
    assert float(cfg.get("max_daily_loss_pct", 0)) > 0, (
        "no percentage kill — a static USD floor does not scale with the account")
    kill = equity * float(cfg["max_daily_loss_pct"])

    assert n >= MIN_ORDER_USD, f"${n} is under the exchange minimum ${MIN_ORDER_USD}"
    assert conc * n / lev <= equity * (1 - float(cfg["min_available_margin_pct"])), \
        "the book cannot fit in usable margin"
    assert stop * 100 <= 100 * float(cfg["backup_sl_max_frac_of_liq"]) / lev, \
        "the stop is unreachable at this leverage and would be silently tightened"
    # The kill must sit BETWEEN one position stopping and the whole book
    # stopping. Both ends are failure modes, in opposite directions:
    #
    #   kill <= one stop-out   the bot halts for the day on its FIRST loser.
    #                          At 92% deployment one position is 13.8% of
    #                          equity, so a 20% kill trips after 1.4 losses and
    #                          the book never gets to run.
    #   kill >  full stop-out  the kill can never fire before every slot has
    #                          already stopped, i.e. it is decoration.
    #
    # This replaced a strict `book * stop < kill` on 2026-09-06, when the
    # operator moved from a $11-per-slot book to deploying the portfolio. That
    # assertion encoded a book small enough for the daily kill to bound its
    # worst case; once the account is fully deployed nothing can be both fully
    # deployed AND bounded by a daily kill, because the kill halts new entries
    # and does not close open ones. The bound on the worst case is the STOP, and
    # it is declared and tested as max_correlated_drawdown_pct in
    # tests/test_live_config_coherence.py.
    one_stop = book * stop / conc
    assert one_stop < kill, (
        f"one position stopping costs ${one_stop:.2f} against a ${kill:.2f} kill — "
        f"the bot halts for the day on its first loser")
    assert kill <= book * stop, (
        f"a full stop-out costs ${book*stop:.2f} but the kill is ${kill:.2f} — "
        f"the kill can never fire, which makes it decoration")

    # 0 means INACTIVE for the absolute USD ceilings, not "a cap of zero". Under
    # equity-fraction sizing a fixed-dollar ceiling silently clips the position
    # as equity grows, and does it with no log line, so these are switched off
    # deliberately and the percentage caps (which scale) do the bounding.
    for key, cap in (("max_xyz_short_notional_pct", equity * float(cfg["max_xyz_short_notional_pct"])),
                     ("max_total_notional_pct", equity * float(cfg["max_total_notional_pct"])),
                     ("short_notional_usd", float(cfg["short_notional_usd"]))):
        if cap <= 0:
            continue
        assert book <= cap, f"{key} caps the book at ${cap:.2f}, under its ${book:.2f}"
    for key in ("strategy_book_notional_usd", "max_trade_notional_usd"):
        v = float(cfg.get(key, 0) or 0)
        if v <= 0:
            continue
        assert n <= v, f"{key} is under one position"

    # A gate that can never fire is not protection, it is decoration.
    assert float(cfg["daily_giveback_min_peak_usd"]) < kill, (
        "the give-back gate arms above a daily peak this account cannot reach")
    assert int(cfg["max_xyz_short_names"]) <= conc
