import type { StoredTrigger } from '@/lib/scanner/store'

interface TriggerCardProps {
  trigger: StoredTrigger
  onAnalyze: (coin: string) => void
}

const TRIGGER_COLORS: Record<string, string> = {
  pctMoveSpike: '#F59E0B',
  volumeSpike: '#3B82F6',
  breakout: '#10B981',
  rangeCompression: '#8B5CF6',
}

function scoreColor(score: number): string {
  if (score >= 5) return '#2E9E68'
  if (score >= 2) return '#F59E0B'
  return '#BE4A40'
}

function statusBadge(t: StoredTrigger): { label: string; color: string; bg: string; border: string } | null {
  if (t.orderId && (t.analyzed === 'long' || t.analyzed === 'short')) {
    return { label: 'EXECUTED', color: '#2E9E68', bg: 'rgba(46,158,104,0.12)', border: 'rgba(46,158,104,0.3)' }
  }
  if (t.gateResults && Object.values(t.gateResults).some(g => !g.pass)) {
    return { label: 'BLOCKED', color: '#BE4A40', bg: 'rgba(190,74,64,0.12)', border: 'rgba(190,74,64,0.3)' }
  }
  if (t.analyzed) return null
  return null
}

// Simple SVG sparkline from trigger score bars
function ScoreBar({ score }: { score: number }) {
  const w = 80, h = 28
  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`}>
      <rect width={w} height={h} rx="4" fill="rgba(0,0,0,0.03)" />
      <rect
        x="4" y={h / 2 - 4}
        width={Math.max(4, (score / 10) * (w - 8))}
        height="8" rx="2"
        fill={scoreColor(score)}
        opacity="0.85"
      />
    </svg>
  )
}

export default function TriggerCard({ trigger, onAnalyze }: TriggerCardProps) {
  const badge = statusBadge(trigger)
  const timeAgo = ((Date.now() - trigger.firedAt) / 1000 / 60).toFixed(0)

  return (
    <div className="card" style={{
      padding: '14px 16px',
      borderColor: scoreColor(trigger.compositeScore) + '40',
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 10 }}>
        <div>
          <div style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 16, fontWeight: 800, color: 'var(--text-primary)', letterSpacing: '-0.01em' }}>
            {trigger.coin}-PERP
          </div>
          <div style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 11, color: 'var(--text-muted)', marginTop: 2 }}>
            ${trigger.mid.toLocaleString('en-US', { maximumFractionDigits: 2 })} · {timeAgo}m ago
          </div>
        </div>
        <ScoreBar score={trigger.compositeScore} />
      </div>

      <div style={{ fontSize: 12, fontWeight: 700, color: scoreColor(trigger.compositeScore), fontFamily: 'var(--font-geist-mono)', marginBottom: 8 }}>
        {trigger.compositeScore.toFixed(1)} / 10
      </div>

      {/* Trigger badges */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginBottom: 10 }}>
        {trigger.triggers.map((t, i) => (
          <span key={i} style={{
            padding: '2px 7px', borderRadius: 4, fontSize: 9, fontWeight: 600,
            color: TRIGGER_COLORS[t.name] ?? '#888',
            background: (TRIGGER_COLORS[t.name] ?? '#888') + '15',
            border: `1px solid ${(TRIGGER_COLORS[t.name] ?? '#888')}30`,
          }}>
            {t.reason}
          </span>
        ))}
      </div>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        {badge && (
          <span style={{
            padding: '2px 8px', borderRadius: 4, fontSize: 9, fontWeight: 700,
            letterSpacing: '0.05em',
            color: badge.color,
            background: badge.bg,
            border: `1px solid ${badge.border}`,
          }}>
            {badge.label}
          </span>
        )}
        <button
          onClick={() => onAnalyze(trigger.coin)}
          style={{
            padding: '6px 14px', borderRadius: 6, border: 'none',
            cursor: 'pointer', fontWeight: 600, fontSize: 11,
            background: 'var(--text-primary)',
            color: 'var(--bg-card)',
            marginLeft: badge ? 'auto' : undefined,
          }}
        >
          Analyze with AI
        </button>
      </div>
    </div>
  )
}
