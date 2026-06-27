# floor_short — can `min_short_volume_usd` drop below $50M?

**Question.** The SHORT-side liquidity floor is $50M (live short books already override to $20M for
rally_exhaustion). Does $20M hold, and can it go lower (into the 5-20M band)? Tested the 3 live short
triggers on NATIVE coins, net of each band's slippage, OOS both halves, scored as EXCESS over a
regime+band matched random-SHORT null.

**Data.** `marginal_dataset.json`, NATIVE only, 1d candles, ~250d window (2025-10-20 → 2026-06-27).
BTC 20d-trend regime from MAIN `dataset.json`. Tape is heavily bearish: **173 BTC-down days vs 108 up**
(this flatters every short — the matched null carries that down-beta, excess strips it out).
Entry is lookahead-safe: signal on close of bar i, **fill at bar i+1 open**, walk i+1..i+hold.
Short ret = (entry−exit)/entry; stop at entry·(1+stop) → ret = −stop; else exit last close.

**Band population (the binding structural fact).**
| band | slip | native coins (n) | which |
|---|---|---|---|
| 5-20M | 25 bps | **12** | SUI NEAR PUMP WLD JTO LIT AVAX FARTCOIN kPEPE ADA BNB TAO |
| 20-50M | 12 bps | **3** | AAVE XPL XRP |
| 50M+ | 6 bps | 5 | BTC HYPE ETH SOL ZEC |

The override band (20-50M) has only **3** native coins today. It can't be validated on its own — its
results are 2-3-coin artifacts. The 5-20M band (12 coins) is the real test, and it *brackets* 20-50M:
if a trigger clears 5-20M's heavier 25bps slippage, it clears 20-50M's 12bps a fortiori.

A "CLEARS" flag requires ALL of: net>0 after band-slip · both OOS halves >0 · excess>0 over the
matched null · n≥8. Slip multiplier swept {0.5, 1.0, 1.5}.

---

## 1) rally_exhaustion (+12%/2d & BTC-DOWN → short, wide stop)

Stop-width is decisive — exactly the documented "sweep the stop" lesson. A tight 15% stop banks the
squeeze and **inverts** the edge (OOS sign-flip); 20-25% holds.

| band | stop | hold | n | net%@1.0x | win | OOS h1/h2 | excess% | t | flag |
|---|---|---|---|---|---|---|---|---|---|
| 5-20M | 15 | 5 | 105 | −0.01 | .56 | +0.83 / **−0.84** | −0.33 | 0.0 | FAILS (tight-stop inversion) |
| 5-20M | **20** | 5 | 105 | **+1.73** | .66 | +1.76 / +1.70 | +1.28 | 1.2 | **CLEARS** |
| 5-20M | **25** | 5 | 105 | **+2.12** | .69 | +1.42 / +2.82 | +1.75 | 1.5 | **CLEARS** |
| 5-20M | 25 | 10 | 105 | +2.57 | .63 | +3.73 / +1.43 | +1.48 | 1.4 | **CLEARS** |
| 20-50M | 20-25 | 5/10 | 25 | +0.7…+2.0 | .52 | **+9…+11 / −6…−8** | — | 0.3 | FAILS (3-coin OOS-split) |
| 50M+ | any | any | 44 | mixed/neg | — | sign-flip | neg | — | FAILS (mega-caps keep ripping) |

- **5-20M with a 20-25% stop CLEARS at all 3 slip mults, both OOS halves, positive excess.** Modest
  t (~1.2-1.6). Per-coin: distributed across ~10 names (JTO +7.3, LIT +9.6, TAO +7.0, PUMP +3.2
  positive; WLD −2.9, FARTCOIN −1.8 negative) — not a single-coin artifact.
- 20-50M "looks" big (net +1-2%) but its OOS halves are a hard +10/−7 sign-flip on 3 coins → noise,
  not signal. Don't trust the 20-50M number in isolation; the 5-20M result is what validates the trigger.
- 50M+ FAILS — exhaustion is a mid-cap phenomenon; mega-caps that are +12% in a BTC-downtape keep going.

**Verdict — rally_exhaustion: lowest valid floor = $5M, but ONLY with a wide (20-25%) stop.** Tight
stops are −EV. Edge is real but modest (t~1.3).

## 2) crash_continue (−8%/2d & BTC-UP → short, hold 10)

The strongest, most robust short trigger. Clears every tested band.

| band | stop | n | net%@1.0x | win | OOS h1/h2 | null% | excess% | t | flag |
|---|---|---|---|---|---|---|---|---|---|
| 5-20M | 8 | 73 | +4.55 | .56 | +5.71 / +3.41 | 0.80 | +3.74 | **3.1** | **CLEARS** |
| 5-20M | 20 | 73 | +5.04 | .71 | +6.78 / +3.34 | 1.44 | +3.59 | **2.8** | **CLEARS** |
| 20-50M | 8 | 18 | +6.39 | .67 | +7.58 / +5.20 | 2.66 | +3.73 | 2.5 | **CLEARS** |
| 20-50M | 20 | 18 | +8.13 | .78 | +8.37 / +7.88 | 4.66 | +3.46 | **3.2** | **CLEARS** |
| 50M+ | 8 | 16 | +1.81 | .63 | +1.84 / +1.78 | 0.01 | +1.80 | 0.9 | CLEARS (thin edge) |
| 50M+ | 20 | 16 | +1.28 | .75 | +4.06 / −1.50 | 1.64 | neg | 0.4 | FAILS |

- **5-20M CLEARS strongly** (t~3, excess +3.7 over the BTC-up null, 8/11 coins positive, only JTO −3.2).
  Survives 25bps band-slip at 1.5x with room to spare (net +4.4%).
- 20-50M CLEARS (net +6-8%, both OOS halves +) but is **XPL-heavy** (XPL n13 +9.9%, AAVE n5 +4.0%) —
  concentrated by the band's 3-coin population, yet directionally agrees with the broad 5-20M result.
- 50M+ marginal — the continuation edge lives in lower-cap names, not mega-caps.

**Verdict — crash_continue: lowest valid floor = $5M.** Robust (t~3), large excess over the matched
BTC-up null, broad across coins, survives slippage. Strongest case for lowering the floor.

## 3) engulf (bearish full-body engulf → short next day, hold 1, stop 20)

| band | n | net%@0.5x / 1.0x / 1.5x | win | OOS h1/h2 | excess% | t@1.0x | flag |
|---|---|---|---|---|---|---|---|
| 5-20M | 201 | +0.54 / +0.42 / +0.29 | .60 | +0.46 / +0.37 | +0.50 | 1.0 | CLEARS (thin, slip-fragile) |
| 20-50M | 51 | +0.17 / +0.11 / +0.05 | .67 | **+1.90 / −1.49** | ~0.0 | 0.1 | FAILS (3-coin OOS-split) |
| 50M+ | 101 | +0.67 / +0.64 / +0.61 | .58 | +1.06 / +0.29 | +0.74 | 1.2 | CLEARS |

- 5-20M technically clears, but it's a **0.3-0.5% per-trade edge that thins toward the cost line** as
  slip rises (t falls below 1 at 1.5x). Positive but fragile.
- 20-50M FAILS (OOS sign-flip, 3-coin artifact).

**Verdict — engulf: keep at $20M+.** The 5-20M edge is too thin and slip-fragile to justify admitting
illiquid-band shorts on this trigger, and it adds no edge at 20-50M.

---

## RECOMMENDATION

**Lower `min_short_volume_usd` from $50M to $20M as the standing global floor** — and codify a
**per-trigger override to $5M for crash_continue and rally_exhaustion (wide 20-25% stop only)**.

Reasoning:
- **$50M is too high.** crash_continue and (wide-stop) rally_exhaustion clear their band slippage, both
  OOS halves, and the matched-null down-beta all the way down to the **5-20M** band. The floor is
  leaving real short edge on the table on mid-cap names (SUI/NEAR/JTO/LIT/WLD class).
- **$20M is the safe, fully-defensible single number now.** It's validated forward by the live override
  and backward here (crash_continue 20-50M t~3.2; bracketed by the broad 5-20M result). The 5-20M coins
  are recognizable mid-caps, not micro perps — the genuinely squeeze-prone bands (2-5M and below) stay
  excluded.
- **$5M is supported by the backtest but gated to two triggers**, not blanket, because: (a) **survivorship**
  bites hardest exactly here — a positive short EV on *surviving* 5-20M coins ignores the un-coverable
  squeeze/blowup tail of coins that died; the wide stop (20-25%) is the only protection and is mandatory;
  (b) rally_exhaustion's 5-20M edge is real but modest (t~1.3); (c) engulf at 5-20M is too thin to ride
  a global floor drop. A per-trigger $5M override (crash_continue + wide-stop rally_exhaustion) captures
  the validated edge without admitting weak engulf shorts on illiquid names.

**Do NOT** drop the global floor straight to $5M, and do NOT lower it for engulf or for any tight-stop
short. Promote $5M from override to global only after a forward shadow window confirms the squeeze tail
isn't worse live than the survivor-biased backtest implies.

**Net single number: `min_short_volume_usd = 20_000_000`** (global), with `5_000_000` as a documented
per-trigger override for crash_continue and wide-stop rally_exhaustion.
