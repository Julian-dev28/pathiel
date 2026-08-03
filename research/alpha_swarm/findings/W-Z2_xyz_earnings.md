# W-Z2 — Earnings/catalyst on xyz tokenized equities: surprise-signed PEAD, pre-earnings drift, closed-market window

Lane Z (event-driven frontier), 2026-07-22. Scoping + backtest only, no live
build. Prior art this must extend, not repeat:

- **W-P1** (`findings/W-P1_edgar_latency.md`): EDGAR filing reaction is real
  (|r| ~2.3x null, p=0.0005), xyz perps genuinely trade 24/7 (94.9% of deep
  off-hours bars have volume), but mechanical direction (long-all,
  first-reaction momentum) REFUTED at +1h/+4h/+24h.
- **W-P3** (`findings/W-P3_llm_signed_edgar.md`): LLM signing filing TEXT
  REFUTED — but the LLM saw only primary-doc shells (earnings numbers live
  in EX-99 exhibits it never got) and SKIPped 91% of earnings events.
- **W-P4** (`findings/W-P4_xyz_pead.md`): classic PEAD REFUTED under both
  available sign rules (first-day reaction sign, cached LLM sign). The one
  survivor: earnings-specific UNSIGNED |r| elevation for ~5 days (p≈0.004),
  absent on non-earnings filings. Closing instruction: "do not rebuild PEAD
  without a genuine surprise measure (consensus-vs-actual), which does not
  exist in our free caches."

W-Z2 supplies exactly that missing ingredient: **analyst-consensus EPS
surprise** from the free Nasdaq API (Zacks-sourced; `dateReported`, actual
EPS, consensus forecast, % surprise per quarter), verified working
2026-07-22. The surprise sign is PIT-knowable minutes after the release —
consensus is published BEFORE the announcement, the actual number is IN the
press release, and an LLM (our stack's core competence) extracts and
compares them in seconds. This is the earnings analog of unlock_short /
young_mover_short: a SCHEDULED, LLM-researchable event class.

Scripts: `hypotheses/W-Z2_fetch.py` (Nasdaq surprise + forward calendar,
HL 1h top-up, funding + book snapshot), `hypotheses/W-Z2_backtest.py`.
Caches: `hypotheses/W-Z2_cache_surprise.json`, `W-Z2_cache_fwd.json`,
`W-Z2_cache_1h_topup.json`, `W-Z2_cache_funding.json`,
`W-Z2_cache_ctxs.json`. Results: `hypotheses/W-Z2_results.json`.

---

## PRE-REGISTERED SPEC — written 2026-07-22 BEFORE any outcome was computed

### Event set

- Start from W-P4's 66 classified earnings events, loaded verbatim from
  `W-P4_results.json:events_detail` (count re-asserted = 66 or abort).
- **TRUE-EARNINGS match:** an event is a true quarterly earnings event iff
  its acceptance UTC date is within ±3 calendar days of a Nasdaq
  `earnings-surprise` row's `dateReported` for the same ticker. This both
  supplies the surprise AND cleans the event set (TSLA/RIVN delivery-report
  2.02s and foreign names without US consensus coverage fall out; counted
  and listed). If `percentageSurprise` is null but eps + consensus are
  present, surprise = (eps − consensus)/|consensus|. sign = +1 if
  surprise > 0, −1 if < 0; surprise = 0 or unavailable → excluded, counted.
- **Candle top-up (only new HL candle data):** 1h candles via
  `fetch_hl_candles` for ONLY the coins with post-2026-07-01 events
  (≤8 coins, one call each), appended to a COPY of `W-P1_cache_1h.json`;
  overlap bars must match the cache exactly or abort. Daily bars rebuilt
  from 1h by UTC calendar day, W-P4 convention verbatim.
- **Rebuild assertion:** recomputed +3d/+5d/+10d/+21d returns must match
  `W-P4_results.json:events_detail.r` 1:1 wherever both exist (tolerance
  1e-9), else abort. (July events whose horizons were None in W-P4 may gain
  values from the top-up; that is the point of the top-up.)

### Cell S — surprise-signed PEAD (PRIMARY verdict cell)

- Entry: open of first UTC daily bar with open_time ≥ acceptance (the bar
  containing acceptance completes first); entry gap ≤ 48h. W-P4 verbatim.
- Horizons: **+1d/+3d/+5d/+10d/+21d** open-to-open; exit = first daily bar
  with open_time ≥ entry + h·24h, dropped if exit opens > 48h past nominal.
- EV25 per event = sign · r − 0.0025 (25 bps round trip).
- **Null (primary):** 2000× same-coin random-TIME draws — per event per
  iteration, a surrogate acceptance drawn uniformly from the coin's 1h
  bar-open times with a valid daily entry (gap ≤ 48h) and a valid +21d
  exit; the event's OWN surprise sign is kept at the surrogate time (W-P4
  Rule-B null construction). Per-iteration statistic = mean EV25 over the
  cell; one-sided MC p = (1+k)/(1+N) in the direction of the observed mean.
  Seed 20260722.
- **Diagnostic null:** 2000× sign-permutation within cell (WHEN vs
  WHICH-SIGN separation). Cannot upgrade a verdict.
- OOS: halves by acceptance time (split at n//2) when scored n ≥ 16.
- Diagnostics (cannot upgrade): |surprise| ≥ 5% subset; beats-only (long
  leg) vs misses-only (short leg); agreement rate of surprise sign vs
  W-P4's first-day-reaction a_sign, plus EV of the agree/disagree split.
- **Verdict thresholds (locked, W-P4-identical):** ROBUST = ≥1 horizon with
  EV25 > 0, random-time p < 0.05, EV25 > 0 in BOTH OOS halves at that
  horizon, an ADJACENT horizon also EV25 > 0, and scored n ≥ 16.
  MARGINAL = EV25 > 0 at ≥1 horizon with p < 0.10 but failing any other
  clause. REFUTED = neither. 5 horizons = 5 looks; halves + adjacency are
  the multiplicity guard.

### Cell P — pre-earnings drift (scheduled-date anticipation)

- Matched TRUE-earnings events only. Announcement day = the acceptance UTC
  day (realized date used as proxy for the scheduled date — dates are
  public weeks ahead and rarely move; caveat noted).
- ONE look, locked: unconditional LONG from the open of the daily bar 5
  calendar days before the announcement UTC day to the open of the
  announcement-day bar (00:00 UTC — always BEFORE the release, since
  acceptance ≥ 00:00 of its own day; both bars must exist, else dropped).
- EV25 = r − 0.0025. Pre-registered direction: classic pre-earnings RUN-UP
  (long, positive).
- Null: 2000× same-coin random 5-day open-to-open windows (uniform over
  daily bars with a valid −5d/+0d pair), one-sided p for mean > null mean.
- ROBUST = EV25 > 0, p < 0.05, both OOS halves > 0, n ≥ 16. MARGINAL =
  EV25 > 0, p < 0.10, halves fragile. REFUTED = neither.

### Cell W — closed-market window continuation (surprise-signed)

- Matched TRUE-earnings events whose acceptance falls OUTSIDE NYSE regular
  hours (09:30–16:00 America/New_York, weekday; no holiday calendar —
  rare-misclassification caveat).
- Entry: open of first 1h bar with open_time ≥ acceptance, gap ≤ 6h (W-P1
  convention). Sign = surprise sign (NOT first-reaction — that was refuted;
  the surprise is knowable from the release text itself at entry).
- Exit: open of first 1h bar with open_time ≥ the next NYSE regular-session
  open (09:30 ET, DST-aware) strictly after entry. Hypothesis: the thin
  perp UNDER-reacts while the real stock is closed; the surprise direction
  keeps paying until the primary market opens and completes the reprice.
- EV25 = sign · r − 0.0025.
- Null: 2000× same-coin draws from 1h bars that are themselves outside
  NYSE regular hours, same sign kept, identical exit rule.
- ROBUST/MARGINAL/REFUTED: as Cell P (one look).

### Costs & structure (deterministic adds, no verdict)

- One `metaAndAssetCtxs dex=xyz` snapshot: per-coin funding rate, impact
  prices (spread proxy), mark/mid, open interest, day notional volume,
  max leverage.
- `fundingHistory` for 3 representative coins (high/mid/low liquidity),
  30d: mean and p90 |daily funding|; worst-case drag applied to Cell S
  holding periods.
- Liquidation distance at max and at policy leverage from universe
  maxLeverage.

### Locked caveats

Survivorship (today's xyz set; positives are upper bounds). ~7 months of
candles → at most 2-3 true earnings per name; ONE year's two-and-a-bit
earnings seasons; n is structurally small. Calendar clustering: overlapping
10-21d windows share the market factor → per-event-independent null
understates variance, p optimistic at long horizons. Nasdaq stores the
FINAL consensus, which can differ slightly from the pre-release consensus
(minor, direction-neutral). UTC-day bars, not exchange dailies. Forward
earnings dates from Nasdaq are partly algorithm-estimated (Zacks) until
confirmed. Realized announcement date proxies the scheduled date in Cell P.

---

RESULTS PENDING — appended below after the run, spec frozen as of this
commit.
