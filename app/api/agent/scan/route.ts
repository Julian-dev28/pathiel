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

  const body = await req.json() as { minScore?: number }
  const minScore = body.minScore ?? 75

  const universe = await getUniverse()

  // Dynamic import avoids circular dependency between lib/agent/* modules
  const { scanOnce } = await import('@/lib/agent/perception')

  const perceptions = await scanOnce({ universe, minScore })

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
      })
    }
  } catch { /* non-fatal — research fallback handles inline perception */ }

  // ── Sync equity from Hyperliquid (spot → perp + total) ──
  try {
    const { getHLAccount, HL_ACCOUNT, transferSpotToPerp } = await import('@/lib/hyperliquid')
    const acct = await getHLAccount(HL_ACCOUNT)
    if (acct.spotUSDC > 5 && acct.equity < 5) {
      // Transfer most spot to perp, leave $5 buffer
      const transferAmt = Math.floor(acct.spotUSDC - 5)
      if (transferAmt > 0) {
        await transferSpotToPerp(transferAmt)
      }
    }
    const updated = await getHLAccount(HL_ACCOUNT)
    const { memory } = await import('@/lib/agent/memory')
    memory.updateEquity(updated.equity + updated.spotUSDC)
  } catch { /* non-fatal */ }

  lastScanAt = Date.now()

  return NextResponse.json({
    perceptions: perceptions as unknown as Perception[],
    count: perceptions.length,
  })
}
