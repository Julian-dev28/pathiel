# `.agent-config.json` — Reference

Every key the bot reads at trade-time. Hot-reloaded on each trade (no
restart for most changes), with two exceptions called out below.

For one-shot tuning by account size, use:

```bash
scripts/config_preset.py list                          # show available presets
scripts/config_preset.py apply small_aggressive        # apply (with diff preview)
scripts/config_preset.py apply --account-size 250      # auto-pick by equity
```

---

## Mode + asset class

### `mode` (string: `"OFF"` | `"LIVE"`)
- `OFF` — bot scans + researches but executes nothing.
- `LIVE` — orders go to real money.

### `enable_crypto` (bool, default `true`)
Scan native HL perps (BTC, ETH, SOL, etc.). **Requires loop restart to take effect** (universe fetched at startup).

### `enable_hip3` (bool, default `false`)
Scan HIP-3 tokenized-equity / commodity perps (`xyz:NVDA`, `km:USOIL`, etc.). Adds ~8 HTTP POSTs per scan. **Requires loop restart to take effect**.

---

## Sizing

### `equity_fraction_per_trade` (float, 0–1, default `0.01`)
Fraction of perp equity committed as MARGIN per trade. With leverage, notional = `equity × fraction × leverage`.

| Account size | Recommended fraction |
|---|---|
| < $500 | 0.05–0.10 (sized for $25-50 margin per trade) |
| $500–$2000 | 0.03–0.05 |
| $2000+ | 0.01–0.03 |

**Math check**: if `equity_fraction × max_concurrent > 1.0`, you'll fully deploy and start tripping `max_total_notional_pct`. For $250 with 0.10 fraction × 18 concurrent = 180% over-deployment, but with 10–40x leverage the per-trade *margin* commitment stays modest. Watch `available` (free margin) on the dashboard; if it drops below 10% of equity, the executor refuses new trades.

### `leverage` (int, default `5`)
Max leverage to request per trade. Actual = `min(this, per-coin HL max)`. BTC: 40x, ETH: 25x, mid-cap alts: 5-20x, HIP-3 equity: 5-20x.

| Account size | Recommended |
|---|---|
| < $500 | 20–40x (need leverage to size meaningfully) |
| $500–$2000 | 10–20x |
| $2000+ | 5–10x |

### `max_trade_notional_usd` (int, default `100000`)
Per-trade hard cap regardless of formula above. Keep well above intended deployment or trades get blocked.

### `max_concurrent` (int, default `10`)
Max simultaneous open positions. With 60s scan + 180min hold, on a busy day you can fill 18-20 slots quickly. Trades over this cap are deferred.

### `max_total_notional_pct` (float, default `1.0`)
Combined open notional cap as multiple of equity. `40.0` = max 40× equity in total notional. Bounds total deployment even when individual trades are within `max_trade_notional_usd`.

### `min_available_margin_pct` (float, default `0.10`)
Refuse new trade if `available / equity < this`. Default 10% leaves headroom for maintenance margin + slippage. Lower = more aggressive deployment, higher risk of HL "insufficient margin" rejections.

### `conviction_sizing` (bool, default `true`)
Scale position size by AI confidence: `conf ≥ 0.80 → 1.5×`, `0.65-0.80 → 1.0×`, `< 0.65 → 0.7×`. Set `false` for flat sizing across all trades.

---

## Risk safety

### `max_daily_loss_usd` (negative number, default `-100`)
Daily-loss killswitch. When `daily_pnl <= this`, ALL new trades blocked until UTC midnight reset.

| Account size | Recommended |
|---|---|
| < $500 | -25 to -50 (10-20% of equity) |
| $500–$2000 | -100 to -200 |
| $2000+ | -500+ |

Too loose = catastrophic days possible. Too tight = locks you out on a normal variance day.

### `cooldown_min` (int, default `60`)
Minimum minutes between trades on the same coin. Prevents over-trading a single market. 30-60 reasonable for active strategies; 120+ for slower.

### `min_ai_confidence` (float 0-1, default `0.35`)
Floor for AI-verdict confidence to execute. Raise to filter out borderline trades; lower to accept more setups. Current default 0.30 with conviction_sizing reducing those bets to 0.7×.

### `counter_regime_min_conf` (float 0-1, default `0.7`)
For trades against the BTC/SP500 regime trend, AI confidence must clear this OR `composite_score ≥ 50` OR `momentumBurst` fired OR a slow-burn trigger fired. Loosened bypass paths added 2026-05-28.

### `max_crypto_long_correlated` (int, default `2`)
Cap on simultaneous correlated crypto longs. Prevents stacking 5 alt longs that all dump together. HIP-3 equity/commodity longs don't count against this.

### `force_execute_composite` (float, default `40`)
If AI says PASS but trigger composite hits this AND `force_execute_slow_burn_count` slow-burn triggers fire, the executor upgrades to LONG conf 0.70. The structure overrides the AI's hedge. Set to 999 to disable.

### `force_execute_slow_burn_count` (int, default `2`)
Min slow-burn triggers (volumeBuildup1h / trendFlip1h / higherLows1h) required for the structural override. Combined with `force_execute_composite`.

---

## Liquidity (volume floors)

### `min_market_volume_usd` (int, default `5000000`)
Crypto perps below this 24h volume are blocked. Default $5M screens illiquid microcaps.

### `min_hip3_volume_usd` (int, default `500000`)
HIP-3 perps below this 24h volume are blocked. Lower because HIP-3 markets carry less volume than crypto majors (xyz:CRCL at $4M is well-tradable).

---

## Filters

### `coin_allowlist` (list, default `[]` empty = allow all)
If non-empty, ONLY these coins are tradable. Useful for whitelisting a focused basket.

### `coin_blocklist` (list, default `[]`)
Coins always blocked regardless of allowlist. Use for known-bad markets.

---

## DSL exit (nested object)

### `dsl_exit.max_loss_pct` (float, default `2.5`)
Max adverse SPOT% move before forced exit. Combined with the ROE cap below — whichever fires first.

### `dsl_exit.max_loss_roe_pct` (float, default `50.0`)
Max ROE% loss (margin %). At 40x leverage, 40% ROE = 1% spot. The min of (max_loss_pct, max_loss_roe_pct/leverage) is the effective stop. Tighter cap = smaller losses per trade but more stop-outs on noise.

### `dsl_exit.protect_pct` (float, default `1.5`)
Spot% move required to engage phase-2 trailing. Lower = trailing locks profit earlier; higher = lets winners run further before tightening.

### `dsl_exit.retrace_threshold` (float 0-1, default `0.30`)
Phase-2 floor gives back this fraction of peak gains. `0.30` locks 70% of peak profit; `0.20` locks 80%; `0.50` lets price retrace half before exit.

### `dsl_exit.hard_timeout_minutes` (float, default `180`)
Max time a position stays open before forced close. 90 = tight (frequent timeouts on slow movers); 180 = balanced; 360 = lets multi-hour setups breathe.

---

## Account-size presets

The `scripts/config_preset.py` tool ships with these presets:

| Preset | For | Style |
|---|---|---|
| `small_aggressive` | $100-500 | Max conviction, high leverage, tight daily loss cap |
| `small_conservative` | $100-500 | Lower leverage, looser stops, longer holds |
| `medium_balanced` | $500-2000 | Default-ish, balanced risk |
| `large_steady` | $2000+ | Low leverage, tight per-trade size, looser caps |
| `hip3_only` | any | Disables crypto, focuses on tokenized equity |
| `crypto_only` | any | Disables HIP-3 |

Run `scripts/config_preset.py show small_aggressive` to see the full values without applying.

---

## What to actually tune day-to-day

Most of these knobs you set once and leave. The three you'd realistically touch:

1. **`mode`**: flip to `OFF` when you want the bot to stop trading (it keeps scanning, just doesn't execute)
2. **`max_daily_loss_usd`**: drop if you want a tighter circuit breaker for the day
3. **`min_ai_confidence`**: raise to filter trades when the AI is being too loose; lower to accept more

Everything else is structural — change it deliberately, not reactively.
