// Shared types — used across lib/agent/*, lib/*, and API routes.

export interface Candle {
  t: number
  o: number
  h: number
  l: number
  c: number
  v: number
}

export type AgentVerdict = 'PASS' | 'LONG' | 'SHORT' | 'CLOSE'
export type TASignal = 'CONFIRMED' | 'WEAK' | 'REJECTED'
export type MarketCategory = 'crypto' | 'equity' | 'commodity'

// Agent config stored in .agent-config.json
export interface AgentConfig {
  mode: 'OFF' | 'LIVE'
  minAiConfidence?: number
  maxConcurrent?: number
  maxTradeNotionalUsd?: number
  maxDailyLossUsd?: number
  minMarketVolumeUsd?: number
  maxTotalNotionalPct?: number
  cooldownMin?: number
  coinAllowlist?: string[]
  coinBlocklist?: string[]
  [key: string]: unknown
}

// HL perp meta response
export interface HLMetaResponse {
  universe: Array<{
    name: string
    szDecimals: number
    maxLeverage: number
    minNtl?: string
  }>
}

// HL spot meta response
export interface HLSpotMetaResponse {
  universe: Array<{
    name: string
    szDecimals?: number
    tokens?: number[]
    index: number
  }>
  tokens: Array<{
    name: string
    szDecimals?: number
  }>
}

// HL clearinghouse state
export interface HLClearinghouseState {
  marginSummary?: { accountValue: string; totalNtlPos: string }
  assetPositions?: Array<{
    position: {
      coin: string
      szi: string
      entryPx: string
      unrealizedPnl: string
      leverage?: { value: string }
    }
  }>
}

// HL spot clearinghouse state
export interface HLSpotClearinghouseState {
  balances?: Array<{ coin: string; total: string; hold: string }>
}

// HL exchange response
export interface HLExchangeResponse {
  status: string
  response?: {
    data?: {
      statuses?: Array<{
        filled?: { totalSz: string; avgPx: string; oid: number }
        resting?: { oid: number }
        error?: string
      }>
    }
  }
}

// Raw candle from HL API
export interface HLCandleRow {
  t: number
  o: string
  h: string
  l: string
  c: string
  v: string
}
