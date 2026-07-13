# W-L — scan-interval / entry-latency sensitivity of the live books (2026-07-13)

**Operator question:** do LOWER scan intervals catch more alpha and end EV+?

**Method.** Entry-latency study on the VALIDATED books' OWN signals — no new
signals invented. Each book's historical entries were reconstructed per its
LIVE rules (`hermes_trader/agents/*_live.py` + `.agent-config.json` geometry),
then the SAME signal was entered with a delay of {0, 1, 3, 6, 12} h after the
signal bar close. Net 25bps, pessimistic intra-bar stops (stop checked first in
every hourly bar, gap-through fills at the open), live stop/hold per book.
Paired same-signal comparison IS the control: every delay trades the identical
event, only the entry bar moves — no matched null needed. Scripts:
`hypotheses/W-L1_latency_backtest.py` (grid), `hypotheses/W-L2_anchor_5m.py`
(sub-hour anchor), `hypotheses/W-L0_extend_funding.py` (funding gap-fill →
`W-L_cache_funding.json`, hourly funding 2025-12-13..2026-07-09, 40 coins).
Results: `W-L1_results.json`, `W-L2_anchor_results.json`.

**Data + resolution floor.** `W-R_cache_hourly.json` (40 liquid coins, 1h bars,
2025-12-13..2026-07-09, 208d). **Hourly bars floor the resolution at 1h**:
"delay 0h" = entry at the open of the first hourly bar after signal close. The
30m-vs-5m question therefore reduces to interpolating the 0→1h edge, plus one
empirical anchor: HL retains only ~5,000 5m bars (~17d, earliest 2026-06-25),
so the anchor set is the largest extreme_fade crashes INSIDE retention (n=4:
DYDX −20.3%, XPL −17.4%, IP −16.8%, JTO −14.0%), not the 208d top-10 (those
predate retention). Survivorship: cache = today's liquid set; absolute EVs are
upper bounds, but the PAIRED deltas are survivorship-neutral (same coins at
every delay).

**Bonferroni.** 5 books x 4 delay comparisons = 20; alpha = 0.05/20 = 0.0025.
**Nothing passes** (closest: extreme_fade @12h, paired sign_p 0.0039,
t = −2.34). Every verdict below is directional — powered by monotone decay
patterns + the paired construction, not by a significant single cell.

## EV25 (%/signal) by entry delay after signal close

| book (side, stop/hold) | n | 0h | 1h | 3h | 6h | 12h | decay shape |
|---|---|---|---|---|---|---|---|
| extreme_fade (L, 20%/3d) | 106 | **+3.23** | +3.11 | +3.51 | +2.29 | +0.51 | flat 0–3h, cliff after 6h |
| rally_exhaustion (S, 25%/5d) | 62 | **+8.50** | +7.59 | +7.30 | +6.57 | +7.59 | monotone −1.9%/6h (t −2.2) |
| engulf_short (S, 20%/1d) | 126 | −0.51 | −0.39 | −0.60 | −0.87 | −1.44 | flat 0–3h; raw EV− everywhere |
| crash_continue (S, 20%/10d) | 48 | **+5.68** | +5.69 | +5.83 | +5.45 | +5.21 | flat to 12h |
| funding_spike_short (S, 15%/5d) | 25 | −0.89 | −1.36 | −1.68 | −2.55 | −4.03 | fastest decay of all books |

Expected EV under a scan every I (latency ~ U(0,I), piecewise-linear grid):

| book | 5m | 30m | 1h | 3h | 6h | 12h | marginal EV of faster-than-current |
|---|---|---|---|---|---|---|---|
| extreme_fade (cur 30m) | 3.22 | 3.20 | 3.17 | 3.26 | 3.08 | 2.24 | 30m→5m: **+0.03** (noise; anchor says ≤0) |
| rally_exhaustion (cur 6h) | 8.46 | 8.27 | 8.05 | 7.65 | 7.29 | 7.18 | 6h→1h: **+0.76**; →30m: +0.98 |
| engulf_short (cur 6h) | −0.50 | −0.48 | −0.45 | −0.48 | −0.61 | −0.88 | 6h→30m: +0.13 (noise) |
| crash_continue (cur 6h) | 5.68 | 5.68 | 5.69 | 5.74 | 5.69 | 5.51 | 6h→30m: **0.00** |
| funding_spike (cur 6h) | −0.91 | −1.01 | −1.12 | −1.39 | −1.75 | −2.52 | 6h→2h: ~+0.5; →30m: +0.74 |

## The 5m anchor (extreme_fade sub-hour)

Post-close drift after the signal-day close, n=4 (thin — say so):
mean −1.60% @+5m, −1.39% @+15m, −2.33% @+30m, −1.49% @+60m (3/4 signals keep
FALLING into the first hour; only JTO recovered after +15m). For the LONG fade
a negative drift means a LATER entry buys CHEAPER — sub-hour speed is worth
zero or slightly negative EV. Intra-day context: the −12% threshold crossed at
minute 20–50 of its hour, and price moved a further −1.2% mean (median −2.3%)
from cross to that hour's close — the crash is still in motion when a fast
scanner would first see it. Both measurements agree with the grid: there is
nothing to harvest between 5m and 60m latency on this book.

## Reconstruction caveats (honest edges)

- **funding_spike_short**: z reconstructed at DAY granularity (settled daily
  funding sums, live W-F2A spec) — the live scanner's rolling-24h z can cross
  2.0 intraday, so the true benefit of faster scanning is likely LARGER than
  the grid shows (decay is monotone; earlier = better). Separate red flag: on
  the full 208d window the book is EV− even at 0h (−0.89%, n=25) vs +2.0%
  (n=11) on the 90d window that overlaps the W-F2A validation. Latency does
  not rescue it; forward shadow grading (KILL: EV25<0 over 15 eps) owns that
  verdict.
- **engulf_short**: raw EV25 negative at every delay on this tape. The
  original validation was EXCESS over a matched same-side null (+1.25%), so
  this is not by itself a refutation — but it does mean faster scanning cannot
  make the book pay; only the tape/regime can.
- **crash_continue** simulated at the live-config stop 20% (code default 8% is
  NOT what runs).
- Skipped as NOT faithfully reconstructable: **majors_swing** (intraday
  trend+pullback state machine with a 2.2% stop — under 1h pessimistic bars
  the stop is inside hourly noise, any number would be fiction),
  **young_listings** (xyz coins absent from the hourly cache),
  **whale_flow** (no historical Binance taker prints), **news_catalyst** (no
  historical news timestamps).
- Fees 25bps flat; funding carry ignored — both delay-invariant, they cancel
  in the paired diffs.

## Cost column — where faster scanning 429s us

One full-universe 1d-candle sweep = 177 live perps x weight 20 ≈ **3,540
weight**, i.e. ~3 min of the ENTIRE 1,200/min IP budget (matches the client
audit: extreme_fade's sweep saturates the IP 4-5 min; budget already at 192%).

| book | scan reads | weight/scan | current /day | at 1h /day | at 30m /day |
|---|---|---|---|---|---|
| extreme_fade (30m) | 1d candles x universe | ~3,540 | ~170k | 85k | 170k |
| rally_exhaustion (6h) | 1d candles x universe | ~3,540 | 14k | 85k | 170k |
| engulf_short (6h) | 1d candles x universe | ~3,540 | 14k | 85k | 170k |
| crash_continue (6h) | 1d candles x universe | ~3,540 | 14k | 85k | 170k |
| funding_spike (6h) | 40 fundingHistory | ~800 | 3.2k | 19.2k | 38.4k |
| news_catalyst (5m) | external RSS | 0 HL | 0 | 0 | 0 |

**The free lunch:** all four daily-bar books consume the SAME completed daily
candles, fetched four separate times. `hl_client._CANDLE_CACHE_TTL_S` defaults
to 90s — too short to span books scheduled apart. Co-schedule the daily-bar
books on one cycle and raise the 1d-candle cache TTL to ~900s (staleness is
harmless: every signal uses COMPLETED bars, TTL 15min << bar period 24h), and
rally_exhaustion rides extreme_fade's existing 30m sweep at **zero marginal HL
weight**. Tightening any of them independently is the expensive path
(+71k weight/day each at 1h) and is what re-creates the 429 storm.

## VERDICTS

| book | current | verdict | recommendation |
|---|---|---|---|
| extreme_fade | 30m | **KEEP** | 30m fine; 1–2h would be EV-neutral 429 relief (decay starts >3h; must stay < entry_window 6h). 5m scanning: refuted by anchor. |
| rally_exhaustion | 6h | **TIGHTEN** | to 30m–1h **via the shared sweep** (zero marginal weight); expected +0.8–1.0%/episode (directional, ns after Bonferroni). Do NOT add an independent sweep. |
| engulf_short | 6h | **KEEP** | no decay inside its 8h window; latency can't fix raw EV− — leave to forward grading. |
| crash_continue | 6h | **KEEP** | flat to 12h; the 10d hold dominates entry timing entirely. |
| funding_spike_short | 6h | **TIGHTEN (conditional)** | to 2h (+6.4k weight/day, cheapest tighten, fastest-decaying signal, and the day-granularity grid UNDERSTATES the live benefit) — but only if the book survives its own forward grading; full-window EV25 @0h is negative. |
| majors_swing | 30m | NOT ASSESSED | 1h bars can't reproduce a 2.2%-stop intraday book. |
| young_listings | 30m | NOT ASSESSED | xyz coins not in cache. |
| whale_flow | 30m | NOT ASSESSED (EV) | but a coverage bug: `window_minutes=15` at 30m cadence observes only 50% of the Binance tape → the W-W verdict sample is half-blind. Set window_minutes=30 (free) or scan at 15m. |
| news_catalyst | 5m | KEEP | external feed, zero HL weight; latency question is news-decay, owned by W-N3/W-N4. |

## Bottom line

Faster scanning barely pays anywhere, because every reconstructable book
triggers on COMPLETED DAILY bars: the signal is born at 00:00 UTC and the
first ~3 hours of decay are flat in all five books. The only real money on
the table is rally_exhaustion's ~+1%/episode from 6h→sub-1h, and that is
capturable for FREE by co-scheduling it onto extreme_fade's existing 30m sweep
with a longer 1d-candle cache TTL — never by adding independent sweeps, which
is exactly where the 429s live (each extra full-universe sweep burns ~3 min of
the whole IP budget; the account already runs at 192% of it). Sub-hour
(30m→5m) scanning is refuted empirically: post-crash price keeps falling into
the first hour, so the fast scanner buys HIGHER, not lower. The one place
latency genuinely matters mechanically — funding_spike's intraday z-crossings
— sits on the cheapest API path (800 weight/scan), but the book first has to
survive its own forward grading before its cadence is worth optimizing.
