// Risk gates — every gate is a pure function returning { pass, reason? }.
// ALL gates are evaluated; results are collected for telemetry, no short-circuit.

export type GateResult = { pass: boolean; reason?: string }

export interface GateContext {
  confidence: number
  currentPositions: Array<{ coin: string; side: string; sizeUSD: number }>
  tradeNotionalUSD: number
  dailyPnl: number
  marketVolume24hUSD: number
  coin: string
  tradeSide: 'long' | 'short'
  hasBinaryNewsRisk: boolean
  equity: number
  totalOpenNotional: number
}

export interface GateResults {
  [key: string]: GateResult
}

// ── Individual gates ─────────────────────────────────────────────────────────

export function confidenceGate(ctx: GateContext, minConfidence: number): GateResult {
  if (ctx.confidence >= minConfidence) return { pass: true }
  return { pass: false, reason: `confidence ${ctx.confidence.toFixed(2)} < ${minConfidence}` }
}

export function maxConcurrentPositionsGate(ctx: GateContext, maxConcurrent: number): GateResult {
  if (ctx.currentPositions.length < maxConcurrent) return { pass: true }
  return { pass: false, reason: `max positions reached (${ctx.currentPositions.length}/${maxConcurrent})` }
}

export function perTradeNotionalCapGate(ctx: GateContext, capUSD: number): GateResult {
  if (ctx.tradeNotionalUSD <= capUSD) return { pass: true }
  return { pass: false, reason: `trade notional $${ctx.tradeNotionalUSD.toFixed(0)} exceeds cap $${capUSD}` }
}

export function dailyLossKillSwitch(ctx: GateContext, maxDailyLoss: number): GateResult {
  if (ctx.dailyPnl > maxDailyLoss) return { pass: true }
  return { pass: false, reason: `daily loss killswitch triggered (PnL $${ctx.dailyPnl.toFixed(0)} <= $${maxDailyLoss})` }
}

export function marketLiquidityFloor(ctx: GateContext, minVolume: number): GateResult {
  if (ctx.marketVolume24hUSD >= minVolume) return { pass: true }
  return { pass: false, reason: `market 24h volume $${(ctx.marketVolume24hUSD / 1e6).toFixed(1)}M below floor $${(minVolume / 1e6).toFixed(1)}M` }
}

export function coinAllowlistGate(ctx: GateContext, allowlist: string[], blocklist: string[]): GateResult {
  if (blocklist.length > 0 && blocklist.includes(ctx.coin)) {
    return { pass: false, reason: `${ctx.coin} is on the coin blocklist` }
  }
  if (allowlist.length > 0 && !allowlist.includes(ctx.coin)) {
    return { pass: false, reason: `${ctx.coin} not on the allowlist` }
  }
  return { pass: true }
}

export function cooldownGate(ctx: GateContext, lastTradeTime: number | undefined, cooldownMin: number): GateResult {
  if (lastTradeTime === undefined) return { pass: true }
  const elapsed = (Date.now() - lastTradeTime) / 60_000
  if (elapsed >= cooldownMin) return { pass: true }
  return { pass: false, reason: `cooldown active (${Math.floor(cooldownMin - elapsed)}min remaining)` }
}

export function oppositeDirectionGuard(ctx: GateContext): GateResult {
  const existing = ctx.currentPositions.find(p => p.coin === ctx.coin)
  if (!existing) return { pass: true }
  if (existing.side !== ctx.tradeSide) {
    return { pass: false, reason: `opposite position exists (${ctx.coin} ${existing.side}) — no auto-flip` }
  }
  return { pass: true }
}

export function correlationCap(ctx: GateContext, maxCryptoCorrelated: number): GateResult {
  if (ctx.tradeSide !== 'long') return { pass: true } // only cap long correlation
  const cryptoCoins = new Set(['BTC', 'ETH', 'SOL', 'BNB', 'XRP', 'DOGE', 'ADA', 'AVAX', 'MATIC', 'LINK', 'DOT', 'UNI', 'ATOM', 'NEAR', 'FTM', 'APT', 'ARB', 'OP', 'INJ', 'TIA', 'SUI', 'SEI', 'WIF', 'PEPE', 'BONK', 'FLOKI', 'TRX', 'LTC', 'BCH', 'ETC', 'XLM', 'ALGO', 'AAVE', 'MKR', 'SNX', 'CRV', 'COMP', 'YFI', 'SUSHI', '1INCH'])
  const existingCryptoLongs = ctx.currentPositions.filter(p => cryptoCoins.has(p.coin) && p.side === 'long').length
  if (existingCryptoLongs < maxCryptoCorrelated) return { pass: true }
  return { pass: false, reason: `crypto long correlation cap reached (${existingCryptoLongs}/${maxCryptoCorrelated})` }
}

export function equityRiskCap(ctx: GateContext, maxTotalNotionalPct: number): GateResult {
  const maxNotional = ctx.equity * maxTotalNotionalPct
  const projectedNotional = ctx.totalOpenNotional + ctx.tradeNotionalUSD
  if (projectedNotional <= maxNotional) return { pass: true }
  return { pass: false, reason: `total notional $${projectedNotional.toFixed(0)} would exceed ${maxTotalNotionalPct * 100}% of equity ($${maxNotional.toFixed(0)})` }
}

export function newsBlackoutGate(ctx: GateContext): GateResult {
  if (!ctx.hasBinaryNewsRisk) return { pass: true }
  return { pass: false, reason: 'binary news risk detected (Fed, earnings, hack within 2h) — standing down' }
}

// ── Evaluate all gates and collect results ────────────────────────────────────

export function evalAllGates(
  ctx: GateContext,
  config: Record<string, unknown>,
  lastTradeTime: number | undefined,
): { results: GateResults; blocked: boolean; blockReasons: string[] } {
  const results: GateResults = {}
  results.confidence = confidenceGate(ctx, (config.minAiConfidence as number) ?? 0.8)
  results.maxConcurrent = maxConcurrentPositionsGate(ctx, (config.maxConcurrent as number) ?? 3)
  results.notionalCap = perTradeNotionalCapGate(ctx, (config.maxTradeNotionalUsd as number) ?? 200)
  results.dailyLoss = dailyLossKillSwitch(ctx, (config.maxDailyLossUsd as number) ?? -100)
  results.liquidity = marketLiquidityFloor(ctx, (config.minMarketVolumeUsd as number) ?? 5_000_000)
  results.coinFilter = coinAllowlistGate(ctx, (config.coinAllowlist as string[]) ?? [], (config.coinBlocklist as string[]) ?? [])
  results.cooldown = cooldownGate(ctx, lastTradeTime, (config.cooldownMin as number) ?? 60)
  results.oppositeGuard = oppositeDirectionGuard(ctx)
  results.correlation = correlationCap(ctx, 2)
  results.equityRisk = equityRiskCap(ctx, (config.maxTotalNotionalPct as number) ?? 0.3)
  results.news = newsBlackoutGate(ctx)

  const blockReasons: string[] = []
  let blocked = false
  for (const [key, result] of Object.entries(results)) {
    if (!result.pass) {
      blocked = true
      blockReasons.push(result.reason ?? key)
    }
  }
  return { results, blocked, blockReasons }
}
