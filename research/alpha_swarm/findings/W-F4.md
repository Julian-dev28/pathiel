# W-F4 oi_price_quadrants — PRE-REGISTRATION + first ripeness pass

## Pre-registration (fixed 2026-07-09, BEFORE outcomes were computed; do not tune)

**Hypothesis.** The joint sign of 24h OI change and 24h price change separates flow
regimes: Q1 (P↑ OI↑, new longs) → continuation LONG; Q2 (P↑ OI↓, short-covering rally,
fuel spent) → fade SHORT; Q3 (P↓ OI↑, new shorts) → continuation SHORT; Q4 (P↓ OI↓,
long-capitulation flush) → fade LONG.

**Exact spec** (all in `hypotheses/W-F4.py`, which enforces it):
- Data: `.state/.oi-timeseries.jsonl` (ts seconds, `oi[coin]=[oi_contracts, px]`,
  ~10-min snapshots; READ-ONLY). Universe = intersection with dataset.json's 40 coins.
- Hourly grid = last snapshot in (h−30min, h]. Signals at hour t: dOI=oi(t)/oi(t−24h)−1,
  dP=px(t)/px(t−24h)−1; valid iff both endpoints exist and ≥18/24 interior hourly points
  exist (guards the logger's 6.8-day hole).
- Thresholds: |dOI| ≥ 5% AND |dP| ≥ 3%. Entry px at t+1h; exit at t+25h (24h hold,
  no stop; 48h recorded). Dedup: per coin+quadrant, no new episode until 24h since entry
  AND one non-qualifying hour.
- Independence gate: a cell is scored ONLY when n_episodes ≥ 15 AND ≥ 8 distinct UTC days.
- Scoring: signed EV net 12/25 bps + funding term (nearest `f` snapshot within 6h ×24,
  from `.state/.data_funding_oi.jsonl`); null = `mc_null.shuffle_label_p` vs all valid
  same-side (coin,hour) 24h forward returns, 3000 iters; OOS halves by time.
- **VALIDATED requires: net25 > 0 AND both OOS halves > 0 AND p ≤ 0.01** (8 planned
  tests incl. the funding-split secondaries → multiple-comparison guard).
- Secondary (when ripe): each quadrant × sign(funding), same gates per sub-cell.

## Ripeness (data through 2026-07-09: 18.7d span, 6.8d hole → ~12 usable days)

| cell | side | n_episodes | distinct days | status |
|---|---|---|---|---|
| Q1 P↑OI↑ | long | 34 | 8 | ripe (barely) |
| Q2 P↑OI↓ | short | 13 | 4 | **NOT RIPE** |
| Q3 P↓OI↑ | short | 25 | 8 | ripe (barely) |
| Q4 P↓OI↓ | long | 38 | 9 | ripe (barely) |

## Scored cells (pre-registered spec, pool n=4529)

| cell | n | gross | net@25 | funding term | OOS25 h1/h2 | null p | verdict vs gate |
|---|---|---|---|---|---|---|---|
| Q1 long | 34 | +0.17% | −0.10% | −0.02% | +0.28/−0.48 | 0.153 | FAILS (net25<0) |
| Q3 short | 25 | −1.07% | −1.32% | −0.01% | +2.43/−4.79 | 0.956 | FAILS (points opposite: P↓OI↑ bounced) |
| Q4 long | 38 | +1.35% | **+1.10%** | −0.01% | **−1.30/+3.49** | **0.010** | FAILS (OOS sign-flip) |

## VERDICT: NOT-RIPE (no cell validates; pre-registration stands)

Deciding numbers: the only live candidate is Q4 (capitulation-flush long, net25 +1.10%,
p=0.010) but it sign-flips across halves (−1.30/+3.49) on a 9-day sample — exactly what
the pre-registered gate exists to reject. Q3's continuation-short premise is currently
CONTRADICTED (−1.07% gross). Q2 has n=13/4 days. Note Q4 overlaps the already-validated
extreme-fade-long (price-only); if Q4 ever validates, the incremental question is whether
OI↓ adds anything BEYOND the price crash — test that then, not now.

**Re-run date: 2026-07-30** (`python hypotheses/W-F4.py`, zero re-tuning). By then the
logger should hold ~2x the usable days → Q2 crosses n≥15 and h1/h2 stop being 4-5-day
slivers. The spec above is frozen; any change to thresholds after this date must be
declared as a NEW (exploratory) study, not this one.
