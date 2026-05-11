// ── Seed the journal with sample data for testing/development ──
// Run: node --import tsx scripts/seed-journal.ts
// Creates realistic synthetic data: triggers, verdicts, trades, and exits.

import * as path from 'node:path'
import * as fs from 'node:fs'

const DATA_DIR = path.join(process.cwd(), 'data')

// Ensure data directory exists
if (!fs.existsSync(DATA_DIR)) {
  fs.mkdirSync(DATA_DIR, { recursive: true })
}

const JOURNAL_FILE = path.join(DATA_DIR, 'trade-journal.json')

function randomBetween(min: number, max: number) {
  return Math.random() * (max - min) + min
}

function randomPick<T>(arr: T[]): T {
  return arr[Math.floor(Math.random() * arr.length)]
}

function generateSeedData() {
  const coins = ['BTC', 'ETH', 'SOL', 'AVAX', 'ARB', 'OP', 'DOGE', 'INJ']
  const modes = ['DRY', 'LIVE']
  const exitReasons = ['take_profit', 'stop_loss', 'time_stop', 'trend_flip', 'manual']
  const promptVersions = ['2026-01-15-swing', '2026-02-01-tight', '2026-03-15-regime']
  const models = ['qwen-32b', 'claude-3.5', 'gpt-4-turbo']
  
  const triggers = []
  const ai_verdicts = []
  const trades = []
  const exits = []
  const market_regimes = []
  const backtest_predictions = []
  const ai_calibration = []
  
  const baseTime = Date.now() - 7 * 24 * 3600 * 1000 // 7 days ago
  let t = baseTime
  
  for (let i = 0; i < 50; i++) {
    t += randomBetween(2 * 3600 * 1000, 8 * 3600 * 1000) // Every 2-8 hours
    
    const coin = randomPick(coins)
    const pctMoveSpike = randomBetween(0, 5)
    const volumeSpike = randomBetween(0, 4)
    const breakout = randomBetween(0, 0.05)
    const rangeCompression = randomBetween(0, 1)
    const composite = pctMoveSpike * 0.35 + volumeSpike * 0.25 + breakout * 10 * 0.25 + rangeCompression * 0.15
    
    const triggerId = `trigger-${i}`
    triggers.push({
      id: triggerId,
      ts_ms: t,
      coin,
      composite_score: Math.min(10, composite),
      pct_move_spike: pctMoveSpike,
      volume_spike: volumeSpike,
      breakout_score: breakout,
      range_compression: rangeCompression,
      mid_price: coin === 'BTC' ? randomBetween(80000, 85000) : randomBetween(2000, 4000),
      interval: '1h',
      candles_count: 48,
    })
    
    // 60% chance of AI verdict
    if (Math.random() < 0.6) {
      const confidence = randomBetween(55, 95)
      const side = Math.random() < 0.6 ? 'long' : 'short' // Slight long bias
      const summary = `Signal: ${pctMoveSpike > 3 ? 'high move spike' : 'normal range'}. Volume ${volumeSpike > 2 ? 'surging' : 'steady'}`
      
      ai_verdicts.push({
        id: `verdict-${i}`,
        ts_ms: t + randomBetween(30000, 120000), // 30-120 seconds later
        trigger_id: triggerId,
        side,
        confidence,
        summary,
        model: randomPick(models),
        prompt_version: randomPick(promptVersions),
      })
      
      // 70% chance of trade execution
      if (Math.random() < 0.7) {
        const entryPx = triggers[triggers.length - 1].mid_price
        const notional = randomBetween(50, 200)
        const size = notional / entryPx
        const leverage = randomPick([3, 5, 5, 10])
        const riskPct = randomBetween(0.01, 0.05)
        
        const tradeId = `trade-${i}`
        trades.push({
          id: tradeId,
          ts_ms: t + randomBetween(60000, 300000),
          verdict_id: `verdict-${i}`,
          side,
          coin,
          entry_px: entryPx,
          size,
          notional_usd: notional,
          mode: randomPick(modes),
          stop_px: side === 'long' ? entryPx * 0.98 : entryPx * 1.02,
          tp_px: side === 'long' ? entryPx * 1.03 : entryPx * 0.97,
          leverage,
          risk_pct: riskPct,
          trigger_type: 'composite',
          backtest_params: { stopMult: 3.5, rrTarget: 1.0, riskPct: 0.02 },
        })
        
        // Most trades get exits (85%)
        if (Math.random() < 0.85) {
          const holdTime = randomBetween(30 * 60 * 1000, 24 * 3600 * 1000) // 30min to 24h
          const isWin = Math.random() < (confidence > 80 ? 0.65 : 0.45)
          const pnl = isWin ? randomBetween(1, 15) : randomBetween(-12, -1)
          const slippage = randomBetween(0.001, 0.003)
          
          const exitId = `exit-${i}`
          exits.push({
            id: exitId,
            ts_ms: t + randomBetween(60000, 300000) + holdTime,
            trade_id: tradeId,
            exit_px: pnl > 0 ? entryPx * 1.01 : entryPx * 0.99,
            pnl_usd: pnl,
            pnl_pct: pnl / notional,
            hold_ms: holdTime,
            reason: randomPick(exitReasons),
            fees_usd: notional * 0.0045,
            slippage_pct: slippage,
          })
          
          // AI calibration record
          const bucket = confidence >= 90 ? '90-100' : confidence >= 80 ? '80-90' : confidence >= 70 ? '70-80' : confidence >= 60 ? '60-70' : '50-60'
          ai_calibration.push({
            id: `cal-${i}`,
            ts_ms: exits[exits.length - 1].ts_ms,
            verdict_id: `verdict-${i}`,
            predicted_confidence: confidence,
            actual_outcome: pnl > 0 ? 'win' : pnl < 0 ? 'loss' : 'breakeven',
            actual_pnl: pnl,
            confidence_bucket: bucket,
          })
        }
      }
    }
    
    // Market regime snapshot every few entries
    if (i % 3 === 0) {
      const adx = randomBetween(15, 45)
      const atrPct = randomBetween(0.003, 0.02)
      const trend1d = randomPick(['up', 'down', 'range'])
      const trend4h = randomPick(['up', 'down', 'range'])
      const regime = adx > 25 && trend1d === 'up' ? 'trending_up' : 
                     adx > 25 && trend1d === 'down' ? 'trending_down' :
                     atrPct < 0.005 ? 'ranging_low_vol' : 'ranging_high_vol'
      
      market_regimes.push({
        id: `regime-${i}`,
        ts_ms: t,
        coin,
        trend_1d: trend1d,
        trend_4h: trend4h,
        adx_14: adx,
        atr_pct: atrPct,
        regime,
        btc_dominance: randomBetween(0.4, 0.6),
      })
    }
    
    // Backtest predictions for trades
    if (Math.random() < 0.4 && trades.length > exits.length) {
      backtest_predictions.push({
        id: `bt-${trades.length - 1}`,
        ts_ms: t,
        trade_id: trades[trades.length - 1].id,
        predicted_wr: 0.53,
        predicted_pf: randomBetween(0.8, 1.5),
        predicted_ret: randomBetween(-0.1, 0.3),
        params_hash: 'abc123',
      })
    }
  }
  
  const journal = {
    triggers,
    ai_verdicts,
    trades,
    exits,
    market_regimes,
    backtest_predictions,
    ai_calibration,
  }
  
  fs.writeFileSync(JOURNAL_FILE, JSON.stringify(journal, null, 2))
  console.log(`  ✓ Seeded journal with realistic sample data:`)
  console.log(`    Triggers:        ${triggers.length}`)
  console.log(`    AI Verdicts:     ${ai_verdicts.length}`)
  console.log(`    Trades:          ${trades.length}`)
  console.log(`    Exits:           ${exits.length}`)
  console.log(`    Regimes:         ${market_regimes.length}`)
  console.log(`    Backtest Preds:  ${backtest_predictions.length}`)
  console.log(`    Calibration:     ${ai_calibration.length}`)
  console.log(``)
  console.log(`  Run: node scripts/analyze-journal.mjs all`)
}

generateSeedData()
