import { NextResponse } from 'next/server'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

// Session history is not persisted with the OpenRouter backend.
export async function GET() {
  return NextResponse.json({ messages: [] })
}
