#!/usr/bin/env node
// AOMI Trader MCP Server — gives Hermes Agent direct tool access to the autonomous trading engine.
// Usage: node scripts/aomi-mcp-server.mjs
//
// Exposes tools: scan, research, execute, watchlist, state, config
// Connects to the running Next.js dev/prod server via localhost API.

import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js'
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js'
import { z } from 'zod'

const BASE_URL = process.env.AOMI_API_URL || process.env.SCANNER_API_URL || 'http://localhost:3000'

function log(msg) {
  process.stderr.write(`[aomi-mcp] ${msg}\n`)
}

async function api(path, opts = {}) {
  const url = `${BASE_URL}${path}`
  const fetchOpts = {
    method: opts.method || 'GET',
    headers: { 'Content-Type': 'application/json' },
    signal: AbortSignal.timeout(30_000),
  }
  if (opts.body) fetchOpts.body = JSON.stringify(opts.body)
  const res = await fetch(url, fetchOpts)
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`${res.status} ${res.statusText}: ${text.slice(0, 300)}`)
  }
  return res.json()
}

// ── Server Setup ────────────────────────────────────────────────────────────

const server = new McpServer({
  name: 'aomi-trader',
  version: '1.0.0',
})

// Tool: Scan all Hyperliquid markets for trigger signals
server.tool('scan', 'Scan every market on Hyperliquid and return triggered candidates above a score threshold.', {
  minScore: z.number().min(0).max(100).default(75).describe('Minimum composite trigger score (0-100, default 75)'),
}, async ({ minScore = 75 }) => {
  try {
    const result = await api('/api/agent/scan', { method: 'POST', body: { minScore } })
    return {
      content: [{ type: 'text', text: JSON.stringify(result, null, 2) }],
    }
  } catch (err) {
    return { content: [{ type: 'text', text: `Scan failed: ${err.message}` }], isError: true }
  }
})

// Tool: Deep AI research on a specific coin
server.tool('research', 'Run deep multi-timeframe AI analysis on a specific market candidate.', {
  coin: z.string().describe('Coin ticker (e.g. BTC, ETH, SOL, TSLA)'),
  perceptionId: z.string().optional().describe('Optional perception ID from a prior scan'),
}, async ({ coin, perceptionId }) => {
  try {
    const body = {}
    if (perceptionId) body.perceptionId = perceptionId
    const result = await api(`/api/agent/research/${encodeURIComponent(coin)}`, { method: 'POST', body })
    return { content: [{ type: 'text', text: JSON.stringify(result, null, 2) }] }
  } catch (err) {
    return { content: [{ type: 'text', text: `Research failed: ${err.message}` }], isError: true }
  }
})

// Tool: Execute a trade based on a prior analysis
server.tool('execute', 'Execute a trade (or simulate) based on a prior analysis verdict, passing through all risk gates.', {
  analysisId: z.string().describe('Analysis ID from a research call'),
}, async ({ analysisId }) => {
  try {
    const result = await api('/api/agent/execute', { method: 'POST', body: { analysisId } })
    return { content: [{ type: 'text', text: JSON.stringify(result, null, 2) }] }
  } catch (err) {
    return { content: [{ type: 'text', text: `Execution failed: ${err.message}` }], isError: true }
  }
})

// Tool: Get agent state (watchlist, recent analyses, trades, config, mode)
server.tool('state', 'Get the full agent state: watchlist, recent perceptions, AI analyses, trades, config, and operating mode.', {}, async () => {
  try {
    const result = await api('/api/agent/state')
    return { content: [{ type: 'text', text: JSON.stringify(result, null, 2) }] }
  } catch (err) {
    return { content: [{ type: 'text', text: `State fetch failed: ${err.message}` }], isError: true }
  }
})

// Tool: Get/set agent config (mode, risk caps, thresholds)
server.tool('config', 'Get or set agent configuration: mode (OFF/LIVE), risk caps, analysis thresholds, coin filters.', {
  mode: z.enum(['OFF', 'LIVE']).optional().describe('Operating mode'),
  autoAnalyzeThreshold: z.number().optional().describe('Min composite score to trigger AI analysis'),
  minAiConfidence: z.number().optional().describe('Min AI confidence for execution'),
  maxConcurrent: z.number().optional().describe('Max concurrent positions'),
  maxTradeNotionalUsd: z.number().optional().describe('Max notional per trade in USD'),
  maxDailyLossUsd: z.number().optional().describe('Max daily loss before kill switch'),
  minMarketVolumeUsd: z.number().optional().describe('Min 24h market volume floor'),
  maxTotalNotionalPct: z.number().optional().describe('Max total notional as % of equity'),
  cooldownMin: z.number().optional().describe('Cooldown minutes between trades on same market'),
  coinAllowlist: z.array(z.string()).optional().describe('Allowed coins (empty = all)'),
  coinBlocklist: z.array(z.string()).optional().describe('Blocked coins'),
}, async (params) => {
  try {
    // Remove undefined params
    const body = Object.fromEntries(Object.entries(params).filter(([, v]) => v !== undefined))
    if (Object.keys(body).length === 0) {
      const result = await api('/api/agent/config')
      return { content: [{ type: 'text', text: JSON.stringify(result, null, 2) }] }
    }
    const result = await api('/api/agent/config', { method: 'POST', body })
    return { content: [{ type: 'text', text: JSON.stringify(result, null, 2) }] }
  } catch (err) {
    return { content: [{ type: 'text', text: `Config failed: ${err.message}` }], isError: true }
  }
})

// ── Start ───────────────────────────────────────────────────────────────────

async function main() {
  log(`starting aomi-trader MCP server — API: ${BASE_URL}`)
  const transport = new StdioServerTransport()
  await server.connect(transport)
  log('connected — listening on stdio')
}

main().catch(err => {
  log(`fatal: ${err.message}`)
  process.exit(1)
})
