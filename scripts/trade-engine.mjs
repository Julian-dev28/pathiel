#!/usr/bin/env node
// Hermes-Trader Autonomous Engine
// Calls the existing Next.js API routes via fetch (no UI needed, just the API).
// Usage: node scripts/trade-engine.mjs [--loop]
//
// Pipeline: Scan → TA Filter (top N) → AI Research (max 3) → Execute (conf >= 70%)
// Reports results to stdout.

// ── Load env ─────────────────────────────────────────────────────────────────
import { config } from 'dotenv'
config({ path: '.env.local' })

const BASE_URL = process.env.NEXT_PUBLIC_BASE_URL || 'http://localhost:3000'
const SCAN_INTERVAL_MS = parseInt(process.env.AGENT_SCAN_INTERVAL_MS || '3600000', 10) // 1h
const MIN_SCORE = parseInt(process.env.AGENT_MIN_SCORE || '70', 10)
const MAX_RESEARCH = parseInt(process.env.MAX_RESEARCH_PER_CYCLE || '3', 10)
const MIN_CONFIDENCE = parseFloat(process.env.MIN_AI_CONFIDENCE || '0.70')

function ts() { return new Date().toISOString().slice(11, 19) }
function log(msg) { console.log(`[${ts()}] ${msg}`) }

// ── API helpers ──────────────────────────────────────────────────────────────
async function apiGet(path) {
  const res = await fetch(`${BASE_URL}${path}`, { signal: AbortSignal.timeout(10_000) })
  if (!res.ok) throw new Error(`${path}: ${res.status}`)
  return res.json()
}

async function apiPost(path, body) {
  const res = await fetch(`${BASE_URL}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(120_000),
  })
  if (!res.ok) {
    let text
    try { text = await res.text() } catch { text = '' }
    throw new Error(`${path}: ${res.status} ${text.slice(0, 200)}`)
  }
  return res.json()
}

async function ensureServer() {
  try { await apiGet('/api/agent/config'); return } catch {}
  log('Starting Next.js API server...')
  const { spawn } = await import('child_process')
  spawn('npx', ['next', 'dev', '-p', '3000'], {
    stdio: 'ignore', detached: true, cwd: process.cwd(),
  }).unref()
  for (let i = 0; i < 20; i++) {
    await new Promise(r => setTimeout(r, 3000))
    try { await apiGet('/api/agent/config'); log('API server ready'); return } catch {}
  }
  throw new Error('API server failed to start after 60s')
}

// ── Trading Cycle ────────────────────────────────────────────────────────────
async function cycle() {
  // 1. Scan all markets
  log('Scanning 230+ markets...')
  const scan = await apiPost('/api/agent/scan', { minScore: MIN_SCORE, withTA: false })

  const percs = scan.perceptions || []
  log(`→ ${percs.length} triggered (score ≥ ${MIN_SCORE})`)

  if (percs.length === 0) {
    log('No signals — market quiet, $0 tokens burned')
    return
  }

  // 2. AI Research (top 3, 15s gap between calls)
  // TA is async background — by next cycle the scan has TA tags
  // For now, skip inline TA (rate limits) and just AI-research top triggers
  let analyzed = 0, executed = 0, trades = []
  const topN = Math.min(MAX_RESEARCH, percs.length)

  for (let i = 0; i < topN; i++) {
    const p = percs[i]
    if (i > 0) { log(`  waiting 15s...`); await new Promise(r => setTimeout(r, 15000)) }

    log(`AI researching ${p.coin} (score: ${p.compositeScore.toFixed(0)})...`)
    try {
      const analysis = await apiPost(`/api/agent/research/${encodeURIComponent(p.coin)}`, {
        perception: {
          id: p.id, coin: p.coin, type: p.type,
          firedAt: p.firedAt, mid: p.mid,
          triggers: p.triggers, compositeScore: p.compositeScore,
        },
      })
      // Route returns { analysis: {...} } — unwrap if needed
      const a = analysis.analysis || analysis
      const verdict = a?.verdict || 'PASS'
      const confidence = typeof a?.confidence === 'number' ? a.confidence : 0
      const reasoning = a?.reasoning || ''
      const analysisId = a?.id || ''
      analyzed++
      log(`  → ${verdict} conf ${(confidence * 100).toFixed(0)}% | ${reasoning?.slice(0, 120) || ''}`)

      // Execute if confident enough
      if (confidence >= MIN_CONFIDENCE && verdict !== 'PASS') {
        log(`  Executing...`)
        const exec = await apiPost('/api/agent/execute', { analysisId })
        if (exec.executed) {
          executed++
          trades.push(`${p.coin} ${a.side} $${exec.sizeUSD?.toFixed(0) || '?'}`)
          log(`  ✓ EXECUTED ${trades[trades.length - 1]}`)
        } else {
          log(`  ✗ Blocked: ${exec.blockedBy?.join(', ') || exec.reason}`)
        }
      } else {
        log(`  PASS (conf ${(confidence * 100).toFixed(0)}% < ${(MIN_CONFIDENCE * 100).toFixed(0)}%)`)
      }
    } catch (e) {
      log(`  Error: ${e.message}`)
    }
  }

  // 3. Check portfolio
  try {
    const portfolio = await apiGet('/api/hl/portfolio')
    log(`Portfolio: $${portfolio.equity?.toFixed(2) || '?'} | ${portfolio.positions?.length || 0} positions`)
    const pnl = portfolio.positions?.reduce((s, p) => s + (p.livePnl || 0), 0) || 0
    if (pnl !== 0) log(`Unrealized PnL: $${pnl.toFixed(2)}`)
  } catch {}

  log(`Cycle done: ${percs.length} triggered → ${analyzed} analyzed → ${executed} executed`)
  if (trades.length > 0) log(`Trades: ${trades.join(', ')}`)
}

// ── Main ─────────────────────────────────────────────────────────────────────
async function main() {
  await ensureServer()

  // Check current config
  try {
    const cfg = await apiGet('/api/agent/config')
    log(`Config: mode=${cfg.mode || '?'} minConf=${cfg.minAiConfidence || '?'} maxTrade=$${cfg.maxTradeNotionalUsd || '?'}`)
  } catch {}

  const isLoop = process.argv.includes('--loop')
  log(isLoop ? 'Loop mode — every 1h' : 'One-shot mode')

  await cycle()

  if (isLoop) {
    while (true) {
      log(`Next scan in ${Math.round(SCAN_INTERVAL_MS / 60000)}m...`)
      await new Promise(r => setTimeout(r, SCAN_INTERVAL_MS))
      await cycle()
    }
  }
}

main().catch(err => {
  log(`FATAL: ${err.message}`)
  process.exit(1)
})
