# W-N — News-catalyst replay: GDELT historical coverage vs ignition events

Lane N, 2026-07-11. Operator mandate: "replay history and make calls with the
RSS feed and news sources considered."

## Hypothesis

Big coin-day movers (>= +12%) are catalyst-ignited (prior finding: 14/15 had no
price precursor), so news COVERAGE should surge before ignition, and a long
entered on the pre-ignition surge should catch the move.

## Pre-registered design (fixed before any GDELT result was viewed)

- Events: 40 coin-days, day_ret >= +12%, dollar_vol >= $5M, per-coin 3-day
  dedup, spread-enforced across 7 blocks of the 208d hourly cache
  (2025-12-13 .. 2026-07-09, 40 coins). Rule + code:
  `research/alpha_swarm/hypotheses/W-N0_events.py` (selection output
  `W-N_events.json`: 162 candidates -> 139 deduped -> 40 selected, every event
  got a matched control).
- Controls: one per event, same coin, random day with |ret| < 4%,
  dvol >= $1M, >= 2d from any event-candidate day (seed 42).
- GDELT DOC API, historical `startdatetime/enddatetime`:
  TimelineVolRaw 9d-ending-at-day-open per coin-day (7d baseline + 48h signal
  window), ArtList 48h-ending-at-open per event.
  `research/alpha_swarm/hypotheses/W-N1_fetch.py`, every payload cached to
  `W-N_cache_gdelt.json`.
- QUERY AMENDMENT (forced, uniform, pre-results): the registered
  `"<SYM>" crypto sourcelang:eng` is structurally rejected by GDELT for
  phrases < 5 chars ("The specified phrase is too short") — symbols < 5 chars
  are queried unquoted. Ambiguous short symbols (IP, MON, LIT) lose
  sensitivity, not validity (each coin-day is scored against its own baseline
  under the same query).
- Surge (pre-registered): trailing-3h article count >= max(3, 3x the coin's
  7d hourly baseline x 3h), scanned over the 24h before the day's UTC open.
  `research/alpha_swarm/hypotheses/W-N1_precedence.py`.
- Replay: long at the open of the first hourly bar after the surge bin; exits
  cell A = trail arm +2% / retrace 0.10 + 15% stop / 24h, cell B = 15% stop /
  24h; pessimistic low-before-high; costs 0/6/12/25/50 bps; matched same-coin
  random-entry null, bootstrap p.
  `research/alpha_swarm/hypotheses/W-N2_replay.py` (exit engine gate-tested,
  5 synthetic cases).

## What actually happened with GDELT (the honest part)

GDELT throughput collapsed the study to a partial sample. Observed 2026-07-11:
HTTP 429 storms that only clear after ~60s quiet, TLS handshake timeouts,
~4 min per successful query even at 10s pacing. 120 queries were queued; at
the coordinator-imposed 3h analysis cap the cache held **10 balanced
event/control timeline pairs** (22 ok timelines, 5 hard failures — IP x2,
JTO, LIT, MON — plus 2 artlists). The fetcher keeps running detached and
appends to `W-N_cache_gdelt.json`; rerunning `W-N1_precedence.py` +
`W-N2_replay.py` on the full cache is free and scripted.

**Sampling bias caveat (major): events fetch in coin-alphabetical order**, so
the 10 analyzed pairs are FARTCOIN/FET/HYPE/JTO/JUP/LIT-heavy — the
GDELT-thinnest names. The unfetched remainder (NEAR, ONDO, SUI, TAO, TRUMP,
UNI, WLD, XRP, ZEC...) is where GDELT coverage plausibly exists. Numbers
below are NOT the final word on those coins.

## W-N1 results (n=10 pairs, partial, biased thin)

| pool | events fire | controls fire | fisher 1-sided p |
|---|---|---|---|
| all OK pairs | 1/10 (10%) | 1/10 (10%) | 0.76 |
| non-thin (>=10 arts/9d) | 1/2 | 1/2 | 0.83 |

- **8/10 pairs are GDELT-BLIND**: FARTCOIN 0 articles per NINE DAYS (all 3
  events), JTO 0-1, JUP 0, FET 2-4, LIT 6-10. For meme/small-cap perps the
  pre-declared failure mode is real: GDELT cannot see them, precedence is
  unmeasurable per-coin. This alone kills GDELT as a live signal source for
  most of the mover universe.
- The single coin with real coverage (HYPE, 70-87 arts/9d): the surge rule
  fired 28h before ignition on the event day — **and also fired on its
  control day** (peak 6.0x). Zero discrimination so far at this density.
- Median lead time: 28h (n=1 — not a statistic, a single observation).
- Headlines for the firing case: ArtList not yet in cache (429 storm; a
  targeted retry also 429'd). The record says nothing — no headline claims
  are made. They will land in `W-N_cache_gdelt.json` as the detached fetcher
  drains; `W-N1_precedence.py` prints them on rerun.

## W-N2 replay grid (n=1 — anecdote, not evidence)

| cell | n | EV0 | EV25 | win25 | null mean | p(null>=obs) | MAE p50/p90 |
|---|---|---|---|---|---|---|---|
| A trail 2%/0.10 + 15% stop / 24h | 1 | +1.98% | +1.73% | 1.00 | +0.83% | 0.54 | -1.1% / -1.1% |
| B 15% stop / 24h | 1 | +14.47% | +14.22% | 1.00 | +0.98% | 0.017 | -1.1% / -1.1% |

The one news-considered call (HYPE 2026-01-27, entry 28h pre-ignition) caught
the +23.6% mover day: +14.2% net at 25bps on the plain 24h hold; the tight
trail banked +1.7% and left the move (consistent with the exit-asymmetry
edge profile). OOS halves impossible at n=1. **Fails every wire gate:
n>=15 NO, p<0.05 only in cell B where n=1 makes p meaningless.**

## VERDICT

**INCONCLUSIVE at n=10 pairs / n=1 replay — and structurally REFUTED as a
GDELT-based live signal for small caps** (0-6 articles/9d on 8 of 10 pairs;
GDELT additionally rejects <5-char symbol phrases, is 429-bound at ~15
queries/h sustained, and per news_catalyst.py:269 must never sit on a live
path). Survivorship: universe is today's liquid set; any positive number is
an upper bound. The full-120 rerun stays live via the detached fetcher; the
decision numbers can be regenerated with:
`.venv/bin/python research/alpha_swarm/hypotheses/W-N1_precedence.py && .venv/bin/python research/alpha_swarm/hypotheses/W-N2_replay.py`

## W-N3 — the spec: zero-capital SHADOW RECORDER (wire refused)

W-N2 fails the wire gates, so per mandate the deliverable is the recorder.
GDELT cannot provide the history; live Google News RSS accrues it forward.

- **New file** `pathia/agents/news_catalyst_live.py`, self-contained,
  copy the young_listings lane pattern (`young_listings_live.py:236
  maybe_run(config, universe, positions, ...)`).
- **Hook**: `scripts/trading_loop.py` next to the existing lane import at
  line 65 (`from pathia.agents.young_listings_live import maybe_run`),
  same call site cadence, throttled to one pass per 30 min via a
  `state_file(".news_catalyst_live_ts")` timestamp (pattern:
  `young_listings_live.py:38`).
- **Read**: for each scan candidate perception returns (vol_pick +
  movers_pick, `perception.py:391-395`), call
  `coin_catalyst(coin)` (`news_catalyst.py:304`) — Google News RSS, 2 cached
  keyless queries/coin, ~1s, zero GDELT, zero HL rate-budget impact.
- **Record**: `shadow_ledger.record_many("news_catalyst", rows)`
  (`shadow_ledger.py:101`), one row per read:
  `side="long"`, `horizon_days=1.0`, `stop_pct=15.0`, `entry_ref_px=mid`,
  `meta={n_recent, surge_x, breaking, top3_titles}`. Both breaking and
  non-breaking reads are recorded — non-breaking reads ARE the matched null.
- **Grade**: `scripts/shadow_status.py` (existing PIT grader) — forward EV of
  breaking vs non-breaking reads.
- **Go-live gate** (same convention as young_listings_live.py:253-257):
  >= 60 forward days AND breaking-read EV25 > 0 AND EV25(breaking) >
  EV25(non-breaking) AND n(breaking) >= 15 — only then discuss feeding
  candidate surfacing; until then research-prompt-only (research.py already
  consumes google_news_search at research.py:107).
- **Config**: `news_catalyst: {enabled: true, shadow_only: true,
  scan_interval_min: 30, surge_x_min: 3.0}`; ships with gate tests for the
  recorder row shape and an eval on the surge threshold, same commit.

## Files

- `research/alpha_swarm/hypotheses/W-N0_events.py` — selection (pre-registered)
- `research/alpha_swarm/hypotheses/W-N_events.json` — 40 events + controls
- `research/alpha_swarm/hypotheses/W-N1_fetch.py` — GDELT fetcher (resumable)
- `research/alpha_swarm/hypotheses/W-N_cache_gdelt.json` — growing cache
- `research/alpha_swarm/hypotheses/W-N1_precedence.py` / `W-N1_precedence_results.json`
- `research/alpha_swarm/hypotheses/W-N2_replay.py` / `W-N2_replay_results.json`
