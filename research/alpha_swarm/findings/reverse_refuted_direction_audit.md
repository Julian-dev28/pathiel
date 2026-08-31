# Reverse refuted-direction audit

**Status:** SETTLED + LIVE 2026-07-20 (graded with matched same-coin
random-time nulls, 2000 draws, price-only @12bps both sides; one surviving
cell wired live same day per operator order). **Started:** 2026-07-20
(Codex), completed + shipped same day (Claude).

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
| mover_pass → short | 23 | 17 | **+6.75%/sig** | +6.70 / +6.79 | **+6.89%** | **0.0005** | SURVIVES, no outlier dependency (17 episodes, max 24.2%, spread crypto+xyz). **LIVE.** |
| mover_b15_up → short | 23 | 10 | +11.37%/sig | +20.73 / +2.01 | +10.35% | 0.0005 | CASHCAT-dependent: ex-outlier (n=8) drops to +4.02%/sig, second half flips NEGATIVE (-1.55%). NOT wired — keep recording. |
| extreme_fade (disarmed subset) → short | 225 | 5 | -4.30%/sig | -7.20 / -2.37 | -6.97% | 0.798 | REFUTED. Disarmed regime has no edge in EITHER direction — the skew-arm is doing its job. |
| majors_swing → short | 21 | 1 | — | — | — | — | No verdict possible — dedups to 1 independent episode (signals cluster same-coin/overlapping-horizon). |

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

## Second sweep, same session: mover_pass / mover_b15_up / extreme_fade(disarmed) / majors_swing

Extended the audit to every remaining refuted-or-never-validated recorder
with enough n to grade. mover_pass's inverse is the CLEANEST result of the
entire audit — robust, diversified, no outlier — and is wired live alongside
news_surge_short (see below). mover_b15_up's inverse looked equally strong
headline (+11.37%/sig) but is CASHCAT-dependent: dropping that one episode
cuts the mean by more than half and flips the second OOS half negative — the
same failure mode premium_fade_short showed earlier ("edge all in one half"),
just hidden by an outlier instead of thin n. NOT wired; keep recording,
revisit with the outlier excluded or at higher n. extreme_fade's DISARMED
subset (the population where zero capital currently trades, since the W-B2
arm blocks the long) has no edge in either direction — its inverse (short)
is -4.30%/sig, both halves negative, mc_p 0.80. This is a clean, reassuring
null: the arm isn't just blocking a good long, the disarmed regime really is
untradeable. majors_swing's 21 raw signals dedup to 1 independent episode —
its signals cluster on the same coin within overlapping horizons, so no
verdict is possible; not evidence either way.

## LIVE 2026-07-20 (operator order: "rebuild then rewire live, $20 / 10x")

`pathia/agents/news_surge_short_live.py` restores the demolished
coin_catalyst() read and rewires it SHORT-only, bounded per the evidence:

- **Records every scan candidate** (crypto AND xyz equities, breaking and
  non-breaking) to a NEW ledger book `news_surge_short` — never conflated
  with the old LONG `news_catalyst` ledger, which stays historical evidence.
- **Trades only breaking reads on xyz: equities.** The n=7 sample was 6/7
  equities and one crypto outlier (CASHCAT +51.7%) — crypto breaking reads
  keep recording at zero capital until they earn their own forward n≥8. This
  is the bounded implementation of the operator's order: real capital only
  where the evidence actually points.
- **Geometry is the exact graded shape**: 15% stop, 1-day hard DSL timeout,
  no trail — the same shape `reverse_refuted_direction_audit` graded, so the
  ledger keeps grading the trade it is taking. $20 notional, 10x leverage
  (operator-specified, not the family's usual 12x).
- Wired: loop call-site (`scripts/trading_loop.py`, after `unlock_short_runin`,
  before `whale_flow`), `_ACTIVE_CLAIM_BOOKS` + `BOOK_PRIORITY` + dashboard
  row. 12 new gate tests (`tests/test_news_surge_short_live.py`) pin the
  equity-only trade gate, crypto-records-but-never-trades behavior, and the
  exact DSL geometry.
- **Mandatory review at 8 resolved forward episodes** (the grader's own
  min_n): `python scripts/shadow_status.py --book news_surge_short` — REFUTED
  or EV25<0 flips `shadow_only=true` same day, no debate, symmetric with
  every other thin-evidence live flip in this file.
- The attention-fade synthesis (control also positive-excess) is NOT wired —
  only the pre-registered breaking-equity-short cell trades. Extending to
  "short every scan candidate" would need its own forward ledger first.

### Stop-geometry correction (found post-deploy, same session)

The books shipped configured at a 15% stop — the width the audit graded — but
that stop **could never fire**. `executor.py:1023` clamps the on-exchange
backup stop to `entry * (backup_sl_max_frac_of_liq / leverage)` = 6% at 10x,
and liquidation sits at 10%. So the advertised 15% stop was decoration; the
real exit was a 6% clamp, and the DSL's `max_loss_pct: 15` was dead weight
sitting beyond both. This is the rally_exhaustion lesson in a new costume:
**the live stop width was not the graded stop width.**

Re-graded both books at their TRUE clamped width before leaving them live —
the edge survives, which is why they stayed on rather than being pulled:

| book | graded @15% | actual live @6% (10x) |
|---|---|---|
| news_surge_short | +12.09%/sig, halves +8.50/+15.69, win 1.00 | **+10.59%**, halves +5.50/+15.69, win 0.875 |
| mover_pass_short | +6.74%/sig, halves +6.70/+6.79, win 0.824 | **+6.09%**, halves +5.21/+6.88, win 0.765 |

Config now states the width that actually executes (`stop_pct: 6.0` at 10x,
exactly at the clamp boundary so nothing is silently substituted), which also
restores the DSL stop as a real second line of defense if backup-SL placement
ever fails (`sl_missing`). Pinned by
`tests/test_live_book_wiring_integrity.py::test_reverse_refuted_books_configure_a_reachable_stop`.

**SYSTEMIC — every other fixed-notional book has the same gap** (audited, NOT
changed: each was graded at its own width and altering six live books at once
is an operator decision, not a follow-on fix):

| book | cfg stop | lev | liq at | ACTUAL stop |
|---|---:|---:|---:|---:|
| rally_exhaustion | 25% | 12x | 8.3% | **5.0%** |
| crash_continue_div_short | 20% | 12x | 8.3% | **5.0%** |
| engulf_short | 20% | 12x | 8.3% | **5.0%** |
| funding_spike_short | 15% | 12x | 8.3% | **5.0%** |
| unlock_short | 15% | 12x | 8.3% | **5.0%** |
| mover_pass (pass_live) | 15% | 12x | 8.3% | **5.0%** |

Every one of these was validated at 15-25% and trades at 5%. The
sweep-stop-width discipline says that gap can invert an edge — each should be
re-graded at 5% the way the two new books were, and any that dies there needs
its leverage cut (a 20% stop needs ≤3x) or its verdict revisited. Not done
here; flagged for the operator.

**Second book, same order:** `pathia/agents/mover_recorders.py` gained
`record_mover_pass_short` — the cleanest inverse in the whole audit (n=17, no
outlier dependency, both halves +6.70/+6.79, mixed crypto+equities). Same
$20/10x/15%-stop/1d-hold geometry, own ledger book `mover_pass_short`, own
dedup key so it never blocks (or gets blocked by) the existing `mover_pass`
long recorder's row on the same PASS event. Wired: loop call-site (right
after `record_mover_pass`, same `action == "none"` branch), claims + priority
+ dashboard, `mover_recorders.pass_short_live` config block. Review bar: n=8.
Collision coverage: `tests/test_live_book_wiring_integrity.py` (book-name /
state-file uniqueness scan, now covers inline `shadow_ledger.record(...)`
literals too — mover_recorders.py has no `_BOOK_NAME` constant, the original
scan pattern would have missed it) + a real-`ClaimsRegistry` test proving
`mover_pass` and `mover_pass_short` cannot both hold the same coin.

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
(`pathia.agents.shadow_ledger.grade_records`) and caches each public
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

## Six-book re-grade at the REAL 5% stop + recorder sweep (2026-07-20, final)

Re-graded the older fixed-notional books at the 5% width they actually
execute (vs the 15-25% they advertise). The clamp has been HELPING, not
hurting — every book with data improves at the tighter stop, because these
are short books whose losers run:

| book | cfg stop | n | EV25 @cfg | EV25 @REAL 5% | delta | verdict |
|---|---:|---:|---:|---:|---:|---|
| mover_pass | 15% | 17 | -6.49% | **-3.52%** | +2.97 | **REFUTED** at its real stop (halves -4.01/-2.85). Already shadow_only — no capital at risk. |
| engulf_short | 20% | 3 | -5.39% | -2.39% | +2.99 | negative lean, n<8, LIVE — watch |
| unlock_short_runin | 15% | 4 | +4.83% | +4.83% | 0.00 | positive, n<8 (stop never reached) |
| crash_continue_div_short | 20% | 0 | — | — | — | 4 rows, 0 resolved |
| rally_exhaustion | 25% | — | — | — | — | **never fired a single row** |
| funding_spike_short | 15% | — | — | — | — | **never fired a single row** |

Correction to the earlier risk note: rally_exhaustion and funding_spike_short
are DORMANT, not risky — they carry $0 real exposure because they have never
opened. The "$240 notional with no EV estimate" framing overstated it.

### Recorder sweep — what merits capital

| cell | n | EV25 | halves | excess | mc_p | verdict |
|---|---:|---:|---|---:|---:|---|
| **attention-fade: SHORT any scan candidate** | **178** | **+2.21%** | **+0.55/+4.12** | **+1.75pp** | **0.0005** | passes every bar |
| news breaking-equity short (LIVE) | 8 | +10.46% | +5.50/+15.69 | +9.76pp | 0.0005 | live, now at min-n |
| whale_flow INVERSE | 58 | +0.66% | -0.02/+1.60 | +1.01pp | 0.031 | MARGINAL — fails both-halves, barely clears fees |
| whale_flow as-recorded | 58 | -1.22% | -0.06/-2.12 | -0.91pp | 0.934 | REFUTED (confirms 07-17 interim) |
| mover_b15_up inverse | 10 | +11.37% | +20.73/+2.01 | +10.35pp | 0.0005 | CASHCAT-dependent — ex-outlier one half flips negative |
| young_listings inverse | 18 | +2.25% | +2.42/+2.09 | +0.82pp | 0.323 | tape beta |
| majors_swing inverse | 1 | — | — | — | — | dedups to n=1 |

**Recommendation: do NOT wire a fourth book.** The attention-fade cell passes
every statistical bar we have, but it is not a new edge — it is the SAME
factor already expressed by three live books (news_surge_short,
mover_pass_short, young_mover_short), all of which fade attention on
overlapping populations. A fourth expression adds concentration, not
diversification, and its capacity is infeasible anyway: 178 episodes / 8 days
= ~22/day, which at $200 notional is $4,400/day against a $136 account. The
three live books already record this population forward; let the evidence
mature rather than stacking correlated risk.

## Regime test (operator theory, 2026-07-20) — half confirmed, and it points elsewhere

Operator: "longs are up, shorts are down — market has a regime and we didn't
consider it." Tested both halves.

**The attention-fade shorts are NOT regime-dependent.** Split the n=126
young-mover sample by same-day tape direction (median return of mature xyz
names that day):

| tape that day | n | SHORT return | win |
|---|---:|---:|---:|
| DOWN | 76 | +2.58% | 62% |
| UP | 50 | **+2.92%** | **68%** |

The edge is marginally BETTER in an up tape and the sample is balanced
(76/50), so it was not harvested from the collapse. The matched same-day
baseline used when the cell was wired already controlled for tape; this
confirms it directly. Live losers (MINIMAX -53.9%, ZHIPU -3.0%) are variance
on n=2 against a 68%-win edge, not regime failure. NO regime gate warranted.

**The momentum book IS regime-exposed — and that is where the damage is.**
The xyz tape fell -9.43% over 14d (19% of names up) then flipped UP over 2d
(+0.86%, 71% up). On that reversal:

| prior-7d quintile | prior | 2d bounce |
|---|---:|---:|
| weakest (xs **shorts** these) | -23.96% | **+1.43%** |
| strongest (xs **longs** these) | +3.63% | -0.34% |

Loser-minus-winner spread **-1.77pp against the L/S book** — the textbook
Daniel-Moskowitz momentum crash, naming the exact live losers (SNDK +5.45%,
NBIS +6.97%, KIOXIA +6.86% bounces against our shorts).

**The real finding is sizing, not signal.** xs_xyz deploys 10 legs at once off
the GLOBAL strategy_book_equity_frac, which put **12.0x gross on the xyz dex**
($119/leg x 10 on $99.52 equity). Market-neutral gross is only safe while the
hedge holds, and a momentum crash is precisely when it does not:

| crash | cost at 12x | % of xyz equity |
|---:|---:|---:|
| 1.77pp (the one that just happened) | $10.57 | 10.6% |
| 5pp | $29.86 | 30% |
| 10pp | $59.71 | 60% |
| 20pp (inside historical range) | $119.42 | **120% — ruin** |

FIX: per-book `xs_xyz_equities.equity_frac = 0.04` (new override, falls back to
the global path when unset) → $48/leg, $478 gross, **4.8x**. A 20pp crash now
costs 48% of the dex instead of 120%. Deliberately per-book so the crypto
xs_momentum book — the only consistently profitable book we have (+$43.35 net,
83% win, 4.3x payoff over 30d) — is untouched.

## The distribution nobody had looked at (30d, 342 closed trades)

win rate **36%**, payoff ratio **1.00x** (avg win $1.92 / avg loss $1.91),
expectancy **-$0.547/trade**. Break-even at 1:1 needs 50%; we are 14pp short.
Median win $0.60 vs median loss $0.70 on 11.4 trades/day — most trades are
noise. Per-book, one book earns and the rest pay for it:

| book | net 30d | win% | payoff |
|---|---:|---:|---:|
| xs_momentum | **+$43.35** | 83% | 4.3x |
| main-engine | **-$172.33** | 30% | 1.16x |
| vol_breakout_long/wide (ripped) | -$75.45 | ~40% | 0.3x |
| engulf_short | -$12.88 | 0% | — |
| extreme_fade | -$9.01 | 0% | — |

main-engine is 157 of the 342 trades and -$172 of the -$187. Fixing the
strategy mix, not the fee, is the lever.
