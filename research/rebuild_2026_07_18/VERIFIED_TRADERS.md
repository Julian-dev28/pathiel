# VERIFIED_TRADERS — what provably profitable HL wallets actually do

2026-07-18. READ-ONLY research; no live config touched. All numbers below are
self-computed from raw Hyperliquid `/info` responses, not trusted from any
ranking. Raw data + scripts (leaderboard snapshot, per-wallet fills/funding
JSON, `screen_candidates.py`, `fetch_wallets.py`, `fetch_named.py`,
`analyze_wallets.py`, `analysis.json`, `pooled.json`):
`/private/tmp/claude-501/-Users-julian-dev-Documents-code-hermes-trader/79fc4494-d85c-45f4-b633-3f5e34eb766a/scratchpad/`.
Fetches were paced at one weight-20 call per 4s (~300 weight/min, shared-IP
budget respected; 1 transient 429 over ~350 calls, recovered by backoff).

Prior art honored (none of it repeated): the guru-translation lane is 0-for-3
on our data — KillaXBT range/deviation REFUTED (`research/killa_xbt/`), the
"25/25 leaderboard" families 0/7 validated with an unlocatable source
(`research/alpha_swarm/findings/W-Q_25_strategy_leaderboard_audit.md`,
`W-R_leaderboard_replication.md`). This wave inverts the method: no narrative,
no indicator translation — only fills.

## 0. Method and attrition funnel

1. **Source**: official `POST /info {"type":"leaderboard"}` returns 422 (not
   publicly exposed — matches `hermes_trader/agents/hyperfeed.py:80`).
   `https://stats-data.hyperliquid.xyz/Mainnet/leaderboard` works: 40,401
   rows, day/week/month/allTime {pnl, roi, vlm} + accountValue per address.
2. **Copyability screen** (deterministic, `screen_candidates.py`): allTime pnl
   ≥ $200k AND month pnl > 0 AND month edge ≥ 3 bps of volume AND month vlm
   $1M-$400M AND accountValue ≥ $20k. Kills HLP-style market makers (sub-bp
   edge on $B volume) whose fills are also unretrievable (HL serves only the
   ~10k most recent fills per wallet). 642 pass; top 40 by month pnl taken.
3. **Verification, not ranking** (`fetch_wallets.py`): per wallet,
   `portfolio` allTime pnlHistory → self-computed trailing-90d PnL; then
   `userFillsByTime` (aggregateByTime, paginated, 120d window) → realized
   closedPnl − fees recomputed from fills; then `userFunding` (30d).
   Bars: 3-month self-computed PnL ≥ $100k AND ≥ 200 fills.
4. **Attrition**: 40 candidates → 10 dropped (wallet younger than 90d,
   unverifiable), 3 dropped (self-computed 3mo PnL NEGATIVE despite a top-40
   month rank — e.g. 0x939f9503: month +$2.78M, 3mo −$1.60M), 3 below $100k,
   2 cut at the top-22 cap, 1 dropped at the fills bar (0x15a4f009: 18 fills).
   **21 verified wallets** + **3 operator-named wallets** (section 5) = **24**.

The month-leaderboard rank is a hot-streak detector, not a skill detector:
6 of 40 top-ranked candidates failed self-computed verification outright and
10 more were too young to verify at all.

## 1. (a) The verified-wallet table

Self-computed, address-prefixed, reproducible. `3mo PnL` = portfolio-series
delta over trailing 90d (uniform verification metric; includes unrealized).
`win net` = closedPnl − fees recomputed from the retrievable fills window
(window length varies with the 10k-fill API depth: days for high-frequency
wallets, full 120d for position traders). `mdd/av` = max drawdown of the 3mo
cumulative-pnl series over account value.

| wallet | acct value | 3mo PnL (self) | fills win net (days) | fills | mdd/av | age d | style (measured) |
|---|---|---|---|---|---|---|---|
| 0xe867fbda | $65.3M | +$22.82M | +$0.39M (120) | 3,092 | 31% | 884 | position trader, 5.2h med hold |
| 0x0Ddf9bAe (pension-usdt.eth) | $19.1M | +$17.61M | +$8.31M (23) | 10,932 | n/a* | — | 3-coin ETH/BTC campaigns |
| 0xf822fa0f | $62.0M | +$11.54M | +$0.00M (10) | 11,103 | 18% | 128 | maker HFT (1,158 fills/day) |
| 0xb83de012 | $106.8M | +$11.42M | −$0.06M (2) | 10,746 | 1% | 506 | taker HFT (4,592 fills/day) |
| 0xcf90cfec | $14.2M | +$9.86M | +$5.04M (43) | 463 | 25% | 114 | campaign whale, 19d holds |
| 0x469E9A7f (Shadow Wallaby) | $16.3M | +$6.99M | +$3.78M (37) | 11,393 | n/a* | — | 83% win scalp-swing, 17.6h |
| 0x3dc90837 | $23.0M | +$6.99M | +$0.60M (22) | 12,000 | 9% | — | HFT mixed |
| 0xd4758770 | $52.7M | +$6.97M | −$0.84M (11) | 10,529 | 33% | 541 | maker HFT (98% maker) |
| 0x9e8b1e51 (= Mirrorly "Venom Gibbon") | $11.9M | +$6.40M | +$4.19M (111) | 5,160 | 16% | — | taker trend, payoff 2.94 |
| 0x218a65e2 | $4.4M | +$5.40M | −$2.22M (101) | 4,706 | 56% | 261 | maker whale, unrealized-heavy |
| 0x6bb97143 | $5.3M | +$5.06M | +$0.22M (95) | 672 | 17% | — | campaign, 15d holds |
| 0x5b5d5120 | $46.2M | +$4.69M | −$0.01M (0.2) | 10,865 | 4% | 506 | pure MM (51k fills/day) |
| 0xf02d16a2 | $24.8M | +$4.58M | −$3.22M (68) | 12,000 | 3% | 100 | fast scalps, unrealized-heavy |
| 0x84abc08c | $9.7M | +$4.25M | −$0.04M (4) | 10,483 | 22% | — | maker HFT |
| 0x60a8c761 | $30.1M | +$3.17M | +$0.02M (32) | 10,760 | 8% | 114 | maker HFT |
| 0xda744273 | $7.4M | +$2.92M | +$0.73M (113) | 2,900 | 2% | 254 | 81% win swing-short, 11.6d holds |
| 0xa312114b ("NAKED SHORTS ONLY") | $21.9M | +$2.77M | +$1.12M (10) | 11,270 | 25% | 611 | fast two-sided, payoff 9.75 |
| 0x4f7634c0 | $4.6M | +$2.76M | +$0.13M (7) | 10,293 | 64% | 394 | HFT |
| 0x48d826da | $7.5M | +$2.50M | +$1.25M (26) | 10,743 | 50% | — | short specialist, 91% win, 24h |
| 0x484bc160 | $21.9M | +$2.29M | +$0.67M (8) | 10,692 | 9% | — | maker HFT |
| 0xFce053a5 (Crystal Mole) | $3.9M | +$2.17M | +$3.79M (114) | 2,591 | n/a* | — | BTC/ETH campaigns, 75% maker |
| 0xd1dd6d99 | $5.5M | +$2.06M | +$0.25M (71) | 8,787 | 25% | 562 | swing, 18h holds, 20/25 short |
| 0xebe126ad | $24.4M | +$1.48M | +$2.69M (28) | 10,284 | 42% | 121 | funding harvester (+$1.21M funding/30d) |
| 0x45d26f28 | $34.0M | +$1.15M | +$0.26M (69) | 11,070 | 22% | 926 | low-vlm carry ($5.4M month pnl on $2.1M vlm) |

\* named wallets' mdd not computed in the same pass; pension and Crystal Mole
are in CURRENT drawdown (leaderboard month: −$4.22M and −$0.41M).
Excluded: 0x15a4f009 (+$2.11M 3mo but 18 fills — position-parker, no
structure to mine). Sum of self-computed 3mo PnL across the 24: **+$148M**.

## 2. (b) Structural contrast — winners vs our bot

"Structured winners" = the 8 wallets with ≥ 10 complete flat-to-flat episodes
in the fills window (0xe867fbda, 0x9e8b1e51, 0x469E9A7f, 0xf02d16a2,
0xda744273, 0xa312114b, 0x48d826da, 0xd1dd6d99; 282 episodes pooled,
+$8.48M realized net among them). Every other verified wallet is either HFT/MM
(uncopyable) or a campaign whale whose holds exceed the observation window.
Our column: `PNL_FORENSICS.md` (2,721 fills, 855 episodes, 06-01→07-17).

| metric | structured winners (median of wallets) | OUR BOT | factor |
|---|---|---|---|
| median hold | **11.6h** (p25 0.9h, p75 55.9h) | **43 min** | ~16x longer |
| trades/week | **4.0** (range 1.0-11.8) | **127.8** | 32x fewer |
| win rate | **63%** | 49% | — |
| payoff (avg win / avg loss) | **2.32** | 0.91 | 2.6x |
| maker share of traded notional | **49%** (4 of 8 wallets ≥ 59%) | **0%** (2,721/2,721 taker) | — |
| avg fee paid | **1.38 bps** of notional | 3.07 bps | 2.2x cheaper |
| median episode max-notional / acct value | **3.8%** (p90 per wallet 0.2-1.5x) | main engine 0.5 eq × 12x = **600%** | ~150x smaller |
| top-3 coins share of |pnl| | **64%** (they own 1-3 markets) | scattered over 80+ coins, 57 HIP-3 | — |
| episodes with multi-fill accumulation | **93%** (adds or partial fills — patient builds) | mostly single-print entries | — |
| entry timing | 71% in 09-22 UTC (baseline 54%), **85% weekdays** (baseline 71%) | uniform 24/7 | — |
| funding PnL, 30d | collect: median +$117k; harvester +$1.21M; SW pays −$146k | **−$1.40** total | — |
| liquidations in window | **3 total across 8 wallets** | 5 (−$73.35, incl. −$64 xyz:BIRD) | — |
| long/short split | 134 long eps (+$2.75M) / 148 short (+$0.64M) — two-sided | 726 long (−$189) / 129 short (−$61) | — |

Two winner morphologies, no third: **(A) high payoff, moderate win** (Venom
Gibbon: 35% win, payoff 2.94, 100% TAKER — proof taker flow isn't fatal if
the payoff is asymmetric) and **(B) high win, wide stops, patient**
(0xda744273: 81% win, payoff 7.07, 11.6d holds, 72% maker; 0x48d826da: 91%
win on 23 consecutive shorts). Our bot's 49%/0.91 sits in neither — it is
the exact signature of a coin-flip paying taker both ways (the
forensics' conclusion, now confirmed against 24 external books).

Majors are not the whole story: median majors (BTC/ETH/SOL) share of traded
notional is only 20%. The real pattern is CONCENTRATION — top-1 coin is
21-98% of each winner's |pnl| (HYPE, ETH, BTC, xyz:CL oil, xyz:MSTR,
xyz:SKHX). Winners specialize in one or a few markets; none of them shotgun
80 coins.

## 3. (c) Concrete parameter corrections for the v2 books

Ranked. Each is implementable in `hermes_trader/v2/` (MINIMAL_SYSTEM.md
spec) without touching the current live loop.

1. **Maker-first entries: post-only limit at (or inside) the touch, taker
   fallback only after N minutes unfilled.** Evidence: structured winners run
   49% median maker notional (5 of 8 > 59%); our 0%/3.07bps vs their
   1.38bps average fee. Both surviving v2 entry books are non-urgent by
   construction — extreme_fade bids INTO a −12% daily close and
   funding_spike_short offers into a crowded pump; a resting post-only order
   is the natural expression, and at 3-5d holds a minutes-scale fill delay is
   noise. Expected save ≥ 2.5bps/side = ~5bps/RT, i.e. ~40% of the measured
   12.3bps median round-trip cost (FEE_VIABILITY.md §0). Implementation:
   `executor.py` entry path gains `entry_style: post_only_then_cross` with a
   30-min cross fallback; ledger `meta.fill_style` records which path filled,
   so the maker-fill rate is itself measured.
2. **Per-position notional cap as a fraction of equity: ≤ 25% of equity per
   position (HL $10.50 min order permitting), delete every path that lets one
   leg exceed 1x equity.** Evidence: winners' median episode max-notional is
   3.8% of account value, per-wallet p90 0.2-1.5x — nobody healthy runs our
   main-engine 6x-equity single legs ($1.7k XRP legs on a $150 account;
   xyz:BIRD −$64 in 8 minutes was 8x). At $150 equity this cap reads: one
   $20-37 ticket per coin, hard. This is the winners' actual "leverage
   discipline": position/equity fraction, not the leverage dial.
3. **Hold-time floor as a config invariant, not a habit: no v2 book may have
   expected hold < 6h, and the DSL exit may not realize a non-stop exit
   before 2h.** Evidence: winners' median hold 11.6h (p75 2.3d); our sub-2h
   bucket destroyed −$385.59 while everything held > 2h made +$134.96; W-G1
   independently measured sub-1h holds −0.67% gross (adverse selection).
   The v2 books (3d/5d/5d) already comply — the invariant exists to stop the
   next fast book from regressing this. Gate test: assert every enabled
   book's `hold_days × 24 ≥ 6`.
4. **Concentrate the book: at most 3 concurrently-open signal coins
   account-wide (claims registry cap), and kill the 57-coin HIP-3 shotgun.**
   Evidence: winners' top-3 concentration is 64% of |pnl|; they own 1-3
   markets each. Ours: 80+ coins traded, HIP-3 net −$136.71 spread across 57
   names with zero specialization. Note this is NOT "majors only" (median
   winner majors share is just 20%; three winners' #1 market is a HIP-3
   name they clearly know) — it is "few markets, deep familiarity, sized
   properly". For v2: extreme_fade + funding_spike keep their whole scan
   universe but the ClaimsRegistry gains `max_concurrent_signal_coins: 3`.
5. **(Optional, weaker) Weekday/session entry tilt.** Winners open 85% of
   episodes on weekdays (baseline 71%) and 71% between 09-22 UTC. Cheap
   config: block NEW entries 22:00 Fri → 00:00 Mon UTC (exits always live).
   Evidence is correlational tilt, not a measured counterfactual — ship it
   only as a recorded shadow field (`meta.weekend`) first and let the ledger
   split it.

## 4. (d) `wallet_follow` shadow recorder — spec

New zero-capital hypothesis for the ledger (this is a NEW data source —
exactly the frontier the alpha-hunt swarm said candle-space saturation
demands). No trading, no config flags, records only.

**Follow set (from this document, hard-coded with evidence):** the 9 verified
copyable wallets — median hold ≥ 4h or campaign style, self-computed 3mo
PnL ≥ $2M: `0xe867fbda…`, `0x9e8b1e51…`, `0xda744273…`, `0x48d826da…`,
`0xd1dd6d99…`, `0xcf90cfec…`, `0x0Ddf9bAe…`, `0x469E9A7f…`, `0xFce053a5…`
(full addresses in `scratchpad/analysis.json`; excluded: all HFT/MM wallets —
0xa312114b and 0xf02d16a2 fail the ≥ 4h bar). Refresh the set quarterly by
re-running this pipeline; never mid-quarter (that would be selection drift).

**Mechanics.** Each 30-min v2 signal cycle: `clearinghouseState` per wallet
(weight 2 × 9 = 18 per 30 min ≈ 0.6 weight/min — negligible). Persist last
`szi` per (wallet, coin) in `.state/wallet_follow_state.json`.

- **OPEN**: |szi| 0 → nonzero, or sign flip. Record via
  `shadow_ledger.record("wallet_follow", coin=…, side=…,
  signal_bar_t=<current completed 1h bar t>, entry_ref_px=<mid at
  detection>, horizon_days=3.0, stop_pct=20.0, meta={wallet, wallet_ntl,
  wallet_entry_px, consensus_n})`. entry_ref_px at DETECTION (not the
  wallet's fill) so copy latency is priced into the graded return.
- **ADD** (position grows ≥ 25%): meta-only row (`side="meta_add"`,
  horizon_days=0 → ungradeable by design), for later add-following analysis.
- **CLOSE** (szi → 0): meta-only row with the wallet's exit ts/px, so an
  exit-copy variant can be graded offline later. The PRIMARY grade stays the
  standard fixed-horizon/stop simulate_exit — deterministic and comparable
  with every other book in the ledger.
- **Dedup**: recorder-side, one open signal per (coin, side) account-wide
  until resolved (consensus_n counts additional wallets instead of new
  rows); ledger-side, `dedup_episodes` collapses any residual same-coin
  clusters inside the horizon. Horizon/stop (3d/20%) chosen to match the
  validated extreme_fade structure and the followed wallets' 12h-2.3d
  central hold mass; both are recorded per-row so a re-grade can sweep them.
- **Sides**: record longs AND shorts (winners are two-sided: 148 of 282
  pooled episodes were shorts).

**Grading bar (pre-committed, standard):** `scripts/shadow_status.py`
verdict — VALIDATED requires mean@12bps > 0 AND both OOS halves > 0 AND
survives 25bps (`shadow_ledger.classify`), at ≥ 30 resolved episodes
(promotion doctrine, not the min_n=8 default). PLUS the matched null this
book must beat: for each graded signal, ≥ 2,000 same-coin random-time
entries drawn from the trailing 90d (the `W-U1_unlock_backtest.py::mc_pvalue`
pattern), same horizon/stop, price@12bps; require p < 0.01. REFUTED at the
standard bar → delete the recorder same day (operator refuted-rule).
Expected signal rate from measured trades/week of the follow set (~2-6/wk
each, heavy dedup overlap): roughly 5-15 signals/wk → the 30-episode bar is
reachable in 3-6 weeks.

**Placement**: ~150-line module in v2 (`v2/recorder.py` sibling or
`wallet_follow_recorder.py`), driven by the 30-min cycle. It must NOT import
`hyperfeed` (dead in v2) — raw `_http_post`/`hl_client` only. Ships with
gate tests: state-file round-trip, open/flip/close delta detection on
synthetic clearinghouse payloads, dedup invariant, and a
no-live-imports test (`test_not_imported_by_live_modules` pattern).

**Why it can fail honestly (record anyway):** copy latency up to 30 min
eats fast-moving entries; whale entries may BE the price impact (their fill
moves the market, our detection buys the top of their print); the follow
set is survivorship-selected today. All three failure modes are exactly what
the forward grade + matched null measure. Zero capital is at risk while it
answers.

## 5. Operator-doc traders: claims vs fills truth

The operator-supplied doc (source: Mirrorly Q2 2026 quarterly insight —
https://mirrorly.xyz/knowledge-base/quarterly-insight-q2-2026) named three
traders. Unlike W-Q, THE SOURCE EXISTS and the headline claims verify. The
name→address mapping came from Mirrorly's own portal search API
(`portal.mirrorly.xyz/api/leaderboard/search`, exposes `exchangeIdentifier`)
plus ENS; cross-checked against the official HL leaderboard snapshot.
Bonus cross-identification: two wallets my pipeline independently mined ARE
Mirrorly-featured traders — 0x9e8b1e51 = "Venom Gibbon", 0xe867fbda =
"Hyper Giraffe".

| claim (doc) | fills/portfolio truth | verdict |
|---|---|---|
| pension-usdt.eth: $10.27M Q2 realized, 3 positions / 6 trades, 100% win, >45d holds | Address 0x0Ddf9bAe… (ENS + HL displayName "Penision Fund"). Self-computed 3mo +$17.61M, allTime +$36.8M — REAL. But "6 trades" = 10,932 fills over the retrievable 23d; exactly 3 symbols (ETH +$7.60M, BTC +$0.71M realized in-window); max ETH position $107.5M = **564% of account value** — and the wallet is **−$4.22M in the last 30d** (leaderboard month window) | headline TRUE, texture FALSE |
| Shadow Wallaby: ~$7.6M Q2, 72 positions / 19.5k trades, 79.2% win, 2-10d holds | Address 0x469E9A7f… (Mirrorly API). Self-computed 3mo +$6.99M — REAL. Fills: 82.8% episode win rate (claim ✓) but payoff only 0.32, median flat-to-flat hold 17.6h, 66% maker, 35 coins, max position 295% of av (xyz:SP500 $48M), and it PAYS funding (−$146k/30d) | mostly TRUE |
| Crystal Mole: $4.19M from 4 positions, 125+d avg holds | Address 0xFce053a5… (Mirrorly API). Self-computed 3mo +$2.17M (Q2-window difference + current −$0.41M month) — directionally real. Fills: 9 coins, BTC +$2.45M / ETH +$1.56M realized over 114d, 75% maker, 23 fills/day patient campaigns, max BTC position 315% of av | plausibly TRUE, magnitude window-sensitive |
| "8-15% equity per position, 5-12x leverage, 1-1.5% risk per trade" | Appears NOWHERE on the Mirrorly source pages (checked Q1+Q2 insights). Fills contradict it: all three run single-position notional at **100-560% of account value**. The risk numbers were invented somewhere between the source and the doc | FABRICATED texture |

Lesson (same as W-Q, milder): operator docs get their HEADLINES from real
sources and their PARAMETERS from vibes. Copy nothing that isn't in fills.

## 6. (e) Honest caveats

1. **Survivorship by construction.** Screening on month>0 AND allTime>0
   selects wallets whose last 30 days worked. The structural table describes
   what winners look like, not what causes winning — losing wallets may also
   scale in patiently and rest limit orders. The wallet_follow forward grade
   is the survivorship-free instrument; the parameter corrections lean on
   agreement with OUR OWN measured forensics (sub-2h bucket −$385, fee math),
   not on the winners' stats alone.
2. **Rank ≠ verify, demonstrated in-sample**: 6 of the top-40 month-ranked
   candidates failed self-computed verification (3 negative over 3mo, 3
   below bar); 10 more were < 90d old. pension is −$4.2M this month while
   being the doc's star.
3. **Capacity mismatch.** These are $4M-$107M accounts. Maker-tier rebates,
   funding harvesting at size ($1.2M/30d), and the market impact of their
   own entries do not exist at $150. What transfers is SHAPE (hold time,
   position fraction, fee route, payoff structure, concentration), not
   alpha.
4. **Copy latency + impact**: wallet_follow detection lags entries by ≤ 30
   min (~2-4% of the follow set's median hold). If their edge is
   sub-30-minute, the recorder will measure ~0 and refute itself — that is
   working as intended.
5. **API depth truncation**: only the ~10k most recent fills are served per
   wallet, so HFT wallets' fill windows cover 0.2-10 days and their
   episode stats are unmeasurable (censored counts reported in
   `analysis.json`); the uniform verification metric is the portfolio-series
   3mo delta. Campaign whales' holds are right-censored (true holds LONGER
   than reported).
6. **"Adds" conflate scaling-in with partial fills** of large resting orders
   (aggregateByTime merges same-block fills only). Read "93% multi-fill
   episodes" as patient execution, not necessarily deliberate pyramiding.
7. **Same-operator multi-wallet** (sub-accounts, vaults) cannot be excluded;
   two Mirrorly-listed wallets appearing in my independent top-40 suggests
   the copy-trading platforms and the leaderboard surface the same small
   population.
8. **Window regime**: all of this is 2026 Q2-Q3 tape (post-top drawdown +
   chop). Winner structure in a violent bull leg may differ; re-run the
   pipeline (one command per script, ~20 min paced) before trusting it in a
   different regime.

## 7. Repro

```
# 1. snapshot + screen (network: 1 GET, ~33MB)
curl -s https://stats-data.hyperliquid.xyz/Mainnet/leaderboard -o leaderboard_raw.json
.venv/bin/python screen_candidates.py            # -> candidates.json (top 40)
# 2. verify (network: paced ~350 /info calls, ~20 min; progress at /tmp/wallet-mine/progress.log)
.venv/bin/python fetch_wallets.py                # -> wallets/<addr>.json, survivors.json
.venv/bin/python fetch_named.py                  # -> the 3 operator-doc wallets
# 3. extract (no network)
.venv/bin/python analyze_wallets.py              # -> analysis.json, pooled.json + tables
```
Scripts live in the scratchpad path at the top of this file; they are
session artifacts, not repo code. The full per-wallet metric set (including
complete follow-set addresses) is preserved durably at
`research/rebuild_2026_07_18/verified_traders_data.json` (a copy of
`analysis.json`). If wallet_follow is approved, the recorder + its tests
become repo code and the follow-set addresses are frozen into it.
