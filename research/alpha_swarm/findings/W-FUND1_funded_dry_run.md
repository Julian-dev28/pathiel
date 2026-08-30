# W-FUND1 — dry run of a funded account: it would trade almost nothing

**Date:** 2026-08-30
**Method:** simulate the live path at funded equity, before any money is at risk.

## Finding 1 — the majors allowlist blocks 92-100% of every live book

Each live book's own forward ledger, checked coin-by-coin against the live
`coin_allowlist`:

| book | signals | tradable under majors | share |
|---|---|---|---|
| news_surge_short | 1899 | 33 | **1.7%** |
| news_surge_multi | 1853 | 31 | **1.7%** |
| social_trending | 312 | 24 | **7.7%** |
| unlock_short_runin | 15 | **0** | **0.0%** |

`unlock_short_runin` — the ONE book with a real n=408 backtest (W-U2) — can
trade **zero** of the signals it was validated on. Its universe is
ALT, ARB, GRAM, GRASS, INIT, KAITO, MEGA, RESOLV, SEI, STRK, XPL, ZK, ZORA, ZRO.
Not one is a major, and by construction never will be: tokens with large
scheduled unlocks are early-stage tokens.

The news books live on CASHCAT (216 signals), ACE (96), xyz:ZHIPU (80), PUMP,
HEMI, KAITO. The edge was MEASURED on the altcoin tail. The majors restriction
does not make those books safer — it makes them inert.

## Finding 2 — the dust floor supports one book, not four

```
each book        $20 notional @ 1x = $20.00 margin
all four         $80.00 margin
free-margin floor 10% of equity must stay free
equity needed    $88.89 for all four to hold simultaneously
at the $25 floor only 1 of 4 books can hold a position
```

Funding to exactly the $25 dust floor buys a system where the first book to
fire consumes the entire budget and the other three are margin-blocked.

## The conflict this exposes

Two directives are in force and they are incompatible as implemented:

1. restrict the universe to majors (capacity and slippage)
2. run only books validated on forward evidence

The books were validated on the tail. Applying (1) to them does not de-risk
them; it silences them.

The capacity argument is real but it is about SIZE, not about eligibility. A $20
order does not move CASHCAT. The majors allowlist is the right instrument for a
book sized as a fraction of equity, and the wrong one for four bounded $20
books that already pass through per-trade liquidity floors.

## What actually protects these books today

`min_market_volume_usd` 700k, `min_short_volume_usd` 20M, `min_hip3_volume_usd`
700k, plus `min_short_volume_usd_override` defaulting to 250k per book. Those
are per-trade liquidity gates, applied to the coin at the moment of entry. They
are the correct protection at $20 notional. None of them was removed.

## Recommendation (operator decision, not applied)

Scope the majors allowlist to what it was reasoned for and let the volume floors
do the liquidity work:

- **Option A** — clear `coin_allowlist`, keep every volume floor. The books
  trade the universe they were validated on. Restores ~100% of signals.
- **Option B** — keep majors, delete all four books. They cannot fire, so
  leaving them "live" is the shadow state by another name.
- **Option C** — keep majors, raise per-book notional, accept the system trades
  a handful of times a year on 1.7% of its signals.

A is the only option where the validated evidence and the live configuration
agree. B is honest. C is the worst of both.

Whichever is chosen, fund to at least **$89**, not $25, or three of the four
books are margin-blocked behind the first one that fires.
