import { NextRequest, NextResponse } from 'next/server'
import { research } from '@/lib/agent/research'
import type { Perception } from '@/lib/agent/perception'
import type { AgentVerdict } from '@/lib/agent/memory'
import { memory } from '@/lib/agent/memory'

export const runtime = 'nodejs'
export const maxDuration = 120

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ coin: string }> }
): Promise<NextResponse> {
  await memory.ensureLoaded()
  const { coin } = await params

  let body: { perceptionId?: string; perception?: Partial<Perception> }
  try {
    body = (await req.json()) as { perceptionId?: string; perception?: Partial<Perception> }
  } catch {
    return NextResponse.json({ error: 'invalid JSON' }, { status: 400 })
  }

  // Build a minimal perception from memory or request body
  let perception: Perception | null = null

  // Try to find by perceptionId in recent memories via the ingest API
  if (body.perceptionId) {
    try {
      const stateRes = await fetch(`${process.env.NEXT_PUBLIC_BASE_URL || 'http://localhost:3000'}/api/agent/state`)
      if (stateRes.ok) {
        const state = (await stateRes.json()) as { recentPerceptions?: Array<Record<string, unknown>> }
        const match = (state.recentPerceptions ?? []).find(
          (p: Record<string, unknown>) => p.id === body.perceptionId && p.coin === coin
        )
        if (match) {
          perception = {
            id: match.id as string,
            coin,
            type: (match.type as 'perp' | 'spot') || 'perp',
            firedAt: Number(match.firedAt) || Date.now(),
            mid: Number(match.mid) || 0,
            triggers: (match.triggers as Array<{ name: string; score: number; reason: string; fired: boolean }>) || [],
            compositeScore: Number(match.compositeScore) || 0,
          }
        }
      }
    } catch { /* skip memory lookup */ }
  }

  // Fallback: build from request body fields
  if (!perception && body.perception) {
    perception = {
      id: body.perception.id as string || 'fallback',
      coin,
      type: (body.perception.type as 'perp' | 'spot') || 'perp',
      firedAt: body.perception.firedAt ?? Date.now(),
      mid: body.perception.mid ?? 0,
      triggers: (body.perception.triggers as Array<{ name: string; score: number; reason: string; fired: boolean }>) || [],
      compositeScore: body.perception.compositeScore ?? 0,
    }
  }

  if (!perception || perception.mid <= 0) {
    return NextResponse.json(
      { error: 'perception not found — must provide valid perceptionId or inline perception data' },
      { status: 404 }
    )
  }

  const analysis = await research(coin, perception)

  // If verdict is actionable (LONG/SHORT/CLOSE), trigger execution
  if (analysis.verdict === 'LONG' || analysis.verdict === 'SHORT' || analysis.verdict === 'CLOSE') {
    try {
      await fetch(`${process.env.NEXT_PUBLIC_BASE_URL || 'http://localhost:3000'}/api/agent/execute`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ analysisId: analysis.id }),
      })
    } catch { /* execution is best-effort; analysis is already recorded */ }
  }

  return NextResponse.json({ analysis })
}
