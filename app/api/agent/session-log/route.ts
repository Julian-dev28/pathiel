// GET /api/agent/session-log — reads the JSONL agent activity log and returns last 50 entries
import { NextResponse } from 'next/server'
import { promises as fs } from 'fs'
import * as path from 'path'
import * as os from 'os'

const LOG_FILE =
  process.env.SESSION_LOG_PATH ||
  path.join(os.homedir(), 'Documents', 'code', 'hermes-trader', '.trader-session-log.jsonl')

export const runtime = 'nodejs'

export async function GET(): Promise<NextResponse> {
  try {
    const raw = await fs.readFile(LOG_FILE, 'utf8')
    const lines = raw.trim().split('\n').filter(l => l.length > 0)
    const last50 = lines.slice(-50).map(line => JSON.parse(line))
    return NextResponse.json(last50)
  } catch {
    return NextResponse.json([])
  }
}
