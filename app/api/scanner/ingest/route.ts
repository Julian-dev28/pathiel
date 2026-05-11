import { triggerStore } from '@/lib/scanner/store'
import type { Trigger } from '@/lib/scanner/engine'

export const runtime = 'nodejs'

interface IngestBody {
  triggers: Trigger[]
  scanTime: number
}

export async function POST(req: Request) {
  let body: IngestBody
  try {
    body = await req.json()
  } catch {
    return Response.json({ error: 'invalid json' }, { status: 400 })
  }

  if (!Array.isArray(body.triggers)) {
    return Response.json({ error: 'triggers must be an array' }, { status: 400 })
  }

  let stored = 0
  for (const t of body.triggers) {
    // Validate required fields
    if (!t.coin || typeof t.firedAt !== 'number' || !Array.isArray(t.triggers) || typeof t.compositeScore !== 'number' || typeof t.mid !== 'number') {
      console.warn('[ingest] skipping malformed trigger:', JSON.stringify(t).slice(0, 200))
      continue
    }
    triggerStore.appendTrigger(t)
    stored++
  }

  return Response.json({ ok: true, stored })
}
