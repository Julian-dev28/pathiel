# C11 gap_fill — INCONCLUSIVE (24/7 perps barely gap; n too thin)

## Hypothesis
Daily-boundary / low-liquidity-hour gaps fill; fade the gap toward the pre-gap close.

## Exact rule
1h candles. Gap at i+1 = |open_{i+1}/close_i − 1| > G. Gap-up → short toward prior close,
gap-down → long. Entry at i+1 open (the gapped tradeable price). Hold {6,12,24}h, stop sweep.
Reported fill probability (touches prior close within horizon). G∈{0.5%,1%,2%}. Excess vs
matched random same-side + mc_null.

## Results
| G | hz | stop | n | fill_rate | EV@12 | EV@25 | win | h1 | h2 |
|--|--|--|--|--|--|--|--|--|--|
| 0.5% | 6 | any | **31** | 0.742 | +1.16% | +1.03% | .516 | +0.38 | +2.00 |

- Only **31** qualifying gaps (≥0.5%) across 40 coins over ~83 days of 1h data, even at the
  loosest threshold; G=1% and 2% produced <30 (filtered out). Stop width is irrelevant (gaps
  are tiny vs 8–40% stops).
- Best cell split: long n=16 (excess +2.3%, p=0.006), short n=15 (excess +0.2%, p=0.37).
  n=15–16 is statistically meaningless.

## VERDICT
**INCONCLUSIVE (data-structural).** Deciding number: **n=31 total gap events ≥0.5%** —
24/7 liquid perps trade continuously, so the equity-style session gap barely exists here.
There is no tradeable sample to validate or refute a gap-fill edge. The 74% fill rate is just
the unconditional base rate of price revisiting a level within 6h. Not pursuable in this
market structure; would need a much longer history AND illiquid coins (absent by survivorship)
to even test. Treat as effectively refuted by absence of the phenomenon.
