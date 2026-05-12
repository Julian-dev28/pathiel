// Auto-executor: validates through risk gates, sizes via Kelly, executes LIVE

import type { AgentAnalysis } from './memory'
import { memory } from './memory'
import { evalAllGates, type GateResults } from './risk-gates'
import { readAgentConfig as readConfig } from './config-store'
import { hlCall, fetchAccountState } from '../hl-client'
import * as crypto from 'crypto'

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

  const user = process.env.HYPERLIQUID_MASTER_ADDRESS || process.env.HYPERLIQUID_WALLET_ADDRESS || ''
  const state = await fetchAccountState(user)
  const { equity, totalNtl: totalOpenNotional, assetPositions } = state

  const positions = assetPositions.map(p => ({
    coin: p.coin,
    side: parseFloat(p.szi) > 0 ? 'long' : 'short',
    sizeUSD: Math.abs(parseFloat(p.szi)) * (analysis.entryPx ?? 0),
  }))

  const dailyPnl = memory.getDailyPnl()

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

  const walletKey = process.env.HYPERLIQUID_PRIVATE_KEY
  if (!walletKey) {
    return { executed: false, mode, analysisId: analysis.id, reason: 'private_key_missing' }
  }

  const orderRes = await fetch(
    `${process.env.NEXT_PUBLIC_BASE_URL || 'http://localhost:3000'}/api/hl/place-order`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        side: analysis.side === 'long' ? 'long' : 'short',
        riskUSD: tradeNotionalUSD,
        leverage: 5,
        coin: analysis.coin,
      }),
    },
  )

  const orderData = await orderRes.json() as { ok: boolean; orderId?: string; error?: string }

  if (!orderData.ok) {
    return {
      executed: false, mode, analysisId: analysis.id,
      reason: `order_failed: ${orderData.error ?? 'unknown'}`,
      gateResults: results,
    }
  }

  memory.recordTrade({
    id: crypto.randomUUID(),
    analysisId: analysis.id,
    coin: analysis.coin,
    side: (analysis.side ?? 'long') as 'long' | 'short',
    entryPx: analysis.entryPx ?? 0,
    sizeUSD: tradeNotionalUSD,
    orderId: orderData.orderId,
    executedAt: Date.now(),
  })

  return {
    executed: true, mode, analysisId: analysis.id,
    orderId: orderData.orderId, gateResults: results,
    sizeUSD: tradeNotionalUSD,
    entryPx: analysis.entryPx ?? 0,
    stopPx: analysis.stopPx,
    tpPx: analysis.tpPx,
  }
}
