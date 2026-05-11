// ── Risk gates: each takes trigger context + account state, returns pass/fail ─

import type { StoredTrigger } from './store'
// Minimal account shape — avoids importing AccountState directly
export interface AccountState { equity: number; totalEquity: number; totalNtl: number }

type HLPosition = { coin: string; side: 'long' | 'short'; sizeUSD: number }

interface AutoTradeConfig {
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

interface TradeEntry {
  coin: string
  side: 'long' | 'short'
  notionalUSD: number
  openedAt?: number
}

// Each gate returns {pass, reason?}. reason must be set when pass=false.
export type GateResult = { pass: boolean; reason?: string }
type GateFn = (
  trigger: StoredTrigger,
  account: AccountState,
  openPositions: TradeEntry[],
  recentTrades: Array<{ coin: string; pnl: number; closedAt: number }>,
  config: AutoTradeConfig,
) => GateResult

// ── 1. Confidence gate: AI verdict must be PASS with confidence ≥ threshold ─

export const confidenceGate: GateFn = (_trigger, _account, _positions, _trades, config) => {
  return { pass: true, reason: undefined }
}

// Confidence is checked at call-site after AI analysis returns.

// ── 2. Max concurrent positions ─

export const maxConcurrentGate: GateFn = (_trigger, _account, positions, _trades, config) => {
  if (positions.length >= config.maxConcurrent) {
    return { pass: false, reason: `${positions.length} open positions (max ${config.maxConcurrent})` }
  }
  return { pass: true }
}

// ── 3. Per-trade notional cap ─

export const perTradeNotionalGate: GateFn = (trigger, account, _positions, _trades, config) => {
  const proposedNotional = config.maxTradeNotionalUsd
  if (proposedNotional <= 0) {
    return { pass: false, reason: 'maxTradeNotionalUsd not set' }
  }
  return { pass: true, reason: undefined }
}

// ── 4. Daily loss kill switch ─
// If 24h realized PnL ≤ -MAX_DAILY_LOSS_USD, block all new trades until next UTC day ─

export const dailyLossKillSwitch: GateFn = (_trigger, _account, _positions, recentTrades, config) => {
  const now = Date.now()
  const dayAgo = now - 86_400_000
  const todayLoss = recentTrades
    .filter(t => t.closedAt >= dayAgo)
    .reduce((sum, t) => sum + t.pnl, 0)

  if (todayLoss <= -config.maxDailyLossUsd) {
    return { pass: false, reason: `daily loss $${todayLoss.toFixed(2)} exceeds cap -$${config.maxDailyLossUsd}` }
  }
  return { pass: true }
}

// ── 5. Market liquidity floor ─
// Skip if 24h volume < MIN_MARKET_VOLUME_USD ─
// Called with external 24h volume param at runtime since triggers don't carry it directly ─

export function marketLiquidityGate(
  trigger: StoredTrigger,
  _account: AccountState,
  _positions: TradeEntry[],
  _trades: Array<{ coin: string; pnl: number; closedAt: number }>,
  config: AutoTradeConfig,
  volume24h: number,
): GateResult {
  if (volume24h < config.minMarketVolumeUsd) {
    return { pass: false, reason: `${trigger.coin} 24h vol $${(volume24h/1e6).toFixed(1)}M < $${(config.minMarketVolumeUsd/1e6).toFixed(0)}M floor` }
  }
  return { pass: true }
}

// ── 6. Coin allowlist / blocklist ─

export const coinFilterGate: GateFn = (trigger, _account, _positions, _trades, config) => {
  if (config.coinAllowlist.length > 0 && !config.coinAllowlist.includes(trigger.coin)) {
    return { pass: false, reason: `${trigger.coin} not in allowlist` }
  }
  if (config.coinBlocklist.length > 0 && config.coinBlocklist.includes(trigger.coin)) {
    return { pass: false, reason: `${trigger.coin} is blocklisted` }
  }
  return { pass: true }
}

// ── 7. Cooldown per market ─
// Don't re-enter a coin within COOLDOWN_MIN minutes ─

export function cooldownGate(
  trigger: StoredTrigger,
  _account: AccountState,
  _positions: TradeEntry[],
  recentTrades: Array<{ coin: string; pnl: number; closedAt: number }>,
  config: AutoTradeConfig,
): GateResult {
  const cooldownMs = config.cooldownMin * 60_000
  const lastTrade = recentTrades
    .filter(t => t.coin === trigger.coin)
    .sort((a, b) => b.closedAt - a.closedAt)[0]

  if (lastTrade) {
    const elapsed = Date.now() - lastTrade.closedAt
    if (elapsed < cooldownMs) {
      return { pass: false, reason: `${trigger.coin} cooldown: ${Math.round((cooldownMs - elapsed) / 60_000)}m remaining` }
    }
  }
  return { pass: true }
}

// ── 8. Opposite direction guard ─
// If open position is long, block a new short on same coin (no auto-flips) ─

export const oppositeDirectionGuard: GateFn = (trigger, _account, positions, _trades, _config) => {
  const existingPos = positions.find(p => p.coin === trigger.coin)
  if (existingPos) {
    return { pass: false, reason: `existing ${existingPos.side} position on ${trigger.coin} — no auto-flip` }
  }
  return { pass: true }
}

// ── 9. Correlation cap ─
// Cap same-direction crypto-perp positions at 2 ─

export const correlationCap: GateFn = (trigger, _account, positions, _trades, _config) => {
  // Determine direction from trigger triggers (look for "long" / "short" in reasons)
  // This is approximate — the real direction comes from the AI verdict.
  // At this stage, we check total same-direction positions.
  const cryptoPositions = positions.filter(p => !isEquityPerp(p.coin))
  const sameDir = cryptoPositions.filter(p => {
    // Approximate: assume bullish triggers = long direction
    // Real gate uses AI verdict direction at call site
    return false // defer to runtime context
  })
  return { pass: true }
}

function isEquityPerp(coin: string): boolean {
  const EQUITY_PERPS = new Set(['TSLA', 'NVDA', 'AAPL', 'AMZN', 'GOOGL', 'MSFT', 'META', 'COIN', 'MSTR'])
  return EQUITY_PERPS.has(coin)
}

// ── Runtime correlation cap with known AI verdict direction ─

export function correlationCapWithDirection(
  openPositions: TradeEntry[],
  newSide: 'long' | 'short',
  maxSameDir = 2,
): GateResult {
  const sameDir = openPositions.filter(p => !isEquityPerp(p.coin) && p.side === newSide)
  if (sameDir.length >= maxSameDir) {
    return { pass: false, reason: `${sameDir.length} crypto ${newSide}s already open (cap ${maxSameDir})` }
  }
  return { pass: true }
}

// ── 10. Equity risk cap ─
// Total open notional ≤ MAX_TOTAL_NOTIONAL_PCT * equity ─

export const equityRiskCap: GateFn = (trigger, account, positions, _trades, config) => {
  const proposedNotional = config.maxTradeNotionalUsd
  const totalNtl = positions.reduce((s, p) => s + p.notionalUSD, 0) + proposedNotional
  const maxNtl = account.totalEquity * config.maxTotalNotionalPct

  if (totalNtl > maxNtl) {
    return { pass: false, reason: `total notional $${totalNtl.toFixed(0)} > ${(config.maxTotalNotionalPct * 100).toFixed(0)}% equity ($${maxNtl.toFixed(0)})` }
  }
  return { pass: true }
}

// ── Run all gates, collect all failures (not short-circuit) ─

export interface AllGateResults {
  pass: boolean
  failures: { gate: string; reason: string }[]
}

export function runAllGates(
  trigger: StoredTrigger,
  account: AccountState,
  openPositions: TradeEntry[],
  recentTrades: Array<{ coin: string; pnl: number; closedAt: number }>,
  config: AutoTradeConfig,
  volume24h: number,
  aiSide: 'long' | 'short',
): AllGateResults {
  const gates: [string, GateResult][] = []

  gates.push(['maxConcurrent', maxConcurrentGate(trigger, account, openPositions, recentTrades, config)])
  gates.push(['perTradeNotional', perTradeNotionalGate(trigger, account, openPositions, recentTrades, config)])
  gates.push(['dailyLossKillSwitch', dailyLossKillSwitch(trigger, account, openPositions, recentTrades, config)])
  gates.push(['marketLiquidity', marketLiquidityGate(trigger, account, openPositions, recentTrades, config, volume24h)])
  gates.push(['coinFilter', coinFilterGate(trigger, account, openPositions, recentTrades, config)])
  gates.push(['cooldown', cooldownGate(trigger, account, openPositions, recentTrades, config)])
  gates.push(['oppositeDirection', oppositeDirectionGuard(trigger, account, openPositions, recentTrades, config)])
  gates.push(['correlationCap', correlationCapWithDirection(openPositions, aiSide)])
  gates.push(['equityRiskCap', equityRiskCap(trigger, account, openPositions, recentTrades, config)])

  const failures = gates.filter(([, r]) => !r.pass).map(([name, r]) => ({ gate: name, reason: r.reason ?? 'unknown' }))

  return { pass: failures.length === 0, failures }
}
