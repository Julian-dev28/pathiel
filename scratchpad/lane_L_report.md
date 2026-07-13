# Lane L report — scan-interval latency study (2026-07-13)

Full findings: `research/alpha_swarm/findings/W-L_scan_latency.md`
Artifacts: `research/alpha_swarm/hypotheses/W-L0_extend_funding.py`,
`W-L1_latency_backtest.py`, `W-L2_anchor_5m.py`, `W-L1_results.json`,
`W-L2_anchor_results.json`, `W-L_cache_funding.json` (hourly funding, 40
coins, 2025-12-13..2026-07-09 — reusable by future funding studies).

## Verdict table

| book | current | verdict | why |
|---|---|---|---|
| extreme_fade | 30m | KEEP | EV flat 0-3h delay (+3.2%), cliff only after 6h; 5m anchor: post-crash price FALLS another 1-2% in the first hour, so faster entry buys higher. 1-2h would be EV-neutral 429 relief. |
| rally_exhaustion | 6h | TIGHTEN → 30m-1h | monotone decay, +0.8-1.0%/episode recoverable (t −2.2, ns after Bonferroni). Capture it FREE by co-scheduling on extreme_fade's sweep + raising the 1d-candle cache TTL (90s → ~900s); an independent sweep costs +71k weight/day. |
| engulf_short | 6h | KEEP | flat 0-3h; raw EV25 negative at EVERY delay on this tape — latency can't fix it, forward grading owns the book verdict. |
| crash_continue | 6h | KEEP | dead flat to 12h delay (10d hold dominates). Simulated at live-config stop 20%, not the code-default 8%. |
| funding_spike_short | 6h | TIGHTEN → 2h, conditional | fastest-decaying signal (−1.7%/6h paired, monotone) and the grid UNDERSTATES the benefit (day-granularity z vs live rolling-24h). Cheapest tighten (+6.4k weight/day). BUT full-window EV25 @0h = −0.89% (n=25) vs +2.0% on the 90d validation window — tighten only if forward grading keeps the book. |
| majors_swing | 30m | NOT ASSESSED | 2.2%-stop intraday state machine; 1h pessimistic bars can't reproduce it faithfully. |
| young_listings | 30m | NOT ASSESSED | xyz coins absent from the hourly cache. |
| whale_flow | 30m | NOT ASSESSED (EV) | side-finding: window_minutes=15 @ 30m cadence = recorder sees only 50% of the Binance tape → W-W verdict sample is half-blind. Set window_minutes=30 (free fix). |
| news_catalyst | 5m | KEEP | external RSS, zero HL weight; owned by W-N3/W-N4. |

## Does faster scanning pay, and where does it 429 us?

Barely anywhere: every reconstructable book fires on COMPLETED daily bars, so
signals are born at 00:00 UTC and the first ~3h of decay are flat across all
five books. Only rally_exhaustion has real money on the table (~+1%/episode
from 6h→sub-1h) and it's capturable at ZERO marginal HL weight via a shared
daily-candle sweep — the 429 danger is exclusively in adding independent
full-universe sweeps (~3,540 weight each ≈ 3 min of the entire 1,200/min IP
budget, already at 192% per the client audit). 30m→5m is empirically refuted
for extreme_fade (n=4 anchor: price keeps falling after the crash-day close).
Statistical note: 20 paired comparisons, Bonferroni alpha 0.0025, none pass —
all verdicts are directional (monotone patterns + paired same-signal control).

## Caveats

- 1h bars floor resolution; delay 0h = first hourly open after signal close.
- 5m anchor n=4 (HL retains only ~17d of 5m bars; the 208d top-10 predate it).
- Absolute EVs are survivorship upper bounds (today's liquid 40); the paired
  deltas are survivorship-neutral.
- funding_spike full-window EV− (−0.89% @0h, n=25) deserves its own eyeball in
  scripts/shadow_status.py — that's a book-health flag, not a latency finding.
