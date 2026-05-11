// ── Auto-trader orchestrator: risk gates -> AI verdict -> Kelly size -> execution ─

import { runAllGates, type AllGateResults } from './risk-gates'
import { triggerStore, type StoredTrigger } from './store'

// ── Quant Journal integration (lazy import to avoid startup errors) ─
async function getJournal() {
  try {
    const { recordTrigger, recordVerdict, recordTrade, recordBacktestPrediction } = await import('../quant/journal')
    return { recordTrigger, recordVerdict, recordTrade, recordBacktestPrediction }
  } catch {
    console.log('[journal] Quant journal not available — trade metrics not recorded.')
    return null
  }
}

const HL_API = 'https://api.hyperliquid.xyz'

interface AutoTradeConfig {
  mode: 'OFF' | 'DRY' | 'LIVE'
  minAiConfidence: number
  maxConcurrent: number
  maxTradeNotionalUsd: number
  maxDailyLossUsd: number
  minMarketVolumeUsd: number
  maxTotalNotionalPct: number
  cooldownMin: number
  coinAllowlist: string[]
  coinBlocklist: string[]
}

interface AccountContext {
  equity: number
  totalEquity: number
  totalNtl: number
  openPositions: { coin: string; side: 'long' | 'short'; notionalUSD: number }[]
}

export interface AIVerdict {
  side: 'long' | 'short' | 'pass'
  confidence: number   // 0-100
  summary: string
}

interface TradeResult {
  executed: boolean
  reason: string
  orderId?: string
  gateResults?: AllGateResults
  simulatedOrder?: { coin: string; side: string; notionalUSD: number; stopPx: number; tpPx: number }
}

// ── Fetch account context for risk gates ─

async function fetchAccountContext(walletAddress: string): Promise<AccountContext> {
  const [perpRes, spotRes] = await Promise.all([
    fetch(`${HL_API}/info`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type: 'clearinghouseState', user: walletAddress }),
    }),
    fetch(`${HL_API}/info`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type: 'spotClearinghouseState', user: walletAddress }),
    }),
  ])

  const perp = await perpRes.json() as {
    marginSummary: { accountValue: string; totalNtlPos: string }
    assetPositions: Array<{ position: { coin: string; szi: string; entryPx: string; unrealizedPnl: string; leverage?: { value: string } } }>
  }
  const spot = await spotRes.json() as { balances?: Array<{ coin: string; total: string }> }

  const equity = parseFloat(perp.marginSummary?.accountValue ?? '0')
  const totalNtl = parseFloat(perp.marginSummary?.totalNtlPos ?? '0')
  const spotUSDC = (spot.balances ?? []).filter(b => ['USDC','USDT','USD'].includes(b.coin))
    .reduce((s, b) => s + parseFloat(b.total), 0)

  const openPositions = (perp.assetPositions ?? [])
    .filter(p => { const s = parseFloat(p.position.szi); return s !== 0 })
    .map(p => ({
      coin: p.position.coin,
      side: parseFloat(p.position.szi) > 0 ? 'long' as const : 'short' as const,
      notionalUSD: Math.abs(parseFloat(p.position.szi)) * parseFloat(p.position.entryPx),
    }))

  return { equity, totalEquity: equity + spotUSDC, totalNtl, openPositions }
}

// ── Fetch 24h volume for a coin ─

async function fetchVolume24h(coin: string): Promise<number> {
  try {
    const endTime = Date.now()
    const startTime = endTime - 25 * 3600_000
    const res = await fetch(`${HL_API}/info`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type: 'candleSnapshot', req: { coin, interval: '1h', startTime, endTime } }),
    })
    const candles = await res.json() as Array<{ v: string }>
    return candles.reduce((sum, c) => sum + parseFloat(c.v ?? '0'), 0)
  } catch {
    return 0
  }
}

// ── Main auto-trade pipeline ─

export async function maybeAutoTrade(
  trigger: StoredTrigger,
  aiVerdict: AIVerdict,
  walletAddress: string,
  config: AutoTradeConfig,
): Promise<TradeResult> {
  // 1. Mode check
  if (config.mode === 'OFF') return { executed: false, reason: 'mode_off' }

  // 1b. Record trigger in journal
  const journal = await getJournal()
  let triggerId: string | undefined
  if (journal) {
    triggerId = journal.recordTrigger({
      coin: trigger.coin,
      composite_score: trigger.compositeScore || 0,
      pct_move_spike: trigger.pctMoveSpike,
      volume_spike: trigger.volumeSpike,
      breakout_score: trigger.breakoutScore,
      range_compression: trigger.rangeCompression,
      mid_price: trigger.mid,
    })
  }

  // 2. AI confidence gate
  if (aiVerdict.confidence < config.minAiConfidence * 100) {
    return { executed: false, reason: `confidence ${aiVerdict.confidence} below ${config.minAiConfidence * 100}` }
  }
  if (aiVerdict.side === 'pass') {
    return { executed: false, reason: 'ai_verdict_pass' }
  }

  // 3. Fetch account context
  let ctx: AccountContext
  try {
    ctx = await fetchAccountContext(walletAddress)
  } catch (err) {
    return { executed: false, reason: `account_fetch_failed: ${err instanceof Error ? err.message : err}` }
  }

  // 4. Recent trades (TODO: populate from userFills endpoint)
  const recentTrades: Array<{ coin: string; pnl: number; closedAt: number }> = []

  // 5. Run ALL risk gates (collect all, don't short-circuit)
  const side = aiVerdict.side as 'long' | 'short'
  const volume24h = await fetchVolume24h(trigger.coin)
  const gateResults = runAllGates(trigger, ctx, ctx.openPositions, recentTrades, config, volume24h, side)

  // Mark analyzed in store
  triggerStore.markAnalyzed(trigger.coin, side)

  // 6. If any gate fails, store results and return
  if (!gateResults.pass) {
    return {
      executed: false,
      reason: `risk_gates_failed: ${gateResults.failures.map(f => f.gate).join(', ')}`,
      gateResults,
    }
  }

  // 7. Kelly sizing (simplified: cap by maxTradeNotionalUsd and 2% of equity)
  const mid = trigger.mid
  const notionalUSD = Math.min(config.maxTradeNotionalUsd, ctx.totalEquity * 0.02)

  if (mid <= 0 || notionalUSD <= 0) return { executed: false, reason: 'invalid_price_or_size', gateResults }

  // 8. DRY mode — log synthetic order
  if (config.mode === 'DRY') {
    const stopPx = side === 'long' ? mid * 0.98 : mid * 1.02
    const tpPx = side === 'long' ? mid * 1.03 : mid * 0.97
    const simulated = { coin: trigger.coin, side, notionalUSD, stopPx, tpPx }
    console.log(`[auto-trade DRY] ${trigger.coin} ${side} $${notionalUSD.toFixed(0)} @ ~$${mid.toFixed(2)} stop=$${stopPx.toFixed(2)} tp=$${tpPx.toFixed(2)}`)
    return { executed: false, reason: 'dry_run', simulatedOrder: simulated, gateResults }
  }

  // 9. LIVE execution
  if (config.mode === 'LIVE') {
    try {
      const baseUrl = process.env.NEXT_PUBLIC_BASE_URL || 'http://localhost:3000'
      const res = await fetch(`${baseUrl}/api/hl/place-order`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ side, riskUsd: notionalUSD }),
      })
      const data = await res.json() as { ok: boolean; orderId?: string; error?: string }
      if (data.ok) {
        triggerStore.markTraded(trigger.coin, data.orderId ?? 'unknown')
        return { executed: true, reason: 'live_executed', orderId: data.orderId, gateResults }
      }
      return { executed: false, reason: `order_failed: ${data.error}`, gateResults }
    } catch (err) {
      return { executed: false, reason: `order_error: ${err instanceof Error ? err.message : err}`, gateResults }
    }
  }

  return { executed: false, reason: 'unknown_mode' }
}
