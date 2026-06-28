# Continuation features — do they separate RUNNERS from FIZZLES at the early breakout?

**Agent:** `continuation_features` (MANTA swarm). Read-only, cache-only (`movers_dataset.json`, 160 coins, 1h, ~83d Apr6–Jun28).

## Setup
- **Entry set (early breakout):** close makes a new 48h high, extension off the 48h base-low in **[3%, 30%]** (early = not yet >30% extended), 24h cooldown per coin. → **2,781 events**.
- **Label:** RUNNER = forward 48h MFE ≥ 50% from the i+1 open. **Base runner rate = 0.90% (25/2781)**; ≥100% = 0.2% (6). Most early breakouts fizzle — as expected.
- **Lookahead-safe:** all features computed on bars ≤ i, fill i+1 open. Trail-exit policy walks only bars > entry.
- **Trail policy:** enter long i+1 open, percentage trailing stop (swept 10/15/20/30%), 48h horizon, costs at 12bps.

## Feature runner-rate lift (full sample, top tercile of the "good" tail)
| feature | direction | lift vs base | monotonic? |
|---|---|---|---|
| **vol_trend** (mean vol last 6 / prior 18) | high | **1.80x** | yes |
| **rs** (coin 24h ret − BTC 24h ret) | high | **1.68x** | yes |
| **coin_ret** (raw 24h momentum) | high | **1.68x** | yes |
| **accel** (ret last 3 − ret prior 3) | high | 1.56x | yes |
| **upper_wick** (avg upper-wick ratio last 4) | low | 1.56x | yes |
| spikeness (last-bar vol / recent mean) | low | 1.20x | weak |
| base_range / blen / hc | — | ≤1.20x or wrong sign | no |

**`rs` == `coin_ret` to 3 decimals — subtracting BTC adds nothing here. The signal is raw trailing momentum, not divergence-vs-BTC.** "Tight base" hypothesis is **refuted**: the WIDER base-range tercile had MORE runners (0.0151 vs 0.0065). Base length and consecutive-higher-closes are noise. One-bar spike (spikeness) is the weak version of vol_trend and not robust.

## Combined continuation score (rs + vol_trend + accel + low-wick, percentile-summed)
| cut | n | runner rate | lift |
|---|---|---|---|
| base | 2781 | 0.90% | 1.0x |
| top 50% | 1391 | 1.58% | 1.8x |
| top 25% | 696 | 2.01% | 2.2x |
| **top 10%** | 279 | **2.87%** | **3.2x** |

## OOS — the lift is REAL in both time halves
Per-feature top-tercile runner-rate lift, split by time (H1 = Apr–mid-May, H2 = mid-May–Jun):
| feature | H1 lift | H2 lift |
|---|---|---|
| vol_trend | 1.94x | 1.50x |
| rs / coin_ret | 1.76x | 1.50x |
| accel | 1.76x | 1.50x |
| upper_wick (low) | 1.41x | 1.50x |

The **runner-rate separation survives OOS** in both halves. This is a genuine continuation tell on *which* early breakouts are more likely to keep going.

## But forward EV is NOT robustly tradeable
| slice | ALL early-breakouts EVnet | SCORE top25% EVnet | runner rate |
|---|---|---|---|
| full | +0.20% | +0.15% | 2.0% |
| **H1** | **+1.34%** | **+2.88%** | 3.5% |
| **H2** | **−0.94%** | **−1.76%** | 0.9% |

- Sign-flip across halves → **noise/regime by `alpha_lib` rules.** The asymmetric trail is +EV only in the trending half. The baseline (unconditioned) breakout EV ALSO sign-flips, so the regime drives tradeability; the score just **amplifies whatever the regime is** (helps in H1, hurts more in H2).
- **Random-breakout null** (matched count) EV ≈ +0.1% to +0.7% — the score's conditioned EV (+0.15% top25%) does **not** exceed the null. The runner-rate lift is real; the EV edge over a random breakout is not.
- **Payoff math:** runners avg MFE +94%, realized +46% on the 15% trail; fizzles avg −0.09%. The asymmetric payoff only clears costs when runner density is high enough (H1 base 1.2%). In the chop half (H2 base 0.58%, runners halve 17→8) there aren't enough runners to pay for the fizzle drag, and momentum-selected fizzles bleed slightly MORE (entered higher up the move).

## VERDICT
**Partial real tell, NOT a standalone tradeable edge.**

There IS a genuine, OOS-robust continuation separator: **rising volume trend, trailing momentum (relative strength, but the BTC-subtraction is redundant), positive acceleration, and small upper wicks** lift the runner-capture rate ~1.5–1.9x per feature and ~3x combined at the top decile, in both time halves. The "tight long base" and candle-count folklore are refuted.

But the lift does **not** convert into robust forward EV: the enter-early + trail payoff sign-flips across time halves and does not beat a random-breakout null on EV. The continuation tell tells you *which breakout is likelier to run*, but firing breakouts is only +EV when the regime is trend-on; the score concentrates capital into the regime rather than overcoming it.

**Actionable use:** treat the score (rising-vol + momentum + clean-structure + low-wick) as a **capital-allocation / ranking overlay** to prioritize which early breakouts to take **once a trend-on regime gate is satisfied** — not as a standalone entry trigger. This is consistent with the house findings: momentum is RELATIVE and regime-dependent, exit/regime is the lever, candle-space is saturated. No new standalone alpha; a modest, regime-gated ranking refinement at best.
