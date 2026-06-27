"""Ownership and claim-registry tests for live strategy books."""

import json

from hermes_trader.agents.rebalancer_owned import OwnedPositions, _live_coin_set


def _pos(coin: str, szi: float):
    return {"position": {"coin": coin, "szi": szi}}


def _fetch_factory(rets):
    def fetch(coin, interval, n):
        r = rets.get(coin, 0.0)
        return [
            {"t": 0, "o": 100, "h": 100, "l": 100, "c": 100, "v": 1},
            {
                "t": 1,
                "o": 100 * (1 + r),
                "h": 100 * (1 + r),
                "l": 100 * (1 + r),
                "c": 100 * (1 + r),
                "v": 1,
            },
        ]
    return fetch


def _uni(coins_or_rets):
    coins = coins_or_rets.keys() if isinstance(coins_or_rets, dict) else coins_or_rets
    return [{"coin": c, "dayNtlVlm": 1e8, "type": "perp"} for c in coins]


_XS_CFG = {
    "xs_momentum": {
        "enabled": True,
        "lookback_days": 1,
        "hold_days": 10,
        "k_per_leg": 2,
        "universe_top_n": 50,
        "min_volume_usd": 1e6,
        "vol_gate": False,
    }
}
_RETS = {"A": 0.50, "B": 0.20, "C": 0.05, "D": -0.05, "E": -0.20, "F": -0.40}


def test_owned_starts_empty(tmp_path):
    op = OwnedPositions(str(tmp_path / "owned.json")).load()
    assert op.current_book() == ([], [])


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
    op = OwnedPositions(str(tmp_path / "owned.json")).load()
    op.add("SOL", "long")
    op.add("SOL", "short")
    longs, shorts = op.current_book()
    assert "SOL" not in longs
    assert "SOL" in shorts


def test_remove_coin_from_book(tmp_path):
    op = OwnedPositions(str(tmp_path / "owned.json")).load()
    op.add("BNB", "long")
    op.remove("BNB")
    assert op.current_book() == ([], [])


def test_remove_nonexistent_is_noop(tmp_path):
    op = OwnedPositions(str(tmp_path / "owned.json")).load()
    op.remove("GHOST")
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


def test_load_missing_file_starts_empty(tmp_path):
    op = OwnedPositions(str(tmp_path / "missing.json")).load()
    assert op.current_book() == ([], [])


def test_load_corrupt_file_starts_empty(tmp_path):
    path = tmp_path / "corrupt.json"
    path.write_text("NOT VALID JSON {{{")
    op = OwnedPositions(str(path)).load()
    assert op.current_book() == ([], [])


def test_prune_removes_coins_not_in_live(tmp_path):
    op = OwnedPositions(str(tmp_path / "owned.json")).load()
    op.add("BTC", "long")
    op.add("ETH", "short")
    op.prune({"ETH"})
    longs, shorts = op.current_book()
    assert "BTC" not in longs
    assert "ETH" in shorts


def test_filter_to_owned_excludes_foreign_positions(tmp_path):
    op = OwnedPositions(str(tmp_path / "owned.json")).load()
    op.add("BTC", "long")
    cur_long, cur_short = op.filter_to_owned([_pos("BTC", 1.0), _pos("ETH", 1.0)])
    assert cur_long == ["BTC"]
    assert cur_short == []


def test_filter_to_owned_short_side(tmp_path):
    op = OwnedPositions(str(tmp_path / "owned.json")).load()
    op.add("ETH", "short")
    cur_long, cur_short = op.filter_to_owned([_pos("ETH", -1.0), _pos("BTC", -1.0)])
    assert cur_long == []
    assert cur_short == ["ETH"]


def test_filter_to_owned_zero_szi_excluded(tmp_path):
    op = OwnedPositions(str(tmp_path / "owned.json")).load()
    op.add("BTC", "long")
    assert op.filter_to_owned([_pos("BTC", 0.0)]) == ([], [])


def test_live_coin_set_extracts_nonzero():
    live = _live_coin_set([_pos("BTC", 1.0), _pos("ETH", -0.5), _pos("SOL", 0.0)])
    assert live == {"BTC", "ETH"}


def test_xs_momentum_close_never_includes_foreign(tmp_path, monkeypatch):
    import hermes_trader.agents.xs_momentum_live as xl

    monkeypatch.setattr(xl, "_OWNED_FILE", str(tmp_path / ".xs_momentum_positions.json"))
    monkeypatch.setattr(xl, "_owned", None)
    monkeypatch.setattr(xl, "_last_ts", lambda: 0.0)
    monkeypatch.setattr(xl, "_save_ts", lambda t: None)
    monkeypatch.setattr(xl, "log_event", lambda e: None)

    closed = []
    positions = [_pos("X", 1.0)]
    xl.maybe_rebalance(
        _XS_CFG,
        _uni(_RETS),
        positions,
        _fetch_factory(_RETS),
        lambda analysis: {"executed": True},
        lambda coin: closed.append(coin),
    )

    assert "X" not in closed


def test_xs_momentum_only_closes_own_coins(tmp_path, monkeypatch):
    import hermes_trader.agents.xs_momentum_live as xl

    owned_path = str(tmp_path / ".xs_momentum_positions.json")
    with open(owned_path, "w") as fh:
        json.dump({"longs": ["B"], "shorts": []}, fh)

    monkeypatch.setattr(xl, "_OWNED_FILE", owned_path)
    monkeypatch.setattr(xl, "_owned", None)
    monkeypatch.setattr(xl, "_last_ts", lambda: 0.0)
    monkeypatch.setattr(xl, "_save_ts", lambda t: None)
    monkeypatch.setattr(xl, "log_event", lambda e: None)

    custom_rets = {"A": 0.50, "X_foreign": 0.40, "C": 0.05, "D": -0.05, "E": -0.20, "F": -0.40}
    closed = []
    xl.maybe_rebalance(
        _XS_CFG,
        _uni(custom_rets),
        [_pos("B", 1.0), _pos("X_foreign", 1.0)],
        _fetch_factory(custom_rets),
        lambda analysis: {"executed": True},
        lambda coin: closed.append(coin),
    )

    assert "B" in closed
    assert "X_foreign" not in closed


def test_xs_momentum_tracked_set_updates_on_open(tmp_path, monkeypatch):
    import hermes_trader.agents.xs_momentum_live as xl

    owned_path = str(tmp_path / ".xs_momentum_positions.json")
    monkeypatch.setattr(xl, "_OWNED_FILE", owned_path)
    monkeypatch.setattr(xl, "_owned", None)
    monkeypatch.setattr(xl, "_last_ts", lambda: 0.0)
    monkeypatch.setattr(xl, "_save_ts", lambda t: None)
    monkeypatch.setattr(xl, "log_event", lambda e: None)

    xl.maybe_rebalance(
        _XS_CFG,
        _uni(_RETS),
        [],
        _fetch_factory(_RETS),
        lambda analysis: {"executed": True},
        lambda coin: None,
    )

    with open(owned_path) as fh:
        saved = json.load(fh)
    assert set(saved["longs"]) == {"A", "B"}
    assert set(saved["shorts"]) == {"E", "F"}


def test_prune_prevents_phantom_close(tmp_path, monkeypatch):
    import hermes_trader.agents.xs_momentum_live as xl

    owned_path = str(tmp_path / ".xs_momentum_positions.json")
    with open(owned_path, "w") as fh:
        json.dump({"longs": ["BTC"], "shorts": []}, fh)

    monkeypatch.setattr(xl, "_OWNED_FILE", owned_path)
    monkeypatch.setattr(xl, "_owned", None)
    monkeypatch.setattr(xl, "_last_ts", lambda: 0.0)
    monkeypatch.setattr(xl, "_save_ts", lambda t: None)
    monkeypatch.setattr(xl, "log_event", lambda e: None)

    closed = []
    xl.maybe_rebalance(
        _XS_CFG,
        _uni(_RETS),
        [],
        _fetch_factory(_RETS),
        lambda analysis: {"executed": True},
        lambda coin: closed.append(coin),
    )

    assert "BTC" not in closed
    with open(owned_path) as fh:
        saved = json.load(fh)
    assert "BTC" not in saved.get("longs", [])


def test_xs_prune_state_to_live_runs_outside_rebalance_timer(tmp_path, monkeypatch):
    import hermes_trader.agents.rebalancer_owned as ro
    import hermes_trader.agents.xs_momentum_live as xl

    owned_path = str(tmp_path / ".xs_momentum_positions.json")
    claims_path = str(tmp_path / ".rebalancer_claims.json")
    with open(owned_path, "w") as fh:
        json.dump({"longs": ["A", "B"], "shorts": ["C"]}, fh)

    registry = ro.ClaimsRegistry(claims_path, active_books=ro.active_claim_books()).load()
    registry.claim("A", "xs_momentum")
    registry.claim("B", "xs_momentum")
    registry.claim("C", "xs_momentum")
    registry.claim("D", "rally_exhaustion")
    registry.save()
    monkeypatch.setattr(ro, "_claims_registry", registry)
    monkeypatch.setattr(xl, "_OWNED_FILE", owned_path)
    monkeypatch.setattr(xl, "_owned", None)

    dropped = xl.prune_state_to_live([_pos("B", 1.0), _pos("D", -1.0)])

    assert dropped == {"longs": ["A"], "shorts": ["C"], "claims": ["A", "C"]}
    with open(owned_path) as fh:
        owned = json.load(fh)
    assert owned == {"longs": ["B"], "shorts": []}
    assert registry.claims() == {"B": "xs_momentum", "D": "rally_exhaustion"}
