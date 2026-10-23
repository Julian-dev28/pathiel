"""Tests for desks/_verify.py — the live instrument verification tool. Every
test mocks the universe (no network, no account calls; `_verify.py` itself is
read-only against the public /info endpoint, but the gate suite never hits
the network regardless). Focused on the "gas" desk added for the natural gas
build, but also exercises the general candidates_for/main contract so a
future desk gets the same coverage for free.
"""
from __future__ import annotations

import pytest

from services.strategies.desks import _verify


def _row(coin, dex="xyz", vol=16_084_503.0, oi=3_289_754.0, max_lev=10, mid="3.21"):
    return {"coin": coin, "dex": dex, "type": "perp", "dayNtlVlm": str(vol),
            "openInterest": str(oi), "maxLeverage": max_lev, "midPx": mid}


FAKE_UNIVERSE = [
    _row("xyz:NATGAS"),
    _row("xyz:CL", vol=230_300_000.0, oi=1.0, max_lev=20, mid="60"),
    _row("xyz:BRENTOIL", vol=218_700_000.0, oi=1.0, max_lev=20, mid="62"),
    _row("xyz:HO", vol=43_000.0, oi=0.0, max_lev=20, mid="2"),   # below MIN_VOLUME_USD
    {"coin": "BTC", "dex": None, "type": "perp", "dayNtlVlm": "999999999",
     "openInterest": "1", "maxLeverage": 50, "midPx": "60000"},
    {"coin": "xyz:NVDA", "dex": "xyz", "type": "spot", "dayNtlVlm": "999999999",
     "openInterest": "1", "maxLeverage": 10, "midPx": "100"},    # not a perp, excluded
]


def test_gas_candidate_group_matches_only_natgas():
    assert _verify._CANDIDATES["gas"] == ["NATGAS"]


def test_candidates_for_gas_finds_natgas_above_the_volume_bar():
    rows = _verify.candidates_for("gas", FAKE_UNIVERSE)
    assert [r["coin"] for r in rows] == ["xyz:NATGAS"]
    row = rows[0]
    assert row["above_bar"] is True
    assert row["volume_24h_usd"] == pytest.approx(16_084_503.0)
    assert row["open_interest_usd"] == pytest.approx(3_289_754.0)
    assert row["max_leverage"] == 10


def test_candidates_for_gas_excludes_non_perp_and_wrong_dex_rows():
    """The fake universe has an xyz:NVDA row (same dex) but it's a spot
    listing, not a perp, and a BTC row (main dex, no dex prefix) that isn't
    in the gas keyword group at all — neither should leak into the result."""
    rows = _verify.candidates_for("gas", FAKE_UNIVERSE)
    assert {r["coin"] for r in rows} == {"xyz:NATGAS"}


def test_candidates_for_oil_still_lists_natgas_as_a_hedge_candidate():
    """oil.py keeps xyz:NATGAS in its own candidate group (it's the hedge
    instrument on that desk); adding the "gas" group must not remove it."""
    rows = _verify.candidates_for("oil", FAKE_UNIVERSE)
    coins = {r["coin"] for r in rows}
    assert {"xyz:CL", "xyz:BRENTOIL", "xyz:NATGAS"} <= coins
    thin = next(r for r in rows if r["coin"] == "xyz:HO")
    assert thin["above_bar"] is False


def test_candidates_for_sorted_by_volume_descending():
    rows = _verify.candidates_for("oil", FAKE_UNIVERSE)
    vols = [r["volume_24h_usd"] for r in rows]
    assert vols == sorted(vols, reverse=True)


def test_main_gas_prints_ok_marker(monkeypatch, capsys):
    monkeypatch.setattr(_verify, "fetch_universe", lambda force_refresh=True: FAKE_UNIVERSE)
    rc = _verify.main(["gas"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "=== gas ===" in out
    assert "[OK ] xyz:NATGAS" in out


def test_main_unknown_desk_errors(monkeypatch, capsys):
    monkeypatch.setattr(_verify, "fetch_universe", lambda force_refresh=True: FAKE_UNIVERSE)
    rc = _verify.main(["not-a-desk"])
    assert rc == 1


def test_main_requires_desk_or_all(monkeypatch):
    monkeypatch.setattr(_verify, "fetch_universe", lambda force_refresh=True: FAKE_UNIVERSE)
    with pytest.raises(SystemExit):
        _verify.main([])


def test_main_all_covers_every_desk_including_gas(monkeypatch, capsys):
    monkeypatch.setattr(_verify, "fetch_universe", lambda force_refresh=True: FAKE_UNIVERSE)
    rc = _verify.main(["--all"])
    assert rc == 0
    out = capsys.readouterr().out
    for desk_id in _verify._CANDIDATES:
        assert f"=== {desk_id} ===" in out
    assert "gas" in _verify._CANDIDATES
