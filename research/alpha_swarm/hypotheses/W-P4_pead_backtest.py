#!/usr/bin/env python
"""W-P4: PEAD on xyz equities — post-earnings-filing drift at day horizons.

Implements findings/W-P4_xyz_pead.md's pre-registered spec verbatim.
Cache-only (W-P3_cache_events.json, W-P3_cache_texts/, W-P3_results.json,
W-P1_cache_1h.json) — no network.

  classify = 8-K items contains 2.02 (text fallback adds 0) OR 6-K text
             matching the nine locked regexes
  bars     = DAILY, built from the 1h cache by UTC calendar day
  entry    = open of first daily bar with open_time >= acceptance (gap <= 48h,
             one event per coin-entry-bar within each subset)
  horizons = +3d/+5d/+10d/+21d open-to-open, exit slack 48h
  rule A   = sign(entry_open / last-completed-pre-acceptance 1h close - 1)
  rule B   = W-P3 cached LLM sign (LONG/SHORT; SKIP excluded)
  costs    = 25 bps round trip
  null     = 2000x matched same-coin random-time (surrogate acceptance at a
             uniform 1h bar-open; identical pipeline), one-sided MC p
  second   = 2000x sign-permutation within cell (diagnostic only)

Outputs W-P4_results.json next to the caches + a printed report.
"""
from __future__ import annotations

import json
import os
import random
import re
import statistics
from bisect import bisect_left, bisect_right
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
EV_CACHE = os.path.join(HERE, "W-P3_cache_events.json")
TXT_DIR = os.path.join(HERE, "W-P3_cache_texts")
LLM_CACHE = os.path.join(HERE, "W-P3_results.json")
HR_CACHE = os.path.join(HERE, "W-P1_cache_1h.json")
OUT = os.path.join(HERE, "W-P4_results.json")

HOUR_MS = 3_600_000
DAY_MS = 86_400_000
COST_RT = 0.0025
N_NULL = 2000
ENTRY_GAP_MAX_MS = 48 * HOUR_MS
EXIT_SLACK_MS = 48 * HOUR_MS
HORIZONS = {"3d": 3, "5d": 5, "10d": 10, "21d": 21}
H_KEYS = list(HORIZONS)
RNG = random.Random(20260720)

# ── locked earnings classifier ───────────────────────────────────────────────

P_8K_TEXT = [re.compile(p, re.I) for p in [
    r"Item\s+2\.02",
    r"Results\s+of\s+Operations\s+and\s+Financial\s+Condition",
]]
P_6K = [re.compile(p, re.I) for p in [
    r"(first|second|third|fourth)\s+quarter[\s\S]{0,60}?(results|earnings)",
    r"quarterly\s+(financial\s+)?results",
    r"(annual|full[- ]year|half[- ]year|interim)\s+(financial\s+)?results",
    r"earnings\s+(release|announcement|call|conference)",
    r"results\s+of\s+operations",
    r"unaudited[\s\S]{0,60}?(financial\s+statements|results)",
    r"(march|june|september|december)\s+quarter[\s\S]{0,30}?results",
    r"\bQ[1-4]\s+20\d\d\b[\s\S]{0,80}?(results|earnings)",
    r"reports\s+[\s\S]{0,120}?net\s+(sales|income)",
]]


def is_earnings(ev: dict) -> bool:
    if ev["form"] == "8-K":
        if "2.02" in (ev.get("items") or ""):
            return True
        txt = _text(ev["accession"])
        return any(p.search(txt) for p in P_8K_TEXT)
    if ev["form"] == "6-K":
        txt = _text(ev["accession"])
        return any(p.search(txt) for p in P_6K)
    return False


def _text(accession: str) -> str:
    path = os.path.join(TXT_DIR, f"{accession}.txt")
    try:
        with open(path) as f:
            return f.read()
    except FileNotFoundError:
        return ""


# ── per-coin bar machinery ───────────────────────────────────────────────────

class Coin:
    def __init__(self, coin: str, rows: list):
        self.coin = coin
        self.h_t = [r[0] for r in rows]           # 1h open times
        self.h_c = [r[4] for r in rows]           # 1h closes
        days: dict[int, list] = {}
        for r in rows:
            d = r[0] // DAY_MS
            if d not in days:
                days[d] = [r[0], r[1], r[4]]      # open_time, open, close
            else:
                days[d][2] = r[4]
        srt = [days[d] for d in sorted(days)]
        self.d_t = [x[0] for x in srt]
        self.d_o = [x[1] for x in srt]

    def entry_idx(self, acc_ms: int):
        """First daily bar with open_time >= acc; None if absent or gap>48h."""
        i = bisect_left(self.d_t, acc_ms)
        if i >= len(self.d_t) or self.d_t[i] - acc_ms > ENTRY_GAP_MAX_MS:
            return None
        return i

    def ret(self, i_entry: int, h_days: int):
        """Open-to-open daily return to first bar >= entry_t + h (48h slack)."""
        target = self.d_t[i_entry] + h_days * DAY_MS
        j = bisect_left(self.d_t, target)
        if j >= len(self.d_t) or self.d_t[j] > target + EXIT_SLACK_MS:
            return None
        return self.d_o[j] / self.d_o[i_entry] - 1.0

    def reaction_sign(self, i_entry: int, acc_ms: int) -> int:
        """Sign of entry_open vs close of last 1h bar fully completed before
        acceptance (bar close time = t+1h <= acc). 0 if no pre-bar / flat."""
        p = bisect_right(self.h_t, acc_ms - HOUR_MS) - 1
        if p < 0:
            return 0
        r = self.d_o[i_entry] / self.h_c[p] - 1.0
        return (r > 0) - (r < 0)

    def surrogate(self, t_ms: int):
        """Run the event pipeline at a surrogate acceptance time.
        Returns (sign, {h: r}) or None if entry invalid."""
        i = self.entry_idx(t_ms)
        if i is None:
            return None
        return (self.reaction_sign(i, t_ms),
                {h: self.ret(i, k) for h, k in HORIZONS.items()})

    def null_pool(self) -> list:
        """1h bar-open times usable as surrogate acceptances: valid entry and
        a valid +21d exit (longest horizon), per the locked spec. DISCLOSED
        AMENDMENT (mechanical, see findings): coins with <21d of history have
        an empty 21d pool yet hold valid 3d/5d events — fall back to a
        +3d-valid pool; longer-horizon draws yield None and are skipped,
        mirroring the event's own None at those horizons."""
        for h_req in (21, 3):
            out = []
            for t in self.h_t:
                i = self.entry_idx(t)
                if i is None or self.ret(i, h_req) is None:
                    continue
                out.append(t)
            if out:
                return out
        return []


def one_sided_p(obs: float, null_means: list) -> float:
    if obs >= 0:
        k = sum(1 for x in null_means if x >= obs)
    else:
        k = sum(1 for x in null_means if x <= obs)
    return (1 + k) / (1 + len(null_means))


def mean(xs):
    return statistics.fmean(xs) if xs else float("nan")


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    raw = json.load(open(EV_CACHE))
    assert len(raw) == 308, f"event cache has {len(raw)} events, expected 308"
    llm = json.load(open(LLM_CACHE))["llm"]
    bars = json.load(open(HR_CACHE))
    coins = {c: Coin(c, rows) for c, rows in bars.items() if rows}

    # ── classify + build daily events (earnings and complement separately) ──
    def build(subset_events):
        """Daily-entry events, deduped one per coin-entry-bar (earliest acc)."""
        out, seen = [], set()
        for ev in sorted(subset_events, key=lambda e: e["acc_ms"]):
            co = coins.get(ev["coin"])
            if co is None or len(co.d_t) < 5:
                continue
            i = co.entry_idx(ev["acc_ms"])
            if i is None:
                continue
            key = (ev["coin"], i)
            if key in seen:
                continue
            seen.add(key)
            d = llm.get(ev["accession"]) or {}
            b_sign = {"LONG": 1, "SHORT": -1}.get(d.get("direction"), 0)
            out.append({
                "ticker": ev["ticker"], "coin": ev["coin"], "form": ev["form"],
                "items": ev.get("items", ""), "accession": ev["accession"],
                "acc_ms": ev["acc_ms"], "acc_iso": ev["acc_iso"],
                "i_entry": i,
                "entry_gap_h": (co.d_t[i] - ev["acc_ms"]) / HOUR_MS,
                "a_sign": co.reaction_sign(i, ev["acc_ms"]),
                "b_sign": b_sign,
                "r": {h: co.ret(i, k) for h, k in HORIZONS.items()},
            })
        return out

    earn_raw = [e for e in raw if is_earnings(e)]
    none_raw = [e for e in raw if not is_earnings(e)]
    print(f"classified earnings: {len(earn_raw)} "
          f"({sum(1 for e in earn_raw if e['form'] == '8-K')} 8-K, "
          f"{sum(1 for e in earn_raw if e['form'] == '6-K')} 6-K) "
          f"of {len(raw)}")

    EARN = build(earn_raw)
    NONE = build(none_raw)
    ALL = build(raw)          # for the B-ALL308 diagnostic
    for name, evs in (("EARN", EARN), ("NONEARN", NONE), ("ALL", ALL)):
        n_a = sum(1 for e in evs if e["a_sign"] != 0)
        n_b = sum(1 for e in evs if e["b_sign"] != 0)
        print(f"{name}: n={len(evs)} (A-signable {n_a}, LLM-signed {n_b}); "
              f"median gap {statistics.median(e['entry_gap_h'] for e in evs):.1f}h"
              if evs else f"{name}: n=0")

    # ── null draws: per event x iteration, surrogate acceptance time ──
    pools = {c: coins[c].null_pool()
             for c in {e["coin"] for e in EARN + NONE + ALL}}
    sur_cache: dict = {}

    def draws_for(events):
        """pre[e_idx][iter] = (sign, {h: r}) at a random surrogate time."""
        pre = []
        for e in events:
            co, pool = coins[e["coin"]], pools[e["coin"]]
            if not pool:
                pre.append([None] * N_NULL)
                continue
            rows = []
            for _ in range(N_NULL):
                t = RNG.choice(pool)
                ck = (e["coin"], t)
                if ck not in sur_cache:
                    sur_cache[ck] = co.surrogate(t)
                rows.append(sur_cache[ck])
            pre.append(rows)
        return pre

    # ── scoring ──
    def score(events, pre, rule, label):
        """rule 'A' uses a_sign (null: surrogate's own sign);
        rule 'B' uses b_sign (null: event sign at surrogate time)."""
        key = "a_sign" if rule == "A" else "b_sign"
        res = {"label": label, "rule": rule, "n_events": len(events)}
        for h in H_KEYS:
            idx = [k for k, e in enumerate(events)
                   if e[key] != 0 and e["r"][h] is not None]
            obs = [events[k][key] * events[k]["r"][h] - COST_RT for k in idx]
            uns = [abs(events[k]["r"][h]) for k in idx]
            hres = {"n": len(obs), "ev25": mean(obs), "unsigned": mean(uns)}
            if len(obs) >= 5:
                nm, nm_u = [], []
                for it in range(N_NULL):
                    vals, uvals = [], []
                    for k in idx:
                        row = pre[k][it]
                        if row is None:
                            continue
                        s_null, r_null = row
                        r = r_null[h]
                        if r is None:
                            continue
                        uvals.append(abs(r))
                        s = s_null if rule == "A" else events[k][key]
                        if s != 0:
                            vals.append(s * r - COST_RT)
                    if vals:
                        nm.append(mean(vals))
                    if uvals:
                        nm_u.append(mean(uvals))
                hres["null_ev25"] = mean(nm)
                hres["p"] = one_sided_p(hres["ev25"], nm)
                hres["null_unsigned"] = mean(nm_u)
                hres["p_unsigned"] = one_sided_p(hres["unsigned"], nm_u)
                # secondary: sign permutation (diagnostic)
                signs = [events[k][key] for k in idx]
                rets = [events[k]["r"][h] for k in idx]
                pm = []
                for _ in range(N_NULL):
                    sh = signs[:]
                    RNG.shuffle(sh)
                    pm.append(mean([s * r - COST_RT for s, r in zip(sh, rets)]))
                hres["p_perm"] = one_sided_p(hres["ev25"], pm)
            res[h] = hres
        return res

    pre_earn = draws_for(EARN)
    pre_none = draws_for(NONE)
    pre_all = draws_for(ALL)

    results = {"spec": "findings/W-P4_xyz_pead.md pre-registered 2026-07-20",
               "n_classified_earn": len(earn_raw),
               "earn_6k_accessions": [e["accession"] for e in earn_raw
                                      if e["form"] == "6-K"],
               "scores": {}}
    S = results["scores"]
    S["EARN_A"] = score(EARN, pre_earn, "A", "earnings, PEAD reaction sign")
    S["EARN_B"] = score(EARN, pre_earn, "B", "earnings, cached LLM sign")
    half = len(EARN) // 2
    S["EARN_A_H1"] = score(EARN[:half], pre_earn[:half], "A", "earn A first half")
    S["EARN_A_H2"] = score(EARN[half:], pre_earn[half:], "A", "earn A second half")
    S["EARN_B_H1"] = score(EARN[:half], pre_earn[:half], "B", "earn B first half")
    S["EARN_B_H2"] = score(EARN[half:], pre_earn[half:], "B", "earn B second half")
    e8 = [e for e in EARN if e["form"] == "8-K"]
    e6 = [e for e in EARN if e["form"] == "6-K"]
    i8 = [k for k, e in enumerate(EARN) if e["form"] == "8-K"]
    i6 = [k for k, e in enumerate(EARN) if e["form"] == "6-K"]
    S["EARN8K_A"] = score(e8, [pre_earn[k] for k in i8], "A", "8-K only (diag)")
    S["EARN6K_A"] = score(e6, [pre_earn[k] for k in i6], "A", "6-K only (diag)")
    S["NONEARN_A"] = score(NONE, pre_none, "A", "non-earnings complement (diag)")
    S["ALL_B"] = score(ALL, pre_all, "B", "all 308, LLM sign (diag)")
    hb = len(ALL) // 2
    S["ALL_B_H1"] = score(ALL[:hb], pre_all[:hb], "B", "all B first half (diag)")
    S["ALL_B_H2"] = score(ALL[hb:], pre_all[hb:], "B", "all B second half (diag)")

    results["events_detail"] = [
        {k: e[k] for k in ("ticker", "form", "items", "accession", "acc_iso",
                           "entry_gap_h", "a_sign", "b_sign", "r")}
        for e in EARN]
    with open(OUT, "w") as f:
        json.dump(results, f, indent=1)
    print(f"-> {OUT}")

    for name, sc in S.items():
        print(f"\n[{name}] {sc['label']} n_events={sc['n_events']}")
        for h in H_KEYS:
            r = sc[h]
            if "p" in r:
                print(f"  +{h:<4} n={r['n']:3d} EV25 {r['ev25']*100:+.2f}% "
                      f"(null {r['null_ev25']*100:+.2f}%) p={r['p']:.4f} "
                      f"perm={r['p_perm']:.4f} | |r| {r['unsigned']*100:.2f}% "
                      f"vs {r['null_unsigned']*100:.2f}% p_u={r['p_unsigned']:.4f}")
            else:
                print(f"  +{h:<4} n={r['n']} (too few)")


if __name__ == "__main__":
    main()
