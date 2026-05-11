// ── Shared auto-trade config: read/write .scanner-config.json ─

import * as fs from 'node:fs'
import * as path from 'node:path'

export interface AutoTradeConfig {
  mode: 'OFF' | 'DRY' | 'LIVE'
  minAiConfidence: number
  maxConcurrent: number
  maxTradeNotionalUsd: number
  maxDailyLossUsd: number
  minMarketVolumeUsd: number
  maxTotalNotionalPct: number
  cooldownMin: number
  coinAllowlist: string[]
  coinBlocklist: string[]
}

const DEFAULT: AutoTradeConfig = {
  mode: 'OFF',
  minAiConfidence: 0.80,
  maxConcurrent: 3,
  maxTradeNotionalUsd: 200,
  maxDailyLossUsd: 100,
  minMarketVolumeUsd: 5000000,
  maxTotalNotionalPct: 0.30,
  cooldownMin: 60,
  coinAllowlist: [],
  coinBlocklist: [],
}

const CONFIG_PATH = path.join(process.cwd(), '.scanner-config.json')

export function readAutoTradeConfig(): AutoTradeConfig {
  try {
    if (!fs.existsSync(CONFIG_PATH)) return { ...DEFAULT }
    const raw = fs.readFileSync(CONFIG_PATH, 'utf-8')
    return { ...DEFAULT, ...JSON.parse(raw) }
  } catch {
    return { ...DEFAULT }
  }
}

export function writeAutoTradeConfig(cfg: Partial<AutoTradeConfig>): AutoTradeConfig {
  const current = readAutoTradeConfig()
  const merged = { ...current, ...cfg }
  fs.writeFileSync(CONFIG_PATH, JSON.stringify(merged, null, 2))
  return merged
}
