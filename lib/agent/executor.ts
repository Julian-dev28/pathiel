// Auto-executor: validates through risk gates, sizes via Kelly, executes LIVE

import type { AgentAnalysis } from './memory'
import { memory } from './memory'
import { evalAllGates, type GateResults } from './risk-gates'
import { readAgentConfig as readConfig } from './config-store'
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

const HL_API = 'https://api.hyperliquid.xyz'

async function hlPost(body: object): Promise<unknown> {
  const res = await fetch(`${HL_API}/info`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`HL API ${res.status}`)
  return res.json()
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

async function getMarketVolume24h(coin: string): Promise<number> {
  try {
    const majors = new Set(['BTC', 'ETH', 'SOL', 'BNB', 'XRP', 'DOGE', 'ADA', 'AVAX'])
    if (majors.has(coin)) return 100_000_000
    return 10_000_000
  } catch {
    return 0
  }
}

export async function maybeExecute(analysis: AgentAnalysis): Promise<ExecutionResult> {
  try {
    const config = await readConfig()
    const mode = (config.mode as string) || 'OFF'

    if (mode === 'OFF') {
      return { executed: false, mode, analysisId: analysis.id, reason: 'mode_off' }
    }

    let equity = 0
    let totalOpenNotional = 0
    let positions: Array<{ coin: string; side: string; sizeUSD: number }> = []
    const user = process.env.HYPERLIQUID_MASTER_ADDRESS || process.env.HYPERLIQUID_WALLET_ADDRESS || ''

    try {
      const [perpAcct, spotAcct] = await Promise.all([
        hlPost({ type: 'clearinghouseState', user }) as Promise<{
          marginSummary?: { accountValue: string; totalNtlPos: string }
          assetPositions?: Array<{ position: { coin: string; szi: string } }>
        }>,
        hlPost({ type: 'spotClearinghouseState', user }) as Promise<{
          balances?: Array<{ coin: string; total: string }>
        }>,
      ])

      const perpEquity = parseFloat(perpAcct.marginSummary?.accountValue ?? '0')
      totalOpenNotional = parseFloat(perpAcct.marginSummary?.totalNtlPos ?? '0')

      // Unified account: perp accountValue already includes spot collateral. 
      // Log spot balances for debugging.
      const spotBalances = (spotAcct.balances ?? [])
        .filter(b => ['USDC', 'USDT', 'USD'].includes(b.coin))
        .map(b => `${b.coin}: ${b.total}`)
        .join(', ') || 'none'
      console.log(`[executor] perp equity=$${perpEquity.toFixed(2)}, spot=${spotBalances}`)
      equity = perpEquity

      // Check perp positions for open position guard
      positions = (perpAcct.assetPositions ?? [])
        .filter(p => parseFloat(p.position.szi) !== 0)
        .map(p => ({
          coin: p.position.coin,
          side: parseFloat(p.position.szi) > 0 ? 'long' : 'short',
          sizeUSD: Math.abs(parseFloat(p.position.szi)) * (analysis.entryPx ?? 0),
        }))
    } catch (err) {
      console.error(`[executor] account fetch failed: ${err}`)
      return { executed: false, mode, analysisId: analysis.id, reason: 'account_fetch_failed' }
    }

    const dailyPnl = memory.getDailyPnl()

    const rewardRisk = analysis.tpPx && analysis.stopPx && analysis.entryPx
      ? Math.abs(analysis.tpPx - analysis.entryPx) / Math.abs(analysis.entryPx - analysis.stopPx)
      : 1.0
    const rawSize = kellySize(analysis.confidence, equity, rewardRisk, (config.maxTradeNotionalUsd as number) ?? 200)
    const tradeNotionalUSD = rawSize > 0 ? rawSize : (analysis.entryPx ?? 0) * 0.001

    const marketVolume24h = await getMarketVolume24h(analysis.coin)

    const recentTrades = memory.getRecentTrades(10)
    const lastTradeForCoin = recentTrades.find(t => t.coin === analysis.coin)
    const lastTradeTime = lastTradeForCoin?.executedAt

    const hasBinaryNews = analysis.newsContext
      ? /fed|fomc|cpi|rate|earnings|hack|exploit|SEC/i.test(analysis.newsContext)
      : false

    const ctx = {
      confidence: analysis.confidence,
      currentPositions: positions,
      tradeNotionalUSD: tradeNotionalUSD,
      dailyPnl,
      marketVolume24hUSD: marketVolume24h,
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

      return {
        executed: false,
        mode,
        analysisId: analysis.id,
        blockedBy: blockReasons,
        gateResults: results,
      }
    }

    // LIVE mode — real orders only
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
        executed: false,
        mode,
        analysisId: analysis.id,
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
      executed: true,
      mode,
      analysisId: analysis.id,
      orderId: orderData.orderId,
      gateResults: results,
      sizeUSD: tradeNotionalUSD,
      entryPx: analysis.entryPx ?? 0,
      stopPx: analysis.stopPx,
      tpPx: analysis.tpPx,
    }
  } catch (err) {
    return {
      executed: false,
      mode: 'ERROR',
      analysisId: analysis.id,
      reason: err instanceof Error ? err.message : String(err),
    }
  }
}
