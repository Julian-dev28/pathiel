export const runtime = 'nodejs'

import { HL_API } from '../../../../lib/hyperliquid'

// 3-second in-memory cache for heartbeat script calling every 60s
let cachedMids: Record<string, string> | null = null
let cachedAt = 0
const CACHE_MS = 3000

export async function GET() {
  if (cachedMids && Date.now() - cachedAt < CACHE_MS) {
    return Response.json(cachedMids)
  }

  try {
    const res = await fetch(`${HL_API}/info`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type: 'allMids' }),
    })

    if (!res.ok) {
      throw new Error(`status ${res.status}`)
    }

    const mids = await res.json() as Record<string, string>
    cachedMids = mids
    cachedAt = Date.now()
    return Response.json(mids)
  } catch (err) {
    return Response.json({ error: String(err) }, { status: 502 })
  }
}
