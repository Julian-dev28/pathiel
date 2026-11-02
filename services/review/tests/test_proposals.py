"""Gate tests for proposals.py: negative slices map to existing sim levers
and their eval command; positive slices protect; noise emits nothing."""
from __future__ import annotations

import dataclasses

from services.review import proposals as P
from services.review import stats as S
from services.strategies.backtest.desk_tick_sim import Params


def row(dim, value, label, n=12, mean_r=-0.8):
    return {"dim": dim, "value": value, "key": f"{dim}={value}", "label": label, "n": n, "mean_r": mean_r}


def test_every_lever_variant_is_a_real_sim_param():
    """A proposal names Params fields that exist (the sim is not edited here)."""
    fields = {f.name for f in dataclasses.fields(Params)}
    for levers in P.LEVERS.values():
        for lv in levers:
            for ov in lv["variants"]:
                assert set(ov) <= fields, ov
                Params(**ov)                       # and they construct
            assert lv["eval"] in P.EVALS


def test_a_leak_in_size_proposes_the_risk_cap_with_its_command():
    out = P.build([row("cause", "size", S.LEAK)])
    (p,) = out["proposals"]
    assert p["lever"] == "risk_cap_frac" and p["id"] == "risk_cap_frac@cause=size"
    assert "services.strategies.evals.loss_fix_test" in p["command"]
    assert "Params(risk_cap_frac=0.1)" in p["params"]
    assert "LOSS_AUDIT_2026-11-02" in p["prior"]


def test_noise_and_fluke_emit_nothing():
    out = P.build([row("cause", "size", S.NOISE), row("week", "weekend", S.FLUKE)])
    assert out == {"proposals": [], "keeps": [], "watch": []}


def test_a_positive_slice_protects_the_lever_that_would_cut_it():
    out = P.build([row("rewrite", "0-15m", S.LEAN_NEG, n=4),
                   row("range", "past own edge", S.LEAN_POS, n=5, mean_r=1.2),
                   row("cause", "wrong_direction", S.LEAN_NEG, n=3)])
    ids = {p["id"] for p in out["proposals"]}
    assert "entry_delay_min@rewrite=0-15m" in ids and "entry_delay_min@cause=wrong_direction" in ids
    assert out["keeps"] == [{"slice": "range=past own edge", "label": S.LEAN_POS, "n": 5, "mean_r": 1.2,
                             "levers": ["no_break_entry"]}]


def test_conflicts_name_the_protected_slice():
    out = P.build([row("range", "past far edge", S.LEAN_NEG), row("range", "past own edge", S.LEAN_POS)])
    nb = next(p for p in out["proposals"] if p["lever"] == "no_break_entry")
    assert nb["conflicts"] == ["range=past own edge"]


def test_desk_bound_stop_levers_follow_the_slice_desks():
    oil_only = P.build([row("cause", "stop_too_tight", S.LEAN_NEG)], {"cause=stop_too_tight": ["oil"]})
    assert [p["lever"] for p in oil_only["proposals"]] == ["stop_buffer"]
    both = P.build([row("cause", "stop_too_tight", S.LEAN_NEG)], {"cause=stop_too_tight": ["metals", "oil"]})
    assert sorted(p["lever"] for p in both["proposals"]) == ["stop_buffer", "stop_buffer_floor_frac"]


def test_a_negative_slice_with_no_lever_goes_on_the_watch_list():
    out = P.build([row("hour", "00-06Z", S.LEAN_NEG)])
    assert not out["proposals"] and out["watch"][0]["slice"] == "hour=00-06Z"


def test_proposals_never_touch_live_config():
    src = open(P.__file__).read()
    for live in ("risk_profile", "PUT /api", "place_bracket", "config("):
        assert live not in src
