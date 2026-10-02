# W-CP1: copy-trading wallet 0xe282...df29 with leverage — REFUTED

2026-09-21. Operator asked for a copy mode on this wallet (the one reverse-
engineered in `W-WH1_wallet_0xe282.md`) with leverage added to newly opened
positions. Backtest first, per `EVIDENCE_DOCTRINE.md`; build only if it clears.
It does not clear.

**Operator override, same day:** built anyway as `copy_trade`
(`pathiel/agents/copy_trade_live.py`), with the rule below, `leverage_mult`
clamped to the 3x this backtest covered, and a mark-to-market demote at -50%
of its sleeve.

- script: `hypotheses/W-CP1_copy_backtest.py`
- numbers: `hypotheses/W-CP1_results.json`
- data: the W-WH1 caches (22,850 fills, funding, ledger, 4h candles) plus
  `W-CP1_cache_maxlev.json` (per-coin max leverage, HL meta 2026-09-21)

## The rule tested

Copy every order he places on a position he opened from flat after the copy
starts: opens, adds (his averaging down) and closes. Open/add size is
`m x his order notional / his equity x our equity`, so our leverage is m times
his. A close takes off the same fraction he did. 12.5bps per side,
$10 minimum order, adds clipped to free cross margin. Liquidation is checked
every 4h bar on the adverse extreme against `notional / (2 x maxLeverage)`.
661 orders over 61 trips, 2025-02-27 to 2026-09-13.

His equity, which the sizing needs, is rebuilt from fills + funding + ledger.
It tracks HL's weekly perp account-value curve within ~3% through 2026-05.
From 2026-06 he is on unified margin and the perp-only curve stops being his
equity. Every USDC send has to count as a flow, whichever dex it leaves from,
or the rebuild drifts by $14.6M.

## Result: liquidated at every multiplier, including half his leverage

Full window, $1,000 start:

| m | multiple | liquidated |
|---|---:|---|
| 0.5 | 0 | 2025-03-10 |
| 1.0 | 0 | 2025-03-04 |
| 1.5 | 0 | 2025-02-28 |
| 2.0 | 0 | 2025-03-02 |
| 3.0 | 0 | 2025-03-02 |

The random-entry null dies too (median 0), so p = 1.0. That fails doctrine
tests 1, 2 (first half 0x at every m), 3 and 4.

"Start copying on the 1st of the month", every month:

| start | m=0.5 | m=1 | m=1.5 | m=2 | m=3 |
|---|---|---|---|---|---|
| 2025-03 | LIQ 2026-02-03 | LIQ 2025-04-06 | LIQ 2025-03-30 | LIQ 2025-03-10 | LIQ 2025-03-09 |
| 2025-04 to 2025-10 | LIQ 2026-02-03 | LIQ 2025-10-10 | LIQ 2025-10-10 | LIQ 2025-10-10 | LIQ 2025-10-10 |
| 2025-11 to 2026-02 | 1.11x, dd -11% | 1.25x, -21% | 1.40x, -29% | 1.55x, -36% | 1.88x, -50% |
| 2026-03 | 1.06x | 1.14x | 1.22x | 1.32x | 1.49x |
| 2026-09 | 1.01x | 1.01x | 1.02x | 1.03x | 1.04x |

Every start from March to October 2025 ends at zero. At m >= 1 the copy dies
with him on 2025-10-10. At m = 0.5 it survives that day, then dies on
2026-02-03 holding the ETH and BTC trips he opened 2025-10-10. He sat through
that drawdown (-$4.57M ETH, -$2.68M BTC mark-to-market) on fresh deposits. A
copier's account gets no deposits.

## The good-looking window has no edge either

Starts from 2025-11 onward are all positive. That is one recovery (21 trips),
chosen after the fact. Tested anyway, same trips re-timed at random on their
own coins, 2000 draws, priced at the 4h close:

| m | actual | null median | p |
|---|---:|---:|---:|
| 1 | 1.107x | 1.362x | 0.85 |
| 2 | 1.216x | 1.698x | 0.82 |
| 3 | 1.301x | 2.003x | 0.79 |

Random entry times on the same coins did better than his actual timing. Over
this window the return is coin exposure in a rising market, not his timing.
Leverage scales that exposure. It adds no edge.

## Verdict

Copying this wallet is copying a martingale whose drawdowns are covered by
deposits. Copied into a bounded account it is liquidated in every start month
through 2025-10, at every multiplier tested. Leverage moves the liquidation
earlier. The one window that survives does worse than random entry. The part
of him with measured edge is already live as `drawdown_ladder` (W-WH2, 1x, no
leverage by design).
