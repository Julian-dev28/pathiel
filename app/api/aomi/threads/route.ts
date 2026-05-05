import { NextResponse } from 'next/server'

export const runtime = 'nodejs'

// Thread persistence is not supported with the OpenRouter backend.
export async function GET() {
  return NextResponse.json({ threads: [] })
}

export async function DELETE() {
  return NextResponse.json({ ok: true })
}
