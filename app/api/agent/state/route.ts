// GET /api/agent/state — full agent state snapshot for the UI
import { NextResponse } from 'next/server'
import { memory } from '@/lib/agent/memory'
import { readAgentConfig as readConfig } from '@/lib/agent/config-store'

export const runtime = 'nodejs'

let lastScanAt: number | null = null

export function setLastScanAt(ts: number) {
  lastScanAt = ts
}

export async function GET() {
  await memory.ensureLoaded()
  const [state, config] = await Promise.all([
    memory.getFullState(),
    readConfig(),
  ])

  return NextResponse.json({
    ...state,
    config,
    lastScanAt,
  })
}
