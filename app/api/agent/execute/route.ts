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

  const analysis = memory.getAnalysisById(body.analysisId)
  if (!analysis) {
    return NextResponse.json({ error: 'analysis not found' }, { status: 404 })
  }

  const result = await maybeExecute(analysis)
  return NextResponse.json(result as ExecutionResult)
}
