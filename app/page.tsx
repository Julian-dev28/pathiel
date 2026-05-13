'use client'

import { useState, useEffect, useCallback } from 'react'

const REFRESH_MS = 3000

function formatNum(n: number, d = 2) {
  return n.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d })
}

function StatusDot({ active }: { active: boolean }) {
  return (
    <span style={{
      display: 'inline-block',
      width: 8, height: 8, borderRadius: '50%',
      background: active ? '#facc15' : '#666666',
      boxShadow: active ? '0 0 6px #facc15' : 'none',
      marginRight: 6,
      verticalAlign: 'middle',
    }} />
  )
}

function Card({ title, children, accent = '#facc15' }: { title: string; children: React.ReactNode; accent?: string }) {
  return (
    <div style={{
      background: '#2b2b2b',
      borderRadius: 10,
      padding: '14px 18px',
      borderLeft: `3px solid ${accent}`,
      flex: 1,
      minWidth: 120,
    }}>
      <div style={{ fontSize: 10, color: '#999999', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 6 }}>
        {title}
      </div>
      <div style={{ fontSize: 22, fontWeight: 700, fontFamily: 'monospace', color: '#facc15' }}>
        {children}
      </div>
    </div>
  )
}

function PipelineStage({ label, status, active }: { label: string; status: string; active: boolean }) {
  return (
    <div style={{
      flex: 1,
      padding: '10px 0',
      textAlign: 'center',
      opacity: active ? 1 : 0.3,
      transition: 'opacity 0.3s',
    }}>
      <div style={{
        fontSize: 11, fontWeight: 600, color: '#bbbbbb',
        marginBottom: 2,
        textTransform: 'uppercase',
        letterSpacing: 0.5,
      }}>
        {label}
      </div>
      <div style={{
        fontSize: 13, fontFamily: 'monospace',
        color: active ? '#facc15' : '#555555',
        background: active ? '#facc1518' : 'transparent',
        padding: '3px 10px',
        borderRadius: 4,
        display: 'inline-block',
      }}>
        {status}
      </div>
    </div>
  )
}

function TableRow({ label, value }: { label: string; value: string }) {
  return (
    <tr>
      <td style={{ color: '#999999', paddingRight: 16, fontSize: 12, whiteSpace: 'nowrap' }}>{label}</td>
      <td style={{ color: '#facc15', fontFamily: 'monospace', fontSize: 12 }}>{value}</td>
    </tr>
  )
}

export default function Home() {
  const [state, setState] = useState<any>(null)
  const [logs, setLogs] = useState<any[]>([])
  const [trades, setTrades] = useState<any[]>([])
  const [lastUpdated, setLastUpdated] = useState<string>('')
  const [error, setError] = useState<string>('')
  const [fetching, setFetching] = useState(false)

  const fetchAll = useCallback(async () => {
    setFetching(true)
    setError('')
    try {
      const [stateRes, logRes, tradeRes] = await Promise.all([
        fetch('/api/agent/state'),
        fetch('/api/agent/session-log'),
        fetch('/api/agent/trades'),
      ])
      if (!stateRes.ok) throw new Error(`state: ${stateRes.status}`)
      setState(await stateRes.json())
      setLogs(logRes.ok ? await logRes.json() : [])
      setTrades(tradeRes.ok ? await tradeRes.json() : [])
      setLastUpdated(new Date().toLocaleTimeString())
    } catch (e: any) {
      setError(e.message)
    } finally {
      setFetching(false)
    }
  }, [])

  useEffect(() => {
    fetchAll()
    const id = setInterval(fetchAll, REFRESH_MS)
    return () => clearInterval(id)
  }, [fetchAll])

  const mode = state?.config?.mode || 'OFF'
  const modeActive = mode === 'LIVE'
  const equity = state?.equity ?? 0
  const liveEquity = state?.liveEquity ?? 0
  const positions = state?.openPositions ?? []
  const analyses = state?.recentAnalyses ?? []
  const perceptions = state?.recentPerceptions ?? []
  const winRate = state?.winRate ?? { rate: 0 }
  const dailyPnl = state?.dailyPnl ?? 0
  const pnlPositive = dailyPnl >= 0

  const scanActive = perceptions.length > 0
  const taActive = perceptions.length > 0
  const researchActive = analyses.length > 0
  const execActive = trades.length > 0

  return (
    <div style={{
      background: '#2b2b2b',
      minHeight: '100vh',
      padding: '20px 24px',
      fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, monospace, system-ui',
      color: '#facc15',
    }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20 }}>
        <div style={{ display: 'flex', alignItems: 'center' }}>
          <span style={{ fontSize: 18, fontWeight: 700, marginRight: 12 }}>Hermes Trader</span>
          <StatusDot active={modeActive} />
          <span style={{ color: modeActive ? '#facc15' : '#555555', fontWeight: 600, textTransform: 'uppercase' }}>{mode}</span>
        </div>
        <div style={{ fontSize: 11, color: '#999999' }}>
          {fetching ? '⟳ refreshing...' : `Updated ${lastUpdated}`}
        </div>
      </div>

      {/* Pipeline Visualization */}
      <div style={{
        background: '#262626',
        borderRadius: 10,
        padding: '14px 20px',
        marginBottom: 16,
        display: 'flex',
        gap: 0,
        alignItems: 'center',
      }}>
        <div style={{ fontSize: 12, color: '#999999', marginRight: 16, fontWeight: 600 }}>PIPELINE</div>
        <PipelineStage
          label="Scan"
          status={perceptions.length > 0 ? `${perceptions.length} triggers` : 'idle'}
          active={scanActive}
        />
        <div style={{ color: '#3a3a3a', fontSize: 16, margin: '0 8px' }}>→</div>
        <PipelineStage
          label="TA Filter"
          status={taActive ? 'applied' : 'idle'}
          active={taActive}
        />
        <div style={{ color: '#3a3a3a', fontSize: 16, margin: '0 8px' }}>→</div>
        <PipelineStage
          label="AI Research"
          status={researchActive ? `${analyses.length} analyzed` : 'idle'}
          active={researchActive}
        />
        <div style={{ color: '#3a3a3a', fontSize: 16, margin: '0 8px' }}>→</div>
        <PipelineStage
          label="Execute"
          status={execActive ? `${trades.length} executed` : 'idle'}
          active={execActive}
        />
      </div>

      {/* Error */}
      {error && (
        <div style={{ background: '#1a0000', border: '1px solid #7f1d1d', borderRadius: 8, padding: '10px 14px', fontSize: 12, color: '#ef4444', marginBottom: 16 }}>
          ⚠ {error}
        </div>
      )}

      {/* Stats Row */}
      <div style={{ display: 'flex', gap: 12, marginBottom: 16, flexWrap: 'wrap' }}>
        <Card title="Equity">
          <span>${formatNum(equity)}</span>
          {liveEquity > 0 && <span style={{ fontSize: 11, color: '#999999', marginLeft: 8 }}>(live ${formatNum(liveEquity)})</span>}
        </Card>
        <Card title="Positions">{positions.length}</Card>
        <Card title="Daily PnL">
          <span style={{ color: pnlPositive ? '#22c55e' : '#ef4444' }}>
            {pnlPositive ? '+' : ''}{formatNum(dailyPnl)}
          </span>
        </Card>
        <Card title="Win Rate">
          {winRate.total > 0 ? `${formatNum(winRate.rate * 100)}%` : 'N/A'}
          {winRate.total > 0 && <span style={{ fontSize: 11, color: '#999999' }}>({winRate.wins}/{winRate.total})</span>}
        </Card>
      </div>

      {/* Two-column bottom */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
        {/* Recent Analyses */}
        <div style={{ background: '#262626', borderRadius: 10, padding: '14px 18px' }}>
          <div style={{ fontSize: 11, color: '#999999', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 10 }}>
            Recent Analyses
          </div>
          {analyses.length === 0 ? (
            <div style={{ fontSize: 12, color: '#666666', fontStyle: 'italic' }}>No analyses yet</div>
          ) : (
            <div style={{ maxHeight: 200, overflowY: 'auto' }}>
              {analyses.slice(-10).reverse().map((a: any, i: number) => (
                <div key={i} style={{
                  fontSize: 12, padding: '6px 0',
                  borderBottom: '1px solid #3a3a3a',
                  display: 'flex', justifyContent: 'space-between',
                }}>
                  <span>
                    <span style={{ color: '#facc15', fontWeight: 600 }}>{a.coin}</span>
                    <span style={{ color: '#999999', marginLeft: 8 }}>
                      {a.side || 'flat'} {a.confidence != null ? `${(a.confidence * 100).toFixed(0)}%` : ''}
                    </span>
                  </span>
                  <span style={{
                    color: a.verdict === 'EXECUTE' ? '#22c55e' : a.verdict === 'PASS' ? '#999999' : '#ef4444',
                    fontSize: 11,
                    fontWeight: 600,
                  }}>
                    {a.verdict}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Config */}
        <div style={{ background: '#262626', borderRadius: 10, padding: '14px 18px' }}>
          <div style={{ fontSize: 11, color: '#999999', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 10 }}>
            Configuration
          </div>
          <table style={{ borderCollapse: 'collapse', width: '100%' }}>
            <tbody>
              <TableRow label="Min Confidence" value={`${((state?.config?.minAiConfidence ?? 0.3) * 100).toFixed(0)}%`} />
              <TableRow label="Max Notional" value={`$${state?.config?.maxTradeNotionalUsd ?? 0}`} />
              <TableRow label="Max Concurrent" value={state?.config?.maxConcurrent ?? 0} />
              <TableRow label="Cooldown" value={`${state?.config?.cooldownMin ?? 30} min`} />
              <TableRow label="Max Daily Loss" value={`-$${state?.config?.maxDailyLossUsd ?? 0}`} />
              <TableRow label="Max Exposure" value={`${state?.config?.maxTotalNotionalPct ?? 15}%`} />
              <TableRow label="Last Scan" value={state?.lastScanAt ? new Date(state.lastScanAt).toLocaleTimeString() : 'Never'} />
            </tbody>
          </table>
        </div>
      </div>

      {/* Recent trades */}
      {trades.length > 0 && (
        <div style={{ background: '#262626', borderRadius: 10, padding: '14px 18px', marginTop: 16 }}>
          <div style={{ fontSize: 11, color: '#999999', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 10 }}>
            Recent Trades
          </div>
          <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: 12 }}>
            <thead>
              <tr style={{ color: '#999999' }}>
                <th style={{ textAlign: 'left', padding: '4px 8px' }}>Time</th>
                <th style={{ textAlign: 'left', padding: '4px 8px' }}>Coin</th>
                <th style={{ textAlign: 'left', padding: '4px 8px' }}>Side</th>
                <th style={{ textAlign: 'right', padding: '4px 8px' }}>Size</th>
                <th style={{ textAlign: 'right', padding: '4px 8px' }}>Entry</th>
                <th style={{ textAlign: 'right', padding: '4px 8px' }}>PnL</th>
              </tr>
            </thead>
            <tbody>
              {trades.slice(-20).reverse().map((t: any, i: number) => (
                <tr key={i} style={{ borderTop: '1px solid #3a3a3a' }}>
                  <td style={{ padding: '4px 8px', color: '#999999' }}>{t.time ? new Date(t.time).toLocaleTimeString() : '-'}</td>
                  <td style={{ padding: '4px 8px', fontWeight: 600, color: '#facc15' }}>{t.coin}</td>
                  <td style={{ padding: '4px 8px', color: t.side === 'LONG' ? '#22c55e' : '#ef4444' }}>{t.side}</td>
                  <td style={{ padding: '4px 8px', textAlign: 'right', fontFamily: 'monospace' }}>{t.size}</td>
                  <td style={{ padding: '4px 8px', textAlign: 'right', fontFamily: 'monospace' }}>{t.entryPrice}</td>
                  <td style={{ padding: '4px 8px', textAlign: 'right', fontFamily: 'monospace', color: (t.pnl ?? 0) >= 0 ? '#22c55e' : '#ef4444' }}>
                    {(t.pnl ?? 0) >= 0 ? '+' : ''}{formatNum(t.pnl ?? 0)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Footer */}
      <div style={{ textAlign: 'center', color: '#666666', fontSize: 11, marginTop: 24 }}>
        Hermes Trader v1.0 • Auto-refresh every {REFRESH_MS / 1000}s
      </div>
    </div>
  )
}
