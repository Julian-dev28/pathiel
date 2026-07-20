"""Gate tests for the xs_xyz_equities book (W-X2 cell A, VERDICT ROBUST —
research/alpha_swarm/findings/W-X2_xs_xyz_equities.md, operator pre-authorized
LIVE wiring). Offline, deterministic, <2s: synthetic candles only, spy
execute/close, no network, and scripts/trading_loop.py is only ever read as
TEXT (importing it starts the LIVE loop)."""
import importlib.util
import json
import os
import time

import pytest

from hermes_trader.agents import xs_xyz
from hermes_trader.agents import xs_xyz_live as xxl
from hermes_trader.agents.xs_momentum import residual_score

_DAY_MS = 86_400_000
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── synthetic candle fixtures ────────────────────────────────────────────────

def _bars_from_rets(rets, close0=100.0, vol_usd=1_000_000.0, end_t=None):
    """Daily dict-bars from a list of daily returns; per-bar volume is chosen so
    close×volume == vol_usd exactly (the eligibility notional). Bars END at
    end_t (default: a fully closed bar, 2 days ago)."""
    if end_t is None:
        end_t = int(time.time() * 1000) - 2 * _DAY_MS
    closes = [close0]
    for r in rets:
        closes.append(closes[-1] * (1.0 + r))
    closes = closes[1:]
    n = len(closes)
    out = []
    for i, c in enumerate(closes):
        t = end_t - (n - 1 - i) * _DAY_MS
        out.append({"t": t, "o": c, "h": c * 1.01, "l": c * 0.99, "c": c,
                    "v": vol_usd / c})
    return out


def _bench_rets(n=64):
    """Benchmark daily returns with nonzero variance (alternating +1%/+2%)."""
    return [0.01 if i % 2 == 0 else 0.02 for i in range(n)]


# ── pure engine: residualization + ranking ───────────────────────────────────

def test_residual_score_isolates_alpha_from_beta():
    """score = r7(coin) − beta·r7(bench), beta = OLS on 30 daily rets. A coin
    whose rets are bench + constant alpha has beta exactly 1 → residual ≈ the
    compounded alpha. A beta-2 coin with NO alpha residualizes to ≈ 0 even
    though its RAW trailing return is far higher — the exact reason raw
    ranking was not the pre-registered PRIMARY."""
    br = _bench_rets()
    bench = _bars_from_rets(br)
    alpha = 0.005
    coin_alpha = _bars_from_rets([r + alpha for r in br])
    coin_beta2 = _bars_from_rets([2 * r for r in br])

    s_alpha = residual_score(coin_alpha, bench, 7, beta_window=30)
    s_beta2 = residual_score(coin_beta2, bench, 7, beta_window=30)
    assert s_alpha == pytest.approx(7 * alpha, rel=0.25)   # ~+3.5% residual
    assert abs(s_beta2) < 0.02                             # leverage compounding crumbs, not alpha
    assert s_alpha > s_beta2


def test_rank_xyz_orders_by_residual_not_raw_return():
    br = _bench_rets()
    bench = _bars_from_rets(br)
    cbc = {
        "xyz:ALPHA": _bars_from_rets([r + 0.005 for r in br]),   # +alpha
        "xyz:BETA2": _bars_from_rets([2 * r for r in br]),       # raw winner, no alpha
        "xyz:FLAT": _bars_from_rets(br),                         # residual ~0
        "xyz:NEG": _bars_from_rets([r - 0.005 for r in br]),     # −alpha
    }
    book = xs_xyz.rank_xyz(cbc, bench, lookback_days=7, k=2, beta_window=30)
    assert book.longs[0] == "xyz:ALPHA"          # not the raw-return winner
    assert book.shorts[-1] == "xyz:NEG"
    assert set(book.longs) | set(book.shorts) == set(cbc)


def test_rank_xyz_empty_book_below_2k_and_without_benchmark():
    br = _bench_rets()
    bench = _bars_from_rets(br)
    cbc = {"xyz:A": _bars_from_rets(br), "xyz:B": _bars_from_rets(br),
           "xyz:C": _bars_from_rets(br)}
    assert xs_xyz.rank_xyz(cbc, bench, 7, k=2).longs == []      # 3 < 2k
    assert xs_xyz.rank_xyz(cbc, [], 7, k=1).longs == []         # no benchmark → no residual


# ── pure engine: eligibility ─────────────────────────────────────────────────

def test_eligibility_history_floor_61_completed_bars():
    """Spec: >= 61 completed daily bars at min_history_bars=60."""
    br = _bench_rets(64)
    ok = {"xyz:OK": _bars_from_rets(br[:61])}                    # 61 bars
    young = {"xyz:YOUNG": _bars_from_rets(br[:60])}              # 60 bars
    assert "xyz:OK" in xs_xyz.filter_eligible(ok, 60, 250_000)
    assert xs_xyz.filter_eligible(young, 60, 250_000) == {}


def test_eligibility_30d_mean_notional_floor():
    br = _bench_rets(64)
    thin = {"xyz:THIN": _bars_from_rets(br, vol_usd=200_000.0)}   # < $250k
    fat = {"xyz:FAT": _bars_from_rets(br, vol_usd=250_000.0)}     # == floor
    assert xs_xyz.filter_eligible(thin, 60, 250_000) == {}
    assert "xyz:FAT" in xs_xyz.filter_eligible(fat, 60, 250_000)
    assert xs_xyz.mean_daily_notional(thin["xyz:THIN"], 30) == pytest.approx(200_000.0)


def test_completed_bars_drops_forming_daily_bar():
    now_ms = int(time.time() * 1000)
    bars = _bars_from_rets(_bench_rets(5), end_t=now_ms - _DAY_MS // 2)  # last bar forming
    kept = xs_xyz.completed_bars(bars, now_ms)
    assert len(kept) == len(bars) - 1
    closed = _bars_from_rets(_bench_rets(5), end_t=now_ms - _DAY_MS)     # just closed → kept
    assert len(xs_xyz.completed_bars(closed, now_ms)) == len(closed)


def test_universe_filter_xyz_equities_only():
    """xyz: prefix only; NON_EQUITY_XYZ (indices/commodities/fx/baskets) and
    the benchmark itself are never legs."""
    universe = [
        {"coin": "BTC"}, {"coin": "@107"}, {"coin": "km:US500"},
        {"coin": "xyz:MU"}, {"coin": "xyz:INTC"},
        {"coin": "xyz:XYZ100"},                   # the benchmark
        {"coin": "xyz:GOLD"}, {"coin": "xyz:DRAM"}, {"coin": "xyz:PURRDAT"},
        {"coin": "xyz:SPOTTY", "type": "spot"},
    ]
    got = xs_xyz.eligible_xyz_coins(universe, benchmark="xyz:XYZ100")
    assert got == ["xyz:MU", "xyz:INTC"]
    # a config'd benchmark outside NON_EQUITY_XYZ is still excluded from legs
    assert "xyz:MU" not in xs_xyz.eligible_xyz_coins(universe, benchmark="xyz:MU")


# ── kill criteria: the pre-committed numbers live as named constants ─────────

def test_kill_criteria_constants_pinned_to_spec():
    """W-X2 findings SPEC block: cumulative fwd net25 < 0 after 12 rebalances →
    shadow_only; any single rebalance EV < −8% → shadow_only; semis ablation
    check at rebalance 6. The constants are re-exported on the live module."""
    for mod in (xs_xyz, xxl):
        assert mod.KILL_CUM_NET25_REBALANCES == 12
        assert mod.KILL_SINGLE_REBALANCE_EV_PCT == -8.0
        assert mod.SEMIS_ABLATION_CHECK_REBALANCE == 6


# ── analysis: e248c13-style wide-only exit policy (mirror of the xs gate) ────

def test_xyz_analysis_carries_book_exit_policy():
    """Mirror of test_xs_analysis_carries_book_exit_policy: legs must never
    register under the MAIN-ENGINE DSL policy (30h timeout / 8h stale-flat /
    tight stop) — the 5d rebalance owns exits."""
    for side in ("long", "short"):
        a = xxl._analysis("xyz:MU", side, 0.12, hold_days=5.0)
        assert a["strategy_book"] == "xs_xyz_equities"
        dsl = a["dsl_exit_override"]
        assert dsl["max_loss_pct"] == 20.0                     # disaster stop only
        assert dsl["max_loss_roe_pct"] == 240.0
        assert dsl["protect_pct"] == 1000.0                    # phase-2 never arms
        assert dsl["retrace_threshold"] == 1.0
        assert dsl["hard_timeout_minutes"] == 5.0 * 1440.0     # the full hold
        assert dsl["breakeven_trigger_pct"] == 0.0
        assert dsl["breakeven_lock_pct"] == 0.0
        assert dsl["stale_flat_timeout_minutes"] == 0.0        # no flat-cutter
        assert dsl["atr_stop"] == {"enabled": False}
        assert dsl["noise_band"] == {"enabled": False}
        assert a["backup_sl_pct_override"] == 20.0
        assert a["tp_scale_fraction_override"] == 0.0          # no mid-hold banking
        # the global $20M short floor would block EVERY xyz short
        assert a["min_short_volume_usd_override"] == 250_000.0


# ── registries: claims, attribution, dashboard, loop wiring ─────────────────

def test_xs_xyz_in_active_claim_books():
    from hermes_trader.agents.rebalancer_owned import active_claim_books
    assert "xs_xyz_equities" in active_claim_books()


@pytest.fixture(scope="module")
def pbb():
    spec = importlib.util.spec_from_file_location(
        "pnl_by_book_xs_xyz_test", os.path.join(_REPO, "scripts", "pnl_by_book.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_xs_xyz_in_book_priority(pbb):
    assert "xs_xyz_equities" in pbb.BOOK_PRIORITY


def test_pnl_attribution_xs_xyz_rebalance_event(pbb, tmp_path, monkeypatch):
    """Live xs_xyz_rebalance events carry NO shadow key and must attribute to
    the book (the xs_rebalance invisibility lesson, W-X2 audit 2026-07-20);
    shadow-tagged events must NOT."""
    ts = int(time.time() * 1000)
    log = tmp_path / "session.jsonl"
    log.write_text(
        json.dumps({"ts": ts, "event": "xs_xyz_rebalance",
                    "open_long": ["xyz:MU"], "open_short": ["xyz:HOOD"]}) + "\n"
        + json.dumps({"ts": ts + 1, "event": "xs_xyz_rebalance", "shadow": True,
                      "open_long": ["xyz:NOPE"], "open_short": []}) + "\n")
    monkeypatch.setattr(pbb, "SESSION_LOG", str(log))
    foot = pbb.extract_footprints(0)
    assert ("xyz:MU", "long", ts) in foot["xs_xyz_equities"]
    assert ("xyz:HOOD", "short", ts) in foot["xs_xyz_equities"]
    assert all(c != "xyz:NOPE" for (c, _, _) in foot["xs_xyz_equities"])


def test_pnl_loop_log_module_maps_to_book(pbb):
    """The exact loop-log source: hermes_trader.agents.xs_xyz_live 'LIVE opened'
    lines must land on xs_xyz_equities, not a nonexistent 'xs_xyz' book."""
    assert pbb._log_module_to_book("xs_xyz_live") == "xs_xyz_equities"
    line = ("2026-07-20 08:07:01,658 INFO:hermes_trader.agents.xs_xyz_live:"
            "[xs-xyz] LIVE opened short xyz:HOOD (resid7 -9.1%)")
    m = pbb.OPEN_LINE_RE.match(line)
    assert m is not None and pbb._log_module_to_book(m.group(3)) == "xs_xyz_equities"


def test_dashboard_row_present():
    from hermes_trader import dashboard as db
    assert "xs_xyz_equities" in db._KNOWN_BOOK_NAMES
    row = next(b for b in db._BOOKS if b[0] == "xs_xyz_equities")
    assert row[1] == "xs_xyz_equities"
    assert "W-X2" in row[2] and "+0.65" in row[2]              # thesis cites the verdict
    assert db._EVENT_BOOK_ALIASES.get("xs_xyz_rebalance") == "xs_xyz_equities"


def test_loop_wiring_text_only():
    """Text-level assertion — scripts/trading_loop.py must NEVER be imported
    (importing it starts the LIVE loop)."""
    src = open(os.path.join(_REPO, "scripts", "trading_loop.py")).read()
    assert "from hermes_trader.agents.xs_xyz_live import maybe_rebalance as _xs_xyz_maybe_rebalance" in src
    i = src.index("_xs_xyz_maybe_rebalance(")
    block = src[max(0, i - 400):i + 400]
    assert "try:" in block and "except Exception as _xxe" in block   # non-fatal like neighbors
    assert "_book_execute" in block and "close_position_market" in block


def test_agent_config_block_values():
    cfg = json.load(open(os.path.join(_REPO, ".agent-config.json")))
    b = cfg["xs_xyz_equities"]
    # SPEC keys are frozen — do not "tune" these without a new validated cell.
    spec = {"enabled": True, "shadow_only": False, "lookback_days": 7,
            "k_per_leg": 5, "hold_days": 5, "min_volume_usd": 250000,
            "benchmark": "xyz:XYZ100", "max_book_positions": 10,
            "history_bars": 60}
    assert {k: b[k] for k in spec} == spec
    # SIZING is deliberately NOT the global fraction any more (2026-07-20): 10
    # simultaneous legs off the global 0.1 put 12x gross on the xyz dex, where a
    # 20pp momentum crash — momentum's documented failure mode, measured live
    # that day at -1.77pp on the L/S spread — would exceed the dex equity.
    assert b["equity_frac"] > 0
    assert "notional_usd" not in b and "equity_fraction" not in b
    assert set(b) - set(spec) == {"equity_frac"}


# ── live wiring: offline end-to-end rebalance ────────────────────────────────

_CFG = {"xs_xyz_equities": {"enabled": True, "shadow_only": False,
                            "lookback_days": 7, "k_per_leg": 2, "hold_days": 5,
                            "min_volume_usd": 250000, "benchmark": "xyz:XYZ100",
                            "max_book_positions": 10, "history_bars": 60}}

_UNIVERSE = [{"coin": c} for c in
             ("BTC", "xyz:XYZ100", "xyz:GOLD",
              "xyz:ALPHA", "xyz:BETA2", "xyz:FLAT", "xyz:NEG", "xyz:THIN")]


def _fake_fetch():
    br = _bench_rets()
    data = {
        "xyz:XYZ100": _bars_from_rets(br),
        "xyz:ALPHA": _bars_from_rets([r + 0.005 for r in br]),
        "xyz:BETA2": _bars_from_rets([2 * r for r in br]),
        "xyz:FLAT": _bars_from_rets(br),
        "xyz:NEG": _bars_from_rets([r - 0.005 for r in br]),
        "xyz:THIN": _bars_from_rets([r - 0.01 for r in br], vol_usd=100_000.0),
    }

    def fetch(coin, interval, n):
        assert interval == "1d"
        if coin not in data:
            raise RuntimeError(f"unexpected fetch {coin}")
        return data[coin][-n:]
    return fetch


@pytest.fixture()
def isolated_state(tmp_path, monkeypatch):
    """Fresh timer/owned/counter files + a fresh claims registry per test."""
    from hermes_trader.agents import rebalancer_owned as ro
    monkeypatch.setattr(xxl, "_TS_FILE", str(tmp_path / "ts"))
    monkeypatch.setattr(xxl, "_OWNED_FILE", str(tmp_path / "owned.json"))
    monkeypatch.setattr(xxl, "_COUNT_FILE", str(tmp_path / "n"))
    monkeypatch.setattr(xxl, "_owned", None)
    fresh = ro.ClaimsRegistry(str(tmp_path / "claims.json"),
                              active_books=ro.active_claim_books())
    monkeypatch.setattr(ro, "_claims_registry", fresh)
    events = []
    monkeypatch.setattr(xxl, "log_event", events.append)
    rows = []
    monkeypatch.setattr(xxl.shadow_ledger, "record_many",
                        lambda book, rs: rows.extend((book, r) for r in rs or []))
    return {"events": events, "ledger": rows, "claims": fresh}


def test_live_rebalance_end_to_end(isolated_state):
    executed, closed = [], []
    plan = xxl.maybe_rebalance(_CFG, _UNIVERSE, [], _fake_fetch(),
                               lambda a: (executed.append(a), {"executed": True})[1],
                               closed.append)
    # k=2 spread: ALPHA long (residual winner), NEG short — BETA2 residualized out of the top
    assert plan is not None
    assert "xyz:ALPHA" in plan["open_long"] and "xyz:NEG" in plan["open_short"]
    assert len(executed) == 4 and not closed
    assert all(a["strategy_book"] == "xs_xyz_equities" for a in executed)
    assert all(a["min_short_volume_usd_override"] == 250000.0 for a in executed)
    # thin coin never ranked (below the $250k mean-notional floor)
    assert all(a["coin"] != "xyz:THIN" for a in executed)

    # claims held by this book only
    claims = isolated_state["claims"].claims()
    assert set(claims) == {a["coin"] for a in executed}
    assert set(claims.values()) == {"xs_xyz_equities"}

    # ledger: one live row per target leg, kill bookkeeping attached
    rows = isolated_state["ledger"]
    assert len(rows) == 4 and all(b == "xs_xyz_equities" for b, _ in rows)
    for _, r in rows:
        assert r["horizon_days"] == 5.0 and r["stop_pct"] == 20.0
        assert r["entry_ref_px"] > 0 and r["signal_bar_t"] > 0
        assert r["meta"]["shadow"] is False and r["meta"]["rebalance_n"] == 1

    # session events: live rebalance event carries NO shadow key; book_open per leg
    evts = isolated_state["events"]
    rebal = [e for e in evts if e.get("event") == "xs_xyz_rebalance"]
    assert len(rebal) == 1 and "shadow" not in rebal[0]
    assert rebal[0]["rebalance_n"] == 1
    opens = [e for e in evts if e.get("event") == "book_open"]
    assert len(opens) == 4
    assert all(e["book"] == "xs_xyz_equities" and "shadow" not in e for e in opens)

    # timer armed: an immediate second call is a no-op
    assert xxl.maybe_rebalance(_CFG, _UNIVERSE, [], _fake_fetch(),
                               lambda a: {"executed": True}, closed.append) is None


def test_shadow_only_records_without_executing(isolated_state):
    cfg = {"xs_xyz_equities": dict(_CFG["xs_xyz_equities"], shadow_only=True)}
    executed, closed = [], []
    plan = xxl.maybe_rebalance(cfg, _UNIVERSE, [], _fake_fetch(),
                               lambda a: executed.append(a), closed.append)
    assert plan is not None and not executed and not closed
    rebal = [e for e in isolated_state["events"] if e.get("event") == "xs_xyz_rebalance"]
    assert len(rebal) == 1 and rebal[0]["shadow"] is True
    assert isolated_state["claims"].claims() == {}
    assert all(r["meta"]["shadow"] is True for _, r in isolated_state["ledger"])


def test_benchmark_failure_does_not_arm_timer(isolated_state):
    def broken(coin, interval, n):
        if coin == "xyz:XYZ100":
            raise RuntimeError("bench down")
        return _fake_fetch()(coin, interval, n)
    executed = []
    assert xxl.maybe_rebalance(_CFG, _UNIVERSE, [], broken,
                               lambda a: executed.append(a), lambda c: None) is None
    assert not executed and not isolated_state["events"]
    # next cycle with a healthy fetch fires normally (timer never armed)
    plan = xxl.maybe_rebalance(_CFG, _UNIVERSE, [], _fake_fetch(),
                               lambda a: (executed.append(a), {"executed": True})[1],
                               lambda c: None)
    assert plan is not None and len(executed) == 4


def test_disabled_and_not_time_are_noops(isolated_state):
    assert xxl.maybe_rebalance({}, _UNIVERSE, [], _fake_fetch(),
                               lambda a: None, lambda c: None) is None
    assert xxl.maybe_rebalance({"xs_xyz_equities": {"enabled": False}}, _UNIVERSE,
                               [], _fake_fetch(), lambda a: None, lambda c: None) is None
    assert not isolated_state["events"] and not isolated_state["ledger"]


# ---------------------------------------------- per-book sizing bound
def test_analysis_carries_per_book_equity_frac_when_set():
    """10 simultaneous legs off the GLOBAL fraction put 12x gross on the xyz
    dex (measured 2026-07-20). A momentum crash is momentum's documented
    failure mode and the market-neutral hedge does NOT hold through one, so
    this book bounds its own gross without shrinking the crypto xs book."""
    from hermes_trader.agents import xs_xyz_live as xl
    a = xl._analysis("xyz:AAPL", "long", 0.05, equity_frac=0.04)
    assert a["strategy_book_equity_frac_override"] == 0.04


def test_analysis_falls_back_to_global_path_when_unset():
    from hermes_trader.agents import xs_xyz_live as xl
    for frac in (0, 0.0, None):
        a = xl._analysis("xyz:AAPL", "long", 0.05, equity_frac=frac or 0)
        assert "strategy_book_equity_frac_override" not in a


def test_configured_frac_keeps_xyz_gross_survivable():
    """A 20pp momentum crash — inside the historical range — must not be able
    to exceed the xyz dex equity. At the old 12x it cost 120% of it."""
    import json
    from pathlib import Path
    cfg = json.loads((Path(__file__).resolve().parents[1] / ".agent-config.json").read_text())
    frac = float(cfg["xs_xyz_equities"]["equity_frac"])
    lev = float(cfg["leverage"])
    legs = 2 * int(cfg["xs_xyz_equities"]["k_per_leg"])
    gross_mult = frac * lev * legs          # gross notional / dex equity
    assert gross_mult <= 6.0, f"xs_xyz gross {gross_mult:.1f}x is too hot"
    assert (gross_mult / 2) * 0.20 < 1.0, "a 20pp crash would exceed dex equity"
