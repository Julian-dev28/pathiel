# B5 realized_vol_mean_reversion

## Hypothesis
Realized vol mean-reverts, so size the book UP after a vol spike (vol about to fall, trends
resolve) and DOWN into compression — a sizing edge, not a directional one.

## Exact rule
- Book: daily market-neutral XS momentum (L=14, top/bottom-8), 285 days.
- Market vol state at decision = avg coin 5d realized vol over returns ending at t-1, terciled.
- Overlays: up_after_spike (1.5x high / 1.0 mid / 0.5 low), down_after_spike (inverse). vs flat.

## Results
Book Sharpe conditioned on PRIOR vol tercile: **low 4.37**, mid 1.58, high 2.45 (non-monotonic;
book likes CALM tape, not post-spike).

| variant | annSharpe | maxDD | meanRet% | lift | h1 | h2 |
|--|--|--|--|--|--|--|
| flat | 2.791 | -24.0% | 0.401 | +0.000 | 4.43 | 1.05 |
| up_after_spike | 2.225 | -27.3% | 0.369 | **-0.566** | 3.80 | 0.21 |
| down_after_spike | 2.931 | -24.7% | 0.433 | +0.140 | 4.50 | 1.63 |

## VERDICT: REFUTED
Deciding number: the stated rule (size UP after a vol spike) returns **-0.566 Sharpe lift** — the
wrong direction. The book performs best in LOW prior vol (Sharpe 4.37 vs 2.45 high), so the only
positive overlay is the INVERSE (size up in compression, +0.140 lift), and even that just reflects
the neutral book's mild preference for calm conditions rather than a vol-mean-reversion timing edge.
No tradeable vol-mean-reversion sizing edge in the claimed form. Survivor-biased upper bound.
