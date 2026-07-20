# Reverse refuted-direction audit

**Status:** SETTLED 2026-07-20 (graded with matched same-coin random-time nulls,
2000 draws, price-only @12bps both sides). **Started:** 2026-07-20 (Codex),
completed same day (Claude).

## Verdict up front

Blanket reversal does NOT work. Of the refuted directional books, every inverse
except one is tape beta or noise once the matched null is applied — the
`classify()` VALIDATED labels from the first pass were computed without a null
and are retracted. ONE cell survives: **short the breaking-news coverage surge
on xyz equities** (the exact inverse of the refuted news_catalyst breaking
long). Its recorder was demolished 2026-07-18, so the evidence is frozen one
episode short of the formal bar — see the decision at the bottom.

| book (inverse of) | raw | n | inverse @12bps | OOS halves | excess vs null | mc_p | honest verdict |
|---|---:|---:|---:|---|---:|---:|---|
| premium_fade_short → long | 35 | 9 | +5.18%/sig | +0.008 / +9.32 | +4.18% | 0.142 | NOT significant; entire edge in the second half; n tiny |
| neg_funding_fade → long | 13 | 4 | +1.76%/sig | +3.16 / +0.35 | +1.59% | 0.093 | PENDING n=4; matches the already-known W-F3 MARGINAL long (dies ~50bps); operator do-not-rebuild stands |
| young_listings (163 longs) → short | 163 | 18 | +2.25%/sig | +2.42 / +2.08 | **+0.82%** | 0.323 | TAPE BETA — random-time shorts on the same coins earned ~+1.4%; young listings just drifted down |
| news_catalyst breaking → short | 114 | 7 | **+13.82%/sig** | +8.79 / +17.60 | **+11.69%** | **0.0005** | SURVIVES. Leave-CASHCAT-out (6 xyz equities only): +7.52%/sig, excess +6.65%, mc_p 0.004 |
| news_catalyst non-breaking → short (control) | 4900 | 171 | +2.41%/sig | +0.30 / +4.49 | +1.55% | 0.0005 | control is ALSO positive-excess — see synthesis; first half thin (+0.30) |

## Synthesis: the real finding is attention-fade, news is its strongest trigger

The control was meant to kill the breaking cell and didn't — it recontextualized
it. Shorting ANY scan candidate at read time beat same-coin random-time shorts
by +1.55%/sig (n=171, mc_p 0.0005); shorting the breaking-surge subset beat it
by +11.69%/sig (+6.65% ex-outlier). The news increment over generic
scanner-fade is ~+5-10pp/sig — real, not just "short the universe." But the
positive control connects to three independent same-window results: mover_pass
interim-refuted (chasing AI-PASSed movers long = −6.36%/sig), mover_b15_up
interim-refuted (chasing +15% movers long = −6.45%/sig), and the AI-long
anti-calibration band. One coherent picture: in this tape, attention spikes
mark next-day tops. The system's long-chasing surfaces all fight it; the
inverse audit measured the same effect from the short side.

Regime caveat that bounds everything above: the clean news ledger spans EIGHT
DAYS (restarted 2026-07-12). Both "OOS halves" live inside one week and one
news regime (semis/AI-hardware wave). The control's first half is +0.30%/sig —
near zero. None of this is deployable evidence; it is a sharply-posed
hypothesis with a pre-built grader.

## The surviving cell, in detail

The refuted news_catalyst book went LONG on breaking coverage surges and lost
−7.33%/sig at its 07-16 bar. The exact inverse — SHORT the coverage surge, 1d
horizon — grades +13.82%/sig (n=7, both halves strongly positive, excess
+11.69% over the same-coin random-time null, mc_p 0.0005). Episodes: xyz:IBM
+14.0, xyz:CXMT +7.8, xyz:MU +7.8, xyz:ASML +6.9, xyz:SKHY +5.9, xyz:DELL
+3.6, CASHCAT +50.7. Dropping the CASHCAT outlier the read stands at +7.52%/sig,
excess +6.65%, mc_p 0.004 on 6 pure tokenized-equity episodes. Interpretation:
in this window, a sudden coverage surge on an xyz equity marked a next-day
sell-off — buy-the-news was the wrong side, consistently.

Caveats that keep this a CANDIDATE, not an edge:
- n=7 (6 ex-outlier), one episode short of the grader's min-n 8 — and FROZEN:
  the recorder died in the 07-18 demolition, no new rows accrue.
- One 8-day clean-epoch window (ledger restarted 2026-07-12 after the
  relevance-bug fix; pre-fix rows are archived tainted, not graded here).
- All signals cluster in one news regime (semis/AI-hardware coverage wave).
- Selection: this cell was found BY conditioning on the direct book failing —
  the same data cannot also validate the flip. Only fresh forward episodes can.

## Decision for the operator (evidence does not decide this alone)

The breaking-surge-short evidence is frozen at n=7: the news_catalyst_live
module (Google News coverage reads) was demolished 2026-07-18 by operator-
approved manifest, and news_ta_quadrant records a different signal (AI polarity
quadrants, not coverage surges). Options:
1. **Rebuild a minimal zero-capital recorder** (restore the coverage-surge read
   from git `8ca189f^`, record SHORT-side rows only, no order path, no config
   resurrection beyond the recorder block). Settles the cell at n≥8-15 within
   weeks. Shadow recorders are normally self-approve, but this reverses a piece
   of an operator demolition, so it waits for a yes.
2. **Let it expire.** The attention-fade synthesis stays as a documented
   hypothesis; re-test only if a future news lane exists anyway.

## Question

For a refuted directional strategy, does taking the **exact opposite side** of
the same point-in-time signal produce a tradeable edge?

This is deliberately narrower than tuning a new strategy. Every candidate
keeps its original entry timestamp, horizon, stop, and funding treatment. Only
`long` <-> `short` changes. This prevents a losing rule from being silently
turned into a different, untested rule.

## Reproducible harness

`scripts/shadow_inverse_status.py` reads existing shadow ledgers without
writing to them or placing orders. It uses the production forward grader
(`hermes_trader.agents.shadow_ledger.grade_records`) and caches each public
candle/funding series once per coin. That means inverse trades retain the
normal same-side stop simulation, round-trip cost tiers, episode de-duplication
and funding PnL.

A matched same-coin random-time null (2000 bootstrap draws over each coin's
fetched window, one random entry per episode per draw, price-only @12bps on
both sides) runs by default; `--meta KEY=VALUE` filters the ledger before
inversion, `--null 0` disables the null, `--seed` fixes the RNG. Reproduce:

```sh
python3 scripts/shadow_inverse_status.py --book premium_fade_short --book neg_funding_fade --book young_listings
python3 scripts/shadow_inverse_status.py --book news_catalyst --meta breaking=true
python3 scripts/shadow_inverse_status.py --book news_catalyst --meta breaking=false   # control
```

## Evidence recorded before this audit

| priority | refuted rule | direct forward result | inversion status before exact ledger run |
|---:|---|---|---|
| 1 | `premium_fade_short` | -6.865%/episode at 12 bps, 9 de-duplicated episodes, both time halves negative | Untested exact inverse. The separate historical D5 study found a *different* premium-rich short setup positive, so this live rule must not be conflated with it. |
| 2 | `news_catalyst` long | -8.65%/signal in the locked n=34 forward window; full ledger -0.761% over 2,214 resolved signals | Untested exact inverse. The recorder includes many non-breaking/no-surge rows, so a raw reversal could be a signal-quality artefact rather than a news alpha. |
| 3 | `young_listings` continuation | Forward ledger -3.499%/signal at 12 bps, n=95 resolved, both halves negative | Backtest already rejects both continuation directions. The only positive response was a one-day **long crash fade**, which is stronger in mature listings and therefore is not a young-listing edge. |
| 4 | `neg_funding_fade` short | Removed after forward loss and funding-correct grading | Existing pre-registered W-F3 independently supports the inverse: an extreme-negative funding settlement, z>=3, followed by a 2h **long**, net +35.5 bps at 25 bps per day-clustered event (n=53, p=0.017, both OOS halves positive). It dies near 50 bps, so it is MARGINAL/shadow-only, not live-ready. |
| 5 | LLM-signed EDGAR at first 1h bar | -0.58/-0.99/-0.45% at +1h/+4h/+24h, all negative before cost | The result says the sign is already in the price and then mean-reverts, but its n=49 sample lacks a pre-registered inverse cell. Inversion is a new latency-specific hypothesis, not a deployment. |

## Guardrails (kept from the first pass, two added)

0. **`classify()` labels from this harness are NOT validation.** The label has
   no null; both first-pass "VALIDATED" results failed the matched null when it
   was added. Only excess>0 at mc_p<0.05 counts, and even that is a candidate.
0b. **The standing auto-flip order does NOT apply here.** It covers forward
   shadow books graded at their bars. An inverse counterfactual on a dead
   ledger is neither — flipping live off it would deploy an untested rule.
1. Treat `VALIDATED` from the inverse harness as a **candidate**, never an
   automatic live flip. It needs a fresh forward book, shortability/liquidity
   review, and the normal pre-committed kill bar.
2. Do not reverse results that merely say an overlay added no lift, a stop was
   too tight, or a feature was redundant. Those are not failed direction calls.
   Examples: vol targeting, sector buckets, and the FIP smoothness layer.
3. Do not count a reversal that is already tested and refuted. Young-listing
   crash-continuation short is exactly the inverse of crash-fade long and is
   negative in all 12 registered cells.
4. Keep funding in every funding-gated inverse. A negative-funding short pays
   funding; omitting it previously overstated that book by about one percentage
   point per signal.
5. The grader's same percentage stop is intentional. A reversed side has a
   different intrabar stop path; `inverse return = -original return` is not a
   valid shortcut.

## Sources

- `research/rebuild_2026_07_18/MINIMAL_SYSTEM.md`
- `research/alpha_swarm/findings/W-F3.md`
- `research/alpha_swarm/findings/W-Y1_young_listings.md`
- `research/alpha_swarm/findings/W-P3_llm_signed_edgar.md`
