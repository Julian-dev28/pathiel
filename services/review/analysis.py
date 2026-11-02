"""Slices of the ledger and the fluke-or-edge table. Pure on its inputs.

A slice is one bucket of one dimension (desk=oil, rewrite=0-15m, ...). Each
gets n, the win rate with its Wilson 95% CI, mean R with a seeded bootstrap
95% CI, net $ with a bootstrap CI of the total, a seeded permutation p
against every other trade (the "all" slice: a sign-flip test against 0),
and a label (stats.label_slice).

R per trade is `r_mult`: net $ (fees and funding in) over the $ the opening
stop risked at full size. A trade with no opening stop has no R: it counts
in n, wins and $, not in the R columns.

Thesis calls are scored apart from trades (`thesis_calls`): the grader's
7-day verdict where a thesis has been graded, and a 24h direction check
from 1h candles for every long/short thesis old enough, each against a coin
flip with an exact binomial test.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Sequence

from pathiel.models.types import Candle

from services.review import stats as S

HOUR_MS = 3_600_000
DAY_MS = 86_400_000


def _rewrite(r: Dict[str, Any]) -> Optional[str]:
    age = r.get("thesis_age_min")
    if age is None:
        return "no thesis"
    return "0-15m" if age <= 15 else "15-60m" if age <= 60 else ">60m"


def _hit(key: str) -> Callable[[Dict[str, Any]], Optional[str]]:
    def f(r: Dict[str, Any]) -> Optional[str]:
        v = r.get(key)       # ungraded is left out: it is just "recent", not a kind of trade
        return "hit" if v is True else "miss" if v is False else None
    return f


def _planned_r(r: Dict[str, Any]) -> Optional[str]:
    x = r.get("planned_r")
    if x is None:
        return None
    return "<2R" if x < 2 else "2-3R" if x < 3 else "3-5R" if x < 5 else "5R+"


def _hour(r: Dict[str, Any]) -> Optional[str]:
    h = r.get("hour_utc")
    if h is None:
        return None
    lo = (int(h) // 6) * 6
    return f"{lo:02d}-{lo + 6:02d}Z"


def _range(r: Dict[str, Any]) -> Optional[str]:
    p = r.get("range_pos")
    if p is None:
        return None
    if p < 0:
        return "past own edge"
    return "near own edge" if p < 1 / 3 else "middle" if p < 2 / 3 else "near far edge" if p <= 1 else "past far edge"


def _risk(r: Dict[str, Any]) -> Optional[str]:
    x = r.get("risk_pct_equity")
    if x is None:
        return None
    return "<5%" if x < 5 else "5-10%" if x < 10 else "10%+"


def _cause(r: Dict[str, Any]) -> List[str]:
    """The cause tags. No "clean" bucket: the tags only fire on losses and
    round trips, so untagged trades are winners by construction and would
    read as an edge that is only a definition."""
    return list(r.get("tags") or [])


def _with_thesis(r: Dict[str, Any]) -> Optional[str]:
    v = r.get("side_agrees")
    return None if v is None else "with thesis" if v else "against thesis"


# (dimension, bucket function); a function may return a list (a trade with
# two cause tags sits in both) or None (not known: left out of that dimension)
DIMENSIONS: List[tuple] = [
    ("all", lambda r: "all"),
    ("desk", lambda r: r.get("desk")),
    ("side", lambda r: r.get("side")),
    ("cause", _cause),
    ("week", lambda r: None if r.get("weekend") is None else ("weekend" if r["weekend"] else "weekday")),
    ("rewrite", _rewrite),
    ("thesis_7d", _hit("thesis_hit_7d")),
    ("thesis_24h", _hit("thesis_hit_24h")),
    ("vs_thesis", _with_thesis),
    ("planned_r", _planned_r),
    ("hour", _hour),
    ("range", _range),
    ("risk", _risk),
]
DIM_FUNCS = dict(DIMENSIONS)


def buckets(r: Dict[str, Any], dim: str) -> List[str]:
    v = DIM_FUNCS[dim](r)
    if v is None:
        return []
    return list(v) if isinstance(v, list) else [v]


def slice_row(records: Sequence[Dict[str, Any]], dim: str, value: str, seed: int = S.SEED,
              n_boot: int = S.N_BOOT, n_perm: int = S.N_PERM) -> Dict[str, Any]:
    """The stats and label for the slice `dim=value` (module doc)."""
    key = "all" if dim == "all" else f"{dim}={value}"
    inside = [r for r in records if value in buckets(r, dim)]
    rest = [] if dim == "all" else [r for r in records if value not in buckets(r, dim)]
    rs = [float(r["r_mult"]) for r in inside if r.get("r_mult") is not None]
    rest_rs = [float(r["r_mult"]) for r in rest if r.get("r_mult") is not None]
    nets = [float(r["net_usd"]) for r in inside]
    wins = sum(1 for x in nets if x > 0)
    sd = S.sub_seed(seed, key)
    ci = S.bootstrap_ci(rs, S.mean, n_boot, sd)
    net_ci = S.bootstrap_ci(nets, sum, n_boot, sd + 1)
    p = S.permutation_p(rs, rest_rs, n_perm, sd + 2) if rs else None
    wlo, whi = S.wilson_ci(wins, len(nets))
    label = S.label_slice(rs, ci, p)
    return {"dim": dim, "value": value, "key": key, "n": len(inside), "n_r": len(rs), "wins": wins,
            "win_rate": wins / len(nets) if nets else None, "win_lo": wlo, "win_hi": whi,
            "mean_r": S.mean(rs) if rs else None, "r_lo": ci[0], "r_hi": ci[1],
            "net_usd": sum(nets), "net_lo": net_ci[0], "net_hi": net_ci[1], "p": p, "label": label,
            "one_trade": S.one_trade_flips(rs) if rs else None}


def slice_table(records: Sequence[Dict[str, Any]], seed: int = S.SEED, n_boot: int = S.N_BOOT,
                n_perm: int = S.N_PERM) -> List[Dict[str, Any]]:
    """Every non-empty slice of every dimension, in DIMENSIONS order then by
    n. A dimension whose only bucket holds every trade adds nothing over
    "all" and is left out (except "all" itself)."""
    out = []
    for dim, _ in DIMENSIONS:
        values: Dict[str, int] = {}
        for r in records:
            for v in buckets(r, dim):
                values[v] = values.get(v, 0) + 1
        if dim != "all" and len(values) == 1 and next(iter(values.values())) == len(records):
            continue
        for v in sorted(values, key=lambda x: (-values[x], x)):
            out.append(slice_row(records, dim, v, seed, n_boot, n_perm))
    return out


# ── thesis calls ───────────────────────────────────────────────────────────

def _close_at(bars: Sequence[Candle], t_ms: int) -> Optional[float]:
    """The close of the last 1h bar fully closed by `t_ms`."""
    px = None
    for b in bars:
        if b.t + HOUR_MS <= t_ms:
            px = b.c
        else:
            break
    return px


def hit_24h(created_ms: int, side: str, bars1h: Sequence[Candle], as_of_ms: int) -> Optional[bool]:
    """Did price move the thesis's way over the 24h after it was written?
    None for flat, too young, or no bars."""
    if side not in ("long", "short") or as_of_ms < created_ms + DAY_MS:
        return None
    bars = sorted(bars1h, key=lambda b: b.t)
    if not bars or bars[-1].t + HOUR_MS < created_ms + DAY_MS:
        return None
    p0, p1 = _close_at(bars, created_ms), _close_at(bars, created_ms + DAY_MS)
    if p0 is None or p1 is None or p1 == p0:
        return None
    return (p1 > p0) == (side == "long")


def thesis_outcomes(theses_by_desk: Dict[str, Sequence[Any]], raw_by_id: Dict[str, Dict[str, Any]],
                    bars1h_by_desk: Dict[str, Sequence[Candle]], as_of_ms: int) -> Dict[str, Dict[str, Any]]:
    """Per thesis id: desk, side, the grader's 7-day `direction_hit` (None if
    ungraded) and the 24h check."""
    out = {}
    for desk, theses in theses_by_desk.items():
        for th in theses:
            raw = raw_by_id.get(th.thesis_id) or {}
            g = raw.get("grade") if isinstance(raw.get("grade"), dict) else {}
            out[th.thesis_id] = {"desk": desk, "side": th.side, "created_ms": th.created_at_ms,
                                 "hit_7d": g.get("direction_hit") if th.side in ("long", "short") else None,
                                 "hit_24h": hit_24h(th.created_at_ms, th.side, bars1h_by_desk.get(desk, []),
                                                    as_of_ms)}
    return out


def join_thesis_outcomes(records: Sequence[Dict[str, Any]], outcomes: Dict[str, Dict[str, Any]]) -> None:
    """Add `thesis_hit_7d` and `thesis_hit_24h` to each record from the
    current thesis outcomes (graded after the trade, so joined at review
    time, never frozen into the ledger). Mutates `records`."""
    for r in records:
        o = outcomes.get(r.get("thesis_id") or "") or {}
        r["thesis_hit_7d"] = o.get("hit_7d")
        r["thesis_hit_24h"] = o.get("hit_24h")


def thesis_calls(outcomes: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Hit rate vs 50% per desk and in total, for each horizon."""
    rows = []
    for horizon in ("hit_7d", "hit_24h"):
        for desk in sorted({o["desk"] for o in outcomes.values()}) + ["all"]:
            xs = [o[horizon] for o in outcomes.values() if o[horizon] is not None and desk in ("all", o["desk"])]
            k, n = sum(1 for x in xs if x), len(xs)
            lo, hi = S.wilson_ci(k, n)
            rows.append({"horizon": horizon.replace("hit_", ""), "desk": desk, "hits": k, "n": n,
                         "rate": k / n if n else None, "lo": lo, "hi": hi, "p": S.binom_two_sided(k, n)})
    return rows
