"""W-N1b — catalyst precedence: did news coverage surge BEFORE ignition?

Cache-only analysis (no network). Inputs:
  - W-N_events.json        (40 pre-registered ignition events + matched controls)
  - W-N_cache_gdelt.json   (TimelineVolRaw 9d-ending-at-open per coin-day,
                            ArtList 48h-ending-at-open per event; W-N1_fetch.py)

PRE-REGISTERED MEASUREMENT (fixed before looking at any GDELT payload):

  Timeline buckets are converted to HOURLY article counts (bucket width is
  detected from the payload timestamps; if the width exceeds 1h the coin-day
  is scored at the coarse width and flagged, if it exceeds 24h the coin-day is
  UNMEASURABLE and reported as such — never silently dropped).

  baseline_hourly  = total count in [open-9d, open-48h) / 168h   (7d window)
  SIGNAL window    = [open-24h, open)                            (the mandate's
                     "coverage in the 24h BEFORE ignition"; the fetch window
                     ends at the day's UTC open so nothing after open leaks in)
  SURGE fires at hour bin t iff  sum(counts[t-2..t]) >= max(3, 3*3*baseline_hourly)
                     (trailing 3h >= 3x the coin's baseline, floor of 3 articles
                     so a 0-baseline coin needs >= 3 real articles, not 1)

  precedence(coin-day) = surge fires at ANY bin of the signal window.
  Precedence RATE is compared events vs controls with Fisher's exact test
  (one-sided, events > controls).
  LEAD TIME (surging events only) = ignition_bar_ms - first firing bin end.

  Coverage floor: a coin-day whose full 9d window holds < 10 articles is
  reported in the thin-coverage table (GDELT can't see the coin) — precedence
  on those is noise either way and they are EXCLUDED from the headline rate
  (reported both with and without, pre-registered).

Output: W-N1_results.json + stdout report (headlines for the clearest cases).
Run:  .venv/bin/python research/alpha_swarm/hypotheses/W-N1_precedence.py
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

REPO = Path("/Users/julian_dev/Documents/code/hermes-trader")
HYP = REPO / "research" / "alpha_swarm" / "hypotheses"
EVENTS = HYP / "W-N_events.json"
CACHE = HYP / "W-N_cache_gdelt.json"
OUT = HYP / "W-N1_precedence_results.json"

HOUR_MS = 3_600_000
DAY_MS = 24 * HOUR_MS
BASE_H = 168.0          # 7d baseline hours
MIN_ARTS = 3            # surge floor
SURGE_X = 3.0
THIN_9D = 10            # <10 articles in 9d = GDELT-blind coin-day


def gdt(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y%m%d%H%M%S")


def parse_ts(s: str) -> int:
    return int(datetime.strptime(s, "%Y%m%dT%H%M%SZ")
               .replace(tzinfo=timezone.utc).timestamp() * 1000)


def timeline_points(payload: dict) -> list[tuple[int, float]]:
    tl = (payload or {}).get("timeline") or []
    if not tl:
        return []
    return [(parse_ts(p["date"]), float(p.get("value") or 0))
            for p in tl[0].get("data", []) if p.get("date")]


def hourly_bins(pts: list[tuple[int, float]], t0: int, t1: int) -> tuple[list[float], int]:
    """Sum bucket counts into hour bins over [t0, t1). Returns (bins, bucket_ms)."""
    n = (t1 - t0) // HOUR_MS
    bins = [0.0] * n
    bucket = min((b[0] - a[0] for a, b in zip(pts, pts[1:])), default=HOUR_MS)
    for t, v in pts:
        if t0 <= t < t1:
            bins[(t - t0) // HOUR_MS] += v
    return bins, bucket


def surge_scan(bins: list[float], baseline_h: float,
               sig_start_idx: int) -> tuple[int | None, float]:
    """First bin index (>= sig_start_idx) where trailing-3h fires, + peak ratio."""
    thr = max(MIN_ARTS, SURGE_X * 3.0 * baseline_h)
    first, peak = None, 0.0
    for i in range(sig_start_idx, len(bins)):
        s3 = sum(bins[max(0, i - 2):i + 1])
        ratio = s3 / max(3.0 * baseline_h, MIN_ARTS / SURGE_X)
        peak = max(peak, ratio)
        if s3 >= thr and first is None:
            first = i
    return first, peak


def fisher_one_sided(a: int, b: int, c: int, d: int) -> float:
    """P(X >= a) for table [[a,b],[c,d]] under hypergeometric null."""
    def lc(n: int, k: int) -> float:
        return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)
    row1, col1, tot = a + b, a + c, a + b + c + d
    p = 0.0
    for x in range(a, min(row1, col1) + 1):
        p += math.exp(lc(col1, x) + lc(tot - col1, row1 - x) - lc(tot, row1))
    return min(p, 1.0)


def score_coin_day(cache: dict, sym: str, open_ms: int) -> dict:
    key = f"tvraw::{sym}::{gdt(open_ms - 9 * DAY_MS)}::{gdt(open_ms)}"
    payload = cache.get(key)
    if payload is None or payload.get("__failed__"):
        return {"status": "FETCH_FAILED"}
    pts = timeline_points(payload)
    t0 = open_ms - 9 * DAY_MS
    bins, bucket = hourly_bins(pts, t0, open_ms)
    total9d = sum(bins)
    base_ct = sum(bins[:int(BASE_H)])            # first 7d
    baseline_h = base_ct / BASE_H
    if bucket > DAY_MS:
        return {"status": "UNMEASURABLE", "bucket_ms": bucket, "total9d": total9d}
    sig_start = len(bins) - 24                    # last 24h
    first, peak = surge_scan(bins, baseline_h, sig_start)
    return {
        "status": "OK", "bucket_ms": bucket, "total9d": total9d,
        "baseline_hourly": round(baseline_h, 4),
        "thin": total9d < THIN_9D,
        "fires": first is not None,
        "first_fire_end_ms": None if first is None else t0 + (first + 1) * HOUR_MS,
        "peak_ratio": round(peak, 2),
        "sig24_count": sum(bins[sig_start:]),
    }


def headlines_for(cache: dict, sym: str, open_ms: int, limit: int = 8) -> list[dict]:
    key = f"art::{sym}::{gdt(open_ms - 2 * DAY_MS)}::{gdt(open_ms)}"
    payload = cache.get(key) or {}
    if payload.get("__failed__"):
        return []
    arts = payload.get("articles") or []
    seen, out = set(), []
    for a in arts:
        t = (a.get("title") or "").strip()
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append({"seen": a.get("seendate"), "domain": a.get("domain"),
                        "title": t})
    return out[:limit]


def main() -> None:
    events = json.loads(EVENTS.read_text())["events"]
    cache = json.loads(CACHE.read_text())
    rows = []
    for e in events:
        ev = score_coin_day(cache, e["sym"], e["open_ms"])
        ct = (score_coin_day(cache, e["sym"], e["control"]["open_ms"])
              if e["control"] else {"status": "NO_CONTROL"})
        lead_h = None
        if ev.get("fires") and ev.get("first_fire_end_ms"):
            lead_h = (e["ignition_ms"] + HOUR_MS - ev["first_fire_end_ms"]) / HOUR_MS
        rows.append({"coin": e["coin"], "sym": e["sym"], "day": e["day"],
                     "ret": e["ret"], "ignition_ms": e["ignition_ms"],
                     "open_ms": e["open_ms"], "event": ev,
                     "control_day": e["control"]["day"] if e["control"] else None,
                     "control": ct, "lead_h": lead_h})

    ok = [r for r in rows if r["event"]["status"] == "OK"
          and r["control"]["status"] == "OK"]
    meas = [r for r in ok if not (r["event"]["thin"] and r["control"]["thin"])]
    thin = [r for r in ok if r["event"]["thin"] and r["control"]["thin"]]

    def rate(rs, side):
        f = sum(1 for r in rs if r[side]["fires"])
        return f, len(rs)

    for name, pool in [("ALL-OK", ok), ("MEASURABLE (non-thin)", meas)]:
        ef, en = rate(pool, "event")
        cf, cn = rate(pool, "control")
        p = fisher_one_sided(ef, en - ef, cf, cn - cf) if en and cn else 1.0
        print(f"[{name}] precedence: events {ef}/{en} "
              f"({100*ef/max(en,1):.0f}%) vs controls {cf}/{cn} "
              f"({100*cf/max(cn,1):.0f}%)  fisher-1s p={p:.4f}")

    leads = sorted(r["lead_h"] for r in ok if r["lead_h"] is not None)
    if leads:
        print(f"lead time (surge->ignition bar): median {leads[len(leads)//2]:.0f}h "
              f"range [{leads[0]:.0f}, {leads[-1]:.0f}]h  n={len(leads)}")

    print(f"\nthin-coverage coin-days (GDELT-blind, <{THIN_9D} arts/9d on both "
          f"event+control): {len(thin)}/{len(ok)}")
    for r in sorted(ok, key=lambda x: x["event"]["total9d"]):
        e, c = r["event"], r["control"]
        print(f"  {r['coin']:9s} {r['day']}  9d_arts ev={e['total9d']:5.0f} "
              f"ctl={c['total9d']:5.0f}  base/h={e['baseline_hourly']:6.2f}  "
              f"fires ev={int(e['fires'])} ctl={int(c['fires'])} "
              f"peak_x={e['peak_ratio']:6.1f}")

    fails = [r for r in rows if r["event"]["status"] != "OK"
             or r["control"]["status"] != "OK"]
    if fails:
        print(f"\nfetch-failed/unmeasurable coin-days: "
              f"{[(r['coin'], r['day'], r['event']['status'], r['control']['status']) for r in fails]}")

    print("\n=== clearest pre-ignition cases (firing events by peak ratio) ===")
    firing = sorted((r for r in ok if r["event"]["fires"] and not r["event"]["thin"]),
                    key=lambda x: -x["event"]["peak_ratio"])
    for r in firing[:10]:
        print(f"\n{r['coin']} {r['day']} (day ret {r['ret']*100:+.1f}%, "
              f"lead {r['lead_h']:.0f}h, peak {r['event']['peak_ratio']:.1f}x):")
        hl = headlines_for(cache, r["sym"], r["open_ms"])
        if not hl:
            print("  (ArtList returned no articles — record says nothing)")
        for h in hl:
            print(f"  {h['seen']}  [{h['domain']}]  {h['title'][:110]}")

    OUT.write_text(json.dumps(rows, indent=1))
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
