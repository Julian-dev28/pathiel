// Auto-executor: validates through risk gates, sizes via Kelly, executes LIVE

import type { AgentAnalysis } from './memory'
import { memory } from './memory'
import { evalAllGates, type GateResults } from './risk-gates'
import { readAgentConfig as readConfig } from './config-store'
import { hlCall, fetchAccountState } from '../hl-client'
import {
  placeHLOrder, placeHLTriggerOrder, setLeverage, getCoinIndex, getHLATR,
  HL_LEVERAGE,
} from '../hyperliquid'
import * as crypto from 'crypto'

const SL_ATR_MULT = 3.5
const TP_ATR_MULT = 1.0

export type ExecutionResult = {
  executed: boolean
  analysisId: string
  mode: string
  orderId?: string
  blockedBy?: string[]
  gateResults?: GateResults
  reason?: string
  sizeUSD?: number
  entryPx?: number
  stopPx?: number
  tpPx?: number
}

function kellySize(
  confidence: number,
  equity: number,
  rewardRiskRatio: number,
  maxTradeNotional: number,
): number {
  const p = confidence
  const q = 1 - p
  const b = rewardRiskRatio
  const fStar = Math.max(0, (p * b - q) / b)
  const halfKelly = fStar / 2
  const notional = halfKelly * equity
  return Math.min(notional, maxTradeNotional)
}

const MAJOR_VOLUMES = new Map<string, number>([
  ['BTC', 1e8], ['ETH', 1e8], ['SOL', 1e8], ['BNB', 1e8],
  ['XRP', 1e8], ['DOGE', 1e8], ['ADA', 1e8], ['AVAX', 1e8],
])
function getMarketVolume24h(coin: string): number {
  return MAJOR_VOLUMES.get(coin) ?? 1e7
}

export async function maybeExecute(analysis: AgentAnalysis): Promise<ExecutionResult> {
  const config = await readConfig()
  const mode = config.mode === 'OFF' ? 'OFF' : (config.mode as string) || 'OFF'

  if (mode === 'OFF') {
    return { executed: false, mode, analysisId: analysis.id, reason: 'mode_off' }
  }

  // Idempotency: don't double-execute if research route already fired this analysis
  const alreadyExecuted = memory.getRecentTrades(100).find(
    t => t.analysisId === analysis.id && t.sizeUSD > 0
  )
  if (alreadyExecuted) {
    return { executed: false, mode, analysisId: analysis.id, reason: 'already_executed', orderId: alreadyExecuted.orderId }
  }

  const user = process.env.HYPERLIQUID_MASTER_ADDRESS || process.env.HYPERLIQUID_WALLET_ADDRESS || ''
  const state = await fetchAccountState(user)
  const { equity, totalNtl: totalOpenNotional, assetPositions } = state

  // Update daily PnL from live equity — resets baseline at UTC midnight
  memory.trackDailyPnl(equity)
  const dailyPnl = memory.getDailyPnl()

  const positions = assetPositions.map(p => ({
    coin: p.coin,
    side: parseFloat(p.szi) > 0 ? 'long' : 'short',
    sizeUSD: Math.abs(parseFloat(p.szi)) * (analysis.entryPx ?? 0),
  }))

  const rewardRisk = analysis.tpPx && analysis.stopPx && analysis.entryPx
    ? Math.abs(analysis.tpPx - analysis.entryPx) / Math.abs(analysis.entryPx - analysis.stopPx)
    : 1.0
  const rawSize = kellySize(analysis.confidence, equity, rewardRisk, Number(config.maxTradeNotionalUsd) || 200)
  const tradeNotionalUSD = rawSize > 0 ? rawSize : (analysis.entryPx ?? 0) * 0.001

  const recentTrades = memory.getRecentTrades(10)
  const lastTradeForCoin = recentTrades.find(t => t.coin === analysis.coin)
  const lastTradeTime = lastTradeForCoin?.executedAt

  const hasBinaryNews = analysis.newsContext
    ? /fed|fomc|cpi|rate|earnings|hack|exploit|SEC/i.test(analysis.newsContext)
    : false

  const ctx = {
    confidence: analysis.confidence,
    currentPositions: positions,
    tradeNotionalUSD,
    dailyPnl,
    marketVolume24hUSD: getMarketVolume24h(analysis.coin),
    coin: analysis.coin,
    tradeSide: (analysis.side ?? 'long') as 'long' | 'short',
    hasBinaryNewsRisk: hasBinaryNews,
    equity,
    totalOpenNotional,
  }

  const { results, blocked, blockReasons } = evalAllGates(ctx, config, lastTradeTime)

  if (blocked) {
    memory.recordTrade({
      id: crypto.randomUUID(),
      analysisId: analysis.id,
      coin: analysis.coin,
      side: (analysis.side ?? 'long') as 'long' | 'short',
      entryPx: analysis.entryPx ?? 0,
      sizeUSD: 0,
      executedAt: Date.now(),
    })

    return { executed: false, mode, analysisId: analysis.id, blockedBy: blockReasons, gateResults: results }
  }

  if (!process.env.HYPERLIQUID_PRIVATE_KEY) {
    return { executed: false, mode, analysisId: analysis.id, reason: 'private_key_missing' }
  }

  const coin = analysis.coin
  const isBuy = analysis.side === 'long'

  // Fetch live mid price — never use stale analysis.entryPx for execution
  const allMids = await hlCall<Record<string, string>>({ type: 'allMids' })
  const midPrice = parseFloat(allMids[coin] ?? '0')
  if (midPrice <= 0) {
    return { executed: false, mode, analysisId: analysis.id, reason: `invalid_price_for_${coin}` }
  }

  // Kelly gives margin amount; multiply by leverage for position notional
  const positionNotional = tradeNotionalUSD * HL_LEVERAGE
  const sizeInCoin = positionNotional / midPrice

  const [{ index: assetIdx }, atr] = await Promise.all([
    getCoinIndex(coin),
    getHLATR('4h', 14, coin),
  ])

  await setLeverage(assetIdx, HL_LEVERAGE)
  const orderRes = await placeHLOrder(isBuy, sizeInCoin, midPrice, coin, assetIdx)

  if (!orderRes.ok) {
    return {
      executed: false, mode, analysisId: analysis.id,
      reason: `order_failed: ${orderRes.error ?? 'unknown'}`,
      gateResults: results,
    }
  }

  // Place SL + TP brackets using 4h ATR
  if (atr > 0 && sizeInCoin > 0) {
    const slPx = isBuy ? midPrice - atr * SL_ATR_MULT : midPrice + atr * SL_ATR_MULT
    const tpPx = isBuy ? midPrice + atr * TP_ATR_MULT : midPrice - atr * TP_ATR_MULT
    // Place sequentially so HL nonces increment correctly
    await placeHLTriggerOrder(isBuy, sizeInCoin, slPx, 'sl', assetIdx)
    await placeHLTriggerOrder(isBuy, sizeInCoin, tpPx, 'tp', assetIdx)
  }

  memory.recordTrade({
    id: crypto.randomUUID(),
    analysisId: analysis.id,
    coin,
    side: (analysis.side ?? 'long') as 'long' | 'short',
    entryPx: midPrice,
    sizeUSD: positionNotional,
    orderId: orderRes.orderId,
    executedAt: Date.now(),
  })

  return {
    executed: true, mode, analysisId: analysis.id,
    orderId: orderRes.orderId, gateResults: results,
    sizeUSD: positionNotional,
    entryPx: midPrice,
    stopPx: atr > 0 ? (isBuy ? midPrice - atr * SL_ATR_MULT : midPrice + atr * SL_ATR_MULT) : analysis.stopPx,
    tpPx: atr > 0 ? (isBuy ? midPrice + atr * TP_ATR_MULT : midPrice - atr * TP_ATR_MULT) : analysis.tpPx,
  }
}
