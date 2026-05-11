// ── Scanner Daemon ───────────────────────────────────────────────────────────
// Runs alongside next dev: node scripts/scanner-daemon.mjs
//
// Loops every SCANNER_INTERVAL_MS (default 60s), calls /api/scanner/scan,
// and lets the Next.js route handle storage.
//
// Uses setTimeout (NOT setInterval) for drift-correct timing.
// Handles SIGTERM/SIGINT for graceful shutdown.

const BASE_URL = process.env.SCANNER_API_URL || 'http://localhost:3000'
const SCAN_INTERVAL_MS = parseInt(process.env.SCANNER_INTERVAL_MS || '60000', 10)
const MIN_SCORE = parseFloat(process.env.SCANNER_MIN_SCORE || '1.0')

let shuttingDown = false

function log(msg) {
  console.log(`[${new Date().toISOString()}] [daemon] ${msg}`)
}

async function scanCycle() {
  const start = Date.now()
  try {
    const res = await fetch(`${BASE_URL}/api/scanner/scan`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ minScore: MIN_SCORE }),
      signal: AbortSignal.timeout(90_000), // 90s timeout per scan
    })

    if (res.status === 429) {
      log('rate limited — skipping, next cycle on schedule')
      return
    }

    if (!res.ok) {
      log(`scan returned ${res.status}: ${await res.text().catch(() => '')}`)
      return
    }

    const data = await res.json()
    const elapsed = Date.now() - start
    const count = data.triggers?.length ?? 0
    log(`scan complete: ${data.scanned ?? '?'} markets, ${count} trigger(s) in ${elapsed}ms`)

    for (const t of data.triggers ?? []) {
      log(`  TRIGGER: ${t.coin} score=${t.compositeScore.toFixed(2)} mid=$${t.mid} — ${t.triggers?.map(tr => tr.reason).join('; ')}`)
    }
  } catch (err) {
    log(`scan failed: ${err.message}`)
  }
}

async function main() {
  log(`starting — interval=${SCAN_INTERVAL_MS / 1000}s, minScore=${MIN_SCORE}, api=${BASE_URL}`)

  process.on('SIGTERM', () => { log('SIGTERM — shutting down'); shuttingDown = true })
  process.on('SIGINT', () => { log('SIGINT — shutting down'); shuttingDown = true })

  // Initial scan after short delay (let next dev warm up)
  await new Promise(r => setTimeout(r, 5000))
  if (shuttingDown) return
  await scanCycle()

  // Subsequent scans with drift-correct timing
  let nextScan = Date.now() + SCAN_INTERVAL_MS

  while (!shuttingDown) {
    const wait = Math.max(1000, nextScan - Date.now())
    await new Promise(r => setTimeout(r, wait))
    if (shuttingDown) break

    // Catch up if we fell multiple cycles behind
    if (Date.now() - nextScan > SCAN_INTERVAL_MS * 2) {
      log('fell behind — resetting schedule')
      nextScan = Date.now() + SCAN_INTERVAL_MS
    }

    await scanCycle()
    nextScan += SCAN_INTERVAL_MS
  }

  log('stopped')
  process.exit(0)
}

main().catch(err => {
  log(`fatal: ${err.message}`)
  process.exit(1)
})
