# W-H4 — WILDCARD: BTC down-shock x funding-crowding flush — REFUTED on cost
# (the conditional structure is REAL gross; it just doesn't pay 12bps)

## Hypothesis (invented + declared for this lane)
After a BTC 1h down-shock (> 2 sigma), alts with crowded LONG positioning
(top-tercile trailing-24h funding) underperform uncrowded alts (bottom tercile)
over the next 3-6h, because forced deleveraging concentrates where positioning
is crowded. Pure conditional/relative structure: the spread should exist ONLY
in shock hours. Spec pre-registered in `hypotheses/W-H4.py` docstring.

## Rule
Funding coverage window (2026-03-29..06-27) on the extended 1h cache. Event:
BTC ret < -2 sigma_168, 6h dedup -> 49 events (57 up-shock mirror events,
declared secondary). Book: LONG bottom-tercile crowd / SHORT top-tercile crowd
(trailing-24h mean hourly funding, lookahead-safe), fill open[i+1], hold {3,6}h,
per-event spread, cost 2x tier. MC null = identical spread at 993 non-shock
bars (this null IS the conditionality test).

## Results (per-event spread)
| cell | n | gross | net12(2x) | net25(2x) | OOS12 h1/h2 | mc_p | excess vs pool |
|--|--|--|--|--|--|--|--|
| DOWN-shock H=3 | 49 | **+0.228%** | **-0.012%** | -0.272% | **-0.154/+0.136** | **0.00167** | +0.270% |
| DOWN-shock H=6 | 49 | -0.051% | -0.291% | -0.551% | -0.147/-0.442 | 0.507 | -0.002% |
| UP-shock H=3 | 57 | -0.214% | -0.454% | -0.714% | -0.321/-0.592 | 0.976 | -0.173% |
| UP-shock H=6 | 57 | -0.186% | -0.426% | -0.686% | -0.300/-0.557 | 0.861 | -0.136% |

Pool (unconditional) spread mean: -0.04 to -0.05% — the crowding spread does
NOT exist outside shock hours, exactly as hypothesized.

## VERDICT: REFUTED on cost (gross structure real)
Deciding numbers: at H=3 after down-shocks the spread is **+0.228% gross with
mc_p 0.00167 and +0.270% excess over the non-shock null** — the shock x
crowding interaction is a real microstructure effect, and it decays within 3-6h
(gone by H=6), which is exactly the liquidation-flush signature. But two alt
legs cost 24bps round-trip: **net12 = -0.012% and the OOS halves flip
(-0.154/+0.136)**. Dead by the 25bps tier (-0.272%). The up-shock mirror
(crowded-short squeeze) does not exist in this book's form (-0.214% gross).
No tradeable edge at our cost tier; would need maker fills or a <=12bps
all-in cost structure to revisit. Survivorship + single-regime window
(90d, one tape) caveats apply.
