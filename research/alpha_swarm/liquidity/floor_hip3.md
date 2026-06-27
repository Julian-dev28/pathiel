# floor_hip3 — can `min_hip3_volume_usd` ($700k) be lowered?

**VERDICT: KEEP $700k.** Lowering admits the $0.1–0.7M band, whose HIP-3 momentum/breakout
longs are -EV gross AND uniformly -EV net of the band's 120 bps round-trip spread, in every
parameter config and with OOS sign-flips where gross is marginally positive. The deciding
number: best net EV achievable in the marginal band across a 12-config sweep is **-0.86%/trade**
(its single gross-positive config, +0.34%, dies on the 120 bps spread). Nothing survives.

Read-only, cache-only. Dataset: `marginal_dataset.json`, 1d candles, HIP-3 = `dex != null`
(xyz/cash/hyna). Min usable history = 60 1d bars. Backtest: `floor_hip3_bt.py`.

---

## (a) History-coverage by band — NOT the decider, but reframes the question

24/7 perps: 1d candles are time-continuous (median spacing 1.0d, max gap 1.0d) — **no
session/weekend gaps** even on tokenized equities. 1h zero-volume hours are low (NVDA/SP500/GOLD
0%, TSLA 1%; RIVN worst at 11%). So "gappy equity sessions" is not a real problem here.

The real data-quality fact: **sparse history is a listing-AGE problem, not a volume-floor problem.**

| Band      | nHIP3 | 1d bars min/med/max | >=60 bars (usable) | too-short coins |
|-----------|-------|---------------------|--------------------|-----------------|
| 0.1-0.7M  | 16    | 27 / 152 / 204      | **13** (81%)       | GME 55, GBP 41, IBM 27 |
| 0.7-2M    | 16    | 2 / 100 / 207       | **8** (50%)        | BOT 2, BE 6, ZHIPU 6, QCOM 6, NOK 13, SMH 13, AVGO 25, RKLB 55 |
| 2-5M      | 14    | 6 / 155 / 226       | 10                 | several recent listings |
| 5-20M     | 14    | 19 / 200 / 228      | 10                 | |
| 20-50M    | 4     | 58 / 156 / 208      | 3                  | |
| 50M+      | 8     | 42 / 141 / 251      | 6                  | |

Key point: the **currently-admitted** $0.7–2M band has WORSE coverage (50% usable; BOT=2 bars,
three coins at 6 bars) than the marginal $0.1–0.7M band (81% usable). The volume floor does not
protect against 6-bar stubs — a recently-listed high-volume coin sails through it. **The right
tool for the stub problem is a separate `min_history_bars >= 60` gate, independent of the volume
floor.** This is a real finding but it argues for ADDING a history gate, not for moving the floor.

---

## (b) EV by band, HIP-3 longs, coins with >=60 1d bars (gross + net @ band-slip, mult sweep, OOS)

Each coin pays its own band's round-trip spread (50M+ 6bps … 0.1-0.7M 120bps). Lookahead-safe
(decide on bar i, fill at i+1 open). Trades pooled per band, time-sorted, split into OOS halves.

### Strategy 1 — 20-bar high breakout + volume burst (hold 5, trail 8%)
| Band      | coins | nTrd | gross% | net@0.5x | net@1.0x | net@1.5x | win | OOS h1 | OOS h2 |
|-----------|-------|------|--------|----------|----------|----------|-----|--------|--------|
| 0.1-0.7M  | 13 | 28 | -1.78 | -2.38 | **-2.98** | -3.58 | 0.21 | -3.86 | -2.09 |
| 0.7-2M    | 8  | 22 | -1.35 | -1.70 | -2.05 | -2.40 | 0.27 | -1.38 | -2.72 |
| 2-5M      | 10 | 26 | -1.26 | -1.49 | -1.71 | -1.94 | 0.38 | -1.69 | -1.74 |
| 5-20M     | 10 | 46 | -0.59 | -0.71 | -0.84 | -0.96 | 0.48 | -1.31 | -0.36 |
| 20-50M    | 3  | 11 | -1.68 | -1.74 | -1.80 | -1.86 | 0.18 | -1.35 | -2.18 |
| 50M+      | 6  | 21 | -0.15 | -0.18 | -0.21 | -0.24 | 0.43 |  0.16 | -0.54 |

Breakout is -EV gross in **every** band. The marginal band is the WORST (-1.78% gross, 21% win).

### Strategy 2 — trend-cross trailing momentum (SMA 10/30, trail 10%, max-hold 20)
| Band      | coins | nTrd | gross% | net@0.5x | net@1.0x | net@1.5x | win | OOS h1 | OOS h2 |
|-----------|-------|------|--------|----------|----------|----------|-----|--------|--------|
| 0.1-0.7M  | 13 | 36 | -0.16 | -0.76 | **-1.36** | -1.96 | 0.28 | +1.13 | -3.86 |
| 0.7-2M    | 8  | 21 | -0.15 | -0.50 | -0.85 | -1.20 | 0.43 | -1.34 | -0.40 |
| 2-5M      | 10 | 25 | -0.80 | -1.03 | -1.25 | -1.48 | 0.24 | +0.05 | -2.45 |
| 5-20M     | 10 | 26 | +0.42 | +0.30 | +0.17 | +0.05 | 0.35 | -1.71 | +2.05 |
| 20-50M    | 3  | 7  | -1.60 | -1.66 | -1.72 | -1.78 | 0.29 | -6.34 | +1.75 |
| 50M+      | 6  | 13 | +0.17 | +0.14 | +0.11 | +0.08 | 0.23 | -2.02 | +1.94 |

The only gross-positive bands (5-20M, 50M+) are well ABOVE the floor and their OOS halves flip
sign (h1 negative, h2 positive) = noise, not a stable edge. The marginal band's apparent gross
near-zero is a mirage: OOS h1 +1.13 → h2 -3.86 (violent flip) and net@1x = **-1.36%**.

### Parameter robustness — marginal band never wins net (12-config breakout sweep)
Best marginal-band net across {lookback 10/20 × hold 3/5/10 × trail 6%/10%} = **-0.86%** (the one
config, lb10/hold3/trail10%, with gross +0.34%). Every other config is more negative; most are
gross-negative too. The admitted $0.7–2M band is also net-negative everywhere, but less bad.

---

## Why it fails (walk the failure mode)
The bot's trade size ($20–$200) makes market impact ~0, so the cost is the **bid-ask spread**,
modeled at 120 bps round-trip for sub-$700k coins. A HIP-3 long needs > +1.20% gross edge per
round trip just to break even in that band. The momentum/breakout signals deliver **negative**
gross edge there. There is no band, no config, no slippage assumption (even 0.5x) where the
marginal band clears zero net. This matches prior repo evidence ("lowering the LONG floor was
-EV"; "+EV band +0.15%@12bps dies by 25bps") — the 120 bps spread is an order of magnitude past
any edge present.

## Caveats
- Survivor-biased universe (today's liquid HIP-3 set) → these EVs are an upper bound; the true
  marginal-band EV is worse. A -EV result on survivor data is doubly damning.
- Candle-only test; the live stack has AI/signal gating on top. But the floor's job is to keep
  dead/thin listings out of the candidate pool in the first place — and the data says the
  $0.1–0.7M pool has no exploitable long edge to gate toward.
- Small n per band (7–46 trades). Not enough to *certify* a positive edge; more than enough to
  reject one, since every point estimate is negative.

## Recommendation
1. **KEEP `min_hip3_volume_usd = $700k`.** Do not lower. (A case exists to RAISE it — the
   $0.7–2M band is also net-negative — but that's a separate question and the live signal/AI
   layer may carry trend-aligned longs there; this candle test can't see that.)
2. **Add a separate `min_history_bars >= 60` (1d) gate.** That is the actual fix for the
   sparse-listing risk the brief worried about — and it bites the admitted $0.7–2M band
   (BOT 2 bars, 3 coins at 6 bars) harder than the marginal band.
