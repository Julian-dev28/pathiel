# W-P4 — PEAD on xyz equities: post-earnings-filing drift at DAY horizons

Lane P (public-record latency), 2026-07-20. Successor to W-P1
(`findings/W-P1_edgar_latency.md`: reaction real, ~2.3x null, p=0.0005, but
first-bar signed capture REFUTED) and W-P3 (`findings/W-P3_llm_signed_edgar.md`:
LLM text signs REFUTED at +1h/+4h/+24h). Hypothesis here is the CLASSIC equity
anomaly neither cell tested: post-earnings-announcement drift — the initial
surprise direction persists for 5-60 DAYS as institutions diffuse in slowly.
That is not a first-bar latency claim; it is a slow-diffusion claim, and the
xyz perps print 24/7 over ~208d of cached history. Zero new data spend: event
set, candles, filing texts and LLM signs are all W-P1/W-P3 caches.
Script: `hypotheses/W-P4_pead_backtest.py` (cache-only, no network).
Results: `hypotheses/W-P4_results.json`.

## PRE-REGISTERED SPEC — written 2026-07-20 BEFORE any outcome was computed

- **Event universe.** W-P1's 308 events, loaded from
  `hypotheses/W-P3_cache_events.json` (carries accession/cik/acc_ms; that file
  was 1:1-asserted against `W-P1_results.json:events_detail` when W-P3 built
  it). Count re-asserted = 308 or abort.
- **EARNINGS CLASSIFIER (deterministic, locked before any return was
  touched).** Designed by inspecting cached INPUT texts only — no join to
  prices was made during design.
  - 8-K: earnings iff the EDGAR `items` metadata contains `2.02` (Results of
    Operations and Financial Condition). Text fallback for 8-Ks whose items
    lack 2.02 but whose cached primary text matches `/Item\s+2\.02/i` or
    `/Results of Operations and Financial Condition/i` — checked during
    design, adds 0 events in this sample.
  - 6-K: earnings iff the cached primary-doc text
    (`W-P3_cache_texts/<accession>.txt`) matches ANY of nine case-insensitive
    regexes:
    1. `(first|second|third|fourth)\s+quarter[\s\S]{0,60}?(results|earnings)`
    2. `quarterly\s+(financial\s+)?results`
    3. `(annual|full[- ]year|half[- ]year|interim)\s+(financial\s+)?results`
    4. `earnings\s+(release|announcement|call|conference)`
    5. `results\s+of\s+operations`
    6. `unaudited[\s\S]{0,60}?(financial\s+statements|results)`
    7. `(march|june|september|december)\s+quarter[\s\S]{0,30}?results`
    8. `\bQ[1-4]\s+20\d\d\b[\s\S]{0,80}?(results|earnings)`
    9. `reports\s+[\s\S]{0,120}?net\s+(sales|income)`
  - TSM monthly-revenue 6-Ks deliberately do NOT count: PEAD is a
    quarterly-earnings-surprise anomaly, monthly revenue is a different
    (higher-frequency, lower-surprise) release class.
  - Classifier output, counted at design time with no outcomes computed:
    **61 8-K (items 2.02) + 5 6-K** (BABA DecQ 03-19, TSM Q1 04-16, BABA
    MarQ+FY 05-13, ASML Q2 07-15, TSM Q2 07-16) = **66 candidates** before
    entry/horizon hygiene.
- **Bars.** DAILY bars built from `W-P1_cache_1h.json` by UTC calendar day:
  open = first 1h bar's open (open_time = that bar's t), close = last 1h
  bar's close, volume summed. Perps trade 24/7 so weekend bars exist. A
  coin's first cached day is partial; recorded as-is.
- **Entry (lookahead-safe).** Open of the first DAILY bar whose open time >=
  acceptance — the bar containing acceptance must complete first, same
  convention as W-P1 scaled to daily. Entry gap (acceptance -> entry open)
  must be <= 48h, else dropped. One event per coin-entry-bar within the
  earnings subset (earliest acceptance kept); same rule separately inside
  the non-earnings diagnostic complement.
- **Horizons.** +3d/+5d/+10d/+21d open-to-open; exit = first daily bar with
  open_time >= entry_open_time + h*24h; horizon dropped for an event if the
  exit bar opens > 48h past nominal. n per horizon reported per cell.
- **Direction rules.**
  - RULE A (PEAD proxy, primary): sign of the FIRST DAY's reaction =
    sign(entry_open / c_pre − 1), where c_pre = close of the last 1h bar
    fully completed BEFORE acceptance (bar close time <= acceptance). Known
    at entry. Zero reaction or no pre-bar -> excluded from A, counted.
  - RULE B (cached LLM signs, free reuse of W-P3): `W-P3_results.json`
    direction per accession, LONG=+1 / SHORT=−1 / SKIP excluded. Primary B
    cell = earnings subset. Diagnostic B cell = all 308 events (max-n answer
    to "do the cached signs redeem themselves at long horizons"). No new
    LLM calls.
- **Costs.** 25 bps round trip: EV25 = sign*r − 0.0025.
- **Null (primary, locked).** Matched same-coin random-TIME, 2000
  iterations. Per event per iteration: draw a surrogate acceptance time
  uniformly from the coin's 1h bar-open times whose implied daily entry has
  gap <= 48h and a valid +21d exit; run the IDENTICAL pipeline at the
  surrogate (daily entry, reaction sign from the prior 1h close, horizon
  returns). Rule A null applies the surrogate's own reaction sign
  (zero-sign draws excluded, mirroring obs); Rule B null keeps the event's
  LLM sign at the surrogate time; unsigned null = |r|. Per-iteration
  statistic = mean over the cell's events; one-sided MC
  p = (1+k)/(1+N) in the direction of the observed mean. Seed 20260720.
- **Secondary diagnostic null (declared now).** 2000x sign-permutation
  within cell (shuffle the sign vector across the same scored events, same
  returns) — separates WHEN from WHICH-SIGN. Cannot upgrade a verdict.
- **Cells.** VERDICT cell: EARN (66 candidates), rules A and B, plus OOS
  halves by acceptance time (split at n//2) whenever a horizon's scored
  n >= 16. Diagnostics (cannot upgrade): EARN-8K, EARN-6K, NONEARN
  complement under rule A (specificity — if "drift" shows up equally on
  non-earnings filings it is not PEAD), B-ALL308, unsigned |r| vs null.
- **Verdict thresholds (locked).** Per rule: ROBUST = >=1 horizon with
  EV25 > 0, random-time p < 0.05, EV25 > 0 in BOTH OOS halves at that
  horizon, AND an adjacent horizon also EV25 > 0 (drift must be smooth in
  h, not a one-cell spike), AND scored n >= 16. MARGINAL = EV25 > 0 at >=1
  horizon with p < 0.10 but failing any other ROBUST clause. REFUTED =
  neither. 2 rules x 4 horizons = 8 looks in the verdict cell; the
  both-halves + adjacent-horizon clauses are the multiplicity guard — a
  lone p in [0.01,0.05) with no neighbor support cannot be ROBUST.
- **Caveats locked in advance.** Survivorship (today's xyz set; any positive
  is an upper bound). ~208d history -> at most 2-3 earnings per name;
  n is structurally small and one season dominates. Earnings-season calendar
  clustering: overlapping 10-21d windows share the market factor and the
  per-event-independent null understates variance — p-values are optimistic
  to an unknown degree at the long horizons. Daily bars are UTC-day
  aggregates of HL 1h perp candles, not exchange dailies. W-P3's LLM signs
  were produced from primary-doc text only (earnings numbers living in
  EX-99 exhibits were often unseen), so RULE B is a weak-form test of
  content-signing. Late-sample events (July) lose the long horizons because
  the candle cache ends 2026-07-19.

## RESULTS (run 2026-07-20, after the spec above was locked)

### Run facts

- Classifier output matched the design-time count exactly: **66 earnings
  events (61 8-K items-2.02, 5 6-K)** of 308; the 8-K text fallback added 0.
- After entry hygiene: EARN n=66 (all 66 A-signable, zero flat reactions),
  NONEARN n=235, ALL n=298. Median entry gap acceptance -> daily open
  **3.8h** (after-hours acceptance ~20-21 UTC -> next UTC midnight).
  Scored n decays 64 -> 60 across +3d -> +21d as July events lose horizons.
- Acceptance span 2026-01-02 -> 2026-07-16; OOS half boundary 2026-04-29
  (33/33) — cleanly Q4-earnings season vs Q1-earnings season.
- A-sign mix 28 long / 38 short. Per-coin max TSLA 5/66 (8%) — far less
  concentrated than W-P1's MSTR 13%.
- **RULE B is structurally starved on earnings:** W-P3's LLM signed only
  **6/66** earnings events (all six LONG). 8-K earnings primary docs are
  shells whose numbers live in EX-99 exhibits the LLM never saw, so it
  SKIPped 91% of exactly the events this cell needs. Locked caveat
  confirmed in the data.
- ONE mechanical deviation from the locked spec, disclosed: coins with <21d
  of history (AMAT, listed 2026-06-29) have an empty 21d-valid null pool
  while holding valid 3d/5d events — the pool falls back to +3d-valid
  surrogates for those coins only; longer-horizon draws yield None and are
  skipped, exactly mirroring the event's own None at those horizons
  (`W-P4_pead_backtest.py:null_pool`).

### Scores (EV net 25bps; p = 2000x same-coin random-time null, one-sided;
perm = 2000x sign-permutation diagnostic)

| cell | h | n | EV25 (p / perm) | \|r\| obs vs null (p_u) |
|---|---|---|---|---|
| **EARN rule A** | +3d | 64 | +0.10% (0.30 / 0.37) | **6.05% vs 4.44% (0.0045)** |
| | +5d | 62 | +0.87% (0.15 / 0.21) | **7.78% vs 5.77% (0.0040)** |
| | +10d | 62 | +0.10% (0.40 / 0.40) | 9.29% vs 8.19% (0.13) |
| | +21d | 60 | +0.19% (0.44 / 0.40) | 13.94% vs 12.29% (0.13) |
| EARN A halves @+5d | H1/H2 | 33/29 | +1.53% / +0.13% (both >0) | — |
| EARN A halves @+10d | H1/H2 | 33/29 | +1.21% / **-1.16%** (flip) | — |
| EARN A halves @+21d | H1/H2 | 33/27 | +1.25% / **-1.10%** (flip) | — |
| **EARN rule B** | +3d | 6 | -0.50% (0.36 / degenerate) | — |
| | +5d | 5 | +2.42% (0.33) | — |
| | +10d | 5 | +4.05% (0.35) | — |
| | +21d | 4 | too few | — |
| EARN 8-K only (diag) A | +5d | 59 | +1.09% (0.11) | 7.98% vs 5.86% (0.0040) |
| NONEARN (diag) A | +3d | 231 | -0.40% (0.52) | 4.31% vs 4.67% (0.90) |
| | +5d | 231 | -0.45% (0.44) | 5.93% vs 5.91% (0.48) |
| | +21d | 203 | +0.41% (0.32) | 12.82% vs 12.49% (0.34) |
| B-ALL308 (diag) | +3d | 48 | -1.22% (0.14) | — |
| | +21d | 40 | +1.35% (0.45); halves +3.48/-1.52 (flip) | — |

### Reading

1. **Rule A (PEAD proxy) fails the locked bar at every horizon.** EV25 is
   positive at all four horizons and sits above the (negative) random-time
   null everywhere — but the best cell is +5d at +0.87% with p=0.15, short
   of even the MARGINAL p<0.10 gate. Halves are both positive at +5d yet
   sign-flip at +10d and +21d (H2 -1.16%/-1.10%): whatever residue exists
   dies inside a week and does not survive the season split at the classic
   PEAD horizons. Permutation p agrees (0.21 at +5d) — neither the timing
   nor the sign assignment carries signal at the locked significance.
2. **Rule B (cached LLM signs) is untestable on earnings in practice and
   negative where testable.** n=6 signed earnings events, all LONG, and the
   +5d/+10d positives are one outlier (SNDK +41.8%/+48.6%); +3d is
   negative; p never below 0.33. On B-ALL308 the long horizons do NOT
   redeem the signs: +21d +1.35% with p=0.45 and an OOS sign-flip
   (+3.48%/-1.52%), +3d -1.22%. The W-P3 refutation stands at day scale.
3. **The one real, earnings-specific fact: excess unsigned movement extends
   to ~5 days.** EARN |r| beats the same-coin null at +3d (6.05% vs 4.44%,
   p=0.0045) and +5d (7.78% vs 5.77%, p=0.0040) but not at +10d/+21d — and
   the NONEARN complement shows NOTHING at any horizon (p_u 0.34-0.90).
   This extends W-P1's 24h reaction result: earnings filings specifically
   keep the perp moving abnormally for roughly a trading week, but by every
   direction proxy we hold (first-day sign, LLM text sign) that movement is
   directionless. Vol-class information, not drift-class.
4. Caveats as locked: one year's two earnings seasons, survivorship,
   calendar clustering (p optimistic at long horizons — which only
   strengthens the refute), UTC-day bars, weak-form Rule B.

## VERDICT: **REFUTED** (both rules, per the locked thresholds)

Classic PEAD does not survive on xyz perps under either direction rule: no
horizon reaches even MARGINAL. The combined lane-P picture is now closed
end-to-end: the reaction to EDGAR filings is real (W-P1), earnings filings
keep realized movement elevated for ~5 days (this cell), but the DIRECTION
is not recoverable from the timestamp (W-P1), the text at first-bar latency
(W-P3), the first-day reaction sign, or the cached text signs at day
horizons (this cell). No exact spec, no kill — nothing to promote.
**Recorder/live: NO-GO. Zero capital.** The only survivor worth a future
thought is the unsigned fact: an earnings-filing VOL filter (e.g. widen
stops / expect movement for ~5d post-2.02) would be an execution overlay,
not an alpha cell — do not rebuild PEAD without a genuine surprise measure
(consensus-vs-actual), which does not exist in our free caches.

