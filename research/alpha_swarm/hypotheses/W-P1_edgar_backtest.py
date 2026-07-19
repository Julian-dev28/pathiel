#!/usr/bin/env python
"""W-P1: EDGAR 8-K/6-K acceptance -> xyz perp drift. Event study vs matched null.

Reads ONLY the W-P1_cache_*.json files (no network). Spec is pre-registered in
findings/W-P1_edgar_latency.md — this script implements it verbatim:

  entry  = open of first 1h bar with open_time >= acceptance (containing bar skipped)
  horiz  = +1h/+4h/+24h open-to-open (exit = first bar >= entry_time + h, <=6h slack)
  events = 8-K/6-K, no amendments, within candle coverage, entry gap <= 24h,
           one event per coin-bar (earliest kept)
  null   = 2000 iters, per-event uniform same-coin draw, same horizon
           (primary: horizon-matched; secondary: + session-matched)
  tests  = unsigned mean|r| vs null; S1 long-all net 25bps; S2 first-reaction
           momentum net 25bps (null for S2 applies the same sign rule at the
           random bar). One-sided MC p in the direction of the observed mean.
  splits = OOS time halves (n>=16); after-hours vs market-hours by acceptance ET.

Outputs W-P1_results.json next to the caches + a printed report.
"""
from __future__ import annotations

import json
import os
import random
import statistics
from bisect import bisect_left
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
UNI_CACHE = os.path.join(HERE, "W-P1_cache_universe.json")
CIK_CACHE = os.path.join(HERE, "W-P1_cache_cik_map.json")
FIL_CACHE = os.path.join(HERE, "W-P1_cache_filings.json")
HR_CACHE = os.path.join(HERE, "W-P1_cache_1h.json")
OUT = os.path.join(HERE, "W-P1_results.json")

ET = ZoneInfo("America/New_York")
HOUR_MS = 3_600_000
COST_RT = 0.0025          # 25 bps round trip
N_NULL = 2000
ENTRY_GAP_MAX_MS = 24 * HOUR_MS
EXIT_SLACK_MS = 6 * HOUR_MS
HORIZONS = {"1h": 1, "4h": 4, "24h": 24}
RNG = random.Random(20260719)


# ── EDGAR timestamp convention ───────────────────────────────────────────────

def tz_check(filings_cache) -> dict:
    """Decide empirically whether acceptanceDateTime is ET-mislabeled-Z or UTC.
    8-K acceptances cluster 16:00-17:30 ET. Raw-hour peak at 16-17 => ET;
    at 20-21 => UTC."""
    hrs = []
    for rec in filings_cache.values():
        for f in rec["filings"]:
            if f["form"] == "8-K" and f["acceptanceDateTime"]:
                hrs.append(int(f["acceptanceDateTime"][11:13]))
    n_et = sum(1 for h in hrs if h in (16, 17))
    n_utc = sum(1 for h in hrs if h in (20, 21))
    verdict = "ET" if n_et >= n_utc else "UTC"
    hist = {h: hrs.count(h) for h in sorted(set(hrs))}
    return {"n": len(hrs), "raw_hour_16_17": n_et, "raw_hour_20_21": n_utc,
            "verdict": verdict, "hist": hist}


def acc_to_utc_ms(s: str, convention: str) -> int:
    naive = datetime.fromisoformat(s.replace("Z", "").replace(".000", ""))
    if convention == "ET":
        return int(naive.replace(tzinfo=ET).timestamp() * 1000)
    return int(naive.replace(tzinfo=timezone.utc).timestamp() * 1000)


def is_market_hours(utc_ms: int) -> bool:
    d = datetime.fromtimestamp(utc_ms / 1000, tz=ET)
    if d.weekday() >= 5:
        return False
    mins = d.hour * 60 + d.minute
    return 9 * 60 + 30 <= mins < 16 * 60


# ── per-coin bar machinery ───────────────────────────────────────────────────

class Coin:
    def __init__(self, coin, rows):
        self.coin = coin
        self.t = [r[0] for r in rows]
        self.o = [r[1] for r in rows]
        self.c = [r[4] for r in rows]
        self.v = [r[5] for r in rows]
        self.session_mkt = [is_market_hours(t) for t in self.t]

    def idx_at_or_after(self, ms):
        i = bisect_left(self.t, ms)
        return i if i < len(self.t) else None

    def ret(self, i_entry, h_bars):
        """Open-to-open return entry bar -> first bar >= entry_t + h (slack-capped)."""
        target = self.t[i_entry] + h_bars * HOUR_MS
        j = self.idx_at_or_after(target)
        if j is None or self.t[j] > target + EXIT_SLACK_MS:
            return None
        return self.o[j] / self.o[i_entry] - 1.0

    def reaction_sign(self, i_entry, acc_ms):
        """S2 direction: entry open vs close of the last bar that fully
        completed BEFORE acceptance (close time t+1h <= acc). With contiguous
        bars that is i_entry-2 (the containing bar i-1 completes after acc);
        after an off-hours gap it is i_entry-1. Known at entry time."""
        p = self.idx_at_or_after(acc_ms - HOUR_MS)
        # bar p opens at >= acc-1h => completes at >= acc; last fully-done is p-1
        p = (p - 1) if p is not None else len(self.t) - 1
        if p < 0 or p >= i_entry:
            p = i_entry - 1 if self.t[i_entry - 1] + HOUR_MS <= acc_ms else i_entry - 2
        if p < 0:
            return 0
        r = self.o[i_entry] / self.c[p] - 1.0
        return (r > 0) - (r < 0)

    def null_sign(self, i):
        """Matched null for S2: surrogate acceptance uniform inside bar i-1
        => reference bar i-2 (same 1-2h reaction window as a contiguous event)."""
        if i < 2:
            return 0
        r = self.o[i] / self.c[i - 2] - 1.0
        return (r > 0) - (r < 0)

    def null_pool(self, session_mkt=None):
        """Bar indices usable as random entries (24h exit must exist)."""
        out = []
        for i in range(len(self.t)):
            if self.ret(i, 24) is None:
                continue
            if session_mkt is not None and self.session_mkt[i] != session_mkt:
                continue
            out.append(i)
        return out


def one_sided_p(obs, null_means):
    """MC p in the direction of the observed mean."""
    if obs >= 0:
        k = sum(1 for x in null_means if x >= obs)
    else:
        k = sum(1 for x in null_means if x <= obs)
    return (1 + k) / (1 + len(null_means))


def mean(xs):
    return statistics.fmean(xs) if xs else float("nan")


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    cmap = json.load(open(CIK_CACHE))
    fils = json.load(open(FIL_CACHE))
    bars = json.load(open(HR_CACHE))

    tz = tz_check(fils)
    print(f"tz check: {tz['verdict']}  (raw h16-17: {tz['raw_hour_16_17']}, "
          f"h20-21: {tz['raw_hour_20_21']}, n={tz['n']})")

    coins = {c: Coin(c, rows) for c, rows in bars.items() if rows}

    # ── build events ──
    events = []          # dicts with coin, ticker, form, acc_ms, i_entry, ...
    per_coin = {}
    for tick, rec in sorted(fils.items()):
        coin = f"xyz:{tick}"
        co = coins.get(coin)
        stats = {"cik": rec["cik"], "name": rec["name"],
                 "n_filings_total": len(rec["filings"]), "bars": 0,
                 "in_window": 0, "kept": 0, "dropped_gap": 0}
        per_coin[tick] = stats
        if co is None or len(co.t) < 48:
            continue
        stats["bars"] = len(co.t)
        seen_entry_bars = set()
        for f in sorted(rec["filings"], key=lambda x: x["acceptanceDateTime"]):
            if not f["acceptanceDateTime"]:
                continue
            acc = acc_to_utc_ms(f["acceptanceDateTime"], tz["verdict"])
            if acc < co.t[0] or acc > co.t[-1] - 25 * HOUR_MS:
                continue
            stats["in_window"] += 1
            # skip the bar CONTAINING acc: entry = first bar with open >= acc
            i = co.idx_at_or_after(acc)   # bar open >= acc  (containing bar has open < acc)
            if i is None:
                continue
            gap = co.t[i] - acc
            if gap > ENTRY_GAP_MAX_MS:
                stats["dropped_gap"] += 1
                continue
            if i in seen_entry_bars:      # one event per coin-bar, earliest kept
                continue
            seen_entry_bars.add(i)
            stats["kept"] += 1
            events.append({
                "ticker": tick, "coin": coin, "form": f["form"],
                "items": f.get("items", ""), "acc_ms": acc,
                "acc_iso": datetime.fromtimestamp(acc / 1000, tz=timezone.utc).isoformat(),
                "mkt_hours": is_market_hours(acc), "i_entry": i,
                "entry_gap_h": gap / HOUR_MS,
                "entry_zero_vol": co.v[i] == 0.0,
            })
    events.sort(key=lambda e: e["acc_ms"])
    n = len(events)
    print(f"events kept: {n} across "
          f"{len(set(e['ticker'] for e in events))} tickers "
          f"({sum(1 for e in events if e['form'] == '8-K')} 8-K, "
          f"{sum(1 for e in events if e['form'] == '6-K')} 6-K); "
          f"after-hours: {sum(1 for e in events if not e['mkt_hours'])}")

    # ── observed returns ──
    for e in events:
        co = coins[e["coin"]]
        e["r"] = {h: co.ret(e["i_entry"], k) for h, k in HORIZONS.items()}
        e["s2_sign"] = co.reaction_sign(e["i_entry"], e["acc_ms"])

    # ── null draws: per event, per iteration, one same-coin uniform bar ──
    pools = {c: coins[c].null_pool() for c in set(e["coin"] for e in events)}
    pools_sess = {}
    for e in events:
        key = (e["coin"], coins[e["coin"]].session_mkt[e["i_entry"]])
        if key not in pools_sess:
            pools_sess[key] = coins[key[0]].null_pool(session_mkt=key[1])

    # Precompute, per event and per iteration, the null draw's returns for all
    # horizons + the S2 sign — so every subset score just averages arrays.
    # null_pre[e_idx][iter] = (r1, r4, r24, sign);  r_* is None if exit missing.
    def precompute(draw_pools):
        pre = []
        ret_cache: dict = {}
        for e in events:
            co = coins[e["coin"]]
            pool = draw_pools(e)
            rows = []
            for _ in range(N_NULL):
                i = RNG.choice(pool)
                ck = (e["coin"], i)
                if ck not in ret_cache:
                    ret_cache[ck] = (co.ret(i, 1), co.ret(i, 4), co.ret(i, 24),
                                     co.null_sign(i))
                rows.append(ret_cache[ck])
            pre.append(rows)
        return pre

    null_pre = precompute(lambda e: pools[e["coin"]])
    null_pre_sess = precompute(
        lambda e: pools_sess[(e["coin"], coins[e["coin"]].session_mkt[e["i_entry"]])]
        or pools[e["coin"]])
    H_POS = {"1h": 0, "4h": 1, "24h": 2}

    def null_stats(sub_idx, h, kind, pre):
        """Per-iteration mean over the event subset; kind in unsigned/s1/s2."""
        hp = H_POS[h]
        out = []
        for it in range(N_NULL):
            vals = []
            for ei in sub_idx:
                row = pre[ei][it]
                r = row[hp]
                if r is None:
                    continue
                if kind == "unsigned":
                    vals.append(abs(r))
                elif kind == "s1":
                    vals.append(r - COST_RT)
                elif kind == "s2":
                    if row[3] != 0:
                        vals.append(row[3] * r - COST_RT)
            if vals:
                out.append(mean(vals))
        return out

    def score(sub_idx, label):
        res = {"label": label, "n": len(sub_idx)}
        for h in HORIZONS:
            obs_u = [abs(events[ei]["r"][h]) for ei in sub_idx
                     if events[ei]["r"][h] is not None]
            obs_s1 = [events[ei]["r"][h] - COST_RT for ei in sub_idx
                      if events[ei]["r"][h] is not None]
            obs_s2 = [events[ei]["s2_sign"] * events[ei]["r"][h] - COST_RT
                      for ei in sub_idx
                      if events[ei]["r"][h] is not None and events[ei]["s2_sign"] != 0]
            hres = {"n_scored": len(obs_u),
                    "unsigned_mean": mean(obs_u),
                    "s1_ev_net": mean(obs_s1),
                    "s2_ev_net": mean(obs_s2), "s2_n": len(obs_s2)}
            if len(obs_u) >= 5:
                nm_u = null_stats(sub_idx, h, "unsigned", null_pre)
                nm_1 = null_stats(sub_idx, h, "s1", null_pre)
                nm_2 = null_stats(sub_idx, h, "s2", null_pre)
                hres["unsigned_null_mean"] = mean(nm_u)
                hres["unsigned_p"] = one_sided_p(hres["unsigned_mean"], nm_u)
                hres["s1_null_mean"] = mean(nm_1)
                hres["s1_p"] = one_sided_p(hres["s1_ev_net"], nm_1)
                hres["s2_p"] = one_sided_p(hres["s2_ev_net"], nm_2)
                nm_us = null_stats(sub_idx, h, "unsigned", null_pre_sess)
                hres["unsigned_p_sessmatched"] = one_sided_p(hres["unsigned_mean"], nm_us)
                hres["unsigned_nullsess_mean"] = mean(nm_us)
            res[h] = hres
        return res

    all_idx = list(range(n))
    results = {"tz_check": tz,
               "n_events": n,
               "entry_gap_h_median": statistics.median(e["entry_gap_h"] for e in events) if n else None,
               "entry_gap_h_p90": (sorted(e["entry_gap_h"] for e in events)[int(0.9 * n)] if n else None),
               "entry_zero_vol_frac": mean([1.0 * e["entry_zero_vol"] for e in events]) if n else None,
               "per_coin": per_coin,
               "scores": {}}

    if n >= 5:
        results["scores"]["ALL"] = score(all_idx, "ALL")
        if n >= 16:
            half = n // 2
            results["scores"]["OOS_H1"] = score(all_idx[:half], "first half")
            results["scores"]["OOS_H2"] = score(all_idx[half:], "second half")
        ah = [i for i in all_idx if not events[i]["mkt_hours"]]
        mh = [i for i in all_idx if events[i]["mkt_hours"]]
        results["scores"]["AFTER_HOURS"] = score(ah, "after-hours acceptance")
        results["scores"]["MARKET_HOURS"] = score(mh, "market-hours acceptance")
        e8 = [i for i in all_idx if events[i]["form"] == "8-K"]
        e6 = [i for i in all_idx if events[i]["form"] == "6-K"]
        results["scores"]["FORM_8K"] = score(e8, "8-K only")
        results["scores"]["FORM_6K"] = score(e6, "6-K only")

    # ── xyz off-hours tradeability profile (structural premise check) ──
    prof = {}
    for co in coins.values():
        for i, t in enumerate(co.t):
            h_et = datetime.fromtimestamp(t / 1000, tz=ET).hour
            b = prof.setdefault(h_et, [0, 0, 0.0])
            b[0] += 1
            b[1] += 1 if co.v[i] > 0 else 0
            if i > 0:
                b[2] += abs(co.o[i] / co.o[i - 1] - 1.0)
    results["hour_profile_ET"] = {
        h: {"bars": b[0], "nonzero_vol_frac": b[1] / b[0],
            "mean_abs_1h_move": b[2] / max(b[0] - 1, 1)}
        for h, b in sorted(prof.items())}
    results["events_detail"] = [
        {k: e[k] for k in ("ticker", "form", "items", "acc_iso", "mkt_hours",
                           "entry_gap_h", "entry_zero_vol", "r", "s2_sign")}
        for e in events]

    with open(OUT, "w") as f:
        json.dump(results, f, indent=1)
    print(f"-> {OUT}")

    # ── printed report ──
    for name, sc in results["scores"].items():
        print(f"\n[{name}] n={sc['n']}")
        for h in HORIZONS:
            r = sc[h]
            if "unsigned_p" in r:
                print(f"  +{h:<3} |r| {r['unsigned_mean']*100:+.3f}% vs null "
                      f"{r['unsigned_null_mean']*100:.3f}% p={r['unsigned_p']:.4f} "
                      f"(sess-matched p={r['unsigned_p_sessmatched']:.4f}) | "
                      f"S1 net {r['s1_ev_net']*100:+.3f}% p={r['s1_p']:.4f} | "
                      f"S2 net {r['s2_ev_net']*100:+.3f}% (n={r['s2_n']}) p={r['s2_p']:.4f}")
            else:
                print(f"  +{h:<3} n_scored={r['n_scored']} (too few for null)")


if __name__ == "__main__":
    main()
