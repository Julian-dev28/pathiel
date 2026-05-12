import { NextResponse } from 'next/server'
import { memory } from '@/lib/agent/memory'

export const runtime = 'nodejs'

export async function GET() {
  await memory.ensureLoaded()
  return NextResponse.json(memory.getAllTrades())
}
