// Hermes-Trader Autonomous Engine — standalone Node.js script
// Usage: node scripts/trade-engine.mjs [one-shot | --loop]
//
// Pipeline: Scan → TA Filter → AI Research → Execute
// No Next.js needed — uses lib/agent modules directly.
// All env vars loaded from .env.local via import-meta-env.

// ── Load env ──────────────────────────────────────────────────────────────────
import { config } from 'dotenv'
config({ path: '.env.local' })

// ── Imports (all lib/agent modules are standalone Node.js) ───────────────────
import { getUniverse, getMarketByCoin } from '../lib/hl-universe.js'
import { scanOnce } from '../lib/agent/perception.js'
import { analyzePerception } from '../lib/agent/ta-filter.js'
import { research } from '../lib/agent/research.js'
import { maybeExecute } from '../lib/agent/executor.js'
import { memory } from '../lib/agent/memory.js'
import { setLastScanAt } from '../app/api/agent/state/route.ts'  // only if on Next.js

const HL_API = 'https://api.hyperliquid.xyz'
const SCAN_INTERVAL_MS = parseInt(process.env.AGENT_HEARTBEAT_INTERVAL_MS || '3600000', 10) // 1h default
const MIN_SCORE = parseInt(process.env.AGENT_MIN_SCORE || '70', 10)

function ts() { return new Date().toISOString().slice(11, 19) }
function log(msg) { console.log(`[${ts()}] ${msg}`) }

// ── HL Helpers (direct, no proxy) ───────────────────────────────────────────
async function hlPost(body) {
  const res = await fetch(`${HL_API}/info`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(15_000),
  })
  if (!res.ok) throw new Error(`HL ${res.status}`)
  return res.json()
}

async function getPortfolio() {
  try {
    const user = process.env.HYPERLIQUID_MASTER_ADDRESS || process.env.HYPERLIQUID_WALLET_ADDRESS || ''
    const acct = await hlPost({ type: 'spotClearinghouseState', user })
    const perp = await hlPost({ type: 'clearinghouseState', user })
    const equity = parseFloat(perp?.marginSummary?.accountValue ?? '0')
    const positions = (perp?.assetPositions ?? []).map(p => ({
      coin: p.position.coin,
      szi: parseFloat(p.position.szi),
      entry: parseFloat(p.position.entryPx),
      pnl: parseFloat(p.position.unrealizedPnl),
    }))
    return { equity, positions }
  } catch { return { equity: 0, positions: [] } }
}

// ── Trading Cycle ───────────────────────────────────────────────────────────
async function cycle() {
  // 1. Load universe & get prices
  const universe = await getUniverse()
  const allMids = await hlPost({ type: 'allMids' })

  // 2. Scan (triggers fire here)
  const perceptions = await scanOnce({ universe, minScore: MIN_SCORE })
  log(`Scanned ${universe.length} markets → ${perceptions.length} triggered (score ≥ ${MIN_SCORE})`)

  if (perceptions.length === 0) {
    log('No triggered signals — market quiet, $0 tokens burned')
    return
  }

  // 3. TA Filter on top candidates (sequential, no rate limit issues)
  let confirmed = []
  const topN = Math.min(8, perceptions.length)
  for (let i = 0; i < topN; i++) {
    const p = perceptions[i]
    if (i > 0) await new Promise(r => setTimeout(r, 2000)) // 2s gap
    const ta = await analyzePerception(p)
    if (ta.signal === 'CONFIRMED') confirmed.push({ perception: p, ta })
  }
  log(`TA: ${confirmed.length} CONFIRMED of ${topN} analyzed`)

  if (confirmed.length === 0) {
    log('No CONFIRMED — saved $0.00.1 in AI tokens') 
    return
  }

  // 4. AI Research (max 2 per cycle)
  let executed = 0
  for (const { perception, ta } of confirmed.slice(0, 2)) {
    const result = await research(perception.coin, perception)
    log(`Research ${perception.coin}: ${result.verdict} conf ${(result.confidence * 100).toFixed(0)}%`)

    if (result.confidence >= 0.85 && result.verdict !== 'PASS') {
      const execResult = await maybeExecute(result)
      if (execResult.executed) {
        log(`EXECUTED: ${execResult.sizeUSD} on ${perception.coin}`)
        executed++
      } else {
        log(`BLOCKED: ${execResult.blockedBy?.join(', ') || execResult.reason}`)
      }
    } else {
      log(`${perception.coin}: not confident enough (${(result.confidence * 100).toFixed(0)}% < 85%)`)
    }
  }

  // 5. Post-cycle status
  const portfolio = await getPortfolio()
  log(`Portfolio: $${portfolio.equity.toFixed(2)} | ${portfolio.positions.length} positions | +${executed} trades this cycle`)
}

// ── Main ────────────────────────────────────────────────────────────────────
async function main() {
  await memory.ensureLoaded()
  const portfolio = await getPortfolio()
  log(`Hermes-Trader Engine — starting — equity $${portfolio.equity.toFixed(2)} | ${portfolio.positions.length} positions`)
  log(`Config: scan every ${SCAN_INTERVAL_MS / 1000 / 60}m | minScore ${MIN_SCORE} | conf ≥ 0.85 | max $25/trade`)

  if (process.argv.includes('--loop')) {
    log('Running loop mode...')
    while (true) {
      try {
        await cycle()
      } catch (err) {
        log(`Cycle error: ${err.message}`)
      }
      await new Promise(r => setTimeout(r, SCAN_INTERVAL_MS))
    }
  } else {
    log('One-shot mode — exiting after cycle')
    await cycle()
  }
}

main().catch(err => {
  log(`FATAL: ${err.message || err}`)
  process.exit(1)
})
