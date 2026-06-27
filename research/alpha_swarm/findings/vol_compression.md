# vol_compression — volatility-compression breakout with stop-width sweep

## Hypothesis
When realized vol/range CONTRACTS into a coil (ATR squeeze), the next-bar
range-expansion breakout is directional — but earlier naive breakout tests
killed it with a tight stop, so a WIDE stop should rescue the edge. Sweep stop width.

## Exact rule tested
- **Universe**: 40 liquid perps (survivor-biased — positive results are upper bounds).
- **Squeeze filter**: ATR(14) at bar i in the **bottom tercile** of its own trailing
  50-bar ATR distribution.
- **Breakout (decided on bar i, lookahead-safe)**: close > prior N-bar high → LONG;
  close < prior N-bar low → SHORT. (N=20 on 1h, N=10 on 1d.)
- **Entry**: filled at **i+1 open** (never peeks bar i close into a same-bar fill).
- **Exit**: `alpha_lib.sweep_stop()`, stop width **{8,15,20,25,40}%**, horizon H,
  optional TP at 1.5x / 2.0x the stop, else horizon-end close.
- **Horizons**: 1h → H∈{24,48,72}; 1d → H∈{3,5,7}.
- **Regime**: BTC daily close vs 20d SMA (up/down). Tested breakouts WITH vs AGAINST.
- **Cost/OOS**: `alpha_lib.summarize` — slippage tiers 0/6/12/25/50 bps, first/second TIME halves.

## Key result tables (EV = mean per-trade % return; h1/h2 = first/second-half mean @12bps)

### 1h, all-regime, no TP (the best timeframe), H=48, n=2340
| stop | EV 0bps | EV 12bps | EV 25bps | EV 50bps | win | h1 | h2 |
|-----:|--------:|---------:|---------:|---------:|----:|------:|------:|
|  8%  | +0.142  | +0.022   | -0.108   | -0.358   |0.506| -0.172| +0.217|
| 15%  | +0.099  | -0.021   | -0.151   | -0.401   |0.517| -0.060| +0.019|
| 20%  | +0.050  | -0.070   | -0.200   | -0.450   |0.517| -0.095| -0.046|
| 25%  | +0.024  | -0.096   | -0.226   | -0.476   |0.517| -0.112| -0.080|
| 40%  | +0.020  | -0.101   | -0.231   | -0.481   |0.517| -0.101| -0.100|

**EV is BEST at the TIGHTEST stop and degrades monotonically as the stop widens** —
the exact opposite of the squeeze-fade "wide stop rescues it" lesson. That lesson is
for mean-reversion/fade bets; a breakout is a *continuation* bet, so a wider stop just
lets the (reverting) losers run further. The wide-stop fix does not apply here.

### Regime split (1h, H=48, no TP) — the only positive aggregates, and why they are fake
| config | stop | EV 12bps | h1 | h2 |
|--------|-----:|---------:|------:|------:|
| WITH btc regime    | 8%  | -0.333 | +0.338 | -1.038 |
| AGAINST btc regime | 15% | +0.510 | -0.252 | +1.280 |

The "AGAINST regime" book looks great in aggregate (+0.4–0.6% @12bps across all stops)
but it is **entirely a second-half phenomenon** (h1 ≈ -0.3, h2 ≈ +1.3). Pure regime luck
from one window, not a stable edge. WITH-regime is the mirror image (h1 +0.34, h2 -1.04).
Both sign-flip → noise.

### 1d, all-regime, no TP, H=5, n=757
| stop | EV 12bps | h1 | h2 |
|-----:|---------:|------:|------:|
|  8%  | -0.708 | -1.418 | +0.011 |
| 15%  | -0.905 | -1.452 | -0.351 |
| 25%  | -1.021 | -1.870 | -0.161 |
| 40%  | -1.165 | -1.991 | -0.328 |
Daily is uniformly, strongly negative at every stop. Wider = worse.

### Take-profit variants (1h H48)
Best cell: TP=1.5x, 15% stop → EV0 +0.166, **EV12 +0.046**, but h1 -0.010 / h2 +0.102
(sign-flip) and **dies by 25bps (-0.084)**. TP=2.0x at 8% stop: EV12 +0.013, also flips.
No TP variant is robust both-halves + survives 25bps.

## Stop-width surface verdict
EV does **not** improve with wider stops; it **peaks at the tightest stop (8%) and
decays monotonically** to 40% on every timeframe and regime. The premise that a wider
stop rescues a compression breakout is **false** for this signal — confirming a breakout
is a momentum/continuation trade, not a fade, so the squeeze-fade stop-width lesson is
the wrong tool.

## VERDICT: **REFUTED**
No configuration is ROBUST +EV across both time-halves at a realistic 12bps.
- The single best both-halves-survivable aggregate (1h H48, 8% stop, no TP) is **+0.022%
  @12bps but sign-flips** (h1 -0.17 / h2 +0.22) and goes **negative by 25bps (-0.108)**.
- Every positive aggregate (AGAINST-regime, TP variants) is second-half-driven and/or
  dies before 25bps slippage.
- Daily compression breakouts are strongly negative everywhere (-0.7% to -2.0%).

This reproduces the prior project finding (price-pattern breakouts are -EV OOS); the
compression filter + stop-width sweep does **not** rescue it. The deciding number: best
candidate = **+0.022% @12bps, h1 -0.17 (sign-flip), dead by 25bps**.

Caveat: survivor-biased universe (dead coins absent), so even these negatives are an
upper bound — the live edge would be worse, not better.
