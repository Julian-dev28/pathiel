import { scanOnce } from '@/lib/scanner/engine'
import { getUniverse } from '@/lib/hl-universe'
import { triggerStore } from '@/lib/scanner/store'

export const runtime = 'nodejs'

// ── Rate-limit guard: max 1 scan per 10 seconds ──────────────────────────────

let lastScanAt = 0
const SCAN_COOLDOWN_MS = 10_000

export async function POST(req: Request) {
  const now = Date.now()
  const elapsed = now - lastScanAt
  if (elapsed < SCAN_COOLDOWN_MS) {
    return Response.json({ error: 'scan_in_progress' }, { status: 429 })
  }
  lastScanAt = now

  const { minScore = 1.0 } = await req.json().catch(() => ({}))
  const universe = await getUniverse()
  const triggers = await scanOnce({ universe, minScore })

  // Store each triggered result
  for (const t of triggers) {
    triggerStore.appendTrigger(t)
  }

  return Response.json({ scanned: universe.length, triggers, lastScanAt: now })
}
