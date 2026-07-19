# W-P3 — LLM-signed EDGAR direction: filing TEXT at acceptance -> LONG/SHORT/SKIP

Lane P (public-record latency), 2026-07-19. Successor cell to W-P1
(`findings/W-P1_edgar_latency.md`), which proved the reaction (|r| ~2.3x null,
p=0.0005 at every horizon) but REFUTED mechanical direction (long-all and
first-reaction momentum both net-negative, OOS sign-flip). Hypothesis here:
an LLM reading the FILING TEXT at acceptance time signs the direction
profitably. Scripts: `hypotheses/W-P3_fetch_texts.py`,
`hypotheses/W-P3_llm_sign.py`, `hypotheses/W-P3_backtest.py`. Caches:
`hypotheses/W-P3_cache_events.json`, `W-P3_cache_primary_docs.json`,
`W-P3_cache_texts/` (extracted primary-doc text per accession). Results
(LLM calls checkpointed + scores): `hypotheses/W-P3_results.json`.

## PRE-REGISTERED SPEC — written 2026-07-19 BEFORE any LLM output was scored

- **Event set: W-P1's, verbatim.** The SAME 308 events (257 8-K, 51 6-K, 47
  tickers), rebuilt deterministically from the W-P1 caches
  (`W-P1_cache_filings.json` + `W-P1_cache_1h.json`) with the identical
  event-construction code, extended only to carry `accessionNumber`, `cik`
  and company name. The rebuild MUST match `W-P1_results.json:events_detail`
  1:1 on (ticker, acc_iso, r) or the run aborts. No new events, no drops.
- **Filing text.** Primary document per accession, URL deterministic:
  `sec.gov/Archives/edgar/data/{cik}/{accession-nodash}/{primaryDocument}`.
  `primaryDocument` name comes from a fresh per-CIK submissions fetch
  (accession -> primaryDocument map); fallback for accessions aged out of
  `recent`: the archive folder's `index.json`, first non-index `.htm`
  document. HTML -> text via stdlib HTMLParser (script/style dropped, tags
  to spaces, entities unescaped, whitespace collapsed). Truncation for the
  prompt: if the extracted text matches `Item\s+\d+\.\d+` (8-K item
  sections), start at the FIRST match; else start at char 0; keep 6,000
  chars. SEC fetches throttled to ~2/s with the research User-Agent.
- **LLM.** Local Claude Code CLI, the repo's `ai_brain.py` ClaudeCliBrain
  invocation shape verbatim: `claude -p --output-format json --max-turns 1
  --tools "" --safe-mode --no-session-persistence`. NO web search, NO tools,
  one call per event, default (best-available) model — the actual model id is
  recorded per event from the envelope's `modelUsage`. Prompt contains ONLY:
  ticker, company name, filing type (8-K/6-K + items string), acceptance
  datetime (UTC), and the truncated filing text, plus the instruction to use
  ONLY the filing content and output strict JSON
  `{"direction": "LONG"|"SHORT"|"SKIP", "conviction": 0-1,
  "reason": "<=20 words"}`. Malformed output after one strict-parse pass
  (raw JSON or fenced JSON) = one immediate re-call; still malformed =
  UNSCORED. CLI usage-limit error = sleep 15 min, retry, up to 4 retries,
  then UNSCORED. UNSCORED events are excluded from all cells and counted in
  the report. Calls are checkpointed to `W-P3_results.json` after every
  event so a limit hit resumes without re-spending calls.
- **Entry/exit/costs: identical to W-P1.** Entry = open of first 1h bar with
  open_time >= acceptance (containing bar skipped). Horizons +1h/+4h/+24h
  open-to-open, exit = first bar >= entry_time + h with 6h slack. 25 bps
  round trip on signed EV. Returns are taken from the W-P1 harness verbatim
  (asserted equal to `events_detail.r`).
- **Signing.** LONG = +1, SHORT = -1, SKIP = excluded from EV (reported as
  skip rate). EV25 per event = sign * r - 0.0025.
- **CONTAMINATION caveat (locked before scoring).** The scoring model's
  training data may contain events before its cutoff — for those, "reading
  the filing" can be memory of what happened next. ALL scoring is split by
  acceptance datetime pre/post **2026-02-01T00:00Z**. The POST-cutoff cell
  (n=283 of 308) is the HONEST cell and the ONLY verdict cell. The
  pre-cutoff cell (n=25) is reported as a contaminated upper bound. Residual
  risk that post-2026-02-01 material leaked into the model is noted, not
  testable here; the per-event model id is recorded as evidence.
- **Null (locked).** Sign-permutation: within each scored cell, shuffle the
  LLM's sign vector across the SAME non-SKIP events 2,000x (permutation
  without replacement — preserves the long/short mix), score each
  permutation with the same returns and cost, one-sided MC p in the
  direction of the observed mean. Fixed seed. This tests "does WHICH filing
  got WHICH sign matter", holding event selection and net long bias fixed.
- **Cells.** PRIMARY: POST (acc >= 2026-02-01), its two time halves POST-H1
  / POST-H2 (sorted by acceptance, split at n//2). Reported context: ALL,
  PRE (contaminated). Pre-registered DIAGNOSTICS (not verdict cells): 8-K
  only, items-{1.01,2.02,8.01} subset (the ALPHA-QUEUE spec's candidate
  trigger set), conviction >= 0.6 subset, per-form skip rates.
- **Decision bar (locked).** ROBUST = in the POST cell, EV25 > 0 at >= 1
  horizon AND EV25 > 0 at that same horizon in BOTH post halves AND
  sign-permutation p < 0.05 at that horizon. MARGINAL = POST EV25 > 0 at
  >= 1 horizon with p < 0.10 but halves fragile, or p in [0.05, 0.10) with
  both halves positive. REFUTED = neither. Pre-cutoff numbers can NEVER
  upgrade the verdict.
- **Survivorship caveat inherited from W-P1:** today's xyz set only; any
  positive result is an upper bound.

## RESULTS (run 2026-07-19, after the spec above was locked)

### Run facts

- Event rebuild: 308/308 matched W-P1 1:1 (ticker, acc_iso, all three
  horizon returns; asserted in `W-P3_fetch_texts.py:build_events`).
- Texts: 308/308 primary docs fetched and cached (0 failures; median
  extracted length 4.7k chars). Primary-doc map covered all accessions from
  the fresh submissions fetch; the index.json fallback was never needed.
- LLM calls: 308/308 scored OK, 0 UNSCORED. Model per envelope:
  **claude-fable-5** (+ claude-haiku-4-5 auxiliary) on every event; ~4s and
  ~$0.09/call, $14.47 recorded total. One operator session-limit hit at
  200/308 paused the run (~2h); the checkpoint resume re-signed the
  remaining 108 with zero loss. (Process note: the CLI phrases the limit
  "You've hit your session limit", which the first detection missed —
  broadened to any `limit`/429 in error output, `W-P3_llm_sign.py:call_cli`.)
- Directions: **258 SKIP (84%), 32 LONG, 18 SHORT.** Skip rate 83% on 8-K,
  86% on 6-K. The reads are content-genuine (dilution ATMs shorted, binding
  GPU/funding deals longed); only 14/258 SKIPs cite the press-release
  exhibit being absent, so the shell-8-K text gap is real but not the main
  driver of the skip rate — most SKIPs are correctly-read routine filings.

### Scores (EV net 25bps; p = 2000x sign-permutation, one-sided)

| cell | n signed | +1h EV25 (p) | +4h EV25 (p) | +24h EV25 (p) |
|---|---|---|---|---|
| **POST-cutoff (VERDICT)** | 49 (L31/S18) | **-0.58%** (0.21) | **-0.99%** (0.08) | **-0.45%** (0.42) |
| POST H1 | 26 | -0.51% | -0.70% | +0.65% |
| POST H2 | 23 | -0.67% | -1.32% | -1.70% |
| PRE-cutoff (contaminated) | 1 of 25 | — | — | — |
| ALL | 50 | -0.58% | -0.98% | -0.63% |
| POST 8-K only (diag) | 42 | -0.67% | -1.21% | -0.50% |
| POST items 1.01/2.02/8.01 (diag) | 33 | -0.61% | -1.31% | -0.65% |
| POST conviction>=0.6 (diag) | 28 | -0.61% | -0.74% | +0.55% (0.17) |

### Reading

1. **The decision bar fails at the first gate.** POST-cutoff EV25 < 0 at
   every horizon; there is no positive cell on which to test halves or p.
   Raw (uncosted) signed EV is ALSO negative at all horizons
   (-0.33/-0.74/-0.20%), so this is not a fee artifact — the LLM's
   directions lose money before costs.
2. **The sign is already in the price, and fading.** The LLM's sign agrees
   with W-P1's S2 first-reaction sign 60% of the time (29/48): by the first
   completed bar the market has usually already moved the way the filing
   reads, and entering there captures the give-back. At +4h the permutation
   p=0.08 is in the WRONG direction — the LLM's specific assignment is
   marginally worse than random shuffles of its own signs. Content reading
   at first-bar latency = buying the pop / shorting the dip.
3. **The contamination split turned out moot.** 24/25 pre-cutoff events were
   SKIPped (routine late-Dec/Jan filings), leaving n=1 — no contaminated
   upper bound worth reporting, and the honest cell carries 49/50 of the
   signed sample anyway.
4. **The one hopeful-looking diagnostic is noise.** Conviction>=0.6 at +24h
   is +0.55% but p=0.17, n=28, and it sign-flips at shorter horizons; it was
   pre-registered as a diagnostic and cannot upgrade the verdict.
5. Caveats: signed n is small (49) and ticker-concentrated (MSTR 6, USAR 5);
   filing text is the primary doc only — earnings numbers living in EX-99.1
   were not shown to the model (14 explicit shell SKIPs); W-P1's
   survivorship caveat applies.

## VERDICT: **REFUTED** (per the locked decision bar)

W-P1 proved the perp moves on EDGAR acceptance; W-P3 shows that even READING
the filing at first-bar latency does not recover a tradeable sign — net EV
negative at every horizon in the honest cell, and no better than shuffled
signs. Combined lane-P conclusion: the reaction is real, fast, and by the
first completed 1h bar it is spent. Any live use of this channel would need
intra-bar (minutes-scale) entry, which the 1h backtest harness cannot
license — that is a different, execution-heavy cell, not a re-run of this
one. **Recorder: NO-GO. Zero capital. Do not rebuild without a
faster-than-first-bar entry design.**

