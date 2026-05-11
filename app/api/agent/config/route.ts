import { NextRequest, NextResponse } from 'next/server'
import { readAgentConfig, writeAgentConfig } from '@/lib/agent/config-store'

export const runtime = 'nodejs'

export async function GET(): Promise<NextResponse> {
  const cfg = await readAgentConfig()
  return NextResponse.json(cfg)
}

export async function POST(req: NextRequest): Promise<NextResponse> {
  const existing = await readAgentConfig()
  const body = await req.json() as Record<string, unknown>
  const merged = { ...existing, ...body }
  await writeAgentConfig(merged)
  return NextResponse.json({ ok: true, config: merged })
}
