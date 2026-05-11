// ── Trade Journal: File-based persistence for trigger → verdict → trade → exit lifecycle ──
// Uses JSON files for zero-dependency persistence. SQLite migration available when needed.

import * as fs from 'node:fs'
import * as path from 'node:path'
import * as crypto from 'node:crypto'

const DATA_DIR = path.join(process.cwd(), 'data')
const JOURNAL_FILE = path.join(DATA_DIR, 'trade-journal.json')

// Ensure data directory exists
if (!fs.existsSync(DATA_DIR)) {
  fs.mkdirSync(DATA_DIR, { recursive: true })
}

interface JournalEntry {
  id: string
  ts_ms: number
}

interface JournalData {
  triggers: Array<JournalEntry & {
    coin: string
    composite_score: number
    pct_move_spike?: number
    volume_spike?: number
    breakout_score?: number
    range_compression?: number
    mid_price: number
    interval?: string
    candles_count?: number
  }>
  ai_verdicts: Array<JournalEntry & {
    trigger_id: string
    side: 'long' | 'short' | 'pass'
    confidence: number
    summary: string
    model?: string
    prompt_version?: string
  }>
  trades: Array<JournalEntry & {
    verdict_id: string
    side: 'long' | 'short'
    coin: string
    entry_px: number
    size: number
    notional_usd: number
    mode: 'LIVE' | 'DRY' | 'BACKTEST'
    stop_px?: number
    tp_px?: number
    leverage?: number
    risk_pct?: number
    trigger_type?: string
    backtest_params?: Record<string, unknown>
  }>
  exits: Array<JournalEntry & {
    trade_id: string
    exit_px: number
    pnl_usd: number
    pnl_pct?: number
    hold_ms?: number
    reason: string
    fees_usd?: number
    slippage_pct?: number
  }>
  market_regimes: Array<JournalEntry & {
    coin: string
    trend_1d: string
    trend_4h: string
    adx_14?: number
    atr_pct: number
    regime: string
    btc_dominance?: number
  }>
  backtest_predictions: Array<JournalEntry & {
    trade_id: string
    predicted_wr: number
    predicted_pf: number
    predicted_ret: number
    params_hash: string
  }>
  ai_calibration: Array<JournalEntry & {
    verdict_id: string
    predicted_confidence: number
    actual_outcome: 'win' | 'loss' | 'breakeven'
    actual_pnl: number
    confidence_bucket: string
  }>
}

function loadJournal(): JournalData {
  if (!fs.existsSync(JOURNAL_FILE)) {
    const empty: JournalData = {
      triggers: [],
      ai_verdicts: [],
      trades: [],
      exits: [],
      market_regimes: [],
      backtest_predictions: [],
      ai_calibration: [],
    }
    fs.writeFileSync(JOURNAL_FILE, JSON.stringify(empty, null, 2))
    return empty
  }
  
  try {
    const data = fs.readFileSync(JOURNAL_FILE, 'utf8')
    return JSON.parse(data) as JournalData
  } catch (err) {
    console.error(`Failed to load journal: ${err}`)
    return {
      triggers: [], ai_verdicts: [], trades: [], exits: [],
      market_regimes: [], backtest_predictions: [], ai_calibration: [],
    }
  }
}

function saveJournal(data: JournalData): void {
  // Write to temp file first, then rename for atomic writes
  const tempFile = JOURNAL_FILE + '.tmp'
  fs.writeFileSync(tempFile, JSON.stringify(data, null, 2))
  fs.renameSync(tempFile, JOURNAL_FILE)
}

// ── Writer functions ──

export function recordTrigger(trigger: {
  coin: string
  composite_score: number
  pct_move_spike?: number
  volume_spike?: number
  breakout_score?: number
  range_compression?: number
  mid_price: number
  interval?: string
  candles_count?: number
}): string {
  const id = crypto.randomUUID()
  const journal = loadJournal()
  journal.triggers.push({
    id,
    ts_ms: Date.now(),
    coin: trigger.coin,
    composite_score: trigger.composite_score,
    pct_move_spike: trigger.pct_move_spike,
    volume_spike: trigger.volume_spike,
    breakout_score: trigger.breakout_score,
    range_compression: trigger.range_compression,
    mid_price: trigger.mid_price,
    interval: trigger.interval,
    candles_count: trigger.candles_count,
  })
  saveJournal(journal)
  return id
}

export function recordVerdict(verdict: {
  trigger_id: string
  side: 'long' | 'short' | 'pass'
  confidence: number
  summary: string
  model?: string
  prompt_version?: string
}): string {
  const id = crypto.randomUUID()
  const journal = loadJournal()
  journal.ai_verdicts.push({
    id,
    ts_ms: Date.now(),
    trigger_id: verdict.trigger_id,
    side: verdict.side,
    confidence: verdict.confidence,
    summary: verdict.summary,
    model: verdict.model,
    prompt_version: verdict.prompt_version,
  })
  saveJournal(journal)
  return id
}

export function recordTrade(trade: {
  verdict_id: string
  side: 'long' | 'short'
  coin: string
  entry_px: number
  size: number
  notional_usd: number
  mode: 'LIVE' | 'DRY' | 'BACKTEST'
  stop_px?: number
  tp_px?: number
  leverage?: number
  risk_pct?: number
  trigger_type?: string
  backtest_params?: Record<string, unknown>
}): string {
  const id = crypto.randomUUID()
  const journal = loadJournal()
  journal.trades.push({
    id,
    ts_ms: Date.now(),
    verdict_id: trade.verdict_id,
    side: trade.side,
    coin: trade.coin,
    entry_px: trade.entry_px,
    size: trade.size,
    notional_usd: trade.notional_usd,
    mode: trade.mode,
    stop_px: trade.stop_px,
    tp_px: trade.tp_px,
    leverage: trade.leverage,
    risk_pct: trade.risk_pct,
    trigger_type: trade.trigger_type,
    backtest_params: trade.backtest_params,
  })
  saveJournal(journal)
  return id
}

export function recordExit(exit: {
  trade_id: string
  exit_px: number
  pnl_usd: number
  pnl_pct?: number
  hold_ms?: number
  reason: string
  fees_usd?: number
  slippage_pct?: number
}): string {
  const id = crypto.randomUUID()
  const journal = loadJournal()
  journal.exits.push({
    id,
    ts_ms: Date.now(),
    trade_id: exit.trade_id,
    exit_px: exit.exit_px,
    pnl_usd: exit.pnl_usd,
    pnl_pct: exit.pnl_pct,
    hold_ms: exit.hold_ms,
    reason: exit.reason,
    fees_usd: exit.fees_usd,
    slippage_pct: exit.slippage_pct,
  })
  saveJournal(journal)
  return id
}

export function recordMarketRegime(regime: {
  coin: string
  trend_1d: string
  trend_4h: string
  adx_14?: number
  atr_pct: number
  regime: string
  btc_dominance?: number
}): string {
  const id = crypto.randomUUID()
  const journal = loadJournal()
  journal.market_regimes.push({
    id,
    ts_ms: Date.now(),
    coin: regime.coin,
    trend_1d: regime.trend_1d,
    trend_4h: regime.trend_4h,
    adx_14: regime.adx_14,
    atr_pct: regime.atr_pct,
    regime: regime.regime,
    btc_dominance: regime.btc_dominance,
  })
  saveJournal(journal)
  return id
}

export function recordBacktestPrediction(prediction: {
  trade_id: string
  predicted_wr: number
  predicted_pf: number
  predicted_ret: number
  params_hash: string
}): string {
  const id = crypto.randomUUID()
  const journal = loadJournal()
  journal.backtest_predictions.push({
    id,
    ts_ms: Date.now(),
    trade_id: prediction.trade_id,
    predicted_wr: prediction.predicted_wr,
    predicted_pf: prediction.predicted_pf,
    predicted_ret: prediction.predicted_ret,
    params_hash: prediction.params_hash,
  })
  saveJournal(journal)
  return id
}

export function recordCalibration(calibration: {
  verdict_id: string
  predicted_confidence: number
  actual_outcome: 'win' | 'loss' | 'breakeven'
  actual_pnl: number
  confidence_bucket: string
}): string {
  const id = crypto.randomUUID()
  const journal = loadJournal()
  journal.ai_calibration.push({
    id,
    ts_ms: Date.now(),
    verdict_id: calibration.verdict_id,
    predicted_confidence: calibration.predicted_confidence,
    actual_outcome: calibration.actual_outcome,
    actual_pnl: calibration.actual_pnl,
    confidence_bucket: calibration.confidence_bucket,
  })
  saveJournal(journal)
  return id
}

// ── Read functions for analysis ──

export function getTriggerProfitability(): Array<{
  trigger_type: string | null
  trades: number
  win_rate: number
  avg_pnl: number
  total_pnl: number
  avg_trigger_score: number
}> {
  const journal = loadJournal()
  
  // Group trades by trigger_type
  const triggerMap = new Map<string | null, {
    trades: number
    wins: number
    total_pnl: number
    scores: number[]
  }>()
  
  for (const trade of journal.trades) {
    const exit = journal.exits.find(e => e.trade_id === trade.id)
    if (!exit) continue
    
    const key = trade.trigger_type || null
    const group = triggerMap.get(key) || { trades: 0, wins: 0, total_pnl: 0, scores: [] }
    
    group.trades++
    if (exit.pnl_usd > 0) group.wins++
    group.total_pnl += exit.pnl_usd
    
    // Get trigger score from the chain: trade → verdict → trigger
    const verdict = journal.ai_verdicts.find(v => v.id === trade.verdict_id)
    if (verdict) {
      const trigger = journal.triggers.find(t => t.id === verdict.trigger_id)
      if (trigger) {
        group.scores.push(trigger.composite_score)
      }
    }
    
    triggerMap.set(key, group)
  }
  
  return Array.from(triggerMap.entries()).map(([type, group]) => ({
    trigger_type: type,
    trades: group.trades,
    win_rate: group.wins / group.trades,
    avg_pnl: group.total_pnl / group.trades,
    total_pnl: group.total_pnl,
    avg_trigger_score: group.scores.length > 0 ? group.scores.reduce((a, b) => a + b, 0) / group.scores.length : 0,
  })).sort((a, b) => b.total_pnl - a.total_pnl)
}

export function getAICalibration(): Array<{
  confidence_bucket: string
  verdicts: number
  actual_wr: number
  avg_stated_conf: number
  avg_pnl: number
}> {
  const journal = loadJournal()
  
  const bucketMap = new Map<string, {
    verdicts: number
    wins: number
    stated_conf_sum: number
    pnl_sum: number
  }>()
  
  for (const cal of journal.ai_calibration) {
    const bucket = cal.confidence_bucket
    const group = bucketMap.get(bucket) || { verdicts: 0, wins: 0, stated_conf_sum: 0, pnl_sum: 0 }
    
    group.verdicts++
    if (cal.actual_outcome === 'win') group.wins++
    group.stated_conf_sum += cal.predicted_confidence
    group.pnl_sum += cal.actual_pnl
    
    bucketMap.set(bucket, group)
  }
  
  return Array.from(bucketMap.entries()).map(([bucket, group]) => ({
    confidence_bucket: bucket,
    verdicts: group.verdicts,
    actual_wr: group.wins / group.verdicts * 100,
    avg_stated_conf: group.stated_conf_sum / group.verdicts,
    avg_pnl: group.pnl_sum / group.verdicts,
  })).sort((a, b) => a.confidence_bucket.localeCompare(b.confidence_bucket))
}

export function getLiveVsBacktestSkew(): Array<{
  coin: string
  trades: number
  live_avg_pnl: number
  backtest_predicted: number | null
  skew: number | null
  assessment: 'in_line' | 'underperforming' | 'degraded' | 'no_backtest'
}> {
  const journal = loadJournal()
  
  const coinMap = new Map<string, {
    trades: number
    live_pnl_sum: number
    bt_predicted_sum: number
    bt_count: number
  }>()
  
  for (const trade of journal.trades) {
    const exit = journal.exits.find(e => e.trade_id === trade.id)
    if (!exit) continue
    
    const pred = journal.backtest_predictions.find(p => p.trade_id === trade.id)
    
    const group = coinMap.get(trade.coin) || { trades: 0, live_pnl_sum: 0, bt_predicted_sum: 0, bt_count: 0 }
    
    group.trades++
    group.live_pnl_sum += exit.pnl_usd
    if (pred) {
      group.bt_predicted_sum += pred.predicted_ret
      group.bt_count++
    }
    
    coinMap.set(trade.coin, group)
  }
  
  return Array.from(coinMap.entries()).map(([coin, group]) => {
    const live_avg = group.live_pnl_sum / group.trades
    const bt_avg = group.bt_count > 0 ? group.bt_predicted_sum / group.bt_count : null
    const skew = bt_avg !== null ? live_avg - bt_avg : null
    
    let assessment: 'in_line' | 'underperforming' | 'degraded' | 'no_backtest' = 'no_backtest'
    if (bt_avg !== null) {
      if (live_avg > bt_avg * 0.8) assessment = 'in_line'
      else if (live_avg > 0) assessment = 'underperforming'
      else assessment = 'degraded'
    }
    
    return {
      coin,
      trades: group.trades,
      live_avg_pnl: live_avg,
      backtest_predicted: bt_avg,
      skew,
      assessment,
    }
  })
}

export function getRegimeEdge(): Array<{
  regime: string
  trades: number
  win_rate: number
  avg_pnl: number
  total_pnl: number
}> {
  const journal = loadJournal()
  
  const regimeMap = new Map<string, {
    trades: number
    wins: number
    total_pnl: number
  }>()
  
  for (const trade of journal.trades) {
    const exit = journal.exits.find(e => e.trade_id === trade.id)
    if (!exit) continue
    
    // Find the regime closest to trade entry time
    const regimes = journal.market_regimes
      .filter(r => r.coin === trade.coin && r.ts_ms <= trade.ts_ms)
      .sort((a, b) => b.ts_ms - a.ts_ms)
    
    const regime = regimes.length > 0 ? regimes[0].regime : 'unknown'
    
    const group = regimeMap.get(regime) || { trades: 0, wins: 0, total_pnl: 0 }
    
    group.trades++
    if (exit.pnl_usd > 0) group.wins++
    group.total_pnl += exit.pnl_usd
    
    regimeMap.set(regime, group)
  }
  
  return Array.from(regimeMap.entries()).map(([regime, group]) => ({
    regime,
    trades: group.trades,
    win_rate: group.wins / group.trades * 100,
    avg_pnl: group.total_pnl / group.trades,
    total_pnl: group.total_pnl,
  })).sort((a, b) => b.total_pnl - a.total_pnl)
}

export function getRecentTrades(limit = 50): Array<{
  id: string
  ts_ms: number
  side: string
  coin: string
  entry_px: number
  mode: string
  pnl_usd?: number
  reason?: string
  exit_px?: number
  hold_ms?: number
}> {
  const journal = loadJournal()
  
  return journal.trades
    .sort((a, b) => b.ts_ms - a.ts_ms)
    .slice(0, limit)
    .map(trade => {
      const exit = journal.exits.find(e => e.trade_id === trade.id)
      return {
        id: trade.id,
        ts_ms: trade.ts_ms,
        side: trade.side,
        coin: trade.coin,
        entry_px: trade.entry_px,
        mode: trade.mode,
        pnl_usd: exit?.pnl_usd,
        reason: exit?.reason,
        exit_px: exit?.exit_px,
        hold_ms: exit?.hold_ms,
      }
    })
}
