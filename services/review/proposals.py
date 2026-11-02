"""What to try next: slices to backtest proposals. Never touches live config.

A LEAK or LEANING- slice maps to the simulator levers that already exist in
services/strategies/backtest/desk_tick_sim.py `Params` and act on exactly
those trades, and to the eval that already sweeps them. The output is a
proposal: the slice, the lever, the Params overrides to try, the command.
Adoption follows the standing rule (docs/strategy/*_TEST_*.md): beats live
on most datasets, as a plateau on its grid, stop inside half the way to
liquidation, and then only on the operator's go.

An EDGE or LEANING+ slice maps to the same levers the other way round: a
"keep / protect" note naming the levers that would cut those trades. When a
lever is both proposed and protected, the proposal says so (`conflicts`).

FLUKE and NOISE slices emit nothing: one trade, or no signal, is not a
reason to run a test.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from services.review import stats as S

THESES = ("--oil-theses-dir $PATHIEL_STATE_DIR/strategies/oil/theses "
          "--metals-theses-dir $PATHIEL_STATE_DIR/strategies/metals/theses")
EVALS = {
    "loss_fix_test": f"python3 -m services.strategies.evals.loss_fix_test {THESES}",
    "brent_stop_buffer_test": f"python3 -m services.strategies.evals.brent_stop_buffer_test {THESES}",
    "silver_stop_floor_test": f"python3 -m services.strategies.evals.silver_stop_floor_test {THESES}",
    "strategy_sweep": ("python3 -m services.strategies.evals.strategy_sweep "
                       "--theses-dir $PATHIEL_STATE_DIR/strategies/oil/theses"),
}

SIZE = {"lever": "risk_cap_frac", "eval": "loss_fix_test",
        "variants": [{"risk_cap_frac": 0.05}, {"risk_cap_frac": 0.10}, {"risk_cap_frac": 0.15}],
        "why": "size so a stop costs at most X% of desk equity, still 20x"}
SIDE_AGE = {"lever": "side_age_h", "eval": "loss_fix_test",
            "variants": [{"side_age_h": 2.0}, {"side_age_h": 4.0}, {"side_age_h": 8.0}],
            "why": "no entry until the call's side has held N hours"}
CONFIRM = {"lever": "confirm_1h", "eval": "loss_fix_test", "variants": [{"confirm_1h": True}],
           "why": "the last closed 1h candle must agree with the side"}
DELAY = {"lever": "entry_delay_min", "eval": "loss_fix_test",
         "variants": [{"entry_delay_min": 15.0}, {"entry_delay_min": 30.0}, {"entry_delay_min": 60.0}],
         "why": "no entry in the first N minutes of a call (the sim's 15m ticks see only part of this; "
                "LOSS_AUDIT_2026-11-02.md checks it on the real fills too)"}
NO_BREAK = {"lever": "no_break_entry", "eval": "loss_fix_test", "variants": [{"no_break_entry": True}],
            "why": "no market entry once the mid is past the entry range's own edge"}
EDGE_LIMIT = {"lever": "entry_mode", "eval": "loss_fix_test", "variants": [{"entry_mode": "edge"}],
              "why": "rest a limit at the 24h edge instead of taking the mid"}
LOCK = {"lever": "lock_trigger_r", "eval": "loss_fix_test",
        "variants": [{"lock_trigger_r": 1.0, "lock_r": 0.5}, {"lock_trigger_r": 1.5, "lock_r": 0.5},
                     {"lock_trigger_r": 2.0, "lock_r": 0.5},
                     {"lock_weekend_only": True, "lock_trigger_r": 1.0, "lock_r": 0.5}],
        "why": "move the stop to entry + 0.5R once the trade is X R up (24/7 or weekend only)"}
BRENT_STOP = {"lever": "stop_buffer", "eval": "brent_stop_buffer_test", "desk": "oil",
              "variants": [{"stop_buffer": 0.75}, {"stop_buffer": 0.90}, {"fixed_buffer_floor_frac": 0.006}],
              "why": "a wider Brent stop beyond the 8h edge"}
SILVER_STOP = {"lever": "stop_buffer_floor_frac", "eval": "silver_stop_floor_test", "desk": "metals",
               "variants": [{"stop_buffer_floor_frac": 0.008}, {"stop_buffer_floor_frac": 0.010}],
               "why": "a wider floor under the silver ATR stop"}
WEEKEND = {"lever": "weekend_off", "eval": "brent_stop_buffer_test",
           "variants": [{"weekend_off": "hold"}, {"weekend_off": "flatten"}],
           "why": "no new entries (or flat) while the futures are shut, Fri 16:00 to Sun 18:00 New York"}
MIN_R = {"lever": "min_r", "eval": "strategy_sweep", "variants": [{"min_r": 2.5}, {"min_r": 3.0}],
         "why": "a higher reward-to-risk gate than the live 2R"}

# (dimension, bucket or "*") -> the levers that act on exactly those trades
LEVERS: Dict[tuple, List[Dict[str, Any]]] = {
    ("all", "*"): [SIZE],
    ("desk", "*"): [SIZE],
    ("side", "*"): [SIDE_AGE, CONFIRM],
    ("cause", "size"): [SIZE],
    ("cause", "wrong_direction"): [SIDE_AGE, CONFIRM, DELAY],
    ("cause", "stop_too_tight"): [BRENT_STOP, SILVER_STOP],
    ("cause", "round_trip"): [LOCK],
    ("week", "weekend"): [WEEKEND],
    ("rewrite", "0-15m"): [DELAY],
    ("rewrite", "15-60m"): [DELAY],
    ("thesis_7d", "miss"): [SIDE_AGE, CONFIRM],
    ("thesis_24h", "miss"): [SIDE_AGE, CONFIRM],
    ("vs_thesis", "against thesis"): [SIDE_AGE, CONFIRM],
    ("planned_r", "<2R"): [MIN_R],
    ("planned_r", "2-3R"): [MIN_R],
    ("range", "past own edge"): [NO_BREAK],
    ("range", "near far edge"): [EDGE_LIMIT],
    ("range", "past far edge"): [EDGE_LIMIT, NO_BREAK],
    ("risk", "10%+"): [SIZE],
    ("risk", "5-10%"): [SIZE],
}


def levers_for(dim: str, value: str, desks: Sequence[str] = ()) -> List[Dict[str, Any]]:
    """The levers for one slice; a desk-bound lever (a Brent or a silver
    stop) only when the slice holds that desk's trades."""
    found = LEVERS.get((dim, value)) or LEVERS.get((dim, "*")) or []
    return [lv for lv in found if not lv.get("desk") or not desks or lv["desk"] in desks]


# What the last test of each lever said, so a proposal is read against it
# (a re-run is worth it when the ledger has new trades, not to re-ask the
# same data). docs/strategy/.
PRIOR = {
    "risk_cap_frac": "LOSS_AUDIT_2026-11-02: no cap cleared the bar; a 15% cap went live as a risk call (9ba18ef)",
    "side_age_h": "LOSS_AUDIT_2026-11-02: none of 2/4/8/12h cleared",
    "confirm_1h": "LOSS_AUDIT_2026-11-02: 0/8 Brent sets, cut the winners",
    "entry_delay_min": "LOSS_AUDIT_2026-11-02: the sim cannot see it; on the real fills it refused 2 losers",
    "no_break_entry": "LOSS_AUDIT_2026-11-02: 1/8 Brent sets; on the real fills it saved $38 and missed $57",
    "entry_mode": "LOSS_AUDIT_2026-11-02: resting limit at the 24h edge, 2/8 Brent sets",
    "lock_trigger_r": "PROFIT_LOCK_TEST_2026-11-01 and LOSS_AUDIT_2026-11-02: do not adopt; weekend lock has no plateau",
    "stop_buffer": "BRENT_STOP_BUFFER_TEST_2026-11-02: keep 0.75",
    "stop_buffer_floor_frac": "SILVER_STOP_FLOOR_TEST_2026-11-02: the 0.8% floor is live (9224cbf)",
    "weekend_off": "BRENT_STOP_BUFFER_TEST_2026-11-02: cleared on Brent with the journal, silver lost; not adopted",
    "min_r": "strategy_sweep 2026-10-30; the live gate is 2R",
}


def params_text(ov: Dict[str, Any]) -> str:
    return "Params(" + ", ".join(f"{k}={v!r}" for k, v in ov.items()) + ")"


def build(rows: Sequence[Dict[str, Any]], desks_by_key: Optional[Dict[str, List[str]]] = None
          ) -> Dict[str, List[Dict[str, Any]]]:
    """Proposals for the negative slices, keep notes for the positive ones,
    and a watch list of negative slices no lever reaches, from a slice
    table (analysis.slice_table)."""
    desks_by_key = desks_by_key or {}
    proposals: Dict[str, Dict[str, Any]] = {}
    keeps: List[Dict[str, Any]] = []
    watch: List[Dict[str, Any]] = []
    protected: Dict[str, List[str]] = {}
    for row in rows:
        lab = row["label"]
        if not (S.is_negative(lab) or S.is_positive(lab)):
            continue
        levers = levers_for(row["dim"], row["value"], desks_by_key.get(row["key"], []))
        if S.is_positive(lab):
            keeps.append({"slice": row["key"], "label": lab, "n": row["n"], "mean_r": row["mean_r"],
                          "levers": [lv["lever"] for lv in levers]})
            for lv in levers:
                protected.setdefault(lv["lever"], []).append(row["key"])
            continue
        for lv in levers:
            pid = f"{lv['lever']}@{row['key']}"
            proposals[pid] = {
                "id": pid, "slice": row["key"], "label": lab, "n": row["n"], "mean_r": row["mean_r"],
                "lever": lv["lever"], "eval": lv["eval"], "variants": lv["variants"],
                "params": [params_text(v) for v in lv["variants"]], "command": EVALS[lv["eval"]],
                "why": lv["why"], "prior": PRIOR.get(lv["lever"], ""), "conflicts": []}
        if not levers:
            watch.append({"slice": row["key"], "label": lab, "n": row["n"], "mean_r": row["mean_r"],
                          "note": "no simulator lever acts on this slice yet"})
    for p in proposals.values():
        p["conflicts"] = sorted(set(protected.get(p["lever"], [])))
    return {"proposals": sorted(proposals.values(), key=lambda p: p["id"]), "keeps": keeps, "watch": watch}
