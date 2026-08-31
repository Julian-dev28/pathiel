# W-Y5 — young-listing SHORT: expansions, overlays, and an out-of-window replication failure

Lane Y follow-up, 2026-07-22. Scripts (all in `hypotheses/`): `W-Y5_fetch.py`
(465-perp daily cache + 105-coin blocked-cohort 1h cache), `W-Y5_fetch_funding.py`
(113-coin funding cache), `W-Y5_lib.py` (shared engine, W-Y1 discipline),
`W-Y5_log_cohorts.py` (PIT log-cohort forward grades → `W-Y5_log_cohorts.json`),
`W-Y5_young_expansion.py` (backtest + overlays → `W-Y5_expansion_results.json`),
`W-Y5_news_join.py` (live-ledger news join). Data: `W-Y5_cache_daily.json`
(465 perps, 400 daily bars, fresh 2026-07-22), `W-Y5_cache_blocked_1h.json`,
`W-Y5_cache_funding.json`, `W-Y5_cache_universe.json`.

Discipline: signal at day-i close → fill at day-(i+1) open (backtests) or
block-hour +1 bar (log cohorts); 25 bps/side headline (40 stress); one open
episode per coin; incomplete holds dropped; matched same-coin random-entry
nulls (2000 iters) + a DAY-MATCHED same-class mature baseline for the base
cells; overlay lift graded by label-permutation p; OOS = calendar halves AND
listing-date cohorts. In-sample listing = first bar > window edge + 3d
(gap-riddled old coins like TON/MKR excluded — 26 false "listings" caught).

## 0. The thing that matters more than any expansion: the base edge does not replicate

The live book was armed on n=126 log coin-days (−2.71%/next-day long vs −0.13%
matched mature, through ~07-19). Three independent re-reads say that window was
regime luck, not structure:

| read | window | n | SHORT ev (net 50bps RT) | excess vs null | mc_p |
|---|---|---:|---:|---:|---:|
| full log cohort, block-time entry, +24h | 06-26 → 07-22 | 120 (27c) | **−0.11%** | −0.40pp | 0.735 |
| … through 07-19 only (the armed window) | 06-26 → 07-19 | 108 | +1.01% | — | — |
| … the days the book has been LIVE | 07-20 → 07-22 | 12 | **−10.18% raw / −5.26% at the real 6% stop, win 8%** | — | — |
| full-history backtest, young xyz (age 2-59, dvol≥$250k) | ~13 months | 3,413 (87c) | −0.44%/ep | +0.00pp vs day-matched mature | 0.507 |

Sub-splits of the log cohort: Jun28–Jul01 −1.24%, Jul02–15 +2.33%, Jul16–19
+2.49%, Jul20–22 −10.18%. The edge existed for ~2.5 weeks of down-tape and
inverted violently when the xyz tape ripped (KIOXIA +17%/d, ZHIPU +15%/d into
our shorts; ledger read: 11 of 12 resolved live xyz episodes are losers, mean
−10.3%/24h raw, capped ≈ −6.5% each by the 6% clamp). This also kills the
retrospective "+6.03% when equity index is UP" regime split in
`mover_recorders.py` — the live 07-20..22 window WAS macro_regime=up and was
the worst stretch in the whole sample. Consistent with W-Y1 (no unique young
edge) and the reverse-refuted audit's own young_listings-inverse verdict
("TAPE BETA", excess +0.82pp, p 0.323) — the n=126 fortnight was the outlier,
and the audit table was right.

`scripts/shadow_status.py --book young_mover_short` still says PENDING 0/28
resolved only because `resolve_after_ms` = (horizon+2) daily bars — the 07-20
rows resolve 07-23 and the mandatory-review-at-8 bar will trip then, on the
numbers above. **Recommended same-day action (operator/parent — this session
is read-only on live config): `mover_recorders.young_short_live.shadow_only:
true`.** The recorder keeps building evidence at zero capital either way.

## (a) Crypto young listings — verdict + thresholds

26 genuine in-sample crypto listings (of 204 cached), 1,259 young coin-days.
The short LEAN is real but tiny and coin-concentrated:

| cell | n | ev25 | OOS T-halves | OOS listing-cohorts | xs vs day-matched mature | mc_p |
|---|---:|---:|---|---|---:|---:|
| h=1 s=6% | 1,259 (26c) | +0.02% | +0.09/−0.04 | +0.30/−0.32 | +0.07 | 0.252 |
| **h=1 s=15%** | 1,259 | **+0.17%** | +0.30/+0.05 | +0.21/+0.13 | **+0.24** | **0.0265** |
| h=2 s=15% | 641 | +0.51% | +0.53/+0.49 | +0.34/+0.72 | +0.15 | 0.265 |
| h=3 s=6% | 444 | +0.85% | +1.43/+0.26 | +1.58/−0.02 | +0.32 | 0.137 |

h=1 s=15% is the only cell passing every formal gate (EV>0 net, both OOS
splits +, mc_p<0.05) — but +0.17%/episode is 3 cents on a $20 leg, ex-top-coin
it halves to +0.09%, and only 16/26 coins are net positive. That is a
statistically detectable listing-beta, not a wireable edge. **VERDICT:
REFUTE for capital; keep the already-wired zero-capital crypto recording.**

Thresholds crypto WOULD need (all directional, none validated):
- **Stop 15-20%, never 6%**: the log's crypto history-blocked cohort earned
  +9.02% raw/24h (n=12, but 2 serially-correlated coins — CASHCAT×10, GRAM×2)
  yet **−2.47% at the live 6% geometry** — young crypto whips through +6%
  before dumping. The 6% clamp that works on equities inverts crypto episodes.
- Age: only band 20-39d carries positive sign (+0.32%); 2-19d is negative
  (bounce chop); the fade is gone by 60-89d (−0.27%).
- dvol floor: $3M > $1M > $250k (+0.36% vs +0.13% vs +0.02% at h1 s6).
- Hold 2-3d beats 1d.
Promotion bar (pre-registered here): forward `young_mover_short` crypto rows —
`shadow_status --book young_mover_short` filtered `meta equity=false` — n≥30
resolved from ≥10 DISTINCT listings (the CASHCAT problem: 11 of the current 14
crypto coin-days are one listing), EV25>0 both halves, then a fresh null.

## (b) Other floor-blocked cohorts as short signals — REFUTED

Same PIT log method as the live book's own armed study (block-time entry,
1h bars, net 50bps), 06-26 → 07-22:

| cohort | n (coins) | SHORT ev24 | ev72 | live-6% geom | excess vs same-coin random-time | mc_p |
|---|---:|---:|---:|---:|---:|---:|
| liquidity-floor blocked, crypto | 117 (67c) | −0.14% | +1.79% | −0.34% | +0.24pp | 0.330 |
| liquidity-floor blocked, xyz | 36 (16c) | +0.23% | +0.08% | −0.44% | +0.19pp | 0.445 |
| history-floor blocked, crypto | 12 (2c) | +9.02% | +18.33% | −2.47% | +2.67pp | 0.321 |

Nothing clears any bar. The thin-liquidity cohort (< $0.7M/24h) simply doesn't
fade — the liquidity floor is blocking UNTRADEABLE, not OVERPRICED, names.
There is no second mover_pass_short hiding behind the other gates. (The
`insufficient_free_margin` cohort is about OUR account, not the coin —
direction-uninformative, skipped.)

## (c) Overlay EV ranking (on BASE young-short h=1 s=6%, net 50bps RT)

xyz equities (base −0.44%/ep, n=3,413) / crypto (base +0.02%/ep, n=1,259):

| overlay | xyz ev25 (n) | xyz lift-p | crypto ev25 (n) | crypto lift-p | verdict |
|---|---:|---:|---:|---:|---|
| **O1 pumped ret0≥+5%** | −0.22% (304) | 0.147 | **+0.60% (249)** | **0.083** | best overlay, crypto-only lean; OOS T-halves +0.66/+0.54 but listing-cohorts +1.23/−0.13 FAIL, mc_p 0.111, ex-STBL +0.29% |
| O1 pumped ret0≥+8% | +0.25% (145) | **0.0135** | +0.43% (172) | 0.220 | xyz: only overlay to flip base positive, but OOS −0.91/+1.39 FAIL |
| O1b not-crashed ret0>−8% | −0.41% (3,271) | **0.0070** | +0.22% (1,037) | **0.020** | tiny but the ONLY overlay with consistent lift in all 3 classes (hip3_other 0.0005) — a GUARD, not an edge: never young-short a coin that crashed ≥8% that day (W-Y1's fade-long bounce eats you) |
| O4b high rvol ≥2× | −0.34% (587) | 0.242 | +0.39% (105) | 0.292 | weak, same attention story as O1 |
| O2 funding day-i > 0 | −0.45% (2,506) | 0.614 | +0.09% (811) | 0.324 | **REFUTED** — crowded-long funding does NOT mark squeezable young listings |
| O4 low rvol < 0.7 | −0.53% (981) | 0.802 | +0.03% (552) | 0.488 | **REFUTED** — low relative volume marks nothing |
| O3 negative news polarity | — | — | — | — | **UNTESTABLE historically**: GDELT blind on these names (`W-N_cache_gdelt.json` empty values), news_surge_short ledger starts 07-20; live join = 23 coin-days, ZERO negative-polarity days, n=1 resolved positive-polarity (−6.51%). Forward path exists for free: keep joining the two ledgers by coin-day (`W-Y5_news_join.py`). |

Best CREATIVE combo found (crypto pumped≥5%, h=3, s=15%): +1.29%/ep, n=180,
OOS +1.61/+0.97 and cohorts +1.20/+1.38 all positive, ex-top-coin +1.07% —
but the same-coin random-young-day null earns almost as much (excess −0.04pp,
mc_p 0.505). The pumped combos are harvesting young-listing beta with better
optics, not adding timing alpha. Funding carry adds ~+0.05pp on covered crypto
episodes — rounding error.

## (d) What is actually worth wiring

Nothing at real capital. Two zero-cost wirings with exact hooks:

1. **Meta enrichment of the existing recorder** so every overlay grades itself
   forward: in `pathia/agents/mover_recorders.py::record_young_mover_short`
   add to `meta`: `day_move_pct` (the call site at `scripts/trading_loop.py`
   ~1020-1040 already holds `perception["daily_move_pct"]` — pass it through),
   `funding_24h` (skip if a fetch is needed; only add free fields), `dvol_usd`
   (`perception["daily_volume_usd"]`). Then the O1/O1b/O4 overlays become
   `shadow_status --book young_mover_short --meta` splits at zero capital.
   PROMOTE (trivial, zero risk, read-only for this session so NOT done here).
2. **Not-crashed guard** if the book ever trades again: skip the short when
   `day_move_pct <= -8` (lift-p 0.007/0.020/0.0005 across all three classes;
   the crashed subset is the W-Y1 fade-long bounce population). Config hook:
   `mover_recorders.young_short_live.min_day_move_pct: -8.0` + 3-line check in
   `record_young_mover_short`. PROMOTE as a guard, conditional on the book
   surviving its 07-23 review.

Explicit REFUTES (do not wire): crypto young-short live leg (any geometry
today), hip3_other young-short (base −0.45%, pumped≥8% −1.26% — other HIP-3
dexes mean-revert UP after young pumps), liquidity-floor-blocked short
recorder, funding overlay, low-rvol overlay.

## (e) Caveats / too-thin-to-trust

- **Survivorship**: universe = live listings today; delisted young coins are
  invisible. Their absence most likely REMOVES short winners (collapsed
  listings), so crypto short EVs are biased down — but ADL/borrow reality on
  dying markets is unmodeled in the other direction. 75 markets returned zero
  candles (index-style; excluded).
- hist_crypto log cohort (n=12, 2 coins) and every live-window read
  (07-20..22, 12 eps, ~2-3 independent days) are n<30 and serially correlated
  — directional color only.
- The n=126 vs my n=120/−0.11% discrepancy: same population, different
  measurement windows (this study adds 07-20..22 forward days and uses
  block-hour +24h throughout). Both are PIT; the delta IS the regime turn.
- ~50+ cells swept in this study; nothing here survives family-wise
  correction. Everything labeled "lean" is exploration for forward recorders,
  not validation.
- xyz funding-net on the whole base: −0.39% vs −0.44% price-only (mild carry
  tailwind, 73% of young days have funding>0); does not change any verdict.

## VERDICT

**REFUTE all young-short expansions at capital.** The base live book fails
out-of-window replication (excess −0.40pp, mc_p 0.735 vs same-coin random
timing; day-matched mature excess +0.00, p 0.51) and is 11/12 losers in its
live forward window — flip `young_short_live.shadow_only=true` at the 07-23
review if not before. Crypto young = detectable but sub-viable listing beta
(best formal cell +0.17%/ep, mc_p 0.0265): keep recording, promotion bar n≥30
resolved / ≥10 distinct listings / EV25>0 both halves. Other floor cohorts:
no signal. Best overlay: pumped-day conditioning (crypto +0.60%/ep, lift-p
0.083) — not null-significant; not-crashed is a free guard worth adding the
day the book trades again.
