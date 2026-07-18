"""Gate tests for the zero-capital wallet_follow recorder (VERIFIED_TRADERS.md §4).

All offline: synthetic clearinghouse position-delta fixtures, injected fetch,
captured ledger writes, throttle/dedup invariants, matched-null arithmetic,
and a text/ast smoke check of the v1 loop wiring (never imports the loop).
"""
from __future__ import annotations

import ast
import json
import time
from pathlib import Path

import pytest

from hermes_trader.agents import wallet_follow_recorder as wf

_REPO = Path(__file__).resolve().parents[1]
_NOW = int(time.time() * 1000)
_H = wf._HOUR_MS
_BAR_T = (_NOW // _H) * _H
W1, W2 = wf.FOLLOW_SET[0], wf.FOLLOW_SET[1]


@pytest.fixture(autouse=True)
def _iso_state(tmp_path, monkeypatch):
    monkeypatch.setattr(wf, "_STATE_FILE", str(tmp_path / "state.json"))


def _captured(monkeypatch):
    out = []
    monkeypatch.setattr(wf.shadow_ledger, "record",
                        lambda book, **kw: out.append((book, kw)) or {})
    return out


def _uni(*pairs):
    return [{"coin": c, "midPx": px, "dayNtlVlm": 1e8} for c, px in pairs]


def _fetcher(book):
    """book: {addr: {coin: (szi, entry_px, ntl)}} -> injected fetch_positions."""
    def fetch(addr):
        w = book.get(addr)
        if w is None:
            return None
        return {c: {"szi": s[0], "entry_px": s[1], "ntl": s[2]}
                for c, s in w.items() if s[0] != 0.0}
    return fetch


def _baseline(monkeypatch, book=None, now_ms=_NOW - 3_600_000 * 2):
    """First poll: every wallet baselined (positions in `book` NOT recorded)."""
    book = book if book is not None else {a: {} for a in wf.FOLLOW_SET}
    for a in wf.FOLLOW_SET:
        book.setdefault(a, {})
    assert wf.maybe_record({}, _uni(), now_ms=now_ms, fetch_positions=_fetcher(book)) == 0
    return book


# ── Signal derivation from synthetic position deltas ──────────────────────────

class TestSignalDerivation:
    def test_open_long_records_gradeable_row_with_spec_shape(self, monkeypatch):
        out = _captured(monkeypatch)
        _baseline(monkeypatch)
        book = {a: {} for a in wf.FOLLOW_SET}
        book[W1] = {"HYPE": (10.0, 40.1, 401.0)}
        n = wf.maybe_record({}, _uni(("HYPE", 40.25)), now_ms=_NOW,
                            fetch_positions=_fetcher(book))
        assert n == 1
        ledger_book, kw = out[0]
        assert ledger_book == "wallet_follow"
        assert kw["coin"] == "HYPE" and kw["side"] == "long"
        assert kw["entry_ref_px"] == 40.25                     # OUR mid at detection
        assert kw["horizon_days"] == 3.0 and kw["stop_pct"] == 20.0
        assert kw["signal_bar_t"] == _BAR_T
        m = kw["meta"]
        assert m["wallet"] == W1 and m["consensus_n"] == 1
        assert m["wallet_entry_px"] == 40.1 and m["wallet_ntl"] == 401.0
        assert m["flipped"] is False

    def test_open_short_and_sign_flip(self, monkeypatch):
        out = _captured(monkeypatch)
        _baseline(monkeypatch, {W1: {"ETH": (2.0, 3000.0, 6000.0)}})
        book = {a: {} for a in wf.FOLLOW_SET}
        book[W1] = {"ETH": (-1.5, 3100.0, 4650.0)}             # long -> short flip
        book[W2] = {"BTC": (-0.1, 90000.0, 9000.0)}            # fresh short
        n = wf.maybe_record({}, _uni(("ETH", 3105.0), ("BTC", 90100.0)), now_ms=_NOW,
                            fetch_positions=_fetcher(book))
        assert n == 2
        by_coin = {kw["coin"]: kw for _, kw in out}
        assert by_coin["ETH"]["side"] == "short" and by_coin["ETH"]["meta"]["flipped"] is True
        assert by_coin["BTC"]["side"] == "short" and by_coin["BTC"]["meta"]["flipped"] is False

    def test_close_records_meta_only_row(self, monkeypatch):
        out = _captured(monkeypatch)
        _baseline(monkeypatch, {W1: {"SOL": (-5.0, 150.0, 750.0)}})
        book = {a: {} for a in wf.FOLLOW_SET}                  # SOL gone -> szi 0
        n = wf.maybe_record({}, _uni(("SOL", 149.0)), now_ms=_NOW,
                            fetch_positions=_fetcher(book))
        assert n == 0                                          # meta rows don't count
        _, kw = out[0]
        assert kw["side"] == "meta_close" and kw["horizon_days"] == 0.0
        assert kw["meta"]["closed_side"] == "short"
        assert kw["meta"]["exit_ref_px"] == 149.0 and kw["meta"]["exit_ts"] == _NOW

    def test_add_threshold_25pct(self, monkeypatch):
        out = _captured(monkeypatch)
        _baseline(monkeypatch, {W1: {"HYPE": (10.0, 40.0, 400.0)}})
        book = {a: {} for a in wf.FOLLOW_SET}
        book[W1] = {"HYPE": (12.0, 40.0, 480.0)}               # +20% -> below bar
        assert wf.maybe_record({}, _uni(("HYPE", 40.0)), now_ms=_NOW,
                               fetch_positions=_fetcher(book)) == 0
        assert out == []
        book[W1] = {"HYPE": (15.0, 40.0, 600.0)}               # +25% vs new base 12
        wf.maybe_record({}, _uni(("HYPE", 40.0)), now_ms=_NOW + 31 * 60_000,
                        fetch_positions=_fetcher(book))
        _, kw = out[0]
        assert kw["side"] == "meta_add" and kw["horizon_days"] == 0.0
        assert kw["meta"]["prev_szi"] == 12.0 and kw["meta"]["new_szi"] == 15.0

    def test_bootstrap_never_records_stale_positions(self, monkeypatch):
        out = _captured(monkeypatch)
        book = {a: {} for a in wf.FOLLOW_SET}
        book[W1] = {"HYPE": (10.0, 40.0, 400.0), "ETH": (-2.0, 3000.0, 6000.0)}
        assert wf.maybe_record({}, _uni(("HYPE", 40.0), ("ETH", 3000.0)), now_ms=_NOW,
                               fetch_positions=_fetcher(book)) == 0
        assert out == []

    def test_fetch_failure_keeps_state_no_fake_closes(self, monkeypatch):
        out = _captured(monkeypatch)
        _baseline(monkeypatch, {W1: {"HYPE": (10.0, 40.0, 400.0)}})
        n = wf.maybe_record({}, _uni(("HYPE", 40.0)), now_ms=_NOW,
                            fetch_positions=lambda addr: None)  # full outage
        assert n == 0 and out == []
        book = {a: {} for a in wf.FOLLOW_SET}                   # recovered, position gone
        wf.maybe_record({}, _uni(("HYPE", 41.0)), now_ms=_NOW + 31 * 60_000,
                        fetch_positions=_fetcher(book))
        assert [kw["side"] for _, kw in out] == ["meta_close"]  # close, not re-open

    def test_no_mid_defers_open_and_retries_next_poll(self, monkeypatch):
        out = _captured(monkeypatch)
        _baseline(monkeypatch)
        book = {a: {} for a in wf.FOLLOW_SET}
        book[W1] = {"NEWCOIN": (3.0, 1.0, 3.0)}
        assert wf.maybe_record({}, _uni(("OTHER", 5.0)), now_ms=_NOW,
                               fetch_positions=_fetcher(book)) == 0
        assert out == []
        n = wf.maybe_record({}, _uni(("NEWCOIN", 1.1)), now_ms=_NOW + 31 * 60_000,
                            fetch_positions=_fetcher(book))
        assert n == 1 and out[0][1]["entry_ref_px"] == 1.1


# ── Dedup (one open signal per coin+side until resolved) ──────────────────────

class TestDedup:
    def test_second_wallet_becomes_consensus_meta_not_new_row(self, monkeypatch):
        out = _captured(monkeypatch)
        _baseline(monkeypatch)
        book = {a: {} for a in wf.FOLLOW_SET}
        book[W1] = {"HYPE": (10.0, 40.0, 400.0)}
        assert wf.maybe_record({}, _uni(("HYPE", 40.0)), now_ms=_NOW,
                               fetch_positions=_fetcher(book)) == 1
        book[W2] = {"HYPE": (99.0, 40.5, 4009.5)}
        assert wf.maybe_record({}, _uni(("HYPE", 40.6)), now_ms=_NOW + 31 * 60_000,
                               fetch_positions=_fetcher(book)) == 0
        sides = [kw["side"] for _, kw in out]
        assert sides == ["long", "meta_consensus"]
        assert out[1][1]["meta"]["consensus_n"] == 2
        assert out[1][1]["meta"]["wallet"] == W2

    def test_opposite_side_is_a_separate_signal(self, monkeypatch):
        out = _captured(monkeypatch)
        _baseline(monkeypatch)
        book = {a: {} for a in wf.FOLLOW_SET}
        book[W1] = {"HYPE": (10.0, 40.0, 400.0)}
        wf.maybe_record({}, _uni(("HYPE", 40.0)), now_ms=_NOW,
                        fetch_positions=_fetcher(book))
        book[W2] = {"HYPE": (-5.0, 40.5, 202.5)}
        n = wf.maybe_record({}, _uni(("HYPE", 40.5)), now_ms=_NOW + 31 * 60_000,
                            fetch_positions=_fetcher(book))
        assert n == 1
        assert [kw["side"] for _, kw in out] == ["long", "short"]

    def test_lock_expires_after_resolution_window(self, monkeypatch):
        out = _captured(monkeypatch)
        _baseline(monkeypatch)
        book = {a: {} for a in wf.FOLLOW_SET}
        book[W1] = {"HYPE": (10.0, 40.0, 400.0)}
        wf.maybe_record({}, _uni(("HYPE", 40.0)), now_ms=_NOW,
                        fetch_positions=_fetcher(book))
        # W1 closes + reopens AFTER the 3d-horizon resolve window -> new episode
        later = _NOW + wf.resolve_after_ms(wf.HORIZON_DAYS) + _H
        book[W1] = {}
        wf.maybe_record({}, _uni(("HYPE", 42.0)), now_ms=later,
                        fetch_positions=_fetcher(book))
        book[W1] = {"HYPE": (8.0, 42.0, 336.0)}
        n = wf.maybe_record({}, _uni(("HYPE", 42.1)), now_ms=later + 31 * 60_000,
                            fetch_positions=_fetcher(book))
        assert n == 1
        assert [kw["side"] for _, kw in out] == ["long", "meta_close", "long"]

    def test_same_wallet_rebuy_inside_window_records_nothing(self, monkeypatch):
        out = _captured(monkeypatch)
        _baseline(monkeypatch)
        book = {a: {} for a in wf.FOLLOW_SET}
        book[W1] = {"HYPE": (10.0, 40.0, 400.0)}
        wf.maybe_record({}, _uni(("HYPE", 40.0)), now_ms=_NOW,
                        fetch_positions=_fetcher(book))
        book[W1] = {}
        wf.maybe_record({}, _uni(("HYPE", 40.0)), now_ms=_NOW + 31 * 60_000,
                        fetch_positions=_fetcher(book))          # meta_close
        book[W1] = {"HYPE": (10.0, 40.0, 400.0)}
        n = wf.maybe_record({}, _uni(("HYPE", 40.0)), now_ms=_NOW + 62 * 60_000,
                            fetch_positions=_fetcher(book))      # rebuy inside window
        assert n == 0
        assert [kw["side"] for _, kw in out] == ["long", "meta_close"]


# ── Throttle + config ─────────────────────────────────────────────────────────

class TestThrottleAndConfig:
    def test_polls_are_throttled_to_poll_minutes(self, monkeypatch):
        calls = []
        _captured(monkeypatch)
        fetch = lambda addr: calls.append(addr) or {}
        assert wf.maybe_record({}, _uni(), now_ms=_NOW, fetch_positions=fetch) == 0
        assert len(calls) == 9                                  # one poll, 9 wallets
        wf.maybe_record({}, _uni(), now_ms=_NOW + 29 * 60_000, fetch_positions=fetch)
        assert len(calls) == 9                                  # inside window: no poll
        wf.maybe_record({}, _uni(), now_ms=_NOW + 30 * 60_000, fetch_positions=fetch)
        assert len(calls) == 18

    def test_custom_poll_minutes(self, monkeypatch):
        calls = []
        _captured(monkeypatch)
        fetch = lambda addr: calls.append(addr) or {}
        cfg = {"wallet_follow": {"poll_minutes": 60}}
        wf.maybe_record(cfg, _uni(), now_ms=_NOW, fetch_positions=fetch)
        wf.maybe_record(cfg, _uni(), now_ms=_NOW + 45 * 60_000, fetch_positions=fetch)
        assert len(calls) == 9
        wf.maybe_record(cfg, _uni(), now_ms=_NOW + 61 * 60_000, fetch_positions=fetch)
        assert len(calls) == 18

    def test_hot_kill(self, monkeypatch):
        out = _captured(monkeypatch)
        n = wf.maybe_record({"wallet_follow": {"enabled": False}}, _uni(),
                            now_ms=_NOW, fetch_positions=lambda a: {})
        assert n == 0 and out == []

    def test_shipped_config_has_the_key(self):
        cfg = json.loads((_REPO / ".agent-config.json").read_text())
        assert cfg["wallet_follow"] == {"enabled": True, "poll_minutes": 30}


# ── Follow set vs the frozen research file ────────────────────────────────────

class TestFollowSet:
    def test_follow_set_matches_frozen_verified_traders_data(self):
        data = json.loads((_REPO / "research" / "rebuild_2026_07_18" /
                           "verified_traders_data.json").read_text())
        frozen = {d["address"].lower() for d in data}
        assert len(wf.FOLLOW_SET) == 9                          # spec §4: the 9 copyable
        assert len(set(wf.FOLLOW_SET)) == 9
        for addr in wf.FOLLOW_SET:
            assert addr.lower() in frozen, f"{addr} not in frozen file"

    def test_hft_wallets_excluded(self):
        # spec §4: 0xa312114b and 0xf02d16a2 fail the >= 4h bar
        for banned in ("0xa312114b", "0xf02d16a2"):
            assert not any(a.lower().startswith(banned.lower())
                           for a in wf.FOLLOW_SET)


# ── Matched random-time null ──────────────────────────────────────────────────

def _bars(closes, start_t=0, day=86_400_000):
    return [{"t": start_t + i * day, "o": c, "h": c * 1.001, "l": c * 0.999,
             "c": c, "v": 1.0} for i, c in enumerate(closes)]


class TestMatchedNull:
    def test_defaults_match_spec(self):
        assert wf.MC_N_DRAWS == 2000
        assert wf.MC_COST_BPS == 12.0
        assert wf.MC_P_REQUIRED == 0.01

    def test_flat_tape_gives_uninformative_p(self):
        bars = _bars([100.0] * 40)
        events = [{"coin": "AAA", "side": "long", "horizon_days": 3.0,
                   "stop_pct": 20.0, "ret": -0.0012}]          # exactly the null EV
        res = wf.mc_null_pvalue(events, {"AAA": bars}, n_draws=300, seed=7)
        assert res is not None and res["n_draws"] == 300
        assert res["p"] == 1.0                                  # every null draw ties
        assert res["pass"] is False

    def test_signal_beating_a_flat_null_gets_small_p(self):
        bars = _bars([100.0] * 40)                              # null return = -12bps
        events = [{"coin": "AAA", "side": "long", "horizon_days": 3.0,
                   "stop_pct": 20.0, "ret": 0.05}] * 5          # +5% observed
        res = wf.mc_null_pvalue(events, {"AAA": bars}, n_draws=500, seed=7)
        assert res["p"] == 0.0 and res["pass"] is True

    def test_deterministic_under_seed_and_none_when_untestable(self):
        bars = _bars([100 + i for i in range(40)])
        events = [{"coin": "AAA", "side": "short", "horizon_days": 3.0,
                   "stop_pct": 20.0, "ret": 0.01}]
        a = wf.mc_null_pvalue(events, {"AAA": bars}, n_draws=200, seed=42)
        b = wf.mc_null_pvalue(events, {"AAA": bars}, n_draws=200, seed=42)
        assert a == b
        assert wf.mc_null_pvalue(events, {"AAA": _bars([100.0] * 3)}) is None
        assert wf.mc_null_pvalue([], {"AAA": bars}) is None


# ── Ledger row shape end-to-end (real shadow_ledger append) ───────────────────

def test_ledger_row_shape_via_real_append(tmp_path, monkeypatch):
    monkeypatch.setattr(wf.shadow_ledger, "_ledger_dir", lambda: str(tmp_path))
    _baseline(monkeypatch, book={a: {} for a in wf.FOLLOW_SET})
    book = {a: {} for a in wf.FOLLOW_SET}
    book[W1] = {"HYPE": (10.0, 40.0, 400.0)}
    assert wf.maybe_record({}, _uni(("HYPE", 40.25)), now_ms=_NOW,
                           fetch_positions=_fetcher(book)) == 1
    rows = wf.shadow_ledger.load("wallet_follow")
    assert len(rows) == 1
    r = rows[0]
    assert r["v"] == 1 and r["book"] == "wallet_follow"
    assert r["side"] == "long" and r["entry_ref_px"] == 40.25
    assert r["horizon_days"] == 3.0 and r["stop_pct"] == 20.0
    assert r["signal_bar_t"] % 3_600_000 == 0
    assert r["meta"]["shadow"] is True
    # the row is gradeable by the standard grader
    kept, dropped = wf.shadow_ledger.dedup_episodes(rows)
    assert len(kept) == 1 and dropped == 0


# ── Loop wiring smoke (text/ast — NEVER imports scripts/trading_loop.py) ──────

class TestLoopWiring:
    SRC = (_REPO / "scripts" / "trading_loop.py").read_text()

    def test_import_line_present(self):
        assert ("from hermes_trader.agents.wallet_follow_recorder import "
                "maybe_record as _wallet_follow_maybe_record") in self.SRC

    def test_exactly_one_callsite_wrapped_in_try_except(self):
        tree = ast.parse(self.SRC)
        calls_in_try: dict = {}                # id(node) -> node (nested Trys see it twice)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            for sub in ast.walk(node):
                if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
                        and sub.func.id == "_wallet_follow_maybe_record"):
                    assert node.handlers, "call-site has no except handler"
                    calls_in_try[id(sub)] = sub
        assert len(calls_in_try) == 1
        call = next(iter(calls_in_try.values()))
        # config first, universe second (module signature)
        assert isinstance(call.args[0], ast.Call) and call.args[0].func.id == "read_agent_config"
        assert isinstance(call.args[1], ast.Name) and call.args[1].id == "universe"

    def test_never_imported_by_v2(self):
        v2 = _REPO / "hermes_trader" / "v2"
        for f in v2.glob("*.py"):
            assert "wallet_follow" not in f.read_text(), f"{f} imports wallet_follow"
