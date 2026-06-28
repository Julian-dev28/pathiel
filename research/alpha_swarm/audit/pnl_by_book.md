# PnL attribution by strategy book

Read-only. Source: HL `userFillsByTime` (realized closing fills) joined against
`~/.hermes-trader-session-log.jsonl` book-open footprints.
Script: `scratchpad/pnl_by_book.py --days N` (self-contained; env-load + paginated
fills + session-log parse). Generated 2026-06-28.

`net = closedPnl - fee` per position episode. An "episode" = one flat->flat round
trip per coin (signed-size walk over fills; partial closes accumulate into the
episode). PnL is realized only.

---

## HEADLINE

1. **The account is one book.** 99.9% of episodes (1420 / 1422 all-time, 236 / 238
   over 14d) are **main-engine** (AI-research longs/shorts). Every dedicated book
   except two never put on live risk in this window — they ran in SHADOW
   (`opened=0`) or never executed. The realized book = main-engine.

2. **It's the LONGS bleeding, not the shorts.** Opposite of the prior edge-profile
   note for this window:
   - main-engine **LONG**: 1119 eps, gross **-137.42**, fees 115.43, **net -252.85**
   - main-engine **SHORT**: 301 eps, gross **+97.82**, fees 49.66, **net +48.16**
   Shorts are the only thing carrying positive PnL (gross AND net). Longs lose
   gross and then fees bury them.

3. **Fees are a primary killer.** All-time gross is roughly breakeven (**-41.17**);
   fees of **165.15** drag the account to **net -206.32**. $115 of those fees sit
   on the long book alone (1119 long round-trips = heavy churn).

4. **The dedicated strategy books are inert, not bleeding.** rally_exhaustion: 1
   live short (net -0.62). extreme_fade: 1 live long (net -1.01). engulf_short,
   crash_continue_div_short, premium_fade_short, hail_mary_short, xs_momentum,
   external_alpha: **zero** live episodes (all shadow / never executed). They
   cannot be credited or blamed for anything yet — there is no live sample.

---

## Per-book net PnL

### All-available (2026-05-02 -> 2026-06-27, 3787 fills, 1422 episodes)

| book | #eps | gross | fees | net | win% | L net | S net |
|---|---|---|---|---|---|---|---|
| main-engine | 1420 | -39.60 | 165.09 | **-204.69** | 51 | 1119L -252.85 | 301S +48.16 |
| extreme_fade | 1 | -0.97 | 0.04 | **-1.01** | 0 | -1.01 | — |
| rally_exhaustion | 1 | -0.60 | 0.02 | **-0.62** | 0 | — | -0.62 |
| crash_continue_div_short | 0 | — | — | — | — | — | — |
| engulf_short | 0 | — | — | — | — | — | — |
| premium_fade_short | 0 | — | — | — | — | — | — |
| hail_mary_short | 0 | — | — | — | — | — | — |
| xs_momentum | 0 | — | — | — | — | — | — |
| external_alpha | 0 | — | — | — | — | — | — |
| **TOTAL** | **1422** | **-41.17** | **165.15** | **-206.32** | | | |

### Last 14d (2026-06-14 -> 2026-06-27, 658 fills, 238 episodes)

| book | #eps | gross | fees | net | win% | L net | S net |
|---|---|---|---|---|---|---|---|
| main-engine | 236 | -58.39 | 25.12 | **-83.52** | 44 | 214L -75.1 | 22S -8.4 |
| extreme_fade | 1 | -0.97 | 0.04 | **-1.01** | 0 | -1.01 | — |
| rally_exhaustion | 1 | -0.60 | 0.02 | **-0.62** | 0 | — | -0.62 |
| **TOTAL** | **238** | **-59.96** | **25.18** | **-85.14** | | | |

14d note: short-side advantage narrows — over the last two weeks longs (-75) AND
shorts (-8) both lose, win% drops to 44%. The +shorts edge is an all-window
effect, mostly earned earlier in May/early-June.

---

## Per-coin (main-engine, all-time)

Worst (net):
| coin | #eps | net | gross | fee | L/S |
|---|---|---|---|---|---|
| BTC | 44 | -44.31 | -29.79 | 14.52 | 26L/18S |
| XRP | 24 | -41.97 | -34.42 | 7.55 | 11L/13S |
| xyz:BIRD | 7 | -21.81 | -21.26 | 0.56 | 5L/2S |
| ZEC | 43 | -21.30 | -14.80 | 6.49 | 38L/5S |
| DOGE | 22 | -17.18 | -10.83 | 6.36 | 6L/16S |
| xyz:ARM | 16 | -16.84 | -16.50 | 0.34 | 15L/1S |
| ENA | 11 | -15.88 | -13.76 | 2.13 | 5L/6S |
| xyz:KR200 | 8 | -15.21 | -14.88 | 0.33 | 7L/1S |
| TON | 25 | -14.10 | -11.75 | 2.35 | 16L/9S |
| TRUMP | 15 | -13.14 | -11.16 | 1.98 | 11L/4S |

Best (net):
| coin | #eps | net | gross | fee | L/S |
|---|---|---|---|---|---|
| xyz:CBRS | 27 | +39.15 | +40.17 | 1.03 | 24L/3S |
| ETH | 32 | +35.00 | +45.18 | 10.17 | 16L/16S |
| ADA | 38 | +25.56 | +34.32 | 8.76 | 26L/12S |
| LIT | 18 | +23.45 | +25.28 | 1.82 | 18L/0S |
| JTO | 24 | +23.19 | +25.53 | 2.34 | 24L/0S |
| BNB | 29 | +19.19 | +25.26 | 6.07 | 25L/4S |
| ONDO | 10 | +18.85 | +21.71 | 2.86 | 7L/3S |
| xyz:PLTR | 9 | +16.84 | +17.12 | 0.28 | 9L/0S |
| SOL | 43 | +11.81 | +23.02 | 11.21 | 28L/15S |

BTC (-44) and XRP (-42) churn hardest (44 and 24 round-trips) and carry the
heaviest fee drag. High episode-count coins (BTC 44, ZEC 43, SOL 43) confirm the
fee-from-churn story: SOL is +23 gross but $11 of fees clip it to +12.

---

## Attribution / matching logic (and its uncertainty)

**The problem.** Every live book routes its opens through the SAME executor
(`execute_fn`) as the main engine (see `rally_exhaustion_live.py:306`,
`extreme_fade_live.py:225`, `xs_momentum_live.py:366`). So an "Open Long/Short"
fill is identical whether main-engine or a book placed it. The fill stream alone
cannot label the book.

**The join.** For each book I collect (coin, side, ts) "open-intent" footprints
from its own session-log events, then attribute a reconstructed episode to a book
iff that book has a footprint with the same coin, matching side (when present),
and ts within **±15 min** of the episode open. First match in a fixed priority
order wins; everything unmatched -> main-engine.

Footprint sources per book:
- short books (rally/engulf/crash/premium/hail_mary): events with `opened>=1` -> `candidates[]`.
- extreme_fade: `extreme_fade_candidates` with `shadow=false` -> `signals[]`.
- xs_momentum: `xs_rebalance` with `shadow=false` -> `open_long`/`open_short`.
- external_alpha: `external_alpha_exec` with `executed=true` -> coin.

**Why the unmatched % is high (99.9%) and that's correct, not a miss.** I checked
the raw footprints directly:
- rally_exhaustion has exactly **1** real footprint (XPL short, the single
  `opened=1` event) -> 1 episode. Correct.
- xs_momentum: every `xs_rebalance` is `shadow=true` -> 0 footprints. Never live.
- external_alpha: every `external_alpha_exec` is `executed=false`
  (reentry_cap / hip3_disabled) -> 0. Never executed.
- engulf/crash/premium/hail_mary: every event `opened=0` (shadow) -> 0.
- extreme_fade: `shadow=false` candidate stream IS noisy (≈1904 listings) but
  most are the same 2 coins (POPCAT short, TNSR long) re-listed each cycle, and
  it is polluted with unit-test fixtures leaking into the live log
  (coins `AAA/BBB/C1/DEDUP/HELD/STALE/OPEN/FRESH` — ignore these). Of the real
  candidates, only EIGEN/VVV/WLD/POPCAT/TNSR ever had episodes, and only **one**
  opened within ±15 min of an extreme_fade listing with matching side. The rest
  of those coins were opened by the main engine hours away from any fade signal,
  so they stay main-engine. Correct.

**Residual ambiguity (be honest).**
- EIGEN/VVV/WLD appear both as extreme_fade candidates and as main-engine
  episodes. The ±15 min window decides; widen it and 1-3 of these long episodes
  could flip from main-engine to extreme_fade (each is small, ~$1-3 net). It does
  not move the headline.
- The 15-min window is a judgment call. Books call `execute_fn` essentially
  synchronously with their log event, so the true latency is seconds; 15 min is
  generous and errs toward catching, not missing, a book open. No double-open is
  possible (the cross-book claims registry blocks it), so at most one book owns a
  coin at a time.
- Bottom line: the attribution is effectively exact here because the non-main
  books simply did not trade live. The fuzzy join only matters for ≤3 tiny
  extreme_fade-vs-main long episodes.

**Episode reconstruction caveat.** 4 episodes (all-time) / 2 (14d) were still
open at the window edge (`open` flag in the table) — their realized PnL so far is
counted; unrealized is not. Survivorship: delisted coins that closed before the
fill window are absent.
