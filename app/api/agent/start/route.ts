import { NextRequest, NextResponse } from 'next/server'
import { spawn } from 'child_process'
import * as fs from 'fs'
import * as os from 'os'
import * as path from 'path'

const PID_FILE = path.join(os.homedir(), '.hermes-trader.pid')
const HEARTBEAT_SCRIPT = path.join(process.cwd(), 'scripts/trade-engine.mjs')

function isAlive(pid: number): boolean {
  try { process.kill(pid, 0); return true } catch { return false }
}

export async function POST(req: NextRequest): Promise<NextResponse> {
  // Check if already running
  if (fs.existsSync(PID_FILE)) {
    const pid = parseInt(fs.readFileSync(PID_FILE, 'utf-8').trim(), 10)
    if (isAlive(pid)) {
      return NextResponse.json({ status: 'already_running', pid })
    }
    // Stale pid file, clean up
    try { fs.rmSync(PID_FILE) } catch {}
  }

  // Spawn heartbeat as detached process so Next.js can stop without killing it
  const child = spawn('node', [HEARTBEAT_SCRIPT, '--loop'], {
    detached: true,
    stdio: 'ignore',
    env: { ...process.env, SCANNER_API_URL: process.env.NEXT_PUBLIC_BASE_URL || 'http://localhost:3000' },
  })
  child.unref()

  fs.writeFileSync(PID_FILE, String(child.pid))
  return NextResponse.json({ status: 'started', pid: child.pid })
}

export async function GET(): Promise<NextResponse> {
  if (!fs.existsSync(PID_FILE)) {
    return NextResponse.json({ running: false, cycle: 0, lastUpdate: null })
  }
  const pid = parseInt(fs.readFileSync(PID_FILE, 'utf-8').trim(), 10)
  if (!isAlive(pid)) {
    try { fs.rmSync(PID_FILE) } catch {}
    return NextResponse.json({ running: false, cycle: 0, lastUpdate: null })
  }
  return NextResponse.json({ running: true, pid })
}
