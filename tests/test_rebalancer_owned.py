"""Ownership and claim-registry tests for live strategy books."""


from pathia.agents.rebalancer_owned import OwnedPositions, _live_coin_set


def _pos(coin: str, szi: float):
    return {"position": {"coin": coin, "szi": szi}}


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


