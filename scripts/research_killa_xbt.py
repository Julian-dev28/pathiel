#!/usr/bin/env python
"""KillaXBT prediction-audit grader (SPEC Parts 1, 8, 12 — research-only).

Deterministic: loads research/killa_xbt/calls.json, validates the record
schema, grades every machine-gradable call against Hyperliquid BTC daily
candles (research/killa_xbt/daily_majors.json, read-only here), and writes
the grades back into calls.json (per-call ``subsequent_market_result`` /
``grade`` and a ``meta.audit_counts`` block). No LLM, no network.

Grading rules (fixed in code, SPEC Part 8):
* strict        — numeric target + stated (not researcher-imposed) horizon +
                  single primary direction; HIT only if the target trades
                  within the horizon with no invalidation touch first.
* direction     — relaxed sign-of-move check over the call's horizon
                  (researcher-imposed windows are flagged, never strict).
* target        — any stated target touched before invalidation, no expiry
                  required (open calls => PENDING, flagged no_expiry).
* timing        — target hit inside the stated window only.
* adverse-first — an excursion >10% against the call (or the stated
                  invalidation) before the target is flagged; such hits are
                  never strict wins.
* dual-path     — calls that lay out both scenarios get NO direction grade.
* NO headline win rate is produced anywhere (sample is survivor-biased).

Usage:
    .venv/bin/python scripts/research_killa_xbt.py            # grade + write
    .venv/bin/python scripts/research_killa_xbt.py --selftest # synthetic checks
    .venv/bin/python scripts/research_killa_xbt.py --dry-run  # print only
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

REPO = Path(__file__).resolve().parents[1]
KILLA = REPO / "research" / "killa_xbt"
CALLS = KILLA / "calls.json"
DAILY = KILLA / "daily_majors.json"

DAY_MS = 86_400_000
ADVERSE_PCT = 0.10  # excursion against the call that triggers the adverse flag

REQUIRED_FIELDS = [
    "id", "asset", "post_url", "posted_at", "retrieved_from", "source_quality",
    "original_text", "prediction_type", "direction", "reference_price",
    "target_low", "target_high", "invalidation_price", "prediction_horizon",
    "methodology_tags", "ambiguities", "subsequent_market_result", "grade",
    "confidence_in_grade",
]


def _date_ms(s: str) -> int:
    d = dt.date.fromisoformat(s)
    return int(dt.datetime(d.year, d.month, d.day, tzinfo=dt.UTC).timestamp() * 1000)


def _fmt(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, dt.UTC).date().isoformat()


class DailySeries:
    """BTC daily bars [t,o,h,l,c,v]; touch/cross scans between dates."""

    def __init__(self, bars: Sequence[Sequence[float]]):
        self.bars = sorted(([int(b[0])] + [float(x) for x in b[1:]] for b in bars),
                           key=lambda b: b[0])
        self.t_end = self.bars[-1][0]

    def window(self, t0: int, t1: Optional[int]) -> List[List[float]]:
        t1 = t1 if t1 is not None else self.t_end + DAY_MS
        return [b for b in self.bars if t0 <= b[0] <= t1]

    def first_touch(self, t0: int, t1: Optional[int], level: float,
                    direction: str) -> Optional[int]:
        """First day the level trades: up => high >= level, down => low <= level."""
        for b in self.window(t0, t1):
            if direction == "up" and b[2] >= level:
                return b[0]
            if direction == "down" and b[3] <= level:
                return b[0]
        return None

    def close_on_or_before(self, t: int) -> Optional[float]:
        prev = None
        for b in self.bars:
            if b[0] > t:
                break
            prev = b[4]
        return prev

    def extreme_against(self, t0: int, t1: int, ref: float, direction: str) -> float:
        """Worst adverse move (fraction of ref) against `direction` in [t0,t1]."""
        worst = 0.0
        for b in self.window(t0, t1):
            adverse = (ref - b[3]) / ref if direction == "up" else (b[2] - ref) / ref
            worst = max(worst, adverse)
        return worst


# ---------------------------------------------------------------------------
# per-kind graders — each returns (result_text, grades_dict)
# ---------------------------------------------------------------------------
def grade_target(m: dict, s: DailySeries) -> Tuple[str, dict]:
    t0 = _date_ms(m["ref_date"])
    horizon = m.get("horizon_days")
    t1 = t0 + horizon * DAY_MS if horizon else None
    direction = m["direction"]
    target = float(m["target"])
    inv = m.get("invalidation")
    hit = s.first_touch(t0, t1, target, direction)
    inv_dir = "down" if direction == "up" else "up"
    inv_hit = s.first_touch(t0, t1, float(inv), inv_dir) if inv else None
    ref = s.close_on_or_before(t0) or float(m.get("target"))
    g: dict = {"no_expiry": horizon is None,
               "horizon_imposed": bool(m.get("horizon_imposed_by_researcher"))}
    if inv_hit is not None and (hit is None or inv_hit < hit):
        g["target"] = "MISS_INVALIDATED_FIRST"
        res = f"invalidation {inv} traded {_fmt(inv_hit)} before target {target}"
    elif hit is not None:
        adverse = s.extreme_against(t0, hit, ref, direction)
        g["target"] = "HIT"
        g["adverse_first"] = adverse >= ADVERSE_PCT
        res = f"target {target} traded {_fmt(hit)}" + \
              (f" AFTER {adverse:.0%} adverse excursion (flagged)" if g["adverse_first"] else "")
    elif t1 is not None and t1 <= s.t_end:
        g["target"] = "MISS_EXPIRED"
        res = f"target {target} never traded by {_fmt(t1)}"
    else:
        g["target"] = "PENDING"
        res = f"target {target} not yet traded (data to {_fmt(s.t_end)})"
    # timing grade only exists when a horizon was stated by the author
    if horizon and not g["horizon_imposed"]:
        g["timing"] = "HIT" if (hit is not None and (t1 is None or hit <= t1)
                                and g["target"] == "HIT") else \
                      ("PENDING" if t1 > s.t_end else "MISS")
    # direction grade withheld on dual-path calls UNLESS a primary was stated
    # (Part 8: dual-path calls are not directional wins w/o a stated primary;
    # with a stated primary the call is graded on that primary, including its
    # own invalidation level — misses count)
    if m.get("dual_path") and not m.get("primary_stated"):
        g["direction"] = "WITHHELD_DUAL_PATH"
    elif m.get("dual_path") and m.get("primary_stated"):
        g["direction"] = ("MISS_INVALIDATED_FIRST"
                          if g["target"] == "MISS_INVALIDATED_FIRST"
                          else ("HIT" if g["target"] == "HIT" else g["target"]))
    # strict: stated horizon + (single path OR stated primary) + clean hit
    if horizon and not g["horizon_imposed"] and (
            not m.get("dual_path") or m.get("primary_stated")):
        g["strict"] = "HIT" if g.get("target") == "HIT" and not g.get("adverse_first") \
            else ("PENDING" if g["target"] == "PENDING" else "MISS")
    else:
        g["strict"] = "NOT_GRADABLE"
    return res, g


def grade_direction(m: dict, s: DailySeries) -> Tuple[str, dict]:
    t0 = _date_ms(m["ref_date"])
    horizon = int(m["horizon_days"])
    t1 = t0 + horizon * DAY_MS
    c0 = s.close_on_or_before(t0)
    c1 = s.close_on_or_before(min(t1, s.t_end))
    g: dict = {"horizon_imposed": bool(m.get("horizon_imposed_by_researcher")),
               "strict": "NOT_GRADABLE"}
    if c0 is None or c1 is None:
        g["direction"] = "UNGRADABLE_NO_DATA"
        return "no price data", g
    if t1 > s.t_end:
        g["direction"] = "PENDING"
        move = c1 / c0 - 1.0
        return f"window open; move so far {move:+.1%}", g
    move = c1 / c0 - 1.0
    ok = move < 0 if m["direction"] == "down" else move > 0
    g["direction"] = "HIT" if ok else "MISS"
    return f"close {c0:.0f} -> {c1:.0f} ({move:+.1%}) over {horizon}d", g


def grade_path(m: dict, s: DailySeries) -> Tuple[str, dict]:
    t = _date_ms(m["ref_date"])
    stages = []
    n_hit = 0
    for lvl, direction in zip(m["path_levels"], m["path_directions"]):
        hit = s.first_touch(t, None, float(lvl), direction)
        if hit is None:
            stages.append(f"{direction} {lvl}: PENDING")
            break
        stages.append(f"{direction} {lvl}: HIT {_fmt(hit)}")
        n_hit += 1
        t = hit
    g = {"path_stages_hit": n_hit, "path_stages_total": len(m["path_levels"]),
         "target": "PARTIAL" if 0 < n_hit < len(m["path_levels"])
         else ("HIT" if n_hit == len(m["path_levels"]) else "MISS"),
         "no_expiry": True, "strict": "NOT_GRADABLE"}
    return "; ".join(stages), g


def grade_band_touch(m: dict, s: DailySeries) -> Tuple[str, dict]:
    t0 = _date_ms(m["ref_date"])
    horizon = m.get("horizon_days")
    t1 = t0 + horizon * DAY_MS if horizon else None
    lo, hi = float(m["band_low"]), float(m["band_high"])
    hit_day = None
    for b in s.window(t0, t1):
        if b[3] <= hi and b[2] >= lo:  # day range overlaps band
            hit_day = b[0]
            break
    g: dict = {"no_expiry": horizon is None}
    if hit_day is not None:
        g["target"] = "HIT"
        res = f"band [{lo:.0f},{hi:.0f}] traded {_fmt(hit_day)}"
    elif t1 is not None and t1 <= s.t_end:
        g["target"] = "MISS_EXPIRED"
        res = f"band never traded by {_fmt(t1)}"
    else:
        g["target"] = "PENDING"
        res = f"band not traded yet (data to {_fmt(s.t_end)})"
    g["strict"] = ("HIT" if g["target"] == "HIT" else
                   "PENDING" if g["target"] == "PENDING" else "MISS") \
        if horizon and not m.get("horizon_imposed_by_researcher") else "NOT_GRADABLE"
    if horizon and not m.get("horizon_imposed_by_researcher"):
        g["timing"] = g["target"] if g["target"] in ("HIT", "PENDING") else "MISS"
    return res, g


def grade_conditional_target(m: dict, s: DailySeries) -> Tuple[str, dict]:
    t0 = _date_ms(m["ref_date"])
    cond = s.first_touch(t0, None, float(m["condition_level"]), m["condition_direction"])
    g: dict = {"no_expiry": m.get("horizon_days") is None, "strict": "NOT_GRADABLE"}
    if cond is None:
        g["condition"] = "NOT_TRIGGERED"
        g["target"] = "NOT_APPLICABLE"
        return f"condition {m['condition_level']} never traded", g
    g["condition"] = f"TRIGGERED {_fmt(cond)}"
    hit = s.first_touch(cond, None, float(m["target"]), m["direction"])
    if hit is not None:
        g["target"] = "HIT"
        res = f"condition traded {_fmt(cond)}; target {m['target']} traded {_fmt(hit)}"
    else:
        g["target"] = "PENDING"
        low = min(b[3] for b in s.window(cond, None))
        res = (f"condition traded {_fmt(cond)}; target {m['target']} not traded "
               f"(post-trigger low {low:.0f})")
    return res, g


def grade_negative_target(m: dict, s: DailySeries) -> Tuple[str, dict]:
    """Claim: `target` will NOT trade before `not_before`."""
    t0 = _date_ms(m["ref_date"])
    t_nb = _date_ms(m["not_before"])
    hit = s.first_touch(t0, min(t_nb, s.t_end), float(m["target"]), m["direction"])
    g: dict = {"strict": "NOT_GRADABLE"}
    if hit is not None:
        g["target"] = "MISS (negative claim violated)"
        res = f"{m['target']} traded {_fmt(hit)} before {m['not_before']}"
    elif s.t_end >= t_nb:
        g["target"] = "HIT"
        res = f"{m['target']} never traded before {m['not_before']}"
    else:
        g["target"] = "PENDING_ON_TRACK"
        res = f"not traded so far; window runs to {m['not_before']}"
    return res, g


GRADERS = {
    "target": grade_target,
    "direction": grade_direction,
    "path": grade_path,
    "band_touch": grade_band_touch,
    "conditional_target": grade_conditional_target,
    "negative_target": grade_negative_target,
}


# ---------------------------------------------------------------------------
def validate(doc: dict) -> List[str]:
    errs = []
    ids = set()
    for c in doc.get("calls", []):
        cid = c.get("id", "?")
        if cid in ids:
            errs.append(f"{cid}: duplicate id")
        ids.add(cid)
        for f in REQUIRED_FIELDS:
            if f not in c:
                errs.append(f"{cid}: missing field {f}")
        sq = c.get("source_quality", "")
        if not any(k in sq for k in ("direct", "archived", "article_quote",
                                     "screenshot", "unverified")):
            errs.append(f"{cid}: bad source_quality {sq!r}")
        m = c.get("machine")
        if m and m.get("kind") not in GRADERS:
            errs.append(f"{cid}: unknown machine.kind {m.get('kind')}")
    return errs


def run(write: bool) -> dict:
    doc = json.loads(CALLS.read_text())
    errs = validate(doc)
    if errs:
        for e in errs:
            print("SCHEMA ERROR:", e)
        raise SystemExit(1)
    series = DailySeries(json.loads(DAILY.read_text())["BTC"])
    counts = {"total": 0, "machine_graded": 0, "no_grade": 0,
              "buckets": {"fully_testable": [], "partially_testable": [],
                          "directionally_testable": [], "pending": [],
                          "unverifiable_or_vague": []},
              "strict": {}, "relaxed_target": {}, "relaxed_direction": {},
              "source_quality": {}}
    for c in doc["calls"]:
        counts["total"] += 1
        sq = c["source_quality"].split()[0].rstrip(",;")
        counts["source_quality"][sq] = counts["source_quality"].get(sq, 0) + 1
        m = c.get("machine")
        if not m:
            counts["no_grade"] += 1
            counts["buckets"]["unverifiable_or_vague"].append(c["id"])
            continue
        res, g = GRADERS[m["kind"]](m, series)
        c["subsequent_market_result"] = res
        c["grade"] = g
        counts["machine_graded"] += 1
        strict = g.get("strict", "NOT_GRADABLE")
        counts["strict"][strict] = counts["strict"].get(strict, 0) + 1
        tgt = g.get("target")
        if tgt:
            counts["relaxed_target"][tgt] = counts["relaxed_target"].get(tgt, 0) + 1
        d = g.get("direction")
        if d:
            counts["relaxed_direction"][d] = counts["relaxed_direction"].get(d, 0) + 1
        # bucket assignment
        if strict in ("HIT", "MISS", "PENDING"):
            counts["buckets"]["fully_testable"].append(c["id"])
        elif tgt in ("HIT", "PARTIAL", "MISS_EXPIRED", "MISS_INVALIDATED_FIRST",
                     "MISS (negative claim violated)"):
            counts["buckets"]["partially_testable"].append(c["id"])
        elif d in ("HIT", "MISS"):
            counts["buckets"]["directionally_testable"].append(c["id"])
        else:
            counts["buckets"]["pending"].append(c["id"])
        print(f"{c['id']} [{m['kind']:>18}] {res}")
        print(f"     grades: { {k: v for k, v in g.items()} }")
    doc["meta"]["audit_counts"] = counts
    doc["meta"]["graded_at"] = dt.datetime.now(dt.UTC).strftime("%Y-%m-%d")
    doc["meta"]["price_source"] = "Hyperliquid BTC 1d (daily_majors.json)"
    if write:
        CALLS.write_text(json.dumps(doc, indent=2))
        print(f"\nwrote grades into {CALLS}")
    print("\nAUDIT COUNTS:", json.dumps(counts, indent=1))
    return counts


# ---------------------------------------------------------------------------
def selftest() -> None:
    """Synthetic series: deterministic checks of every grader."""
    day0 = _date_ms("2025-01-01")
    # 100 -> ramps to 120 by day 20 -> falls to 80 by day 60 -> flat
    bars = []
    px = 100.0
    for i in range(120):
        if i < 20:
            px += 1.0
        elif i < 60:
            px -= 1.0
        bars.append([day0 + i * DAY_MS, px, px + 1, px - 1, px, 1.0])
    s = DailySeries(bars)

    res, g = grade_target({"ref_date": "2025-01-01", "kind": "target",
                           "direction": "up", "target": 115,
                           "invalidation": 90, "horizon_days": 30}, s)
    assert g["target"] == "HIT" and g["strict"] == "HIT", (res, g)

    res, g = grade_target({"ref_date": "2025-01-01", "kind": "target",
                           "direction": "up", "target": 150,
                           "invalidation": 90, "horizon_days": 90}, s)
    assert g["target"] == "MISS_INVALIDATED_FIRST", (res, g)

    res, g = grade_target({"ref_date": "2025-01-01", "kind": "target",
                           "direction": "down", "target": 85,
                           "invalidation": None, "horizon_days": None}, s)
    assert g["target"] == "HIT" and g["adverse_first"] is True, (res, g)  # +20% first
    assert g["strict"] == "NOT_GRADABLE"  # no horizon

    res, g = grade_direction({"ref_date": "2025-01-01", "kind": "direction",
                              "direction": "down", "horizon_days": 59,
                              "horizon_imposed_by_researcher": True}, s)
    assert g["direction"] == "HIT" and g["strict"] == "NOT_GRADABLE", (res, g)

    res, g = grade_path({"ref_date": "2025-01-01", "kind": "path",
                         "path_levels": [115, 100, 85, 50],
                         "path_directions": ["up", "down", "down", "down"]}, s)
    assert g["path_stages_hit"] == 3 and g["target"] == "PARTIAL", (res, g)

    res, g = grade_band_touch({"ref_date": "2025-01-01", "kind": "band_touch",
                               "band_low": 79, "band_high": 82,
                               "horizon_days": 90}, s)
    assert g["target"] == "HIT", (res, g)

    res, g = grade_conditional_target({"ref_date": "2025-01-01",
                                       "kind": "conditional_target",
                                       "condition_level": 95,
                                       "condition_direction": "down",
                                       "direction": "down", "target": 82}, s)
    assert g["condition"].startswith("TRIGGERED") and g["target"] == "HIT", (res, g)

    res, g = grade_negative_target({"ref_date": "2025-01-01",
                                    "kind": "negative_target", "direction": "up",
                                    "target": 200, "not_before": "2025-03-01"}, s)
    assert g["target"] == "HIT", (res, g)  # 200 never traded before Mar 1

    res, g = grade_negative_target({"ref_date": "2025-01-01",
                                    "kind": "negative_target", "direction": "up",
                                    "target": 115, "not_before": "2025-03-01"}, s)
    assert "MISS" in g["target"], (res, g)
    print("selftest OK (9 assertions)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        selftest()
        return
    run(write=not args.dry_run)


if __name__ == "__main__":
    main()
