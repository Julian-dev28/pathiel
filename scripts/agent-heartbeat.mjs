// Agent heartbeat loop — runs as a standalone Node process alongside `next dev`
// Usage: node scripts/agent-heartbeat.mjs
// Polls the HL universe, scans for triggers, pushes high-score perceptions
// into the auto-analysis pipeline. All calls go through the Next.js API.
//
// Uses setTimeout (NOT setInterval) for drift-correct timing.
// NEVER crashes on network errors — logs and retries next cycle.
//
// Pipeline: Scan → TA Filter (server-side) → AI Research (CONFIRMED only) → Execute

const BASE_URL = process.env.SCANNER_API_URL || 'http://localhost:3000'
const SCAN_INTERVAL_MS = parseInt(process.env.AGENT_HEARTBEAT_INTERVAL_MS || '180000', 10) // 3min (was 60s)
const MIN_SCORE = parseInt(process.env.AGENT_MIN_SCORE || '80', 10) // higher bar -> fewer false triggers
const MAX_AI_PER_CYCLE = parseInt(process.env.AGENT_MAX_AI_PER_CYCLE || '2', 10) // only top 2 signals per cycle
const AI_RATE_LIMIT_MS = 15_000  // max 1 AI call per 15s globally (was 10s)

let lastAIAt = 0

function ts() {
  return new Date().toISOString().slice(11, 19)
}

function log(msg) {
  console.log(`[${ts()}] ${msg}`)
}

async function api(path, body) {
  const res = await fetch(`${BASE_URL}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(90_000),
  })
  if (!res.ok) throw new Error(`${res.status}: ${await res.text().slice(0, 200)}`)
  return res.json()
}

async function getConfig() {
  const res = await fetch(`${BASE_URL}/api/agent/config`)
  if (!res.ok) return { mode: 'OFF', autoAnalyzeThreshold: MIN_SCORE, minAiConfidence: 0.80 }
  return res.json()
}

// ── Main loop ──────────────────────────────────────────────────────────────

async function tick() {
  const cycleStart = Date.now()

  // 1. Scan
  let scans = 0, triggered = 0, analyzed = 0, traded = 0, equity = 0

  try {
    const config = await getConfig()

    const scanResult = await api('/api/agent/scan', { minScore: MIN_SCORE })
    const { perceptions = [], count = 0, scanned = 0 } = scanResult
    scans = scanned || count
    triggered = perceptions.length

    // Perceptions are auto-stored by the scan route

    // Fetch updated state for equity
    try {
      const stateRes = await fetch(`${BASE_URL}/api/agent/state`)
      if (stateRes.ok) {
        const state = await stateRes.json()
        equity = state.equity || 0
      }
    } catch { /* ignore state read */ }

    // 2. Run TA filter via /api/agent/scan (server-side indicator computation)
    // The scan route already runs TA on all triggered perceptions
    // so enriched perceptions come back with taSignal, taScore, etc.
    // Heartbeat filters to only AI-analyze CONFIRMED signals.
    let aiCount = 0
    const confirmed = perceptions.filter(p => {
      const sig = p.taSignal
      return sig === 'CONFIRMED'
    })
    log(`TA filter: ${confirmed.length} confirmed of ${perceptions.length} triggered`)

    // 3. Auto-analyze only CONFIRMED signals (skip WEAK/REJECTED)
    for (const p of perceptions) {
      // Only AI-analyze CONFIRMED signals — REJECTED and WEAK are statistical dead ends
      const sig = p.taSignal
      if (sig !== 'CONFIRMED') continue
      if (!config.mode || config.mode === 'OFF') break
      if (aiCount >= MAX_AI_PER_CYCLE) break

      const now = Date.now()
      if (now - lastAIAt < AI_RATE_LIMIT_MS) continue

      lastAIAt = now
      aiCount++

      try {
        const result = await api(`/api/agent/research/${encodeURIComponent(p.coin)}`, {
          perceptionId: p.id,
        })
        analyzed++
        if (result && result.executed) traded++
      } catch (err) {
        log(`research ${p.coin}: ${err.message}`)
      }
    }
  } catch (err) {
    log(`scan cycle failed: ${err.message} — retrying next cycle`)
  }

  const elapsed = Date.now() - cycleStart
  log(`scanned=${scans} triggered=${triggered} analyzed=${analyzed} traded=${traded} equity=$${equity.toFixed(0)} (${elapsed}ms)`)
}

// ── Bootstrap ───────────────────────────────────────────────────────────────

async function main() {
  log(`heartbeat started — interval=${SCAN_INTERVAL_MS / 1000}s, minScore=${MIN_SCORE}, api=${BASE_URL}`)
  log(`env vars: AGENT_HEARTBEAT_INTERVAL_MS, AGENT_MIN_SCORE, AGENT_MAX_AI_PER_CYCLE, SCANNER_API_URL`)

  process.on('SIGTERM', () => { log('SIGTERM — stopping'); process.exit(0) })
  process.on('SIGINT', () => { log('SIGINT — stopping'); process.exit(0) })

  // Warm-up: wait for next dev
  await new Promise(r => setTimeout(r, 8000))

  await tick()

  let nextTick = Date.now() + SCAN_INTERVAL_MS
  while (true) {
    const wait = Math.max(1000, nextTick - Date.now())
    await new Promise(r => setTimeout(r, wait))
    nextTick += SCAN_INTERVAL_MS
    if (Date.now() - nextTick > SCAN_INTERVAL_MS * 2) {
      log('fell behind — resetting schedule')
      nextTick = Date.now() + SCAN_INTERVAL_MS
    }
    await tick()
  }
}

main().catch(err => { log(`fatal: ${err.stack || err.message}`); process.exit(1) })
