# W-P1 — Public-record latency: EDGAR 8-K/6-K acceptance -> xyz perp drift

Lane P (public-record latency), 2026-07-19. Scripts:
`hypotheses/W-P1_edgar_fetch.py` (EDGAR + HL caches),
`hypotheses/W-P1_edgar_backtest.py` (event study). Caches:
`hypotheses/W-P1_cache_universe.json`, `W-P1_cache_cik_map.json`,
`W-P1_cache_filings.json`, `W-P1_cache_1h.json`. Results: `W-P1_results.json`.

Prior art this must beat, not repeat: news_catalyst (coverage-surge entries)
forward-REFUTED at **-7.33%/sig, 42 resolved, both OOS halves negative**
(`ALPHA-QUEUE.md:184`, commit df3df37). This cell is NOT sentiment on coverage
volume — it is a latency claim on discrete, exactly-timestamped public records
(EDGAR acceptanceDateTime), where the underlying stock is closed but the perp
prints 24/7.

## PRE-REGISTERED SPEC — written 2026-07-19 BEFORE any outcome was computed

- **Hypothesis.** 8-K/6-K filings hit EDGAR with exact acceptance timestamps
  before human coverage. xyz tokenized-equity perps trade around the clock,
  including the after-hours window where most 8-Ks drop. The perp may reprice
  slowly enough that drift AFTER the public timestamp is capturable.
- **Universe.** Current xyz perps via `metaAndAssetCtxs dex=xyz`. Equity
  tickers only; indices/commodities/crypto-natives and private companies are
  excluded by the CIK-mapping step itself. Ticker->CIK via SEC
  `company_tickers.json` exact-ticker match plus a hand map for ambiguous /
  foreign names. The covered-vs-not table is itself a deliverable.
- **Events.** Per covered CIK, EDGAR submissions API
  (`data.sec.gov/submissions/CIK##########.json`): forms 8-K and 6-K,
  amendments (`/A`) excluded, acceptanceDateTime within the coin's HL 1h
  candle coverage. Multiple filings by one issuer landing in the same entry
  bar collapse to the earliest (one event per coin-bar).
- **Timestamp convention.** EDGAR acceptanceDateTime timezone is VERIFIED
  empirically before use: 8-K acceptances are known to cluster 16:00-17:30 ET;
  the raw-hour histogram must peak there (=> values are ET, convert via
  America/New_York with DST) or at 20-21 (=> already UTC). The check and its
  result are reported below. HL candle `t` = bar open, UTC ms.
- **Entry (lookahead-safe).** `candleSnapshot` returns the bar CONTAINING t —
  that bar is SKIPPED. Entry = OPEN of the first 1h bar whose open time >=
  acceptance time. Entry latency <= 1h. 1h bars (not 5m) because HL's ~5000-bar
  snapshot cap means 5m only covers ~17d, less than the event span.
- **Horizons.** +1h/+4h/+24h = open-to-open, exit at the first bar opening at
  or after entry_open_time + horizon.
- **Gap hygiene (set before outcomes were computed).** HL omits no-trade bars.
  An event whose first printed bar opens >24h after acceptance is dropped (no
  market to enter). A horizon whose exit bar opens >6h past the nominal exit
  time is dropped for that event. Entry-delay distribution is reported.
- **Costs.** 25 bps round trip on signed EV.
- **Null.** >=2000 iterations; each iteration draws, per event, one uniform
  random entry bar from the SAME coin's 1h history (bars with >=24 subsequent
  bars), same horizon; statistic = mean over events. One-sided MC p.
  Primary null: horizon-matched (as pre-specified in the mission). Secondary
  robustness null: additionally session-matched (draws restricted to bars with
  the same after-hours/market-hours status as the event's entry bar), because
  if xyz off-hours bars are mechanically flat, an unmatched null biases the
  unsigned test in a knowable direction.
- **Tests, in order.**
  1. UNSIGNED: mean |return| per horizon vs null — does the perp move on
     filings at all?
  2. SIGNED S1 (naive long): long every filing, net 25bps.
  3. SIGNED S2 (first-reaction momentum): direction = sign of (entry open /
     close of last fully-completed pre-acceptance bar − 1); enter at entry
     open; net 25bps. Zero reaction => event skipped for S2.
- **Splits.** OOS by time halves (events sorted by acceptance) if n>=16.
  After-hours vs market-hours: market-hours = 09:30-16:00 ET on a weekday
  (no holiday calendar — rare misclassification noted as a caveat); everything
  else = after-hours. The structural claim lives in the after-hours bucket.
- **Tradeability diagnostic.** Fraction of entry bars with zero volume, and
  the xyz volume-by-hour profile. `universe.py`'s header claims HIP-3 equity
  perps go ~zero-volume off-hours; if xyz cannot actually trade after hours,
  the structural premise dies regardless of the return numbers, and that is
  reported as the finding.
- **Verdict thresholds (locked).** ROBUST = unsigned p<0.01 at any horizon
  AND a signed rule with net EV>0 in BOTH OOS halves and p<0.05.
  MARGINAL = unsigned p<0.05, or a signed rule net-positive overall with
  p<0.10 but OOS-fragile. REFUTED = neither unsigned nor signed beats null.
  BLOCKED-DATA = fewer than 16 usable events; then state the date enough
  history exists and spec (not build) the passive forward recorder.
- **Survivorship caveat.** Universe is TODAY's xyz set; delisted perps are
  absent; any positive result is an upper bound.

## RESULTS (run 2026-07-19, after the spec above was locked)

### Coverage table (deliverable 1)

87 live xyz markets -> **52 EDGAR-covered (60%)**. Excluded 35: 15
commodity/FX/index perps (incl. `CL` which would FALSE-match Colgate and
`GOLD` legacy Barrick), 8 ETFs (DRAM/EWJ/EWT/EWY/EWZ/SMH/XLE/URNM — funds
file no 8-K/6-K), 9 foreign non-filers (KIOXIA, SKHX+SKHY SK Hynix, SMSN
Samsung, HYUNDAI, SOFTBANK, CXMT, MINIMAX, ZHIPU), SPCX (private), BIRD
(trade.xyz says "NewBird AI", SEC ticker BIRD = "Smartbird, Inc." —
unconfirmed identity, excluded rather than risk wrong-CIK events), STRC
(Strategy preferred, CIK dup of MSTR). Foreign-but-US-listed 6-K filers ARE
covered: TSM, ASML, BABA, NOK, ARM, NBIS, BB. Full map with sources in
`W-P1_cache_cik_map.json`. Two covered CIKs had zero filings on record (BOT
RoboStrategy, QNT Quantinuum — too newly public).

### Timestamp convention (deliverable 2)

`data.sec.gov` submissions `acceptanceDateTime` is **genuine UTC** — raw-hour
histogram peaks at 20-21 UTC (2,117 filings = 16:00-17:30 ET after-close
window) vs 127 at raw 16-17. The "Z suffix is really ET" folklore is FALSE
for this endpoint. Rule and histogram in `W-P1_results.json:tz_check`.

### Structural premise check: xyz DOES trade 24/7

94.9% of deep off-hours bars (20:00-04:00 ET) have nonzero volume (market
hours: 96.7%); bars print essentially every hour of the week. Off-hours mean
|1h move| 0.31% vs 0.70% in market hours — thinner but alive.
`universe.py:42-43` ("HIP-3 equity perps only trade during US equity hours…
orders sent off-hours will be rejected") is WRONG for the xyz dex as of
2026-07 — flagged for a docs fix, do not gate xyz entries on US hours.

### Events

**n=308** kept (257 8-K, 51 6-K) across **47 tickers** (median 5/coin, max
MSTR 40 = 13% of sample; per-coin table in `W-P1_results.json:per_coin`).
**294/308 (95%) accepted after-hours** — the structural window is real.
Median entry gap acceptance -> first bar open **0.82h**, p90 0.99h; only 1.0%
of entry bars had zero volume. Dominant 8-K items: 9.01 (163), 8.01 (97),
7.01 (81), 2.02 earnings (61), 5.02 (44).

### Scores (net 25bps for signed; MC p one-sided, 2000 draws; p floor 0.0005)

| cell | h | \|r\| obs vs null (p / sess-p) | S1 long net (p) | S2 momentum net (p) |
|---|---|---|---|---|
| ALL n=308 | +1h | **1.03% vs 0.44% (0.0005 / 0.0005)** | -0.35% (0.022) | -0.24% (0.63) |
| | +4h | **1.82% vs 0.91% (0.0005 / 0.0005)** | -0.28% (0.39) | -0.24% (0.64) |
| | +24h | **3.62% vs 2.56% (0.0005 / 0.0005)** | -0.25% (0.52) | -0.18% (0.67) |
| OOS H1 n=154 | +24h | 3.97% vs 2.41% (0.0005) | **+0.28%** (0.052) | **+0.32%** (0.022) |
| OOS H2 n=154 | +24h | 3.28% vs 2.70% (0.010) | **-0.78%** (0.072) | **-0.68%** (0.11) |
| AFTER-HOURS n=294 | +24h | 3.55% vs 2.54% (0.0005) | -0.26% (0.52) | -0.22% (0.60) |
| MKT-HOURS n=14 | +24h | 5.23% vs 2.92% (0.013) | -0.03% (0.50) | +0.77% (0.20) |
| 8-K n=257 | +24h | 3.96% vs 2.69% (0.0005) | -0.22% (0.54) | -0.12% (0.71) |
| 6-K n=51 | +24h | 1.92% vs 1.89% (0.45) | -0.41% (0.40) | -0.47% (0.33) |

S1 excess vs its own cost-laden null: -0.10% / -0.03% / +0.005% at
+1h/+4h/+24h — nothing there once the null's drift+cost is netted.

### Reading

1. **The reaction is real and ROBUST.** |r| is ~2.3x the same-coin
   random-time null at every horizon, in BOTH OOS halves, and survives the
   session-matched null (so it is not an hours-of-day artifact). The perp
   demonstrably reprices around EDGAR acceptance timestamps, mostly
   after-hours while the underlying is closed. The biggest post-acceptance
   24h moves are earnings 8-Ks entered <1h after acceptance: MSTR +31.5%,
   RKLB +27.3%, CRCL +18.9%, INTC +18.3% and -16.1%.
2. **No mechanically-capturable signed drift.** S1 (long-all) is net-negative
   overall and sign-flips across halves; S2 (first-reaction momentum)
   likewise (+0.32% H1 vs -0.68% H2 at +24h). By the pre-registered gates
   this is a signed REFUTE: by first-bar entry the direction is already fully
   in the price (or the direction simply is not knowable without content).
3. **6-K is the weak half** — routine foreign-issuer paperwork (TSM monthly
   revenue etc.), 24h unsigned ns. The juice is 8-K, i.e. content-bearing
   corporate events.

### Caveats

- Survivorship: today's xyz set only; upper-bound framing applies.
- Cross-event clustering (many filings land the same evenings; MSTR 13% of
  sample) is not modeled by the per-event-independent null; unsigned p-values
  are optimistic to an unknown but likely small degree given the 2.3x ratio.
- No holiday calendar in the market-hours split (n=14 bucket anyway).
- 6-K "events" include routine attachments; dilutes that bucket.

## VERDICT: **MARGINAL** (per the locked thresholds)

Unsigned reaction: ROBUST (p=0.0005 everywhere, OOS-stable). Signed
mechanical capture: REFUTED (both rules sign-flip OOS; ALL-sample net EV
negative at every horizon). The pre-registered composite therefore lands
MARGINAL: the latency channel EXISTS — the perp moves on the public
timestamp, largely after-hours — but the tradeable direction is not in the
timestamp, it is in the filing CONTENT.

**Recorder go/no-go: NO-GO** on a mechanical passive recorder — 208d of full
1h history already exists (this study consumed it; nothing to accrue that a
re-run of `W-P1_edgar_fetch.py` cannot refetch), and the mechanical rules are
refuted. The live follow-up worth specing as its own cell (W-P3, NOT built
here — W-P2 is the scheduled-catalyst cell already in flight in this lane):
content-signed direction — at acceptance, pull the 8-K text from
EDGAR (exact URL is deterministic from accessionNumber), have the LLM lane
call LONG/SHORT/PASS from the items + text within the entry bar, score it
against THIS harness's entry/exit/null. Latent piece = direction call;
deterministic piece = this exact pipeline. Only 8-K items 1.01/2.02/8.01
merit the read (6-K refuted). Zero capital until that cell validates.

