'use client'

import React from 'react'
import { useState, useEffect, useCallback, useRef } from 'react'

// ── Types ─────────────────────────────────────────────────────────────────────

interface PortfolioPosition {
  coin: string
  side: string
  szi: number
  entryPx: number
  unrealizedPnl: number
  leverage: number
  notional: number
  markPrice: number
  livePnl: number
}

interface PortfolioData {
  equity: number
  totalNotional: number
  positions: PortfolioPosition[]
}

interface Analysis {
  id: string
  coin: string
  verdict: string
  confidence: number
  reasoning: string
  side?: string | null
  entryPx?: number
  stopPx?: number
  tpPx?: number
  createdAt: number
  taSignal?: string
  taScore?: number
  taReason?: string
  executed?: boolean
  blockedBy?: string[]
}

interface Trade {
  id: string
  coin: string
  side: string
  entryPx: number
  sizeUSD: number
  executedAt: number
  exitPx?: number
  pnl?: number
}

interface WatchlistItem {
  coin: string
  type: string
  mid: number
  compositeScore: number
  status: string
  triggers: Array<{ name: string; fired: boolean; reason: string }>
}

interface SessionLogEntry {
  cycle: number
  timestamp: string
  markets_scanned?: number
  triggers_fired?: number
  message?: string
  research_verdicts?: Array<{ coin: string; verdict: string; confidence: number; reasoning: string }>
  trades_executed?: Array<{ coin: string; side: string; sizeUSD: number; entryPx: number }>
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function ago(tsMs: number): string {
  if (!tsMs) return '-'
  const sec = Math.floor((Date.now() - tsMs) / 1000)
  if (sec < 30) return 'now'
  if (sec < 60) return `${sec}s`
  const min = Math.floor(sec / 60)
  return min < 60 ? `${min}m` : `${Math.floor(min / 60)}h`
}

function fmt(ts: string | number): string {
  const d = new Date(ts)
  return d.toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit' })
}

function verdictColor(v: string): string {
  const u = v?.toUpperCase() ?? ''
  if (u === 'LONG') return '#2E9E68'
  if (u === 'SHORT') return '#BE4A40'
  if (u === 'CLOSE') return '#3C6EA0'
  return '#C2956B'
}

function taColor(s: string): string {
  switch (s) {
    case 'CONFIRMED': return '#2E9E68'
    case 'WEAK': return '#F59E0B'
    case 'REJECTED': return '#BE4A40'
    default: return '#666'
  }
}

// ── Main Dashboard ────────────────────────────────────────────────────────────

export default function TradingDesk() {
  const [portfolio, setPortfolio] = useState<PortfolioData | null>(null)
  const [analyses, setAnalyses] = useState<Analysis[]>([])
  const [trades, setTrades] = useState<Trade[]>([])
  const [watchlist, setWatchlist] = useState<WatchlistItem[]>([])
  const [sessionLog, setSessionLog] = useState<SessionLogEntry[]>([])
  const [utcTime, setUtcTime] = useState('')
  const [winRate, setWinRate] = useState({ rate: 0, wins: 0, total: 0 })
  const [equity, setEquity] = useState(0)
  const [dailyPnl, setDailyPnl] = useState(0)

  // Hermes status
  type AgentStatus = 'idle' | 'starting' | 'active' | 'stopping'
  const [agentStatus, setAgentStatus] = useState<AgentStatus>('idle')
  const [lastCycleAt, setLastCycleAt] = useState<number | null>(null)
  const [cycleCount, setCycleCount] = useState(0)
  const [marketsScanned, setMarketsScanned] = useState(0)
  const statusRef = useRef<AgentStatus>('idle')
  statusRef.current = agentStatus

  // Clock
  useEffect(() => {
    const update = () => setUtcTime(
      new Date().toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit', timeZone: 'UTC' }) + ' UTC'
    )
    update()
    const id = setInterval(update, 1000)
    return () => clearInterval(id)
  }, [])

  // ── Poll portfolio every 8s ─────────────────────────────────────────────
  const fetchPortfolio = useCallback(async () => {
    try {
      const res = await fetch('/api/hl/portfolio')
      if (res.ok) {
        const data = await res.json()
        setPortfolio(data)
        if (data.equity != null) setEquity(data.equity)
      }
    } catch { /* ignore */ }
  }, [])

  // ── Poll agent state every 5s ───────────────────────────────────────────
  const fetchAgentState = useCallback(async () => {
    try {
      const res = await fetch('/api/agent/state')
      if (res.ok) {
        const d = await res.json()
        if (Array.isArray(d.recentAnalyses)) setAnalyses(d.recentAnalyses)
        if (Array.isArray(d.recentTrades)) setTrades(d.recentTrades)
        if (Array.isArray(d.watchlist)) setWatchlist(d.watchlist.slice(0, 10))
        // Note: equity comes from /api/hl/portfolio (the real source of truth)
        // Agent memory equity is only populated after scans — don't clobber
        if (d.winRate) setWinRate(d.winRate)
        if (d.dailyPnl != null) setDailyPnl(d.dailyPnl)
      }
    } catch { /* ignore */ }
  }, [])

  // ── Poll heartbeat status every 5s ──────────────────────────────────────
  const fetchStatus = useCallback(async () => {
    if (statusRef.current === 'starting' || statusRef.current === 'stopping') return
    try {
      const res = await fetch('/api/agent/start')
      if (res.ok) {
        const d = await res.json()
        setAgentStatus(d.running ? 'active' : 'idle')
      }
    } catch { /* ignore */ }
  }, [])

  // ── Poll session log every 5s ───────────────────────────────────────────
  const fetchSessionLog = useCallback(async () => {
    try {
      const res = await fetch('/api/agent/session-log')
      if (res.ok) {
        const log: SessionLogEntry[] = await res.json()
        if (Array.isArray(log)) {
          setSessionLog(log.slice(-40))
          const last = log[log.length - 1]
          if (last?.timestamp) {
            setLastCycleAt(new Date(last.timestamp).getTime())
            setCycleCount(last.cycle ?? cycleCount)
            if (last.markets_scanned != null) setMarketsScanned(last.markets_scanned)
          }
        }
      }
    } catch { /* ignore */ }
  }, [cycleCount])

  // ── Start / Stop ────────────────────────────────────────────────────────
  const startAgent = async () => {
    setAgentStatus('starting')
    try {
      const res = await fetch('/api/agent/start', { method: 'POST' })
      if (res.ok) setAgentStatus('active')
      else setAgentStatus('idle')
    } catch { setAgentStatus('idle') }
  }

  const stopAgent = async () => {
    setAgentStatus('stopping')
    try {
      const res = await fetch('/api/agent/stop', { method: 'POST' })
      if (res.ok) setAgentStatus('idle')
      else setAgentStatus('active')
    } catch { setAgentStatus('active') }
  }

  // Initial + intervals
  useEffect(() => {
    fetchPortfolio()
    fetchAgentState()
    fetchStatus()
    fetchSessionLog()
    const pId = setInterval(fetchPortfolio, 8000)
    const sId = setInterval(fetchAgentState, 5000)
    const tId = setInterval(fetchStatus, 5000)
    const lId = setInterval(fetchSessionLog, 5000)
    return () => { clearInterval(pId); clearInterval(sId); clearInterval(tId); clearInterval(lId) }
  }, [fetchPortfolio, fetchAgentState, fetchStatus, fetchSessionLog])

  // ── Derived ──────────────────────────────────────────────────────────────
  const positions = portfolio?.positions ?? []
  const totalPnl = portfolio
    ? positions.reduce((sum: number, p: PortfolioPosition) => sum + (p.livePnl ?? 0), 0)
    : dailyPnl
  const closedTrades = trades.filter(t => t.exitPx != null)
  const tradeWins = closedTrades.filter(t => (t.pnl ?? 0) > 0).length
  const tradePnl = closedTrades.reduce((s: number, t: Trade) => s + (t.pnl ?? 0), 0)

  const isActive = agentStatus === 'active'
  const isBusy = agentStatus === 'starting' || agentStatus === 'stopping'

  // ════════════════════════════════════════════════════════════════════════
  return (
    <div style={{ minHeight: '100vh', background: '#0a0a0a', color: '#d4d4d4', fontFamily: "var(--font-geist-mono, 'SF Mono', 'JetBrains Mono', monospace)" }}>

      {/* ── Top Control Bar ─────────────────────────────────── */}
      <nav style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 24px', borderBottom: '1px solid #1a1a1a', background: '#0a0a0a', position: 'sticky', top: 0, zIndex: 10 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
          <span style={{ fontWeight: 800, fontSize: 14, color: '#e5e5e5', letterSpacing: '0.04em' }}>hermes-trader</span>

          {/* Status badge */}
          <span style={{
            fontSize: 10, fontWeight: 700, padding: '3px 10px', borderRadius: 4,
            background: isActive ? 'rgba(46,158,104,0.10)' : 'rgba(190,74,64,0.10)',
            color: isActive ? '#2E9E68' : '#BE4A40',
            border: `1px solid ${isActive ? 'rgba(46,158,104,0.3)' : 'rgba(190,74,64,0.3)'}`,
            display: 'flex', alignItems: 'center', gap: 6,
          }}>
            <span style={{
              width: 6, height: 6, borderRadius: '50%',
              background: isActive ? '#2E9E68' : '#BE4A40',
              display: 'inline-block',
              animation: isActive ? 'pulse-live 1.5s infinite' : 'none',
            }} />
            {isBusy ? '...' : isActive ? 'ACTIVE' : 'IDLE'}
          </span>

          {/* Last cycle */}
          {lastCycleAt && isActive && (
            <span style={{ fontSize: 11, color: '#666' }}>
              cycle #{cycleCount} · {ago(lastCycleAt)} · {marketsScanned} mkts
            </span>
          )}
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          {/* Start / Stop */}
          <button
            onClick={isActive ? stopAgent : startAgent}
            disabled={isBusy}
            style={{
              fontSize: 11, fontWeight: 700, padding: '6px 14px', borderRadius: 6,
              cursor: isBusy ? 'not-allowed' : 'pointer',
              background: isActive ? 'rgba(190,74,64,0.10)' : 'rgba(46,158,104,0.10)',
              color: isActive ? '#BE4A40' : '#2E9E68',
              border: `1px solid ${isActive ? 'rgba(190,74,64,0.3)' : 'rgba(46,158,104,0.3)'}`,
              textTransform: 'uppercase', letterSpacing: '0.04em',
              opacity: isBusy ? 0.6 : 1,
              transition: 'all 0.15s',
            }}
          >
            {isBusy ? agentStatus === 'starting' ? 'Starting...' : 'Stopping...' : isActive ? 'Stop' : 'Start'}
          </button>

          <div style={{ fontSize: 11, color: '#555' }}>{utcTime}</div>
        </div>
      </nav>

      {/* ── Main Content ─────────────────────────────────── */}
      <div style={{ padding: '16px 20px 20px', maxWidth: 1600, margin: '0 auto' }}>

        {/* ── Stats Row ──────────────────────────────────── */}
        <div style={{ background: '#111', border: '1px solid #222', borderRadius: 8, padding: '14px 20px', marginBottom: 16 }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', gap: 10 }}>
            {[
              { label: 'EQUITY', value: `$${(equity || 0).toFixed(2)}`, color: '#e5e5e5' },
              { label: 'UNREAL PnL', value: `$${totalPnl >= 0 ? '+' : ''}${totalPnl.toFixed(2)}`, color: totalPnl >= 0 ? '#2E9E68' : '#BE4A40' },
              { label: 'WIN RATE', value: winRate.total > 0 ? `${(winRate.rate * 100).toFixed(1)}%` : '-', color: winRate.rate >= 0.5 ? '#2E9E68' : '#e5e5e5' },
              { label: 'TRADES', value: `${winRate.total} (${tradeWins}W/${Math.max(0, winRate.total - tradeWins)}L)`, color: '#e5e5e5' },
              { label: 'TRADE PnL', value: `${tradePnl >= 0 ? '+' : ''}$${tradePnl.toFixed(2)}`, color: tradePnl >= 0 ? '#2E9E68' : '#BE4A40' },
              { label: 'POSITIONS', value: `${positions.length}`, color: positions.length > 0 ? '#F59E0B' : '#555' },
            ].map(({ label, value, color }) => (
              <div key={label} style={{ background: '#0a0a0a', padding: '8px 12px', borderRadius: 6 }}>
                <div style={{ fontSize: 10, color: '#666', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 4 }}>{label}</div>
                <div style={{ fontSize: 16, fontWeight: 800, color, letterSpacing: '-0.02em' }}>{value}</div>
              </div>
            ))}
          </div>
        </div>

        {/* ── 3-Column Grid ───────────────────────────── */}
        <div style={{ display: 'grid', gridTemplateColumns: '240px 1fr 280px', gap: 16 }}>

          {/* ── Left: Positions + Watchlist ───────────── */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>

            {/* Positions */}
            <div style={{ background: '#111', border: '1px solid #222', borderRadius: 8, padding: 14 }}>
              <div style={{ fontSize: 10, fontWeight: 700, color: '#666', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 10 }}>Positions ({positions.length})</div>
              {positions.length === 0 ? (
                <div style={{ fontSize: 12, color: '#555', textAlign: 'center', padding: '24px 0' }}>FLAT</div>
              ) : (
                positions.map((p, i) => {
                  const c = p.side === 'long' ? '#2E9E68' : '#BE4A40'
                  const pnl = p.livePnl ?? p.unrealizedPnl ?? 0
                  return (
                    <div key={i} style={{ background: '#0a0a0a', padding: '8px 10px', borderRadius: 6, marginBottom: 6, borderLeft: `3px solid ${c}` }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 3 }}>
                        <span style={{ fontSize: 12, fontWeight: 800 }}>{p.coin}</span>
                        <span style={{ fontSize: 9, fontWeight: 700, padding: '1px 5px', borderRadius: 3, background: `${c}20`, color: c, border: `1px solid ${c}40`, textTransform: 'uppercase' }}>{p.side}</span>
                      </div>
                      <div style={{ fontSize: 10, color: '#888', lineHeight: 1.6 }}>
                        <div>Size: <span style={{ color: '#ccc' }}>{p.szi?.toFixed(4)}</span></div>
                        <div>Entry: <span style={{ color: '#ccc' }}>${p.entryPx?.toLocaleString()}</span></div>
                        <div>PnL: <span style={{ color: pnl >= 0 ? '#2E9E68' : '#BE4A40', fontWeight: 700 }}>{pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}</span></div>
                        <div>Lev: <span style={{ color: '#ccc' }}>{p.leverage}x</span></div>
                      </div>
                    </div>
                  )
                })
              )}
            </div>

            {/* Watchlist */}
            {watchlist.length > 0 && (
              <div style={{ background: '#111', border: '1px solid #222', borderRadius: 8, padding: 14 }}>
                <div style={{ fontSize: 10, fontWeight: 700, color: '#666', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 8 }}>Watchlist</div>
                {watchlist.map((w) => {
                  const fired = w.triggers?.filter(t => t.fired).length ?? 0
                  const total = w.triggers?.length ?? 0
                  const sc = w.compositeScore >= 80 ? '#2E9E68' : w.compositeScore >= 60 ? '#F59E0B' : '#666'
                  const st = w.status === 'scanning' ? '#3C6EA0' : w.status === 'analyzing' ? '#F59E0B' : w.status === 'trading' ? '#2E9E68' : '#666'
                  return (
                    <div key={w.coin} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '6px 0', borderBottom: '1px solid #1a1a1a', fontSize: 11 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <span style={{ fontWeight: 800, color: '#e5e5e5', width: 60 }}>{w.coin}</span>
                        <span style={{ color: '#888', fontSize: 10 }}>${w.mid?.toLocaleString() ?? '-'}</span>
                      </div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        {total > 0 && <span style={{ fontSize: 9, color: fired > 0 ? '#F59E0B' : '#555' }}>{fired}/{total}</span>}
                        <span style={{ fontSize: 10, fontWeight: 700, color: sc, width: 28, textAlign: 'right' }}>{w.compositeScore ?? '-'}</span>
                        <span style={{ fontSize: 8, fontWeight: 700, padding: '1px 5px', borderRadius: 3, background: `${st}15`, color: st, border: `1px solid ${st}30`, textTransform: 'uppercase' }}>{w.status}</span>
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </div>

          {/* ── Center: Reasoning Stream + Decisions ──── */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>

            {/* Session Log / Reasoning Stream */}
            <div style={{ background: '#111', border: '1px solid #222', borderRadius: 8, padding: 14, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
              <div style={{ fontSize: 10, fontWeight: 700, color: '#666', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 10, flexShrink: 0 }}>Activity Log</div>
              <div style={{ flex: 1, overflow: 'auto', maxHeight: 400 }}>
                {sessionLog.length === 0 ? (
                  <div style={{ fontSize: 11, color: '#555', padding: '20px 0' }}>{isActive ? '// awaiting first cycle...' : '// start Hermes to begin'}</div>
                ) : (
                  sessionLog.slice().reverse().map((entry, i) => {
                    const ts = entry.timestamp ? new Date(entry.timestamp).getTime() : 0
                    return (
                      <div key={i} style={{ padding: '7px 10px', borderBottom: '1px solid #111', fontSize: 11 }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 3 }}>
                          <span style={{ fontWeight: 700, color: '#888' }}>
                            #{entry.cycle ?? '?'}{' '}
                            {entry.markets_scanned ? `${entry.markets_scanned} mkts` : ''}
                            {entry.triggers_fired != null ? ` · ${entry.triggers_fired} fired` : ''}
                          </span>
                          <span style={{ color: '#555', fontSize: 10 }}>{entry.timestamp ? fmt(entry.timestamp) : '-'}</span>
                        </div>
                        {entry.message && (
                          <div style={{ color: '#999', fontSize: 10, lineHeight: 1.4 }}>{entry.message}</div>
                        )}
                        {entry.research_verdicts?.map((v, vi) => (
                          <div key={vi} style={{ color: verdictColor(v.verdict), fontSize: 10 }}>
                            {'  '}→ {v.coin} {v.verdict} {(v.confidence * 100).toFixed(0)}%
                          </div>
                        ))}
                        {entry.trades_executed?.map((t, ti) => (
                          <div key={ti} style={{ color: '#2E9E68', fontSize: 10 }}>
                            {'  '}→ EXEC {t.coin} {t.side} @ ${t.entryPx?.toFixed(2)} (${t.sizeUSD})
                          </div>
                        ))}
                      </div>
                    )
                  })
                )}
              </div>
            </div>

            {/* Recent AI Verdicts */}
            <div style={{ background: '#111', border: '1px solid #222', borderRadius: 8, padding: 14, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
              <div style={{ fontSize: 10, fontWeight: 700, color: '#666', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 10, flexShrink: 0 }}>AI Verdicts</div>
              <div style={{ flex: 1, overflow: 'auto', maxHeight: 300 }}>
                {analyses.length === 0 ? (
                  <div style={{ fontSize: 12, color: '#555', textAlign: 'center', padding: '20px 0' }}>No analyses yet</div>
                ) : (
                  <div>
                    {analyses.slice().reverse().slice(0, 15).map((a) => {
                      const vc = verdictColor(a.verdict)
                      return (
                        <div key={a.id ?? `${a.coin}-${a.createdAt}`} style={{ padding: '7px 10px', borderBottom: '1px solid #111', borderLeft: `2px solid ${vc}40`, background: a.executed ? `${vc}06` : 'transparent' }}>
                          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                              <span style={{ fontWeight: 800, fontSize: 11, color: vc }}>{a.verdict}</span>
                              <span style={{ fontWeight: 700, fontSize: 12, color: '#e5e5e5' }}>{a.coin}</span>
                              {a.executed && <span style={{ fontSize: 8, fontWeight: 700, padding: '1px 4px', borderRadius: 3, background: 'rgba(46,158,104,0.1)', color: '#2E9E68', border: '1px solid rgba(46,158,104,0.25)' }}>FILLED</span>}
                              {a.blockedBy?.length && <span style={{ fontSize: 8, fontWeight: 700, padding: '1px 4px', borderRadius: 3, background: 'rgba(190,74,64,0.1)', color: '#BE4A40', border: '1px solid rgba(190,74,64,0.25)' }}>BLOCKED</span>}
                            </div>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                              <span style={{ fontSize: 9, color: taColor(a.taSignal ?? 'PASS') }}>{a.taSignal ?? '-'}</span>
                              <span style={{ fontSize: 10, color: '#888' }}>{(a.confidence * 100).toFixed(0)}%</span>
                              <span style={{ fontSize: 9, color: '#555' }}>{ago(a.createdAt)}</span>
                            </div>
                          </div>
                          {a.reasoning && (
                            <div style={{ fontSize: 10, color: '#777', lineHeight: 1.4, marginTop: 3 }}>
                              {a.reasoning.split('\n').filter(l => l.trim()).slice(0, 1).map((line, li) => {
                                const cl = line.replace(/^[•\-*]\s*/, '').replace(/\*\*(.*?)\*\*/g, '$1').trim()
                                if (!cl) return null
                                return <div key={li} style={{ display: 'flex', gap: 5 }}><span style={{ color: '#555', flexShrink: 0 }}>·</span><span>{cl}</span></div>
                              }).filter(Boolean)}
                            </div>
                          )}
                        </div>
                      )
                    })}
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* ── Right: Trade Log ──────────────────────── */}
          <div style={{ background: '#111', border: '1px solid #222', borderRadius: 8, padding: 14, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
            <div style={{ fontSize: 10, fontWeight: 700, color: '#666', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 10, flexShrink: 0 }}>
              Trade Log
              {trades.length > 0 && <span style={{ color: '#555', fontWeight: 400 }}> ({closedTrades.length} closed)</span>}
            </div>
            <div style={{ flex: 1, overflow: 'auto', maxHeight: 750 }}>
              {trades.length === 0 ? (
                <div style={{ fontSize: 12, color: '#555', textAlign: 'center', padding: '40px 0' }}>No trades yet</div>
              ) : (
                <div>
                  <div style={{ display: 'grid', gridTemplateColumns: '60px 50px 70px 80px 70px 50px', gap: 0, paddingBottom: 6, borderBottom: '1px solid #222', marginBottom: 4 }}>
                    {['COIN', 'SIDE', 'SIZE', 'ENTRY', 'P&L', 'AGE'].map(h => (
                      <span key={h} style={{ fontSize: 8, fontWeight: 700, color: '#555', textTransform: 'uppercase', letterSpacing: '0.06em' }}>{h}</span>
                    ))}
                  </div>
                  {trades.slice().reverse().slice(0, 30).map((t, i) => {
                    const pnl = t.pnl ?? 0
                    const c = t.side === 'long' ? '#2E9E68' : '#BE4A40'
                    return (
                      <div key={t.id ?? i} style={{ display: 'grid', gridTemplateColumns: '60px 50px 70px 80px 70px 50px', gap: 0, padding: '5px 0', borderBottom: '1px solid #111', fontSize: 10, alignItems: 'center' }}>
                        <span style={{ fontWeight: 700, color: '#ccc' }}>{t.coin}</span>
                        <span style={{ fontSize: 9, textTransform: 'uppercase', color: c, fontWeight: 700 }}>{t.side.slice(0, 1)}</span>
                        <span style={{ color: '#888' }}>${t.sizeUSD.toFixed(0)}</span>
                        <span style={{ color: '#888' }}>{t.entryPx?.toFixed(2) ?? '-'}</span>
                        <span style={{ fontWeight: 700, color: pnl > 0 ? '#2E9E68' : pnl < 0 ? '#BE4A40' : '#555' }}>
                          {t.exitPx != null ? `${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}` : 'OPEN'}
                        </span>
                        <span style={{ color: '#555', fontSize: 9 }}>{ago(t.executedAt)}</span>
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          </div>

        </div>
      </div>

      <style dangerouslySetInnerHTML={{ __html: `@keyframes pulse-live{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.4;transform:scale(.85)}}` }} />
    </div>
  )
}
