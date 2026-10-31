# Silver thesis test, 2026-10-31

Operator, 2026-10-31: after the market scout (`MARKET_SCOUT_2026-10-31.md`)
named silver as the only other market worth testing, "run the 30 day test".
Question: would AI theses on `xyz:SILVER`, traded with the oil desk's live
rules, have made money, and more than just picking a side?

## How it ran

| Step | What |
|---|---|
| Theses | 30 days, 2026-10-01 to 10-30, one a day at 16:00Z, Opus, price data only, no news. `services/thesis/evals/replay.py --desk metals --primary xyz:SILVER` |
| Trading | `services/strategies/evals/thesis_backtest.py`: live rules (market entry off the 24h range, 2R gate, target 0.5% inside the 24h edge, fixed stop, re-entry), stop buffer 1.3 x silver's 1h ATR (0.41), 20x, never chase the thesis entry, $64 start |
| Controls | always short, always long, daily sign (20-day trend), same rules and window |
| Raw output | `/tmp/thesis-replay-silver/theses/`, `/tmp/thesis-backtest/silver.json` |

## Result

Silver fell 19% over the window (60.64 to 49.33).

| Series | $64 became | %/day | Trades | Win | Total R | Max drawdown |
|---|---|---|---|---|---|---|
| AI theses | $173.95 | 3.43 | 13 | 54% | 10.5 | 38% |
| Always short | $184.44 | 3.63 | 13 | 46% | 9.7 | 49% |
| Daily sign | $184.44 | 3.63 | 13 | 46% | 9.7 | 49% |
| Always long | $0.61 | -14.55 | 33 | 12% | -21.3 | 100% |

Theses: 26 short, 2 long (10-06, 10-15), 2 flat (10-23 a CLI failure, 10-30
the all-time-low cap). The long trades (10-07) netted -0.28R (-$2.61); the shorts carried it.

## Reading it

1. **The AI read the direction right** (26 of 30 short in a 19% fall), but on
   this window it added nothing over plain "always short": $10 less, with a
   lower drawdown (38% vs 49%) from the long days and the never-chase ceiling
   skipping some entries.
2. **One regime only.** A one-way fall flatters any short. Always long went
   to zero on the same rules, so a wrong side at 20x is fatal here. This is
   not yet evidence the rules work on silver in a range or an uptrend.
3. **Oil is still the better desk.** The live oil journal (12 theses from
   2026-10-18) on the same rules, rerun 2026-10-31: 18.7%/day. Silver: 3.4%/day.
4. **The all-time-low cap is wrong for silver.** The venue's silver history
   starts 2025-12-26, so its "all-time low" is a 10-month low. 10 of 30
   theses targeted it (54.817, then 53.053, then 52.694), and price broke each
   one; on 10-30 the cap turned the call flat at 51.97 before silver fell to
   49.3. Under the live rules the thesis sets only side and entry ceiling, so
   the cap cost one flat day here, but it would cap targets on any young market.

## Bugs found and fixed during the test

- `services/strategies/backtest/data.py` dropped flat theses, so the previous
  call stayed in force through flat days. Fixed (flat = no new entry);
  the live oil journal result is unchanged (commit ac3f673).
- A flat fallback after two rejected drafts lost its token counts. Fixed
  (commit 75990ec).

## Open

- 10-23: the thesis call failed with `error_max_turns` (the model tried a tool
  call). One in 30; the live desk would keep the previous thesis.
- The all-time cap needs a young-market rule before silver could go live.

## Usage

Opus only, no Fable: 30 thesis days, about 420k tokens in and 130k out
(25 new days: 352k in, 107k out).
