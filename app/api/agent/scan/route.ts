import { NextRequest, NextResponse } from 'next/server'
import type { Perception } from '../../../../lib/agent/perception'
import { getUniverse } from '../../../../lib/hl-universe'

export const runtime = 'nodejs'

let lastScanAt = 0

export async function POST(req: NextRequest): Promise<NextResponse> {
  const elapsed = Date.now() - lastScanAt
  if (elapsed < 30_000 && lastScanAt > 0) {
    return NextResponse.json(
      { error: `Rate limited. Try again in ${Math.ceil((30_000 - elapsed) / 1000)}s` },
      { status: 429 }
    )
  }

  const body = await req.json() as { minScore?: number; withTA?: boolean }
  const minScore = body.minScore ?? 75
  const withTA = body.withTA !== false // default true

  const universe = await getUniverse()

  // Dynamic import avoids circular dependency between lib/agent/* modules
  const { scanOnce } = await import('@/lib/agent/perception')

  const perceptions = await scanOnce({ universe, minScore })

  // TA filter is run async in background — don't block the scan response
  // The heartbeat (scripts/agent-heartbeat.mjs) handles TA on perceptions in memory
  if (withTA && perceptions.length > 0) {
    ;(async () => {
      try {
        const { analyzePerceptions } = await import('../../../../lib/agent/ta-filter')
        const results = await analyzePerceptions(perceptions.slice(0, 8), 1)
        const { memory } = await import('@/lib/agent/memory')
        for (const p of perceptions.slice(0, 8)) {
          const ta = results.get(p.id)
          if (ta) {
            memory.recordPerception({
              id: p.id, coin: p.coin, type: p.type,
              firedAt: p.firedAt, mid: p.mid,
              triggers: p.triggers, compositeScore: p.compositeScore,
              taSignal: ta.signal, taScore: ta.score,
            })
          }
        }
      } catch {}
    })()
  }

  // Auto-store perceptions in agent memory so research can find them by ID
  try {
    const { memory } = await import('@/lib/agent/memory')
    for (const p of perceptions) {
      memory.recordPerception({
        id: p.id,
        coin: p.coin,
        type: p.type,
        firedAt: p.firedAt,
        mid: p.mid,
        triggers: p.triggers,
        compositeScore: p.compositeScore,
        ...(p as Record<string, unknown>).taSignal ? {
          taSignal: (p as Record<string, unknown>).taSignal,
          taScore: (p as Record<string, unknown>).taScore,
        } : {},
      })
    }
  } catch { /* non-fatal — research fallback handles inline perception */ }

  // ── Sync equity from Hyperliquid (unified: perp accountValue includes spot collateral) ──
  try {
    const { HL_ACCOUNT } = await import('@/lib/hyperliquid')
    const perpRes = await fetch(`https://api.hyperliquid.xyz/info`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type: 'clearinghouseState', user: HL_ACCOUNT }),
    })
    if (perpRes.ok) {
      const perp = await perpRes.json() as { marginSummary?: { accountValue: string } }
      const { memory } = await import('@/lib/agent/memory')
      memory.updateEquity(parseFloat(perp.marginSummary?.accountValue ?? '0'))
    }
  } catch { /* non-fatal */ }

  lastScanAt = Date.now()

  return NextResponse.json({
    perceptions: perceptions as unknown as Perception[],
    count: perceptions.length,
  })
}
