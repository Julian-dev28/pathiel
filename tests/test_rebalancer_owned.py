"""Tests for the OwnedPositions ownership tracker (rebalancer_owned.py) and the integration
with the four live rebalancers: xs_momentum, vol_dispersion, sortino, amihud.

Critical invariants tested:
 (a) A rebalancer's close list NEVER includes a foreign position (one it didn't open).
 (b) Tracked set updates correctly on open and close.
 (c) Externally-vanished coins are pruned from the owned set.
 (d) max_open_pairs cap in pairs_live enforces the slot limit.

All tests use tmp paths so they never touch live state files.
"""
import json
import os
import tempfile
import pytest

from hermes_trader.agents.rebalancer_owned import OwnedPositions, _live_coin_set


# ─────────────────────────────────────────────────────────────────────────────
# Helper: build a minimal live-positions list in the format all rebalancers expect
# ─────────────────────────────────────────────────────────────────────────────

def _pos(coin: str, szi: float):
    """Wrap a coin+size into the nested dict the rebalancers receive from the loop."""
    return {"position": {"coin": coin, "szi": szi}}


# ═══════════════════════════════════════════════════════════════════════════════
# Unit tests: OwnedPositions
# ═══════════════════════════════════════════════════════════════════════════════

def test_owned_starts_empty(tmp_path):
    op = OwnedPositions(str(tmp_path / "owned.json")).load()
    longs, shorts = op.current_book()
    assert longs == [] and shorts == []


def test_add_long_records_coin(tmp_path):
    op = OwnedPositions(str(tmp_path / "owned.json")).load()
    op.add("BTC", "long")
    longs, shorts = op.current_book()
    assert "BTC" in longs and "BTC" not in shorts


def test_add_short_records_coin(tmp_path):
    op = OwnedPositions(str(tmp_path / "owned.json")).load()
    op.add("ETH", "short")
    longs, shorts = op.current_book()
    assert "ETH" in shorts and "ETH" not in longs


def test_add_side_flip_moves_coin(tmp_path):
    """Adding a coin to the opposite side removes it from the old side (no dual membership)."""
    op = OwnedPositions(str(tmp_path / "owned.json")).load()
    op.add("SOL", "long")
    op.add("SOL", "short")   # flip to short
    longs, shorts = op.current_book()
    assert "SOL" not in longs
    assert "SOL" in shorts


def test_remove_coin_from_longs(tmp_path):
    op = OwnedPositions(str(tmp_path / "owned.json")).load()
    op.add("BNB", "long")
    op.remove("BNB")
    longs, shorts = op.current_book()
    assert "BNB" not in longs and "BNB" not in shorts


def test_remove_coin_from_shorts(tmp_path):
    op = OwnedPositions(str(tmp_path / "owned.json")).load()
    op.add("LINK", "short")
    op.remove("LINK")
    longs, shorts = op.current_book()
    assert "LINK" not in shorts


def test_remove_nonexistent_is_noop(tmp_path):
    """Removing a coin we never opened should not raise."""
    op = OwnedPositions(str(tmp_path / "owned.json")).load()
    op.remove("GHOST")   # must not raise
    assert op.current_book() == ([], [])


def test_save_and_reload(tmp_path):
    path = str(tmp_path / "owned.json")
    op1 = OwnedPositions(path).load()
    op1.add("BTC", "long")
    op1.add("ETH", "short")
    op1.save()

    op2 = OwnedPositions(path).load()
    longs, shorts = op2.current_book()
    assert "BTC" in longs
    assert "ETH" in shorts


def test_save_and_reload_empty(tmp_path):
    path = str(tmp_path / "owned.json")
    op1 = OwnedPositions(path).load()
    op1.save()   # save empty set

    op2 = OwnedPositions(path).load()
    assert op2.current_book() == ([], [])


def test_load_missing_file_starts_empty(tmp_path):
    op = OwnedPositions(str(tmp_path / "missing.json")).load()
    assert op.current_book() == ([], [])


def test_load_corrupt_file_starts_empty(tmp_path):
    path = str(tmp_path / "corrupt.json")
    with open(path, "w") as fh:
        fh.write("NOT VALID JSON {{{")
    op = OwnedPositions(path).load()
    assert op.current_book() == ([], [])


# ─────────────────────────────────────────────────────────────────────────────
# (b) Tracked set updates correctly on open and close
# ─────────────────────────────────────────────────────────────────────────────

def test_tracked_set_after_sequence(tmp_path):
    """Open BTC/ETH as longs, SOL as short; close ETH; BTC+SOL remain, ETH gone."""
    op = OwnedPositions(str(tmp_path / "owned.json")).load()
    op.add("BTC", "long")
    op.add("ETH", "long")
    op.add("SOL", "short")
    op.remove("ETH")
    longs, shorts = op.current_book()
    assert "BTC" in longs
    assert "ETH" not in longs
    assert "SOL" in shorts


# ─────────────────────────────────────────────────────────────────────────────
# (c) Externally-vanished coins are pruned
# ─────────────────────────────────────────────────────────────────────────────

def test_prune_removes_coins_not_in_live(tmp_path):
    """If BTC is in our owned set but not in live positions (stopped out), prune drops it."""
    op = OwnedPositions(str(tmp_path / "owned.json")).load()
    op.add("BTC", "long")
    op.add("ETH", "short")
    # Live account only has ETH now (BTC was stopped out)
    live = {"ETH"}
    op.prune(live)
    longs, shorts = op.current_book()
    assert "BTC" not in longs
    assert "ETH" in shorts


def test_prune_keeps_coins_still_live(tmp_path):
    op = OwnedPositions(str(tmp_path / "owned.json")).load()
    op.add("BTC", "long")
    op.add("ETH", "short")
    live = {"BTC", "ETH"}
    op.prune(live)
    longs, shorts = op.current_book()
    assert "BTC" in longs and "ETH" in shorts


def test_prune_empty_live_clears_all(tmp_path):
    op = OwnedPositions(str(tmp_path / "owned.json")).load()
    op.add("BTC", "long")
    op.add("ETH", "short")
    op.prune(set())   # no live positions at all
    longs, shorts = op.current_book()
    assert longs == [] and shorts == []


# ─────────────────────────────────────────────────────────────────────────────
# filter_to_owned: intersection of owned set with live positions
# ─────────────────────────────────────────────────────────────────────────────

def test_filter_to_owned_excludes_foreign_positions(tmp_path):
    """The key invariant: a coin in live positions that we did NOT open is NOT in cur_long/cur_short.

    This is the CORE of the fix: a foreign position (opened by the thought-engine or another
    rebalancer) is NOT in our owned set → cannot appear in close_long/close_short.
    """
    op = OwnedPositions(str(tmp_path / "owned.json")).load()
    op.add("BTC", "long")    # we opened this

    # Live positions: BTC (ours) + ETH (opened by the thought-engine — foreign)
    positions = [_pos("BTC", 1.0), _pos("ETH", 1.0)]

    cur_long, cur_short = op.filter_to_owned(positions)
    assert "BTC" in cur_long      # ours → in cur_long
    assert "ETH" not in cur_long  # foreign → MUST NOT appear


def test_filter_to_owned_intersection_only(tmp_path):
    """Owned but not live (stopped out) AND live but not owned both excluded from cur."""
    op = OwnedPositions(str(tmp_path / "owned.json")).load()
    op.add("BTC", "long")    # we opened it
    op.add("SOL", "long")    # we opened it but it was stopped out (not in live positions)

    positions = [_pos("BTC", 1.0), _pos("ETH", 1.0)]  # ETH is foreign

    cur_long, cur_short = op.filter_to_owned(positions)
    assert cur_long == ["BTC"]      # only the intersection
    assert cur_short == []


def test_filter_to_owned_short_side(tmp_path):
    op = OwnedPositions(str(tmp_path / "owned.json")).load()
    op.add("ETH", "short")

    positions = [_pos("ETH", -1.0), _pos("BTC", -1.0)]   # BTC short is foreign

    cur_long, cur_short = op.filter_to_owned(positions)
    assert "ETH" in cur_short
    assert "BTC" not in cur_short


def test_filter_to_owned_all_foreign(tmp_path):
    """If we own nothing, filter_to_owned returns empty regardless of live positions."""
    op = OwnedPositions(str(tmp_path / "owned.json")).load()
    positions = [_pos("BTC", 1.0), _pos("ETH", -1.0), _pos("SOL", 1.0)]
    cur_long, cur_short = op.filter_to_owned(positions)
    assert cur_long == [] and cur_short == []


def test_filter_to_owned_zero_szi_excluded(tmp_path):
    """A position with szi=0 should not count as live (it's closed)."""
    op = OwnedPositions(str(tmp_path / "owned.json")).load()
    op.add("BTC", "long")
    positions = [_pos("BTC", 0.0)]   # szi=0 → not live
    cur_long, cur_short = op.filter_to_owned(positions)
    assert cur_long == []


# ─────────────────────────────────────────────────────────────────────────────
# _live_coin_set helper
# ─────────────────────────────────────────────────────────────────────────────

def test_live_coin_set_extracts_nonzero(tmp_path):
    positions = [_pos("BTC", 1.0), _pos("ETH", -0.5), _pos("SOL", 0.0)]
    live = _live_coin_set(positions)
    assert "BTC" in live
    assert "ETH" in live
    assert "SOL" not in live


def test_live_coin_set_empty(tmp_path):
    assert _live_coin_set([]) == set()
    assert _live_coin_set(None) == set()


# ═══════════════════════════════════════════════════════════════════════════════
# (a) Integration: rebalancer close list NEVER contains a foreign position
#
# We test this via the xs_momentum_live module, then verify the same property
# holds for vol_dispersion, sortino, and amihud by exercising their live paths.
# ═══════════════════════════════════════════════════════════════════════════════

def _fetch_factory(rets):
    """fetch(coin, interval, n) -> minimal 2-bar series for xs_momentum."""
    def fetch(coin, interval, n):
        r = rets.get(coin, 0.0)
        return [{"t": 0, "o": 100, "h": 100, "l": 100, "c": 100, "v": 1},
                {"t": 1, "o": 100 * (1 + r), "h": 100 * (1 + r), "l": 100 * (1 + r),
                 "c": 100 * (1 + r), "v": 1}]
    return fetch


def _uni(coins):
    return [{"coin": c, "dayNtlVlm": 1e8, "type": "perp"} for c in coins]


_XS_CFG = {"xs_momentum": {"enabled": True, "shadow_mode": False, "lookback_days": 1,
                            "hold_days": 10, "k_per_leg": 2, "universe_top_n": 50,
                            "min_volume_usd": 1e6, "vol_gate": False}}
_RETS = {"A": 0.50, "B": 0.20, "C": 0.05, "D": -0.05, "E": -0.20, "F": -0.40}


def test_xs_momentum_close_never_includes_foreign(tmp_path, monkeypatch):
    """Core invariant: xs_momentum LIVE close list must NOT contain coins it never opened.

    Setup: foreign coin X (opened by thought-engine) is in live positions.
    The rebalancer's owned set is empty (it hasn't opened anything).
    Expected: X must never appear in close_fn calls.
    """
    import hermes_trader.agents.xs_momentum_live as xl

    # Point owned state to tmp dir
    monkeypatch.setattr(xl, "_OWNED_FILE", str(tmp_path / ".xs_momentum_positions.json"))
    monkeypatch.setattr(xl, "_owned", None)   # force reload from tmp

    monkeypatch.setattr(xl, "_last_ts", lambda: 0.0)
    monkeypatch.setattr(xl, "_save_ts", lambda t: None)
    monkeypatch.setattr(xl, "log_event", lambda e: None)

    closed = []
    opened = []
    close_fn = lambda coin: closed.append(coin)
    execute_fn = lambda analysis: opened.append(analysis["coin"])

    # X is a foreign position (thought-engine opened it), not in xs_momentum's owned set
    positions = [_pos("X", 1.0)]

    xl.maybe_rebalance(_XS_CFG, _uni(_RETS), positions,
                       _fetch_factory(_RETS), execute_fn, close_fn)

    assert "X" not in closed, f"Foreign coin X was closed by xs_momentum: {closed}"


def test_xs_momentum_only_closes_own_coins(tmp_path, monkeypatch):
    """xs_momentum opened B (long), target drops B. It should close B but NOT foreign X."""
    import hermes_trader.agents.xs_momentum_live as xl

    owned_path = str(tmp_path / ".xs_momentum_positions.json")
    # Pre-populate: xs_momentum previously opened B as long
    with open(owned_path, "w") as fh:
        json.dump({"longs": ["B"], "shorts": []}, fh)

    monkeypatch.setattr(xl, "_OWNED_FILE", owned_path)
    monkeypatch.setattr(xl, "_owned", None)

    monkeypatch.setattr(xl, "_last_ts", lambda: 0.0)
    monkeypatch.setattr(xl, "_save_ts", lambda t: None)
    monkeypatch.setattr(xl, "log_event", lambda e: None)

    closed = []
    opened = []
    # Target book: top 2 longs = A, B; B is still in target so no close expected.
    # But let's set rets so B is NOT in the top-2 anymore by excluding it.
    # _RETS has A=0.5, B=0.2 → B stays in top-2. To test a close, use rets where B is lowest.
    custom_rets = {"A": 0.50, "X_foreign": 0.40, "C": 0.05, "D": -0.05, "E": -0.20, "F": -0.40}
    # B is no longer in universe so it won't be in target; owned set has B → should close B.
    # X_foreign is in live positions but NOT in owned → must NOT be closed.
    positions = [_pos("B", 1.0), _pos("X_foreign", 1.0)]  # X_foreign is a foreign coin

    xl.maybe_rebalance(_XS_CFG, _uni(custom_rets), positions,
                       _fetch_factory(custom_rets), lambda a: opened.append(a["coin"]),
                       lambda c: closed.append(c))

    assert "X_foreign" not in closed, f"Foreign X_foreign closed by xs_momentum: {closed}"
    # B was owned and is not in the new target universe → should be closed
    assert "B" in closed, f"Expected B to be closed; got: {closed}"


def test_xs_momentum_tracked_set_updates_on_open_and_close(tmp_path, monkeypatch):
    """After a LIVE rebalance, owned set reflects newly opened coins."""
    import hermes_trader.agents.xs_momentum_live as xl

    owned_path = str(tmp_path / ".xs_momentum_positions.json")
    monkeypatch.setattr(xl, "_OWNED_FILE", owned_path)
    monkeypatch.setattr(xl, "_owned", None)

    monkeypatch.setattr(xl, "_last_ts", lambda: 0.0)
    monkeypatch.setattr(xl, "_save_ts", lambda t: None)
    monkeypatch.setattr(xl, "log_event", lambda e: None)

    xl.maybe_rebalance(_XS_CFG, _uni(_RETS), [],
                       _fetch_factory(_RETS), lambda a: None, lambda c: None)

    # Read back what was saved
    with open(owned_path) as fh:
        saved = json.load(fh)

    # Target: top 2 longs = A, B; top 2 shorts = E, F
    assert "A" in saved["longs"] or "B" in saved["longs"], f"Expected opens in longs: {saved}"
    assert "E" in saved["shorts"] or "F" in saved["shorts"], f"Expected opens in shorts: {saved}"


# ─────────────────────────────────────────────────────────────────────────────
# vol_dispersion_live: same foreign-position invariant
# ─────────────────────────────────────────────────────────────────────────────

def _vd_cfg(shadow=False):
    return {"vol_dispersion": {"enabled": True, "shadow_mode": shadow,
                               "hold_days": 10, "idio_vol_window": 5,
                               "k_per_tercile": 1, "universe_top_n": 50,
                               "min_volume_usd": 1e6}}


def _minimal_vd_fetch(coins):
    """Returns enough bars (window+1=6 bars) for vol_dispersion with window=5."""
    def fetch(coin, interval, n):
        if coin == "BTC":
            # BTC benchmark — flat then slight move so beta calculation works
            return [{"t": i, "o": 100, "h": 100, "l": 100, "c": 100 + i * 0.01, "v": 1e6}
                    for i in range(max(n, 6))]
        if coin in coins:
            # slight alternating so idio_vol_score can compute
            closes = [100.0 + i * 0.5 for i in range(max(n, 6))]
            return [{"t": i, "o": c, "h": c, "l": c, "c": c, "v": 1e6}
                    for i, c in enumerate(closes)]
        return []
    return fetch


def test_vol_dispersion_close_never_includes_foreign(tmp_path, monkeypatch):
    """vol_dispersion LIVE: foreign position must not appear in close_fn calls."""
    import hermes_trader.agents.vol_dispersion_live as vl

    owned_path = str(tmp_path / ".vol_dispersion_positions.json")
    monkeypatch.setattr(vl, "_OWNED_FILE", owned_path)
    monkeypatch.setattr(vl, "_owned", None)
    monkeypatch.setattr(vl, "_last_ts", lambda: 0.0)
    monkeypatch.setattr(vl, "_save_ts", lambda t: None)
    monkeypatch.setattr(vl, "log_event", lambda e: None)

    coins = [f"COIN{i:02d}" for i in range(15)]
    closed = []
    # Foreign coin FOREIGN_COIN is in live positions; owned set is empty
    positions = [_pos("FOREIGN_COIN", 1.0)]

    vl.maybe_rebalance(_vd_cfg(), _uni(coins), positions,
                       _minimal_vd_fetch(set(coins)),
                       lambda a: None, lambda c: closed.append(c))

    assert "FOREIGN_COIN" not in closed, f"Foreign coin closed by vol_dispersion: {closed}"


# ─────────────────────────────────────────────────────────────────────────────
# sortino_live: foreign-position invariant
# ─────────────────────────────────────────────────────────────────────────────

def _sf_cfg(shadow=False):
    return {"sortino_factor": {"enabled": True, "shadow_mode": shadow,
                               "hold_days": 10, "window": 5,
                               "k_per_tercile": 1, "universe_top_n": 50,
                               "min_volume_usd": 1e6}}


def test_sortino_close_never_includes_foreign(tmp_path, monkeypatch):
    import hermes_trader.agents.sortino_live as sl

    owned_path = str(tmp_path / ".sortino_positions.json")
    monkeypatch.setattr(sl, "_OWNED_FILE", owned_path)
    monkeypatch.setattr(sl, "_owned", None)
    monkeypatch.setattr(sl, "_last_ts", lambda: 0.0)
    monkeypatch.setattr(sl, "_save_ts", lambda t: None)
    monkeypatch.setattr(sl, "log_event", lambda e: None)

    coins = [f"COIN{i:02d}" for i in range(15)]
    closed = []
    positions = [_pos("FOREIGN_COIN", -1.0)]

    sl.maybe_rebalance(_sf_cfg(), _uni(coins), positions,
                       _minimal_vd_fetch(set(coins)),
                       lambda a: None, lambda c: closed.append(c))

    assert "FOREIGN_COIN" not in closed, f"Foreign coin closed by sortino: {closed}"


# ─────────────────────────────────────────────────────────────────────────────
# amihud_live: foreign-position invariant
# ─────────────────────────────────────────────────────────────────────────────

def _af_cfg(shadow=False):
    return {"amihud_factor": {"enabled": True, "shadow_mode": shadow,
                              "hold_days": 10, "window": 5,
                              "k_per_tercile": 1, "universe_top_n": 50,
                              "min_volume_usd": 1e6}}


def _amihud_fetch(coins):
    """Like _minimal_vd_fetch but bars include volume for the Amihud ratio."""
    def fetch(coin, interval, n):
        if coin == "BTC":
            return [{"t": i, "o": 100, "h": 100, "l": 100, "c": 100 + i * 0.01, "v": 1e6}
                    for i in range(max(n, 6))]
        if coin in coins:
            closes = [100.0 + i * 0.5 + (i % 3) * 0.2 for i in range(max(n, 6))]
            return [{"t": i, "o": c, "h": c, "l": c, "c": c, "v": 500_000.0}
                    for i, c in enumerate(closes)]
        return []
    return fetch


def test_amihud_close_never_includes_foreign(tmp_path, monkeypatch):
    import hermes_trader.agents.amihud_live as al

    owned_path = str(tmp_path / ".amihud_positions.json")
    monkeypatch.setattr(al, "_OWNED_FILE", owned_path)
    monkeypatch.setattr(al, "_owned", None)
    monkeypatch.setattr(al, "_last_ts", lambda: 0.0)
    monkeypatch.setattr(al, "_save_ts", lambda t: None)
    monkeypatch.setattr(al, "log_event", lambda e: None)

    coins = [f"COIN{i:02d}" for i in range(9)]   # amihud k_per_tercile=1 needs 9
    closed = []
    positions = [_pos("FOREIGN_COIN", 1.0)]

    al.maybe_rebalance(_af_cfg(), _uni(coins), positions,
                       _amihud_fetch(set(coins)),
                       lambda a: None, lambda c: closed.append(c))

    assert "FOREIGN_COIN" not in closed, f"Foreign coin closed by amihud: {closed}"


# ─────────────────────────────────────────────────────────────────────────────
# Prune integration: externally-vanished coin is NOT re-closed
# ─────────────────────────────────────────────────────────────────────────────

def test_prune_prevents_phantom_close(tmp_path, monkeypatch):
    """If xs_momentum previously owned BTC but it got stopped out externally, no phantom close."""
    import hermes_trader.agents.xs_momentum_live as xl

    owned_path = str(tmp_path / ".xs_momentum_positions.json")
    # Pre-populate: xs_momentum thinks it holds BTC (long), but BTC is gone from live positions
    with open(owned_path, "w") as fh:
        json.dump({"longs": ["BTC"], "shorts": []}, fh)

    monkeypatch.setattr(xl, "_OWNED_FILE", owned_path)
    monkeypatch.setattr(xl, "_owned", None)
    monkeypatch.setattr(xl, "_last_ts", lambda: 0.0)
    monkeypatch.setattr(xl, "_save_ts", lambda t: None)
    monkeypatch.setattr(xl, "log_event", lambda e: None)

    closed = []
    # BTC is NOT in live positions → it was stopped out externally
    positions = []

    xl.maybe_rebalance(_XS_CFG, _uni(_RETS), positions,
                       _fetch_factory(_RETS), lambda a: None,
                       lambda c: closed.append(c))

    # BTC was pruned (not in live) so it should not appear in close
    assert "BTC" not in closed, f"Phantom close: BTC was closed despite not being in live positions: {closed}"

    # Owned state should also have BTC removed after prune+save
    with open(owned_path) as fh:
        saved = json.load(fh)
    assert "BTC" not in saved.get("longs", []), "BTC should have been pruned from owned state"


# ═══════════════════════════════════════════════════════════════════════════════
# pairs_live: max_open_pairs cap
# ═══════════════════════════════════════════════════════════════════════════════

def _make_cbc_pairs(coin_patterns):
    """Build candles_by_coin for pairs_live tests."""
    from math import log
    cbc = {}
    for coin, rets in coin_patterns.items():
        p = 100.0
        closes = [p]
        for r in rets:
            p *= (1 + r)
            closes.append(p)
        cbc[coin] = [{"t": i, "o": c, "h": c, "l": c, "c": c, "v": 1e6}
                     for i, c in enumerate(closes)]
    return cbc


def test_pairs_max_open_pairs_cap(tmp_path, monkeypatch):
    """max_open_pairs=1 should cap to at most 1 new pair opened even if signals fire many."""
    import hermes_trader.agents.pairs_live as pl
    from hermes_trader.agents.pairs_engine import PairTrade

    monkeypatch.setattr(pl, "_STATE_FILE", str(tmp_path / ".pairs_state.json"))
    monkeypatch.setattr(pl, "_last_ts", lambda: 0.0)
    monkeypatch.setattr(pl, "_save_ts", lambda t: None)
    monkeypatch.setattr(pl, "log_event", lambda e: None)

    # Return many signals when compute_signals is called
    fake_opens = [
        PairTrade("A", "B", 1, 3.0, 0.0, 1.0),
        PairTrade("C", "D", -1, 2.8, 0.0, 1.0),
        PairTrade("E", "F", 1, 2.6, 0.0, 1.0),
    ]
    monkeypatch.setattr(pl, "compute_signals",
                        lambda *a, **kw: (fake_opens, []))

    cfg = {"pairs_statarb": {"enabled": True, "shadow_mode": False,
                             "scan_interval_hours": 6, "entry_z": 2.5,
                             "exit_z": 0.5, "min_corr": 0.6, "window": 30,
                             "universe_top_n": 40, "min_volume_usd": 1e6,
                             "max_open_pairs": 1}}
    opened = []

    # We need len(cbc) >= 4 for pairs_live to proceed; monkeypatch compute_signals anyway
    # so just pass a dummy fetch that returns enough bars
    def fake_fetch(coin, interval, n):
        return [{"t": i, "o": 100, "h": 100, "l": 100, "c": 100, "v": 1e6} for i in range(n)]

    coins = [{"coin": c, "dayNtlVlm": 1e8, "type": "perp"} for c in ["A","B","C","D","E","F"]]
    pl.maybe_run(cfg, coins, [], fake_fetch,
                 lambda a: opened.append(a["coin"]),
                 lambda c: None)

    # With max_open_pairs=1, at most 1 pair (= 2 legs) should be opened
    assert len(opened) <= 2, f"Expected at most 1 pair (2 legs), got {len(opened)}: {opened}"


def test_pairs_max_open_pairs_default_allows_4(tmp_path, monkeypatch):
    """Default max_open_pairs=4: 3 signals all fit, all opened."""
    import hermes_trader.agents.pairs_live as pl
    from hermes_trader.agents.pairs_engine import PairTrade

    monkeypatch.setattr(pl, "_STATE_FILE", str(tmp_path / ".pairs_state.json"))
    monkeypatch.setattr(pl, "_last_ts", lambda: 0.0)
    monkeypatch.setattr(pl, "_save_ts", lambda t: None)
    monkeypatch.setattr(pl, "log_event", lambda e: None)

    fake_opens = [
        PairTrade("A", "B", 1, 3.0, 0.0, 1.0),
        PairTrade("C", "D", -1, 2.8, 0.0, 1.0),
        PairTrade("E", "F", 1, 2.6, 0.0, 1.0),
    ]
    monkeypatch.setattr(pl, "compute_signals",
                        lambda *a, **kw: (fake_opens, []))

    cfg = {"pairs_statarb": {"enabled": True, "shadow_mode": False,
                             "scan_interval_hours": 6, "entry_z": 2.5,
                             "exit_z": 0.5, "min_corr": 0.6, "window": 30,
                             "universe_top_n": 40, "min_volume_usd": 1e6}}
                             # max_open_pairs not set → default 4

    opened = []
    coins = [{"coin": c, "dayNtlVlm": 1e8, "type": "perp"} for c in ["A","B","C","D","E","F"]]

    def fake_fetch(coin, interval, n):
        return [{"t": i, "o": 100, "h": 100, "l": 100, "c": 100, "v": 1e6} for i in range(n)]

    pl.maybe_run(cfg, coins, [], fake_fetch,
                 lambda a: opened.append(a["coin"]),
                 lambda c: None)

    # All 3 pairs (6 legs) should be opened — they fit in the cap of 4
    assert len(opened) == 6, f"Expected 6 legs (3 pairs), got {len(opened)}: {opened}"


def test_pairs_cap_highest_z_first(tmp_path, monkeypatch):
    """When capped, the pairs with the HIGHEST |z| are kept."""
    import hermes_trader.agents.pairs_live as pl
    from hermes_trader.agents.pairs_engine import PairTrade

    monkeypatch.setattr(pl, "_STATE_FILE", str(tmp_path / ".pairs_state.json"))
    monkeypatch.setattr(pl, "_last_ts", lambda: 0.0)
    monkeypatch.setattr(pl, "_save_ts", lambda t: None)
    monkeypatch.setattr(pl, "log_event", lambda e: None)

    # 3 signals: z=2.6, z=3.0, z=2.8 — with cap=1, should keep z=3.0 (highest |z|)
    fake_opens = [
        PairTrade("E", "F", 1, 2.6, 0.0, 1.0),
        PairTrade("A", "B", 1, 3.0, 0.0, 1.0),   # highest z — should be kept
        PairTrade("C", "D", -1, 2.8, 0.0, 1.0),
    ]
    monkeypatch.setattr(pl, "compute_signals",
                        lambda *a, **kw: (fake_opens, []))

    cfg = {"pairs_statarb": {"enabled": True, "shadow_mode": False,
                             "scan_interval_hours": 6, "entry_z": 2.5,
                             "exit_z": 0.5, "min_corr": 0.6, "window": 30,
                             "universe_top_n": 40, "min_volume_usd": 1e6,
                             "max_open_pairs": 1}}
    opened = []
    coins = [{"coin": c, "dayNtlVlm": 1e8, "type": "perp"} for c in ["A","B","C","D","E","F"]]

    def fake_fetch(coin, interval, n):
        return [{"t": i, "o": 100, "h": 100, "l": 100, "c": 100, "v": 1e6} for i in range(n)]

    pl.maybe_run(cfg, coins, [], fake_fetch,
                 lambda a: opened.append(a["coin"]),
                 lambda c: None)

    # Only the A/B pair (z=3.0) should have been opened
    assert "A" in opened and "B" in opened, f"Highest-z pair A/B not opened; got: {opened}"
    assert "E" not in opened and "C" not in opened, f"Lower-z pairs should be excluded; got: {opened}"
