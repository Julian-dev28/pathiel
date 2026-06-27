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
Scale position size by AI confidence: a high-conviction setup bets a larger fraction of equity. Set `false` for flat sizing across all trades.

### `conviction_tiers` (list of `[min_confidence, multiplier]`, optional)
Overrides the built-in confidence tiers used by `conviction_sizing`. Each pair is `[threshold, size_multiplier]`; the highest threshold the AI confidence clears wins, and the multiplier scales `equity_fraction_per_trade` for that trade. Hot-reloaded — no restart needed.

Default (when unset) reproduces the prior hardcoded behavior:
```json
"conviction_tiers": [[0.80, 1.5], [0.65, 1.0], [0.0, 0.7]]
```

Example — bet more aggressively on strong setups and smaller on weak ones:
```json
"conviction_tiers": [[0.85, 2.0], [0.70, 1.2], [0.0, 0.5]]
```
So at `equity_fraction_per_trade: 0.10`, a 0.90-confidence trade sizes at an effective 0.20 fraction (2.0×), while a 0.55-confidence trade sizes at 0.05 (0.5×). Malformed entries are ignored and fall back to the default tiers. The whale-signal boost (`whale_size_multiplier`) still multiplies on top, clamped at 2× base.

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

### `daily_giveback_halt_pct` (float 0-1, default `0` = off)
**Daily give-back breaker** (2026-06-06). Once the day's PnL has peaked at `>= daily_giveback_min_peak_usd`, block NEW entries if it then retraces more than this fraction from that peak. Existing positions keep riding their own stops; resets at the UTC roll. Locks in green days so a won day can't fully round-trip (e.g. `0.35` = halt after giving back 35% from peak). Measures TRUE account PnL (aggregate equity, not main-dex-only). `0` disables.

### `daily_giveback_min_peak_usd` (float, default `20`)
Arm threshold for the give-back breaker — it stays disarmed until the day's peak PnL reaches this, so a tiny `+$2` peak can't trip a halt. Scale to account size.

### `cooldown_min` (int, default `60`)
Minimum minutes between trades on the same coin. Prevents over-trading a single market. 30-60 reasonable for active strategies; 120+ for slower. Also skips the paid AI research call for a non-held coin still inside this window (a re-entry would be gate-blocked anyway).

### `held_research_interval_min` (int, default `10`)
How often a coin you ALREADY HOLD is re-researched for a possible AI `CLOSE`. Without this, a held position that keeps triggering pays for a "hold" PASS on every ~60s scan. The DSL exit engine still handles fast/loss exits in real time every scan regardless — this only paces the slower "thesis broke → close" judgment. Lower = more responsive AI closes but more token spend; higher = leans more on DSL for exits. Hot-reloaded.

### `min_ai_confidence` (float 0-1, default `0.35`)
Floor for AI-verdict confidence to execute. Raise to filter out borderline trades; lower to accept more setups. Current default 0.30 with conviction_sizing reducing those bets to 0.7×.

### `counter_regime_min_conf` (float 0-1, default `0.7`)
For trades against the BTC/SP500 regime trend, AI confidence must clear this OR `composite_score ≥ 50` OR `momentumBurst` fired OR a slow-burn trigger fired. Loosened bypass paths added 2026-05-28. The `composite ≥ 50` path is NOT disabled by `block_counter_trend_bypass` (only the binary-trigger bypass is).

### `crowded_with_min_conf` (float 0-1, default `0` = off)
**SHORT_CROWDED squeeze caution** (2026-06-06). A trend-aligned trade that is ALSO *with the crowd* (a short into `SHORT_CROWDED` funding, or a long into `LONG_CROWDED`) normally gets a free "aligned" pass — but those are exactly what gets squeezed on a reversal. When set, such a trade must clear this confidence bar or it's blocked `via:crowded_squeeze`. Filters squeeze-prone weak entries while letting strong setups through. `0.80` is a moderate filter; too high neuters the down-short edge (SHORT_CROWDED is common in downtrends). `0` disables.

### `tp_scale_fraction` (float 0-1, default `0.5`)
Fraction of a position auto-banked at the take-profit target via a server-side reduce-only TP trigger placed at entry (`1 ATR` past entry). Banks e.g. half at target while the rest rides the DSL trail — captures profit instead of round-tripping into the trailing stop. `0` = no TP scale-out (trail only).

### `aligned_min_conf` (float 0-1, optional, default unset)
Confidence bar for a trade WITH the regime (trend-aligned), typically lower than `counter_regime_min_conf`. Lets aligned shorts in a selloff / aligned longs in a rally clear at a lower bar than counter-trend trades. Unset = use `min_ai_confidence`.

### `block_counter_trend_bypass` (bool, default `false`)
When `true`, the binary-trigger bypass (momentum_burst / slow_burn / whale) can NO LONGER push a counter-trend trade through the regime gate — it must clear real conviction (conf or `composite ≥ 50`). Stops low-conviction longs being forced into a downtrend. Does NOT touch aligned/neutral trades or the composite≥50 path.

### `whale_scan_bypass` (bool, default `false`)
Let whale-accumulation signals (oi_funding_anomaly) surface a coin for research even when it scores below the composite scan gate (whale loads on FLAT price, which scores low on momentum triggers).

### `max_crypto_long_correlated` (int, default `2`)
Cap on simultaneous correlated crypto longs. Prevents stacking 5 alt longs that all dump together. HIP-3 equity/commodity longs don't count against this.

## Signal surfacing (gated)

These surface extra candidates for AI research beyond the weighted composite gate; the AI + risk gates still adjudicate. All default OFF unless noted.

### `momentum_continuation` (nested, `enabled` default `false`)
`{enabled, min_trend_pct (8), max_pullback_pct (6), weight (0.4), log_near_miss}`. Surfaces a coin in a sustained ORDERLY uptrend now consolidating (already-extended movers the spike/breakout triggers miss) and adds its weight to the composite — so a strong momentum long can clear the regime gate's `composite ≥ 50` path even counter-trend. Enable when you want to ride extended momentum; the counter-trend gate + caps back it up.

### `candlestick_patterns` (nested, `enabled` default `false`)
`{enabled, wick_body_ratio (2.0), context_lookback (6), context_pct (1.5)}`. Reversal candles at exhaustion — shooting-star/bearish-engulfing (→ SHORT) and hammer/bullish-engulfing (→ LONG), each requiring a preceding move so they fire at tops/bottoms, not every bar. Weight-0 surfacing signal; the research prompt also gets the last 12 raw 1h OHLC bars so the LLM reads price action directly.

---

## Liquidity (volume floors)

### `min_market_volume_usd` (int, default `5000000`)
Crypto perps below this 24h volume are blocked. Default $5M screens illiquid microcaps.

### `min_hip3_volume_usd` (int, default `500000`)
HIP-3 perps below this 24h volume are blocked. Lower because HIP-3 markets carry less volume than crypto majors (xyz:CRCL at $4M is well-tradable).

### `min_short_volume_usd` (int, default `0` = off)
A SEPARATE, deeper 24h-volume floor for SHORTS only — thin markets squeeze, so a short needs more liquidity than a long in the same name. `0` disables (shorts use the general floor).

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

## Exit, sizing & signal blocks (nested)

All hot-read. README "Configuration" has the concise version.

### `dsl_exit` — trailing-stop engine
- `max_loss_pct` (3.5) + `max_loss_roe_pct` (18) — hard stop, whichever binds first (ROE cap = `pct / leverage` in spot terms; at 10x, 18% ROE = 1.8% spot).
- `protect_pct` (1.5) + `retrace_threshold` (0.30) — trail tightness. **Low = scalp (bank fast); high = trend-ride (let it run).** `phase2_tiers` = profit-scaled retrace ladder.
- `stale_flat_timeout_minutes` — flatten a position that never reaches `protect_pct` within this window.
- `regime_aware {enabled, trend_ride{…}}` — when `detect_regime()=='up'`, swap to looser trend-ride params (scalp chop / ride trends). Default OFF.

### `atr_risk_sizing` `{enabled, risk_per_trade_pct}`
Equal-risk (Turtle-N): notional = `risk_per_trade_pct × equity / stop_width`. Overrides flat `equity_fraction` — volatile coins get smaller size, tight-stop coins bigger (capped by `max_trade_notional_usd`).

### `gex_signal`
HIP-3 options-wall caution flag. When enabled, the runner gate blocks long entries jammed under a nearby long-gamma call wall.

---

## What to actually tune day-to-day

Most of these knobs you set once and leave. The three you'd realistically touch:

1. **`mode`**: flip to `OFF` when you want the bot to stop trading (it keeps scanning, just doesn't execute)
2. **`max_daily_loss_usd`**: drop if you want a tighter circuit breaker for the day
3. **`min_ai_confidence`**: raise to filter trades when the AI is being too loose; lower to accept more

Everything else is structural — change it deliberately, not reactively.
