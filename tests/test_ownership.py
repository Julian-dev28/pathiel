"""pathiel.agents.ownership: the desk must never act on a position or order
it did not place.

2026-11-17 23:25Z: a desk tick closed the operator's own hand-placed
xyz:BRENTOIL long because `sweep_brackets` reported it uncovered. This is
the regression suite for the structural fix: a cloid tag, a journal
fallback, and the pure function that decides ownership from the two. Gate
tests only -- no network, deterministic, sub-second.
"""
from __future__ import annotations

from pathiel.agents import ownership as O

COIN = "xyz:BRENTOIL"


def _fill(coin=COIN, side="B", sz=1.0, time=1_000, oid=1, cloid=None, tid=None):
    f = {"coin": coin, "side": side, "sz": sz, "time": time, "oid": oid}
    if cloid is not None:
        f["cloid"] = cloid
    if tid is not None:
        f["tid"] = tid
    return f


# ── the cloid tag ────────────────────────────────────────────────────────────

def test_make_cloid_is_a_valid_hyperliquid_cloid_with_the_pathiel_prefix():
    cloid = O.make_cloid()
    assert cloid.startswith("0x")
    assert len(cloid) == 2 + O.CLOID_HEX_LEN
    assert int(cloid[2:], 16) >= 0          # every char is valid hex
    assert cloid[2:2 + len(O.CLOID_PREFIX)] == O.CLOID_PREFIX


def test_make_cloid_is_different_every_call():
    assert O.make_cloid() != O.make_cloid()


def test_is_pathiel_cloid_true_for_a_desk_cloid():
    assert O.is_pathiel_cloid(O.make_cloid())
    assert O.is_pathiel_cloid("0x" + O.CLOID_PREFIX + "0" * 24)


def test_is_pathiel_cloid_false_for_none_empty_operator_or_malformed():
    assert O.is_pathiel_cloid(None) is False
    assert O.is_pathiel_cloid("") is False
    assert O.is_pathiel_cloid("0x" + "0" * 32) is False                 # HL's "no cloid" sentinel
    assert O.is_pathiel_cloid("0x" + "ab" * 16) is False                 # some other app's cloid
    assert O.is_pathiel_cloid("0x" + O.CLOID_PREFIX + "0" * 10) is False  # too short


def test_is_pathiel_cloid_is_case_insensitive():
    cloid = O.make_cloid().upper()
    assert O.is_pathiel_cloid(cloid)


# ── the journal ──────────────────────────────────────────────────────────────

def test_record_and_load_journal_round_trips(tmp_path, monkeypatch):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    O.record_desk_order(COIN, O.make_cloid(), "entry", oid=101, side="long", size=1.27, px=107.2)
    O.record_desk_order(COIN, O.make_cloid(), "entry", oid=102, side="long", size=0.5, px=108.0)
    rows = O.load_journal()
    assert [r["oid"] for r in rows] == [101, 102]
    assert O.journal_oids(rows) == {101, 102}


def test_load_journal_on_a_missing_file_is_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    assert O.load_journal() == []
    assert O.journal_oids([]) == set()


def test_load_journal_skips_a_torn_last_line(tmp_path):
    path = tmp_path / "ownership_journal.jsonl"
    path.write_text('{"oid": 1, "coin": "xyz:BRENTOIL"}\n{"oid": 2, "co')
    rows = O.load_journal(str(path))
    assert [r["oid"] for r in rows] == [1]


# ── opening_fills (pure) ─────────────────────────────────────────────────────

def test_opening_fills_of_a_flat_coin_is_empty():
    assert O.opening_fills({"coin": COIN}, []) == []


def test_opening_fills_is_just_the_entry_when_nothing_else_happened():
    fills = [_fill(oid=1, time=1000)]
    assert O.opening_fills({"coin": COIN}, fills) == fills


def test_opening_fills_includes_a_topup_but_not_a_prior_closed_trade():
    closed_trade = [_fill(oid=1, time=100, sz=2.0), _fill(oid=2, side="A", time=200, sz=2.0)]
    current = [_fill(oid=3, time=300, sz=1.0), _fill(oid=4, time=400, sz=0.5)]  # topup
    chain = O.opening_fills({"coin": COIN}, closed_trade + current)
    assert [f["oid"] for f in chain] == [3, 4]


def test_opening_fills_ignores_a_partial_reduce_that_never_reaches_flat():
    # open 2.0, reduce to 1.0 (never flat), topup 0.5: every opening fill
    # since the last flat point stays in the chain, the reduce does not
    # clear it (module doc: only an exact flat resets the chain).
    fills = [_fill(oid=1, time=100, sz=2.0), _fill(oid=2, side="A", time=200, sz=1.0),
             _fill(oid=3, time=300, sz=0.5)]
    chain = O.opening_fills({"coin": COIN}, fills)
    assert [f["oid"] for f in chain] == [1, 3]


def test_opening_fills_only_considers_the_named_coin():
    fills = [_fill(coin="xyz:SILVER", oid=1, time=100), _fill(coin=COIN, oid=2, time=200)]
    assert [f["oid"] for f in O.opening_fills({"coin": COIN}, fills)] == [2]


# ── is_desk_owned (pure): the five required cases ───────────────────────────

def test_is_desk_owned_true_for_a_pure_desk_fill():
    desk_cloid = O.make_cloid()
    fills = [_fill(oid=1, time=100, sz=1.27, cloid=desk_cloid)]
    position = {"coin": COIN, "szi": 1.27}
    assert O.is_desk_owned(position, fills, journal=[]) is True


def test_is_desk_owned_false_for_a_pure_operator_fill():
    fills = [_fill(oid=1, time=100, sz=14.66, cloid=None)]
    position = {"coin": COIN, "szi": 14.66}
    assert O.is_desk_owned(position, fills, journal=[]) is False


def test_is_desk_owned_false_for_a_mixed_position():
    # the entry is the desk's, a top-up carries no tag at all: refuse the
    # whole position rather than guess which part is ours.
    fills = [_fill(oid=1, time=100, sz=1.0, cloid=O.make_cloid()),
             _fill(oid=2, time=200, sz=0.5, cloid=None)]
    position = {"coin": COIN, "szi": 1.5}
    assert O.is_desk_owned(position, fills, journal=[]) is False


def test_is_desk_owned_true_for_a_missing_cloid_with_a_journal_hit():
    # pre-cloid desk order: no tag on the fill, but the journal recorded the
    # oid at placement time.
    fills = [_fill(oid=55, time=100, sz=8.17, cloid=None)]
    position = {"coin": COIN, "szi": 8.17}
    journal = [{"oid": 55, "coin": COIN, "kind": "entry"}]
    assert O.is_desk_owned(position, fills, journal=journal) is True


def test_is_desk_owned_false_for_missing_cloid_and_missing_journal():
    fills = [_fill(oid=99, time=100, sz=14.66, cloid=None)]
    position = {"coin": COIN, "szi": 14.66}
    assert O.is_desk_owned(position, fills, journal=[]) is False


# ── is_desk_owned: extra edges worth locking down ───────────────────────────

def test_is_desk_owned_false_for_a_flat_position():
    assert O.is_desk_owned({"coin": COIN, "szi": 0.0}, [_fill(cloid=O.make_cloid())], journal=[]) is False


def test_is_desk_owned_false_when_no_fills_explain_the_position_at_all():
    # the position is open but `fills` does not reach back far enough to
    # show how -- never assumed to be ours for lack of evidence.
    assert O.is_desk_owned({"coin": COIN, "szi": 14.66}, [], journal=[]) is False


def test_is_desk_owned_false_when_the_chain_undershoots_the_live_size():
    # only part of the position's history is in `fills` (e.g. too short a
    # lookback): the desk cloid is real, but it only explains part of the
    # size, so this refuses to vouch for the rest.
    fills = [_fill(oid=1, time=100, sz=5.0, cloid=O.make_cloid())]
    position = {"coin": COIN, "szi": 14.66}
    assert O.is_desk_owned(position, fills, journal=[]) is False


def test_is_desk_owned_default_journal_reads_the_real_journal(tmp_path, monkeypatch):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    O.record_desk_order(COIN, O.make_cloid(), "entry", oid=7, side="long", size=1.0, px=100.0)
    fills = [_fill(oid=7, time=100, sz=1.0, cloid=None)]
    position = {"coin": COIN, "szi": 1.0}
    # journal=None -> is_desk_owned itself stays pure (it never reads the
    # journal file); only check_position_ownership defaults to the live one.
    assert O.is_desk_owned(position, fills, journal=O.load_journal()) is True


# ── check_position_ownership (impure, fetchers injected: still no network) ──

def test_check_position_ownership_flat_coin_is_flat_and_desk_owned():
    info = O.check_position_ownership(
        COIN, user="0xabc",
        fetch_state=lambda user, include_hip3: {"asset_positions": []})
    assert info["flat"] is True and info["desk_owned"] is True


def test_check_position_ownership_no_user_is_flat_and_desk_owned(monkeypatch):
    # Hermetic: a checkout with a configured wallet would otherwise resolve
    # the real address and reach the venue.
    monkeypatch.setattr("pathiel.client.hl_client.resolve_user_address", lambda: "")
    info = O.check_position_ownership(COIN, user="")
    assert info["flat"] is True and info["desk_owned"] is True and info["read_failed"] is True


def test_check_position_ownership_desk_position_from_cloid(tmp_path, monkeypatch):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    desk_cloid = O.make_cloid()
    state = {"asset_positions": [{"position": {"coin": COIN, "szi": "14.66", "entryPx": "90.0",
                                               "leverage": {"value": 20, "type": "isolated"}}}]}
    info = O.check_position_ownership(
        COIN, user="0xabc", fetch_state=lambda user, include_hip3: state,
        fetch_fills=lambda user, start_ms: [_fill(oid=1, time=100, sz=14.66, cloid=desk_cloid)])
    assert info == {"coin": COIN, "flat": False, "desk_owned": True, "size": 14.66,
                    "entry_px": 90.0, "leverage": 20}


def test_check_position_ownership_operator_position_from_no_tag(tmp_path, monkeypatch):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    state = {"asset_positions": [{"position": {"coin": COIN, "szi": "14.66", "entryPx": "90.7165",
                                               "leverage": {"value": 20, "type": "isolated"}}}]}
    info = O.check_position_ownership(
        COIN, user="0xabc", fetch_state=lambda user, include_hip3: state,
        fetch_fills=lambda user, start_ms: [_fill(oid=1, time=100, sz=14.66, cloid=None)])
    assert info["flat"] is False and info["desk_owned"] is False
    assert info["size"] == 14.66 and info["entry_px"] == 90.7165 and info["leverage"] == 20


def test_check_position_ownership_position_read_failure_refuses(tmp_path, monkeypatch):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))

    def boom(user, include_hip3):
        raise RuntimeError("no")

    info = O.check_position_ownership(COIN, user="0xabc", fetch_state=boom)
    assert info["flat"] is False and info["desk_owned"] is False and info["read_failed"] is True


def test_check_position_ownership_fills_read_failure_refuses(tmp_path, monkeypatch):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    state = {"asset_positions": [{"position": {"coin": COIN, "szi": "14.66", "entryPx": "90.0",
                                               "leverage": 20}}]}

    def boom(user, start_ms):
        raise RuntimeError("no")

    info = O.check_position_ownership(COIN, user="0xabc", fetch_state=lambda user, include_hip3: state,
                                      fetch_fills=boom)
    assert info["flat"] is False and info["desk_owned"] is False


def test_journal_fallback_matches_project_state_dir(monkeypatch):
    """With PATHIEL_STATE_DIR unset the journal lands in the project root,
    the directory pathiel/state.py uses for every other state file."""
    import os as _os
    import pathiel.state as st
    from pathiel.agents import ownership as own
    monkeypatch.delenv("PATHIEL_STATE_DIR", raising=False)
    root = _os.path.dirname(_os.path.dirname(_os.path.abspath(st.__file__)))
    assert own.journal_path() == _os.path.join(root, own.JOURNAL_FILE)
