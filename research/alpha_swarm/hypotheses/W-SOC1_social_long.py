#!/usr/bin/env python3
"""W-SOC1 — coverage-surge LONG: ride the attention instead of fading it.

OPERATOR QUESTION (2026-07-23): we wired news/coverage surges only as SHORTS
(fade the pop) and they bled. Does the LONG side — go WITH the attention spike —
have an edge? This is the inverse of news_surge_short / mover_pass_short.

PRE-REGISTERED SPEC (written before any forward return was scored)
-----------------------------------------------------------------
Events: untainted rows (signal_bar_t >= 2026-07-12 00:00 UTC; the pre-07-12 news
ledger is TAINTED — model faked headlines before web-search enforcement, see
HANDOFF/memory) from the shadow ledgers:
  - news_catalyst.jsonl  (crypto scan candidates, side long, surge_x, 07-13..07-18)
  - news_surge_short.jsonl (equity + crypto surges, surge_x/breaking/equity, 07-20..07-22)
Signal: meta.surge_x >= 2.0 (coverage >= 2x the coin's recent baseline). One event per
coin per UTC hour (earliest kept). Sub-cells: breaking=true; equity vs crypto.

Entry (lookahead-safe): first 1h bar whose OPEN time >= the record's `ts` (when the
signal was actually written, ~30m after signal_bar_t) — never the forming bar. Forward
horizons +4h / +24h / +48h, entry_open -> exit_close. Fees 0/25/50 bps round trip.
For each event we score BOTH sides:
  LONG  EV = +r - fee        SHORT EV = -r - fee
so the head-to-head "should we have gone long not short" is explicit.

Null (pre-registered): matched SAME-COIN random-time, 2000 iters. Per event draw a
random 1h-bar open time from that coin's cached series with a valid +24h exit; run the
identical entry/horizon pipeline; per-iter statistic = mean LONG net25 over the cell.
One-sided p in the direction of the observed mean. Seed 20260723.

Verdict gate (locked): ROBUST = LONG net25 > 0 AND random-time p < 0.05 AND net25 > 0
in BOTH time-halves at the PRIMARY horizon (+24h). MARGINAL = net25 > 0 with p < 0.10
failing a clause. else REFUTED.

Caveats locked: SHORT spans (07-13..07-22 = ~10 days, one tape) — a positive is a weak,
regime-bound upper bound, survivorship-biased (today's live universe), funding not
modelled, daily-turnover fees are real. 24/7 crypto + xyz perps.
"""
from __future__ import annotations

import json
import os
import random
import statistics as st
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path("/Users/julian_dev/Documents/code/hermes-trader")
ST_DIR = REPO / ".state" / "shadow_ledger"
SCRATCH = Path(os.environ.get("SOC1_SCRATCH",
    "/private/tmp/claude-501/-Users-julian-dev-Documents-code-hermes-trader/"
    "63d7d57b-6290-4704-91fe-5931e8fda8f7/scratchpad"))
CACHE = SCRATCH / "W-SOC1_1h.json"
RESULTS = Path(__file__).with_name("W-SOC1_results.json")

TAINT_CUT_MS = 1783900800000  # ~2026-07-11 12:00 UTC; strictly before is tainted
SURGE_MIN = 2.0
SEED = 20260723
HORIZONS_H = [4, 24, 48]
PRIMARY_H = 24
FEE = {"ev0": 0.0, "ev25": 0.0025, "ev50": 0.0050}
T, O, C = 0, 1, 4  # bar indices used


def load_events():
    seen, evs = set(), []
    for book in ("news_catalyst", "news_surge_short"):
        p = ST_DIR / f"{book}.jsonl"
        if not p.exists():
            continue
        for line in p.read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            m = r.get("meta") or {}
            if r.get("signal_bar_t", 0) < TAINT_CUT_MS:
                continue
            if float(m.get("surge_x", 0) or 0) < SURGE_MIN:
                continue
            coin = r.get("coin")
            ts = int(r.get("ts") or r.get("signal_bar_t"))
            hour = ts // 3_600_000
            key = (coin, hour)
            if not coin or key in seen:
                continue
            seen.add(key)
            evs.append({"coin": coin, "ts": ts, "book": book,
                        "breaking": bool(m.get("breaking")),
                        "equity": bool(m.get("equity")), "surge_x": float(m.get("surge_x"))})
    evs.sort(key=lambda e: e["ts"])
    return evs


def build_cache(coins, force=False):
    SCRATCH.mkdir(parents=True, exist_ok=True)
    payload = json.loads(CACHE.read_text()) if (CACHE.exists() and not force) else {"candles": {}}
    have = payload["candles"]
    missing = [c for c in coins if c not in have or not have.get(c)]
    if missing:
        sys.path.insert(0, str(REPO))
        from hermes_trader.client.hl_client import fetch_hl_candles  # network, read-only
        for attempt in range(3):
            still = []
            for coin in missing:
                try:
                    cs = fetch_hl_candles(coin, "1h", 5000)
                except Exception as ex:  # noqa: BLE001
                    cs = None
                    print(f"  fetch {coin} FAILED {ex}", file=sys.stderr)
                if cs:
                    have[coin] = [[b.t, float(b.o), float(b.h), float(b.l),
                                   float(b.c), float(b.v)] for b in cs[:-1]]
                else:
                    still.append(coin)
                time.sleep(0.35)  # gentle: live loop shares the HL rate budget
            missing = still
            if not missing:
                break
            print(f"  retry pass {attempt+1}: {len(missing)} coins left", file=sys.stderr)
        CACHE.write_text(json.dumps(payload))
    return have


def entry_idx(bars, ts_ms):
    for i, b in enumerate(bars):
        if b[T] >= ts_ms:
            return i
    return None


def fwd_return(bars, e, h):
    # exit index = e + h - 1 (h bars held, open->close); it must be a real, complete
    # bar. build_cache() already dropped the in-progress final bar, so the last cached
    # bar is a valid exit.
    if e is None or e + h - 1 >= len(bars) or bars[e][O] <= 0:
        return None
    return bars[e + h - 1][C] / bars[e][O] - 1.0


def cell_stats(rets, side):
    """rets: list of raw forward returns. side +1 long / -1 short."""
    out = {}
    for k, fee in FEE.items():
        ser = [side * r - fee for r in rets]
        out[k] = round(st.fmean(ser) * 100, 4) if ser else None
    return out


def matched_null(cand_events, cand, h, fee):
    """Per event draw random valid entry from the coin's series; mean LONG net25."""
    rnd = random.Random(SEED)
    per_event_pool = {}
    for coin in {e["coin"] for e in cand_events}:
        bars = cand.get(coin) or []
        pool = [i for i in range(len(bars)) if i + h - 1 < len(bars) and bars[i][O] > 0]
        per_event_pool[coin] = (bars, pool)
    draws = []
    for _ in range(2000):
        vals = []
        for e in cand_events:
            bars, pool = per_event_pool[e["coin"]]
            if not pool:
                continue
            i = rnd.choice(pool)
            r = fwd_return(bars, i, h)
            if r is not None:
                vals.append(r - fee)  # long side
        if vals:
            draws.append(st.fmean(vals))
    return draws


def run():
    evs = load_events()
    coins = sorted({e["coin"] for e in evs})
    print(f"events: {len(evs)} surge>=2x untainted, {len(coins)} unique coins")
    cand = build_cache(coins)
    got = [c for c in coins if cand.get(c)]
    print(f"candles: {len(got)}/{len(coins)} coins fetched")

    def score(subset, label):
        rets24 = []
        scored = []
        for e in subset:
            bars = cand.get(e["coin"])
            if not bars:
                continue
            ei = entry_idx(bars, e["ts"])
            row = {"e": e, "ei": ei}
            for h in HORIZONS_H:
                row[h] = fwd_return(bars, ei, h)
            if row[PRIMARY_H] is not None:
                scored.append(row)
        res = {"label": label, "n": len(scored)}
        for h in HORIZONS_H:
            rets = [row[h] for row in scored if row[h] is not None]
            if not rets:
                continue
            res[f"h{h}"] = {"n": len(rets),
                            "long": cell_stats(rets, +1),
                            "short": cell_stats(rets, -1),
                            "mean_raw_ret_pct": round(st.fmean(rets) * 100, 4)}
        # OOS halves at primary horizon (LONG net25)
        prim = [row for row in scored if row[PRIMARY_H] is not None]
        prim.sort(key=lambda r: r["e"]["ts"])
        half = len(prim) // 2
        def ln25(rows):
            xs = [row[PRIMARY_H] - FEE["ev25"] for row in rows]
            return round(st.fmean(xs) * 100, 4) if xs else None
        res["long_h24_half1"] = ln25(prim[:half])
        res["long_h24_half2"] = ln25(prim[half:])
        # matched null on LONG net25 at primary horizon
        obs = st.fmean([row[PRIMARY_H] - FEE["ev25"] for row in prim]) if prim else 0.0
        draws = matched_null([row["e"] for row in prim], cand, PRIMARY_H, FEE["ev25"])
        if draws:
            _hit = (lambda x: x >= obs) if obs >= 0 else (lambda x: x <= obs)
            ge = sum(1 for x in draws if _hit(x))
            res["long_h24_null_p"] = round((1 + ge) / (1 + len(draws)), 4)
            res["long_h24_null_mean_pct"] = round(st.fmean(draws) * 100, 4)
        # verdict on primary LONG
        h24 = res.get(f"h{PRIMARY_H}", {})
        ln = (h24.get("long") or {}).get("ev25")
        p = res.get("long_h24_null_p", 1.0)
        both = (res["long_h24_half1"] or -1) > 0 and (res["long_h24_half2"] or -1) > 0
        if ln is not None and ln > 0 and p < 0.05 and both:
            res["verdict"] = "ROBUST"
        elif ln is not None and ln > 0 and p < 0.10:
            res["verdict"] = "MARGINAL"
        else:
            res["verdict"] = "REFUTED"
        return res

    cells = {
        "ALL": score(evs, "ALL surge>=2x"),
        "BREAKING": score([e for e in evs if e["breaking"]], "breaking only"),
        "EQUITY": score([e for e in evs if e["equity"]], "xyz equities only"),
        "CRYPTO": score([e for e in evs if not e["equity"]], "crypto only"),
    }
    return {"events": len(evs), "coins_fetched": len(got), "cells": cells}


def show(r):
    for name, c in r["cells"].items():
        print(f"\n=== {name}: {c['label']}  (n={c['n']}) ===")
        for h in HORIZONS_H:
            hc = c.get(f"h{h}")
            if not hc:
                continue
            print(f"  +{h}h (n={hc['n']}): raw {hc['mean_raw_ret_pct']:+.3f}%  "
                  f"LONG net25={hc['long']['ev25']:+.3f}%  SHORT net25={hc['short']['ev25']:+.3f}%")
        print(f"  LONG +24h halves: {c.get('long_h24_half1')} / {c.get('long_h24_half2')}  "
              f"null p={c.get('long_h24_null_p')} (null mean {c.get('long_h24_null_mean_pct')})")
        print(f"  VERDICT: {c['verdict']}")


if __name__ == "__main__":
    res = run()
    show(res)
    RESULTS.write_text(json.dumps(res, indent=2))
    print(f"\nwrote {RESULTS.name}")
