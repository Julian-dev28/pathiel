// POST /api/agent/ingest — heartbeat daemon pushes perceptions here
import { NextRequest, NextResponse } from 'next/server'
import { memory } from '@/lib/agent/memory'
import { setLastScanAt } from '@/app/api/agent/state/route'

export const runtime = 'nodejs'

interface IngestBody {
  perceptions: Array<{
    id: string
    coin: string
    type: 'perp' | 'spot'
    firedAt: number
    mid: number
    triggers: Array<{ name: string; score: number; reason: string }>
    compositeScore: number
  }>
  scanTime: number
  scanned: number
}

export async function POST(req: NextRequest) {
  await memory.ensureLoaded()
  let body: IngestBody
  try {
    body = await req.json() as IngestBody
  } catch {
    return NextResponse.json({ error: 'invalid JSON' }, { status: 400 })
  }

  if (!Array.isArray(body.perceptions)) {
    return NextResponse.json({ error: 'perceptions must be an array' }, { status: 400 })
  }

  let stored = 0
  for (const p of body.perceptions) {
    // Validate required fields
    if (!p.coin || !p.type || !p.firedAt || typeof p.mid !== 'number' ||
        !Array.isArray(p.triggers) || typeof p.compositeScore !== 'number') {
      continue
    }
    memory.recordPerception(p)
    stored++
  }

  // Rebuild watchlist from all stored perceptions
  // (the daemon sends the full batch each cycle)
  memory.updateWatchlist(
    body.perceptions.map(p => ({
      coin: p.coin,
      type: p.type,
      mid: p.mid,
      compositeScore: p.compositeScore,
      firedAt: p.firedAt,
    }))
  )

  setLastScanAt(body.scanTime)

  return NextResponse.json({ ok: true, stored, scanned: body.scanned })
}
