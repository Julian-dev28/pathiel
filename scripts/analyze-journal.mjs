// ── Analysis CLI: Answers the hard questions about what's actually profitable ──
// Run: node scripts/analyze-journal.mjs [command]
// Commands: summary, triggers, calibration, regimes, skew, all

import { readFileSync, existsSync } from 'node:fs'
import { join } from 'node:path'

const JOURNAL_FILE = join(process.cwd(), 'data', 'trade-journal.json')

function loadJournal() {
  if (!existsSync(JOURNAL_FILE)) {
    console.log('⚠  No journal found at data/trade-journal.json')
    console.log('   The journal is populated automatically when the scanner runs in DRY or LIVE mode.')
    console.log('   Run: node scripts/scanner-daemon.mjs')
    console.log('')
    console.log('   Or manually seed with sample data to test the analysis system:')
    console.log('   node --experimental-specifier-resolution=node scripts/seed-journal.mjs')
    process.exit(0)
  }
  const raw = readFileSync(JOURNAL_FILE, 'utf8')
  return JSON.parse(raw)
}

const fmtPct = (x) => `${x > 0 ? '+' : ''}${(x * 100).toFixed(2)}%`
const fmtUsd = (x) => `${x >= 0 ? '+$' : '-$'}${Math.abs(x).toFixed(2)}`
const fmtMs = (ms) => {
  const h = Math.floor(ms / 3600000)
  const m = Math.floor((ms % 3600000) / 60000)
  return h > 0 ? `${h}h ${m}m` : `${m}m`
}

function showSummary(journal) {
  const { triggers, ai_verdicts, trades, exits, market_regimes, backtest_predictions, ai_calibration } = journal
  
  console.log('══════════════════════════════════════════════')
  console.log('             TRADE JOURNAL SUMMARY')
  console.log('══════════════════════════════════════════════')
  console.log()
  console.log(`  Triggers fired:        ${triggers.length.toLocaleString()}`)
  console.log(`  AI verdicts:           ${ai_verdicts.length.toLocaleString()}`)
  console.log(`  Trades recorded:       ${trades.length.toLocaleString()}`)
  console.log(`     LIVE:               ${trades.filter(t => t.mode === 'LIVE').length}`)
  console.log(`     DRY:                ${trades.filter(t => t.mode === 'DRY').length}`)
  console.log(`  Trades closed:         ${exits.length.toLocaleString()}`)
  console.log(`  Regime snapshots:      ${market_regimes.length.toLocaleString()}`)
  console.log(`  Backtest predictions:  ${backtest_predictions.length.toLocaleString()}`)
  console.log(`  Calibration records:   ${ai_calibration.length.toLocaleString()}`)
  console.log()
  
  if (exits.length === 0) {
    console.log('  → No closed trades yet. Run the scanner in DRY mode to start collecting outcomes.')
    return
  }
  
  const totalPnl = exits.reduce((s, e) => s + e.pnl_usd, 0)
  const wins = exits.filter(e => e.pnl_usd > 0)
  const losses = exits.filter(e => e.pnl_usd <= 0)
  const winSum = wins.reduce((s, e) => s + e.pnl_usd, 0)
  const losSum = losses.reduce((s, e) => s + e.pnl_usd, 0)
  const pf = losSum < 0 ? (winSum / Math.abs(losSum)) : Infinity
  const validHolds = exits.filter(e => e.hold_ms && e.hold_ms > 0)
  const avgHold = validHolds.length > 0 ? validHolds.reduce((s, e) => s + e.hold_ms, 0) / validHolds.length : 0
  
  console.log('  ── Performance ──')
  console.log(`  Total P&L:           ${fmtUsd(totalPnl)}`)
  console.log(`  Win Rate:            ${(wins.length / exits.length * 100).toFixed(1)}%`)
  console.log(`  Profit Factor:       ${isFinite(pf) ? pf.toFixed(2) : '∞'}`)
  if (avgHold > 0) console.log(`  Avg Hold Time:       ${fmtMs(avgHold)}`)
  if (wins.length > 0) console.log(`  Avg Win:             ${fmtUsd(winSum / wins.length)}`)
  if (losses.length > 0) console.log(`  Avg Loss:            ${fmtUsd(losSum / losses.length)}`)
  console.log(`  Best Trade:          ${fmtUsd(Math.max(...exits.map(e => e.pnl_usd)))}`)
  console.log(`  Worst Trade:         ${fmtUsd(Math.min(...exits.map(e => e.pnl_usd)))}`)
  console.log()
  
  // Equity curve summary
  let peak = 0, maxDD = 0, running = 0
  for (const e of exits.sort((a, b) => a.ts_ms - b.ts_ms)) {
    running += e.pnl_usd
    peak = Math.max(peak, running)
    const dd = peak > 0 ? (peak - running) / peak : 0
    maxDD = Math.max(maxDD, dd)
  }
  console.log(`  Max Drawdown:        ${fmtPct(-maxDD)}`)
  console.log(`  Net from closed:     ${fmtUsd(running)}`)
  console.log()
}

function getDominantTrigger(trigger) {
  const scores = [
    { name: 'pctMoveSpike', value: trigger.pct_move_spike ?? 0 },
    { name: 'volumeSpike', value: trigger.volume_spike ?? 0 },
    { name: 'breakout', value: trigger.breakout_score ?? 0 },
    { name: 'rangeCompression', value: trigger.range_compression ?? 0 },
  ]
  scores.sort((a, b) => b.value - a.value)
  return scores[0].name
}

function showTriggerAnalysis(journal) {
  const { triggers, ai_verdicts, trades, exits } = journal
  
  console.log('══════════════════════════════════════════════')
  console.log('         TRIGGER PROFITABILITY ANALYSIS')
  console.log('══════════════════════════════════════════════')
  console.log()
  
  if (triggers.length === 0) {
    console.log('  No triggers recorded yet.')
    return
  }
  
  // Group triggers by dominant component
  const triggerFreq = new Map()
  for (const t of triggers) {
    const dominant = getDominantTrigger(t)
    const group = triggerFreq.get(dominant) || { count: 0, scores: [] }
    group.count++
    group.scores.push(t.composite_score)
    triggerFreq.set(dominant, group)
  }
  
  console.log('  Trigger Frequency (from scanner):')
  console.log()
  for (const [name, group] of [...triggerFreq.entries()].sort((a, b) => b[1].count - a[1].count)) {
    const avgScore = group.scores.reduce((a, b) => a + b, 0) / group.scores.length
    console.log(`  ${name.padEnd(25)} ${group.count.toString().padStart(5)} triggers  avg score: ${avgScore.toFixed(2)}`)
  }
  console.log()
  
  if (exits.length === 0) {
    console.log('  → No closed trades yet to attribute profitability.')
    console.log('  Run scanner in DRY mode and let trades resolve to get attribution data.')
    return
  }
  
  // Attribute closed trades to their dominant trigger
  const tradeByTrigger = new Map()
  
  for (const trade of trades) {
    const exit = exits.find(e => e.trade_id === trade.id)
    if (!exit) continue
    
    const verdict = ai_verdicts.find(v => v.id === trade.verdict_id)
    if (!verdict) continue
    
    const trigger = triggers.find(t => t.id === verdict.trigger_id)
    if (!trigger) continue
    
    const dominant = getDominantTrigger(trigger)
    const group = tradeByTrigger.get(dominant) || { count: 0, wins: 0, totalPnl: 0 }
    
    group.count++
    if (exit.pnl_usd > 0) group.wins++
    group.totalPnl += exit.pnl_usd
    
    tradeByTrigger.set(dominant, group)
  }
  
  if (tradeByTrigger.size === 0) {
    console.log('  → No trades could be attributed to triggers yet.')
    console.log('  Make sure trigger_id → verdict_id → trade_id chain is intact.')
    return
  }
  
  console.log('  Trigger Profitability (attributed to closed trades):')
  console.log()
  console.log('  ' + 'Trigger'.padEnd(20) + 'Trades'.padStart(8) + '  WR'.padStart(8) + '  Total PnL'.padStart(12) + '  Avg PnL'.padStart(12))
  console.log('  ' + '-'.repeat(70))
  
  for (const [name, group] of [...tradeByTrigger.entries()].sort((a, b) => b[1].totalPnl - a[1].totalPnl)) {
    const wr = (group.wins / group.count * 100).toFixed(1) + '%'
    const avgPnl = fmtUsd(group.totalPnl / group.count)
    console.log(`  ${name.padEnd(20)}${group.count.toString().padStart(6)}  ${wr.padStart(7)}  ${fmtUsd(group.totalPnl).padStart(10)}  ${avgPnl.padStart(10)}`)
  }
  console.log()
  
  // Key insight
  const sorted = [...tradeByTrigger.entries()].sort((a, b) => b[1].totalPnl - a[1].totalPnl)
  if (sorted.length >= 2) {
    const best = sorted[0]
    const worst = sorted[sorted.length - 1]
    console.log(`  Key: ${best[0]} is your best trigger (${fmtUsd(best[1].totalPnl)} total, ${(best[1].wins/best[1].count*100).toFixed(0)}% WR).`)
    if (worst[1].totalPnl < 0) {
      console.log(`       ${worst[0]} is your worst (${fmtUsd(worst[1].totalPnl)} total). Consider adjusting its weight or threshold.`)
    }
  }
}

function getConfidenceBucket(confidence) {
  if (confidence >= 90) return '90-100'
  if (confidence >= 80) return '80-90'
  if (confidence >= 70) return '70-80'
  if (confidence >= 60) return '60-70'
  return '50-60'
}

function showAICalibration(journal) {
  const { ai_verdicts, trades, exits, ai_calibration } = journal
  
  console.log('══════════════════════════════════════════════')
  console.log('           AI CONFIDENCE CALIBRATION')
  console.log('══════════════════════════════════════════════')
  console.log()
  
  if (exits.length === 0 && ai_calibration.length === 0) {
    console.log('  → No calibration data yet. Closed trades are needed to calibrate AI confidence.')
    return
  }
  
  const buckets = new Map()
  
  // Build from existing calibration records (preferred)
  if (ai_calibration.length > 0) {
    for (const cal of ai_calibration) {
      const bucket = cal.confidence_bucket
      const group = buckets.get(bucket) || { count: 0, wins: 0, statedSum: 0, totalPnl: 0 }
      group.count++
      if (cal.actual_outcome === 'win') group.wins++
      group.statedSum += cal.predicted_confidence
      group.totalPnl += cal.actual_pnl
      buckets.set(bucket, group)
    }
  } else {
    // Build from trades + verdicts directly
    for (const trade of trades) {
      const exit = exits.find(e => e.trade_id === trade.id)
      if (!exit) continue
      
      const verdict = ai_verdicts.find(v => v.id === trade.verdict_id)
      if (!verdict) continue
      
      const bucket = getConfidenceBucket(verdict.confidence)
      const group = buckets.get(bucket) || { count: 0, wins: 0, statedSum: 0, totalPnl: 0 }
      
      group.count++
      if (exit.pnl_usd > 0) group.wins++
      group.statedSum += verdict.confidence
      group.totalPnl += exit.pnl_usd
      
      buckets.set(bucket, group)
    }
  }
  
  if (buckets.size === 0) {
    console.log('  → No confidence data found in trades or calibration records.')
    return
  }
  
  console.log('  Confidence'.padEnd(18) + 'Verdicts'.padStart(10) + '  Actual WR'.padStart(12) + '  Avg Stated'.padStart(12) + '  Total PnL'.padStart(12))
  console.log('  ' + '-'.repeat(70))
  
  const orderedBuckets = ['50-60', '60-70', '70-80', '80-90', '90-100']
  for (const bucket of orderedBuckets) {
    const group = buckets.get(bucket)
    if (!group) continue
    const avgStated = (group.statedSum / group.count).toFixed(1) + '%'
    const wr = (group.wins / group.count * 100).toFixed(1) + '%'
    console.log(`  ${bucket.padEnd(17)}${group.count.toString().padStart(8)}  ${wr.padStart(10)}  ${avgStated.padStart(10)}  ${fmtUsd(group.totalPnl).padStart(10)}`)
  }
  console.log()
  
  // Calibration check
  console.log('  Calibration Check:')
  for (const bucket of orderedBuckets) {
    const group = buckets.get(bucket)
    if (!group || group.count < 5) continue
    const [low, high] = bucket.split('-').map(Number)
    const expectedMid = (low + high) / 2
    const actualWr = group.wins / group.count
    const delta = actualWr - expectedMid / 100
    
    if (Math.abs(delta) > 0.15) {
      const direction = delta > 0 ? 'UNDERCONFIDENT' : 'OVERCONFIDENT'
      console.log(`    ⚠ ${bucket}% bucket: AI says ~${expectedMid.toFixed(0)}% but actual is ${(actualWr*100).toFixed(0)}% → ${direction}`)
    } else {
      console.log(`    ✓ ${bucket}% bucket: Well calibrated (${(actualWr*100).toFixed(0)}% actual vs ${expectedMid.toFixed(0)}% expected)`)
    }
  }
  console.log()
  
  // Overall reliability
  let totalCount = 0, totalWins = 0
  for (const [, group] of buckets) {
    totalCount += group.count
    totalWins += group.wins
  }
  if (totalCount >= 20) {
    const overallWR = totalWins / totalCount * 100
    console.log(`  Overall: ${(overallWR).toFixed(1)}% win rate across ${totalCount} closed trades.`)
    if (overallWR > 55) {
      console.log('  → AI is generating positive-edge trades statistically.')
    } else if (overallWR < 45) {
      console.log('  ⚠ AI win rate below 45% -- edge is marginal or nonexistent.')
    }
  }
}

function showRegimeAnalysis(journal) {
  const { trades, exits, market_regimes } = journal
  
  console.log('══════════════════════════════════════════════')
  console.log('           REGIME-SPECIFIC EDGE')
  console.log('══════════════════════════════════════════════')
  console.log()
  
  if (market_regimes.length === 0) {
    console.log('  → No regime snapshots recorded yet.')
    console.log('  Regime detection should run as part of the scanner or backtest flow.')
    console.log('  See docs/journal-schema.md for the market_regimes table structure.')
    return
  }
  
  if (exits.length === 0) {
    console.log('  → No closed trades to attribute to regimes.')
    return
  }
  
  // Attribute each trade to the regime at entry time
  const regimeGroups = new Map()
  
  for (const trade of trades) {
    const exit = exits.find(e => e.trade_id === trade.id)
    if (!exit) continue
    
    // Find closest regime snapshot at or before trade entry
    const regime = market_regimes
      .filter(r => r.coin === trade.coin && r.ts_ms <= trade.ts_ms)
      .sort((a, b) => b.ts_ms - a.ts_ms)[0]
    
    const regimeName = regime ? regime.regime : 'unknown'
    const group = regimeGroups.get(regimeName) || { count: 0, wins: 0, totalPnl: 0 }
    
    group.count++
    if (exit.pnl_usd > 0) group.wins++
    group.totalPnl += exit.pnl_usd
    
    regimeGroups.set(regimeName, group)
  }
  
  if (regimeGroups.size === 0) {
    console.log('  → No trades could be attributed to regimes.')
    return
  }
  
  console.log('  Regime'.padEnd(25) + 'Trades'.padStart(8) + '  WR'.padStart(8) + '  Total PnL'.padStart(12) + '  Avg PnL'.padStart(12))
  console.log('  ' + '-'.repeat(70))
  
  for (const [regime, group] of [...regimeGroups.entries()].sort((a, b) => b[1].totalPnl - a[1].totalPnl)) {
    const wr = (group.wins / group.count * 100).toFixed(1) + '%'
    const avgPnl = fmtUsd(group.totalPnl / group.count)
    console.log(`  ${regime.padEnd(24)}${group.count.toString().padStart(8)}  ${wr.padStart(7)}  ${fmtUsd(group.totalPnl).padStart(10)}  ${avgPnl.padStart(10)}`)
  }
  console.log()
  
  // Insight
  const sorted = [...regimeGroups.entries()].sort((a, b) => b[1].totalPnl - a[1].totalPnl)
  if (sorted.length >= 2) {
    const best = sorted[0]
    const worst = sorted[sorted.length - 1]
    if (best[1].totalPnl > 0 && worst[1].totalPnl < 0) {
      console.log(`  → Best regime: ${best[0]} (${fmtUsd(best[1].totalPnl)})`)
      console.log(`  → Worst regime: ${worst[0]} (${fmtUsd(worst[1].totalPnl)})`)
      console.log(`  Consider blocking entries in ${worst[0]} regime.`)
    }
  }
}

function showSkewAnalysis(journal) {
  const { trades, exits, backtest_predictions } = journal
  
  console.log('══════════════════════════════════════════════')
  console.log('       LIVE vs. BACKTEST PERFORMANCE SKEW')
  console.log('══════════════════════════════════════════════')
  console.log()
  
  if (backtest_predictions.length === 0) {
    console.log('  → No backtest predictions recorded yet.')
    console.log('  Run: node scripts/backtest.mjs [coin] and store predictions via recordBacktestPrediction().')
    console.log('  Without backtest predictions, you cannot measure live vs. expected performance.')
    return
  }
  
  if (exits.length === 0) {
    console.log('  → No closed trades to compare against backtest.')
    return
  }
  
  // Group by coin
  const coinGroups = new Map()
  
  for (const trade of trades) {
    const exit = exits.find(e => e.trade_id === trade.id)
    if (!exit) continue
    
    const pred = backtest_predictions.find(p => p.trade_id === trade.id)
    
    const group = coinGroups.get(trade.coin) || {
      livePnl: 0, btPred: 0, btCount: 0, count: 0, wins: 0
    }
    
    group.livePnl += exit.pnl_usd
    group.count++
    if (exit.pnl_usd > 0) group.wins++
    if (pred) {
      group.btPred += pred.predicted_ret
      group.btCount++
    }
    
    coinGroups.set(trade.coin, group)
  }
  
  console.log('  Coin'.padEnd(12) + 'Trades'.padStart(8) + '  Live Avg'.padStart(12) + '  BT Pred'.padStart(12) + '  Skew'.padStart(10) + '  Assessment'.padStart(14))
  console.log('  ' + '-'.repeat(76))
  
  for (const [coin, group] of coinGroups.entries()) {
    const liveAvg = group.livePnl / group.count
    const btAvg = group.btCount > 0 ? group.btPred / group.btCount : null
    const skew = btAvg !== null ? liveAvg - btAvg : null
    
    let assessment = 'no_bt'
    if (btAvg !== null) {
      if (liveAvg > btAvg * 0.8) assessment = 'in_line'
      else if (liveAvg > 0) assessment = 'underperform'
      else assessment = 'degraded'
    }
    
    const liveStr = fmtUsd(liveAvg)
    const btStr = btAvg !== null ? fmtUsd(btAvg) : 'N/A'
    const skewStr = skew !== null ? fmtUsd(skew) : 'N/A'
    
    console.log(`  ${coin.padEnd(11)}${group.count.toString().padStart(7)}  ${liveStr.padStart(10)}  ${btStr.padStart(10)}  ${skewStr.padStart(9)}  ${assessment.padStart(12)}`)
  }
  console.log()
  
  if (coinGroups.size > 0) {
    const totalLive = [...coinGroups.values()].reduce((s, g) => s + g.livePnl, 0)
    const totalBt = [...coinGroups.values()].reduce((s, g) => s + (g.btCount > 0 ? g.btPred : 0), 0)
    const totalBtCount = [...coinGroups.values()].reduce((s, g) => s + g.btCount, 0)
    
    if (totalBtCount > 0) {
      const skew = totalLive - totalBt
      console.log(`  Overall: Live ${fmtUsd(totalLive)} vs BT expected ${fmtUsd(totalBt)} → skew ${fmtUsd(skew)}`)
      if (Math.abs(skew) > 100) {
        console.log('  ⚠ Significant skew. Investigate: slippage, timing differences, regime shift.')
      } else {
        console.log('  ✓ Skew is within acceptable range.')
      }
    } else {
      console.log('  → No trades have backtest predictions attached.')
    }
  }
}

// ── Main ──

const command = process.argv[2] || 'summary'

const journal = loadJournal()

switch (command) {
  case 'summary':
    showSummary(journal)
    break
  case 'triggers':
    showTriggerAnalysis(journal)
    break
  case 'calibration':
    showAICalibration(journal)
    break
  case 'regimes':
    showRegimeAnalysis(journal)
    break
  case 'skew':
    showSkewAnalysis(journal)
    break
  case 'all':
    showSummary(journal)
    console.log('\n')
    showTriggerAnalysis(journal)
    console.log('\n')
    showAICalibration(journal)
    console.log('\n')
    showRegimeAnalysis(journal)
    console.log('\n')
    showSkewAnalysis(journal)
    break
  default:
    console.log(`Unknown command: ${command}`)
    console.log('')
    console.log('Available commands:')
    console.log('  summary      - Overview of journal and aggregate performance')
    console.log('  triggers     - Which scanner triggers are actually profitable')
    console.log('  calibration  - AI confidence vs. actual outcomes')
    console.log('  regimes      - Performance by market regime')
    console.log('  skew         - Live performance vs. backtest expectations')
    console.log('  all          - Run all analyses')
    process.exit(1)
}
