// GET /api/agent/state — full agent state snapshot for the UI
import { NextResponse } from 'next/server'
import { memory } from '@/lib/agent/memory'
import { readAgentConfig as readConfig } from '@/lib/agent/config-store'
import { HL_API, HL_MASTER } from '@/lib/hyperliquid'

export const runtime = 'nodejs'

let lastScanAt: number | null = null

export function setLastScanAt(ts: number) {
  lastScanAt = ts
}

async function fetchLiveEquity(): Promise<number> {
  const user = HL_MASTER
  if (!user) return 0
  try {
    const [perpRes, spotRes] = await Promise.all([
      fetch(`${HL_API}/info`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type: 'clearinghouseState', user }),
      }),
      fetch(`${HL_API}/info`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type: 'spotClearinghouseState', user }),
      }),
    ])
    const perp = await perpRes.json() as { marginSummary?: { accountValue: string } }
    const perpEquity = parseFloat(perp.marginSummary?.accountValue ?? '0')
    if (perpEquity > 0) return perpEquity
    const spot = await spotRes.json() as { balances?: Array<{ coin: string; total: string }> }
    return (spot.balances ?? [])
      .filter(b => ['USDC', 'USDT', 'USD'].includes(b.coin))
      .reduce((sum, b) => sum + parseFloat(b.total), 0)
  } catch {
    return 0
  }
}

export async function GET() {
  await memory.ensureLoaded()
  const [state, config, liveEquity] = await Promise.all([
    memory.getFullState(),
    readConfig(),
    fetchLiveEquity(),
  ])

  if (liveEquity > 0) memory.updateEquity(liveEquity)

  return NextResponse.json({
    ...state,
    equity: liveEquity > 0 ? liveEquity : state.equity,
    liveEquity,
    config,
    lastScanAt,
  })
}
