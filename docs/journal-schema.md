# Trade Journal Schema

## Purpose
Persistent SQLite database that captures the complete lifecycle from trigger to exit, enabling:
- Live vs. backtest reconciliation
- AI confidence calibration
- Trigger profitability attribution
- Portfolio correlation analysis
- Regime-specific edge measurement

## Database Location
`data/trade-journal.db` (persisted across restarts, excluded from git)

## Schema

### 1. triggers
Every scanner trigger that fires, whether it leads to a trade or not.

| Column | Type | Notes |
|--------|------|-------|
| id | TEXT PK | UUID |
| ts_ms | INTEGER | When trigger fired (epoch ms) |
| coin | TEXT | Asset symbol |
| composite_score | REAL | 0-10 weighted score |
| pct_move_spike | REAL | Z-score value |
| volume_spike | REAL | Z-score value |
| breakout_score | REAL | Distance beyond range |
| range_compression | REAL | 0-1 percentile score |
| mid_price | REAL | Current mid price at trigger |
| interval | TEXT | Candle interval (1h, 4h, etc.) |
| candles_count | INTEGER | Number of candles used |

### 2. ai_verdicts
AI analysis results for each trigger.

| Column | Type | Notes |
|--------|------|-------|
| id | TEXT PK | UUID |
| trigger_id | TEXT FK → triggers(id) | Which trigger this analyzed |
| ts_ms | INTEGER | When verdict was generated |
| side | TEXT | 'long' / 'short' / 'pass' |
| confidence | REAL | 0-100 AI stated confidence |
| summary | TEXT | AI reasoning summary |
| model | TEXT | Which model was used |
| prompt_version | TEXT | Strategy/prompt version tag |

### 3. trades
Executed or simulated trades.

| Column | Type | Notes |
|--------|------|-------|
| id | TEXT PK | UUID |
| verdict_id | TEXT FK → ai_verdicts(id) | Which verdict triggered this |
| ts_ms | INTEGER | Entry timestamp |
| side | TEXT | 'long' / 'short' |
| coin | TEXT | Asset symbol |
| entry_px | REAL | Fill price |
| size | REAL | Position size in coin units |
| notional_usd | REAL | Entry notional value |
| mode | TEXT | 'LIVE' / 'DRY' / 'BACKTEST' |
| stop_px | REAL | Initial stop loss price |
| tp_px | REAL | Take profit price |
| leverage | REAL | Leverage used |
| risk_pct | REAL | Risk as % of equity |
| trigger_type | TEXT | Which trigger led to this (composite name) |
| backtest_params | JSON | Snapshot of backtest params used |

### 4. exits
Trade completions.

| Column | Type | Notes |
|--------|------|-------|
| id | TEXT PK | UUID |
| trade_id | TEXT FK → trades(id) | Which trade this closed |
| ts_ms | INTEGER | Exit timestamp |
| exit_px | REAL | Exit price |
| pnl_usd | REAL | Realized P&L in USD |
| pnl_pct | REAL | P&L as % of notional |
| hold_ms | INTEGER | Duration held |
| reason | TEXT | 'take_profit' / 'stop_loss' / 'time_stop' / 'manual' / 'trend_flip' / 'trailing_stop' |
| fees_usd | REAL | Total fees paid |
| slippage_pct | REAL | Entry/exit slippage vs. mid |

### 5. backtest_predictions
What the backtest would have predicted for each trigger.

| Column | Type | Notes |
|--------|------|-------|
| id | TEXT PK | UUID |
| trade_id | TEXT FK → trades(id) | Which trade this corresponds to |
| predicted_wr | REAL | Backtest win rate for this setup |
| predicted_pf | REAL | Expected profit factor |
| predicted_ret | REAL | Expected return on risk |
| params_hash | TEXT | Hash of backtest params used |
| ts_ms | INTEGER | When prediction was generated |

### 6. market_regimes
Regime classification at time of each trade.

| Column | Type | Notes |
|--------|------|-------|
| id | TEXT PK | UUID |
| ts_ms | INTEGER | Timestamp |
| coin | TEXT | Asset symbol |
| trend_1d | TEXT | 'up' / 'down' / 'range' |
| trend_4h | TEXT | 'up' / 'down' / 'range' |
| adx_14 | REAL | 14-period ADX |
| atr_pct | REAL | ATR / price (volatility) |
| regime | TEXT | 'trending_up' / 'trending_down' / 'ranging_low_vol' / 'ranging_high_vol' |
| btc_dominance | REAL | Optional: market context |

### 7. ai_calibration
Tracked outcomes for AI verdict confidence calibration.

| Column | Type | Notes |
|--------|------|-------|
| id | TEXT PK | UUID |
| verdict_id | TEXT FK → ai_verdicts(id) | Which verdict |
| predicted_confidence | REAL | AI stated confidence (0-100) |
| actual_outcome | TEXT | 'win' / 'loss' / 'breakeven' |
| actual_pnl | REAL | Realized P&L |
| confidence_bucket | TEXT | '50-60' / '60-70' / '70-80' / '80-90' / '90-100' |
| calibrated_wr | REAL | Actual win rate in this bucket |

## Indexes

```sql
CREATE INDEX idx_triggers_coin_ts ON triggers(coin, ts_ms);
CREATE INDEX idx_triggers_score ON triggers(composite_score DESC);
CREATE INDEX idx_verdicts_trigger ON ai_verdicts(trigger_id);
CREATE INDEX idx_trades_ts ON trades(ts_ms);
CREATE INDEX idx_trades_coin ON trades(coin);
CREATE INDEX idx_exits_trade ON exits(trade_id);
CREATE INDEX idx_regimes_coin_ts ON market_regimes(coin, ts_ms);
CREATE INDEX idx_calibration_bucket ON ai_calibration(confidence_bucket);
```

## Key Queries

### Trigger Profitability
```sql
SELECT 
    t.trigger_type,
    COUNT(*) as trades,
    SUM(CASE WHEN e.pnl_usd > 0 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) as win_rate,
    AVG(e.pnl_usd) as avg_pnl,
    SUM(e.pnl_usd) as total_pnl,
    AVG(t.composite_score) as avg_trigger_score
FROM trades t
JOIN exits e ON t.id = e.trade_id
GROUP BY t.trigger_type
ORDER BY total_pnl DESC;
```

### AI Confidence Calibration
```sql
SELECT 
    ac.confidence_bucket,
    COUNT(*) as verdicts,
    SUM(CASE WHEN ac.actual_outcome = 'win' THEN 1 ELSE 0 END) * 100.0 / COUNT(*) as actual_wr,
    AVG(ac.predicted_confidence) as avg_stated_conf,
    AVG(ac.actual_pnl) as avg_pnl
FROM ai_calibration ac
GROUP BY ac.confidence_bucket
ORDER BY ac.confidence_bucket;
```

### Live vs. Backtest Skew
```sql
SELECT 
    t.coin,
    COUNT(*) as trades,
    AVG(e.pnl_usd) as live_avg_pnl,
    AVG(bp.predicted_ret) as backtest_predicted,
    AVG(e.pnl_usd) - AVG(bp.predicted_ret) as skew,
    CASE 
        WHEN AVG(e.pnl_usd) > AVG(bp.predicted_ret) * 0.8 THEN 'in_line'
        WHEN AVG(e.pnl_usd) > 0 THEN 'underperforming'
        ELSE 'degraded'
    END as assessment
FROM trades t
JOIN exits e ON t.id = e.trade_id
LEFT JOIN backtest_predictions bp ON t.id = bp.trade_id
GROUP BY t.coin;
```

### Regime-Specific Edge
```sql
SELECT 
    mr.regime,
    COUNT(*) as trades,
    SUM(CASE WHEN e.pnl_usd > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*) as win_rate,
    AVG(e.pnl_usd) as avg_pnl,
    SUM(e.pnl_usd) as total_pnl
FROM trades t
JOIN exits e ON t.id = e.trade_id
JOIN market_regimes mr ON t.coin = mr.coin AND mr.ts_ms <= t.ts_ms
    AND mr.ts_ms = (SELECT MAX(ts_ms) FROM market_regimes WHERE coin = t.coin AND ts_ms <= t.ts_ms)
GROUP BY mr.regime
ORDER BY total_pnl DESC;
```

## Migration Path

1. Create `data/` directory (gitignored)
2. Initialize database with schema
3. Add journal writer functions to:
   - Scanner trigger engine → writes to `triggers`
   - AI verdict endpoint → writes to `ai_verdicts`
   - Trade execution → writes to `trades`
   - Trade close → writes to `exits`
   - Market data polling → writes to `market_regimes`
4. Add analysis queries to `lib/quant/`
5. Build reconciliation reports in `docs/`
