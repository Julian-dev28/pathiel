# W-P2 — Scheduled-catalyst playbook: policy binaries -> post-resolution BTC/ETH drift

Lane P (public-record latency), 2026-07-19. Scripts:
`hypotheses/W-P2_enumerate.py` (systematic vote enumeration),
`hypotheses/W-P2_polymarket_fetch.py` (pre-event odds),
`hypotheses/W-P2_candles_fetch.py` (HL 1h BTC/ETH 2024-01 -> now),
`hypotheses/W-P2_backtest.py` (event study + matched null).
Caches: `hypotheses/W-P2_cache_*.json`. Results: `hypotheses/W-P2_results.json`.

Prior art this must beat, not repeat: news_catalyst (unscheduled coverage
surges) forward-REFUTED at -7.33%/sig. W-P2 is NOT sentiment on coverage — it
is an event study on SCHEDULED binary policy resolutions with exact public
timestamps (roll-call gavel times, SEC order release times), direction
conditioned on SURPRISE vs pre-event prediction-market odds.

## PRE-REGISTERED SPEC — written 2026-07-19 BEFORE any price or odds data was fetched

- **Hypothesis.** Scheduled binary policy events (crypto bill floor votes, SEC
  ETF decision orders, confirmations) resolve at knowable timestamps. If the
  outcome was NOT fully priced (contested or upset vs pre-event Polymarket
  odds), BTC/ETH drift AFTER resolution in the outcome-implied direction is
  exploitable. Priced events (>0.85) should show ~nothing (control cell).

### Event set — SYSTEMATIC enumeration (operator mandate: no marquee seeding)
- **House:** crawl ALL clerk.house.gov roll-call XMLs, 2024-01 -> 2026-07
  (`/evs/{year}/roll{NNN}.xml`). Fields kept: legis-num, vote-question,
  vote-desc, vote-result, action-date, action-time (ET). Exact resolution
  timestamp = action-time (America/New_York -> UTC).
- **Senate:** LIS vote menus `vote_menu_118_2.xml`, `_119_1`, `_119_2`
  (all votes incl. nominations); per-match detail XML for the timestamped
  `vote_date`. Covers 2026 votes deterministically (no recall bias).
- **Keyword filter (pre-committed, word-boundary, case-insensitive), tier 1:**
  digital asset(s) · digital commodit(y|ies) · cryptocurrenc(y|ies) · crypto ·
  blockchain · stablecoin · bitcoin · ethereum · virtual currency · central
  bank digital currency · CBDC · SAB 121 · token · Securities and Exchange
  Commission · Commodity Futures Trading Commission · \bSEC\b · \bCFTC\b ·
  custody · innovation and technology. Compound rule: "mining" counts only
  with (digital|crypto|bitcoin|proof) co-occurring. **Tier 2 (finreg beta):**
  financial innovation · fintech|financial technology · Bank Secrecy ·
  payment system. The famous bills (FIT21/CLARITY/GENIUS/SAB121/Anti-CBDC)
  must FALL OUT of this filter; counts reported: total rolls scanned, tier-1
  matches, tier-2 matches, triaged-in, vs the marquee-only list length.
- **Triage (latent step, pre-committed BEFORE price fetch).** Each keyword
  match is hand-classified in the table below into: DIRECT (crypto policy),
  BETA (finreg with plausible crypto beta), IRRELEVANT (false positive, e.g.
  unrelated "token"/"custody" hits, repeat quorum calls). Only DIRECT+BETA are
  scored. Per bill-chamber milestone the DECISIVE votes enter (cloture fail,
  successful cloture retry, passage, veto override); repeated procedural
  votes inside the same milestone collapse to the decisive one. Nominations:
  confirmation vote only.
- **SEC side (declared NON-systematic).** No machine-readable index of SEC
  decision-order release CLOCK times exists; SEC events enter from a fixed
  pre-committed shortlist of crypto 19b-4/S-1 decision orders 2024-2026 with
  press-verified release times (listed in the classification table); any
  event whose release time cannot be pinned to +-60min is excluded from the
  +1h horizon and enters +4h/+24h/+72h at the first bar after the
  upper-bound timestamp. The live spec's calendar watcher closes this gap
  going forward (completeness audit below).
- **Pre-committed exclusions:** the 2024-11-05 US presidential election
  (continuous multi-hour resolution, not a discrete policy gavel; and it
  would dominate the sample), state-level actions, agency staff guidance
  without a scheduled decision date (e.g. SAB 122 rescission), court rulings
  (unscheduled).

### Classification & sign convention (locked before scoring)
- Each event is classified **bullish** or **bearish for crypto** in the table
  below BEFORE any price data is fetched (the AI-playbook analog).
- **Sign convention:** bullish-classified event PASSES -> LONG from first
  completed bar after resolution; bullish event FAILS/BLOCKED -> SHORT.
  Bearish-classified inverse (bearish passes -> SHORT, bearish fails -> LONG).
  A veto of a bullish measure = the bullish measure BLOCKED -> SHORT.
- **Instruments:** BTC and ETH HL perps. Primary observation per event =
  equal-weight basket of BTC+ETH signed net returns (one obs/event, preserves
  cross-leg correlation); per-instrument breakdown reported.

### Odds & surprise buckets (locked)
- **Source:** Polymarket Gamma API (`gamma-api.polymarket.com`) market
  metadata + CLOB `prices-history` for the matching binary market.
- **p_realized = probability of the REALIZED outcome at T-24h before the
  resolution timestamp** (last trade at or before that instant). PRIMARY.
  T-1h snapshot reported as robustness only (day-of floor drama / in-progress
  votes contaminate T-1h; pre-committed reasoning, not post-hoc).
- **Buckets:** priced p_realized>0.85 · contested 0.40-0.85 · upset <0.40.
- Events with no matching Polymarket market: odds=NA, scored separately as
  the unconditioned cell (never pooled into the bucketed claim).

### Scoring (locked)
- **Entry (lookahead-safe):** HL `candleSnapshot` returns the bar CONTAINING
  t — that bar is SKIPPED. Entry = OPEN of the first 1h bar whose open time
  >= resolution timestamp. 1h bars (5m history doesn't span 2024).
- **Horizons:** +1h/+4h/+24h/+72h from entry bar open, open-to-open, exit at
  first bar opening at/after entry_open + horizon.
- **Costs:** 25 bps round trip on signed EV.
- **Null:** >=2000 iterations. Each iteration: for every event draw one
  uniform random 1h entry bar (with >=72h of subsequent history) from
  2024-01 -> 2026-07, SAME timestamp for both legs, apply the event's sign
  and horizon, same costs; statistic = mean basket EV. One-sided MC p.
  Unsigned |move| vs null reported too (do these events move price at all).
- **Primary test (locked to avoid horizon/bucket shopping):**
  contested+upset pooled bucket, +24h horizon, basket EV net 25bps.
  All other cells exploratory. OOS by time halves if overall n>=16.
- **Importance buckets (operator mandate, ex-ante score):**
  stage weight (signature/veto/final chamber-2 passage=3, chamber-1
  passage/confirmation/SEC final order=2, cloture/rule/procedural=1)
  + scope (BTC/ETH-wide policy=1, niche=0)
  + Polymarket market exists with >=$1M volume (=1).
  HIGH>=4 · MID=3 · LOW<=2. EV reported by importance bucket.
- **Verdict thresholds (locked).** ROBUST = primary cell net EV>0 in BOTH OOS
  halves AND p<0.05 vs matched null. MARGINAL = primary cell p<0.10, or
  net-positive overall with OOS fragility. REFUTED = primary cell fails both.
  INCONCLUSIVE on the conditional claim if odds-covered contested+upset n<8
  (then the unconditioned cell is reported and the forward recorder is
  specced regardless).
- **Survivorship:** BTC/ETH majors, no delisting risk; the bias here is
  EVENT-side (bills that never reached a floor vote are absent — the filter
  only sees scheduled resolutions, which is exactly the tradeable set).

## SPEC AMENDMENTS — added after enumeration, still BEFORE any instrument-price fetch

1. **Procedural-vote rule (sharpened).** A procedural vote (rule/PQ/cloture/
   MTP) enters ONLY if it FAILED (a failed procedural on a crypto bill is a
   surprise resolution) or if it is the first successful RETRY after such a
   failure. All other procedurals collapse into the decisive vote.
2. **Cluster rule.** Decisive crypto votes in the same chamber on the same
   day (e.g. CLARITY 19:30Z / GENIUS 19:53Z / Anti-CBDC 20:01Z on
   2025-07-17) collapse to ONE event timestamped at the LAST vote,
   importance = max member. Prevents triple-counting one price path.
3. **SEC timestamp pin.** Where a matching Polymarket market existed, the
   SEC-order resolution timestamp = first minute the realized side printed
   >=0.95 on the known event date (public-knowledge pin from the ODDS
   market, not the instrument), press-corroborated. Events with neither a
   pin nor a press clock time to +-60min: excluded from +1h, entered at
   upper-bound timestamp for +4h/+24h/+72h.

## ENUMERATION RESULT (counts — the no-cherry-picking audit)

- Floor votes scanned: **2,325** (House 1,128: 517/362/249 for 2024/25/26;
  Senate 1,197: 339/659/199). Bill-title enrichment: 770 bills (acronym
  leak-proofing: "GENIUS Act" carries no keyword — its long title does).
- Keyword matches: **89** (all tier-1; tier-2 zero net of tier-1).
- Marquee cross-check: every operator-named event (FIT21, SAB121 votes +
  veto + override, GENIUS chain, CLARITY, Anti-CBDC, Atkins) fell out of
  the filter. The filter ADDED events recall missed: the two CRA
  resolutions killing the IRS DeFi-broker rule (S.J.Res. 3, H.J.Res. 25 —
  the first standalone crypto law, Apr 2025), Deploying American
  Blockchains Act, and the failed 2025-07-15 Crypto-Week rule vote.
- 2026 check: zero crypto floor votes 2026-01 -> 2026-07-16 (CLARITY sits on
  the Senate calendar; committee markup 2026-05-14 is not a floor vote).
  Consistent with press. The 2026 sample is legitimately empty.
- Notable exclusions (documented, not silent): Selig CFTC confirmation
  (2025-12-18) was an EN-BLOC mass-nominee vote 53-43 — not a crypto
  binary; Laken Riley / farm-bill / SPEED-Act / Fix-Our-Forests hits =
  false positives via "custody"/"CFTC reauth"/bill-list rules -> IRRELEVANT;
  H.R. 1770 (blockchain study rider) -> IRRELEVANT; voice-vote bills absent
  by construction (no roll call, no timestamp).

## CLASSIFICATION TABLE — locked BEFORE price/odds fetch

Sign: +1 = long entry after resolution, -1 = short. All events below are
bullish-classified; sign = +1 on pass, -1 on fail/veto. Stage/scope per the
locked importance formula. PM slug filled at odds-fetch time; buckets from
p_realized(T-24h) per the locked definition.

| id  | event                                             | ts_utc (res.)      | outcome | sign | stage | scope | +1h? |
|-----|---------------------------------------------------|--------------------|---------|------|-------|-------|------|
| E01 | House passes H.J.Res.109 (SAB121 repeal)          | 2024-05-08T21:49Z  | pass    | +1   | 2     | 1     | y    |
| E02 | House passes H.R.6572 (Deploying Am. Blockchains) | 2024-05-15T22:24Z  | pass    | +1   | 2     | 0     | y    |
| E03 | Senate passes H.J.Res.109 (SAB121 -> president)   | 2024-05-16T15:31Z  | pass    | +1   | 3     | 1     | y    |
| E04 | House passes FIT21 (H.R.4763)                     | 2024-05-22T21:38Z  | pass    | +1   | 2     | 1     | y    |
| E05 | House passes CBDC Anti-Surveillance (H.R.5403)    | 2024-05-23T17:49Z  | pass    | +1   | 2     | 0     | y    |
| E06 | Biden VETOES H.J.Res.109                          | 2024-05-31T22:00Z* | blocked | -1   | 3     | 1     | n    |
| E07 | House override of veto FAILS                      | 2024-07-11T15:32Z  | fail    | -1   | 3     | 1     | y    |
| E08 | SEC approves spot BTC ETFs (34-99306)             | 2024-01-10T21:00Z^ | pass    | +1   | 3     | 1     | y    |
| E09 | SEC approves spot ETH ETF 19b-4s (34-100224)      | 2024-05-23T21:45Z^ | pass    | +1   | 3     | 1     | y    |
| E10 | SEC declares ETH ETF S-1s effective               | 2024-07-22T21:00Z* | pass    | +1   | 3     | 1     | n    |
| E11 | SEC approves IBIT options (34-101128)             | 2024-09-20T20:00Z* | pass    | +1   | 2     | 0     | n    |
| E12 | Senate passes S.J.Res.3 (IRS DeFi-broker CRA)     | 2025-03-04T22:05Z  | pass    | +1   | 2     | 0     | y    |
| E13 | House passes H.J.Res.25 (DeFi-broker CRA)         | 2025-03-11T21:51Z  | pass    | +1   | 2     | 0     | y    |
| E14 | Senate re-passes H.J.Res.25 (-> president)        | 2025-03-27T00:01Z  | pass    | +1   | 3     | 0     | y    |
| E15 | Senate CONFIRMS Atkins as SEC chair               | 2025-04-09T22:58Z  | pass    | +1   | 2     | 1     | y    |
| E16 | GENIUS cloture-on-MTP FAILS 48-49                 | 2025-05-08T17:51Z  | fail    | -1   | 1     | 1     | y    |
| E17 | GENIUS cloture RETRY passes 66-32                 | 2025-05-20T00:41Z  | pass    | +1   | 1     | 1     | y    |
| E18 | Senate PASSES GENIUS 68-30                        | 2025-06-17T21:11Z  | pass    | +1   | 2     | 1     | y    |
| E19 | Crypto-Week rule vote FAILS 196-223               | 2025-07-15T18:28Z  | fail    | -1   | 1     | 1     | y    |
| E20 | Crypto-Week rule RETRY passes (9h vote)           | 2025-07-17T03:03Z  | pass    | +1   | 1     | 1     | y    |
| E21 | House passes CLARITY+GENIUS+Anti-CBDC (cluster)   | 2025-07-17T20:01Z  | pass    | +1   | 3     | 1     | y    |
| E22 | Trump SIGNS GENIUS Act                            | 2025-07-18T19:00Z* | pass    | +1   | 3     | 1     | y    |
| E23 | SEC approves generic listing standards            | 2025-09-17T22:30Z* | pass    | +1   | 3     | 1     | y    |

`^` = to be pinned by Polymarket >=0.95 jump (amendment 3); `*` = press
upper-bound; E06/E10/E11 excluded from +1h (precision worse than +-60min).
n = 23 (16 congressional incl. 1 cluster + 5 SEC + 2 executive).

**Amendment 4 (market matching, locked before any price pull).** A Polymarket
market maps to an event only if the EVENT's resolution deterministically
resolves the MARKET's question (dated markets count when the event decides
them). Intermediate votes on a path to enactment get odds=NA even where
enactment markets existed — enactment odds are not vote odds. Search of the
Gamma catalog under this rule yields exactly four mappings:
E07 <- `house-overturns-bidens-sab-121-veto` (realized side **No**, $7.3k),
E08 <- `bitcoin-etf-approved-by-jan-15` (**Yes**, $12.6M),
E09 <- `ethereum-etf-approved-by-may-31` (**Yes**, $13.2M),
E22 <- `us-enacts-stablecoin-bill-in-2025` (**Yes**, $267k).
No FIT21 / SAB121-vote / Atkins / GENIUS-vote-level / CLARITY-House /
generic-listing markets existed. Consequence, stated ahead of scoring: the
odds-conditioned bucket claim is capped at n=4 < 8 -> the conditional cell
will be INCONCLUSIVE by the locked threshold regardless of numbers; the
unconditioned cell carries the study. An exploratory qualitative-surprise
split (fail/blocked events are surprises by construction) will be reported
and labeled exploratory.

## RESULTS (filled after the spec + table above were committed to disk)

**Amendment 5 (price source, forced by availability, pre-scoring).** HL prunes
1h candles beyond ~5000 bars — every 2024/2025 window returned empty
(fetch log in `W-P2_candles_fetch.py` header). Study series switched to
Binance spot BTCUSDT/ETHUSDT 1h, 22,328 contiguous bars 2024-01-01 -> now,
verified gap-free. Costs stay 25bps RT (perp execution assumption). HL tail
(post-2025-12) kept for overlap sanity; no event falls in it.

### Scoreboard (basket = equal-weight BTC+ETH, signed, net 25bps, MC null n=2000)

| cell                          | n  | +1h     | +4h     | +24h            | +72h    |
|-------------------------------|----|---------|---------|-----------------|---------|
| ALL events                    | 23 | -0.28%  | -0.16%  | -0.59% (p=.75)  | -1.11%  |
| unconditioned (odds NA)       | 19 | -0.25%  | -0.26%  | -0.76% (p=.81)  | -1.17%  |
| PRIMARY contested+upset       | 2  | -0.22%  | +1.02%  | +1.16% (p=.23)  | -0.81%  |
| priced (>0.85)                | 2  | -0.55%  | -0.31%  | -0.73%          | -0.88%  |
| EXPL qual-surprise (4 fails)  | 4  | -0.42%  | -1.41%  | **-4.14%** (p=.996) | **-6.59%** (p=.991) |
| EXPL passes only              | 19 | -0.26%  | +0.11%  | +0.15% (p=.30)  | +0.04%  |
| importance HIGH (>=4)         | 9  | -0.28%  | +0.14%  | +0.20% (p=.32)  | -0.76%  |

- **Unsigned test: these events do not move BTC/ETH at all.** Mean |basket
  move| vs null p_unsig = 0.21-0.69 at every horizon on ALL. Scheduled policy
  resolutions produce no abnormal majors movement at 1-72h. The only
  near-signal is the fail subset (p_unsig 0.06-0.08) — and it moves the
  WRONG way.
- **OOS halves sign-flip:** +24h H1(2024) +0.10% vs H2(2025) -1.23% -> noise.
- **Per-instrument (+24h net):** BTC -0.40%, ETH -0.79%. Slippage sweep
  ALL/+24h: -0.34% at 0bps -> already dead pre-cost.
- **Primary registered cell** (contested+upset, +24h): n=2 (E08 +2.88%,
  E09 -0.55%) -> INCONCLUSIVE by the locked n<8 rule, numbers reported for
  the record. Timestamp sensitivity: with press release times instead of PM
  pins, the pair averages +0.54% (E08 +2.07%, E09 -0.99%) — weaker. The E08
  pin (19:13Z) preceded the ~21:00Z order release (anticipatory odds
  certainty), which FLATTERS the hypothesis; even flattered, nothing passes.
- **The anti-signal, read honestly.** All 4 bullish-blocked events
  (veto -0.79%, override-fail -1.17%, GENIUS-cloture-fail -8.75%,
  Crypto-Week-rule-fail -5.86% at +24h net, shorts) resolved with the market
  rallying. Inverting (BUY the policy failure) would have made +4.1%/event
  at +24h, 4/4. But the tape explains it: the cloture fail landed inside the
  2025-05-08 ETH squeeze (ETH leg +14.85%/24h), the rule fail inside
  Crypto-Week melt-up, Atkins-confirm (-6.65%, a pass) inside the
  tariff-pause giveback. Macro dwarfs policy at these horizons; n=4,
  post-hoc inversion, in-sample regime coincidence -> NOT tradeable, logged
  as exploratory only.
- **Survivorship:** BTC/ETH majors — none. Event-side bias: bills that never
  reached the floor are absent, which is exactly the tradeable universe.

### VERDICT: **REFUTED** (unconditioned claim); conditional-odds claim **INCONCLUSIVE (BLOCKED-DATA, n=4 odds-covered < 8)**

The deciding numbers: signed EV negative at every horizon on ALL (best cell
-0.16%; already -0.34% at ZERO cost), no unsigned excess movement
(p=0.21-0.69), OOS sign-flip, and the pre-declared primary cell is
structurally underpowered because Polymarket carried vote-level markets for
only 4 of 23 events. The market prices scheduled US crypto policy into
BTC/ETH continuously, not at resolution; there is no post-resolution drift
to harvest on majors. Consistent with saturated-candle-space doctrine and
distinct from (but rhyming with) news_catalyst's -7.33%/sig refutation.

**GO/NO-GO on the playbook build: NO-GO.**

### Parked forward-recorder spec (owed by the locked INCONCLUSIVE branch — spec only, do NOT build)

The one cell this study could not power — a genuinely CONTESTED binary with
a liquid vote-level market — has a live instance approaching: CLARITY Act
Senate floor vote (H2-2026, `clarity-act-signed-into-law-in-2026` trading
~0.43, $1.9M vol). If the operator wants the forward test, the minimal
correct shape is:

1. **Calendar watcher (deterministic):** poll congress.gov floor schedules +
   Senate cloture filings + SEC open-meeting calendar daily; emit
   `upcoming_events.json` (event, expected window, matching PM slug).
   Completeness audit = this study's keyword filter re-run weekly over new
   roll calls; any resolved crypto vote NOT pre-listed by the watcher is a
   coverage bug (recall-vs-filter, the no-leak invariant).
2. **Pre-event AI playbook (latent, pre-committed):** >=24h before the
   window, freeze a JSON contract: {event_id, classification bull/bear,
   branches: [{outcome, pm_odds_at_freeze, side, size, stop, horizon}],
   kill conditions}. Stored before resolution; grading is against the frozen
   file only.
3. **Resolution watcher (deterministic):** poll clerk/LIS vote XML + PM
   >=0.95 jump; first source to confirm timestamps the resolution.
4. **Shadow recorder:** write the branch-selected paper trade to
   shadow_ledger at the first completed 1h bar, standard grading. No live
   flip without VALIDATED per the standing ledger rule.

Given REFUTED historicals, this is PARKED: activate only if the operator
wants the CLARITY-vote single-shot recorded when it schedules.
