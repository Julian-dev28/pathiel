import { promises as fs } from 'fs'
import * as path from 'path'

const CONFIG_PATH = path.join(process.cwd(), '.agent-config.json')

const DEFAULT_CONFIG: Record<string, unknown> = {
  mode: 'OFF',
}

export async function readAgentConfig(): Promise<Record<string, unknown>> {
  try {
    const raw = await fs.readFile(CONFIG_PATH, 'utf8')
    return JSON.parse(raw) as Record<string, unknown>
  } catch {
    return { ...DEFAULT_CONFIG }
  }
}

export async function writeAgentConfig(cfg: Record<string, unknown>): Promise<void> {
  await fs.writeFile(CONFIG_PATH, JSON.stringify(cfg, null, 2), 'utf8')
}
