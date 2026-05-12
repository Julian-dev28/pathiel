import { NextResponse } from 'next/server'
import * as fs from 'fs'
import * as os from 'os'
import * as path from 'path'

const PID_FILE = path.join(os.homedir(), '.hermes-trader.pid')

function isAlive(pid: number): boolean {
  try { process.kill(pid, 0); return true } catch { return false }
}

export async function POST(): Promise<NextResponse> {
  if (!fs.existsSync(PID_FILE)) {
    return NextResponse.json({ status: 'not_running' })
  }
  const pid = parseInt(fs.readFileSync(PID_FILE, 'utf-8').trim(), 10)
  if (isAlive(pid)) {
    try { process.kill(pid, 'SIGTERM') } catch {}
  }
  try { fs.rmSync(PID_FILE) } catch {}
  return NextResponse.json({ status: 'stopped', pid })
}
