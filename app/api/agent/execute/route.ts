import { NextRequest, NextResponse } from 'next/server'
import { maybeExecute, type ExecutionResult } from '@/lib/agent/executor'
import { memory } from '@/lib/agent/memory'

export const runtime = 'nodejs'

export async function POST(req: NextRequest): Promise<NextResponse> {
  await memory.ensureLoaded()
  let body: { analysisId?: string }
  try {
    body = (await req.json()) as { analysisId?: string }
  } catch {
    return NextResponse.json({ error: 'invalid JSON' }, { status: 400 })
  }

  if (!body.analysisId) {
    return NextResponse.json({ error: 'analysisId required' }, { status: 400 })
  }

  let analysis = memory.getAnalysisById(body.analysisId)

  // Fallback: fetch from state API (handles Next.js hot-reload module isolation)
  if (!analysis) {
    try {
      const stateRes = await fetch(`${process.env.NEXT_PUBLIC_BASE_URL || 'http://localhost:3000'}/api/agent/state`)
      if (stateRes.ok) {
        const state = await stateRes.json() as { recentAnalyses?: Record<string, unknown>[] }
        const match = (state.recentAnalyses ?? []).find((a: Record<string, unknown>) => a.id === body.analysisId)
        if (match) {
          analysis = {
            id: match.id as string,
            perceptionId: (match.perceptionId as string) || 'unknown',
            coin: match.coin as string,
            verdict: match.verdict as 'LONG' | 'SHORT' | 'PASS' | 'CLOSE',
            confidence: match.confidence as number,
            side: (match.side as 'long' | 'short' | null) ?? null,
            entryPx: match.entryPx as number,
            stopPx: match.stopPx as number,
            tpPx: match.tpPx as number,
            reasoning: (match.reasoning as string) || '',
            newsContext: match.newsContext as string | undefined,
            createdAt: match.createdAt as number,
          }
        }
      }
    } catch { /* non-fatal */ }
  }

  if (!analysis) {
    return NextResponse.json({ error: 'analysis not found' }, { status: 404 })
  }

  const result = await maybeExecute(analysis)
  return NextResponse.json(result as ExecutionResult)
}
