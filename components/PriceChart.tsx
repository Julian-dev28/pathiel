'use client'

import { useEffect, useRef } from 'react'
import type { PricePoint } from '@/lib/types'

const WINDOW_MS = 10 * 60 * 1000   // 10-min sliding window
const PAD = { t: 12, r: 20, b: 30, l: 58 }

function linePath(ctx: CanvasRenderingContext2D, pts: [number, number][]) {
  if (pts.length < 2) return
  ctx.moveTo(pts[0][0], pts[0][1])
  for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1])
}

export default function PriceChart({
  priceHistory,
  strikePrice,
  currentPrice,
}: {
  priceHistory: PricePoint[]
  strikePrice: number
  currentPrice: number
}) {
  const containerRef = useRef<HTMLDivElement>(null)
  const canvasRef    = useRef<HTMLCanvasElement>(null)
  const rafRef       = useRef<number | undefined>(undefined)

  // All live state lives in a ref — RAF closure reads it without stale captures
  const live = useRef({
    history:      [] as PricePoint[],
    strikePrice:  0,
    currentPrice: 0,
    pulseT:       0,   // 0→1 pulse cycle timer
    cssW:         600,
    cssH:         240,
  })

  // Sync incoming props into the ref every render
  live.current.history      = priceHistory
  live.current.strikePrice  = strikePrice
  live.current.currentPrice = currentPrice

  useEffect(() => {
    const canvas    = canvasRef.current
    const container = containerRef.current
    if (!canvas || !container) return

    const dpr = window.devicePixelRatio || 1
    const ctx  = canvas.getContext('2d')!

    const resize = () => {
      const rect = container.getBoundingClientRect()
      live.current.cssW = rect.width  || 600
      live.current.cssH = rect.height || 240
      canvas.width  = live.current.cssW * dpr
      canvas.height = live.current.cssH * dpr
      canvas.style.width  = `${live.current.cssW}px`
      canvas.style.height = `${live.current.cssH}px`
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    }
    resize()
    const ro = new ResizeObserver(resize)
    ro.observe(container)

    let prevNow = performance.now()

    const draw = (now: number) => {
      const dt = Math.min((now - prevNow) / 1000, 0.05)
      prevNow  = now

      const s = live.current
      const { cssW: W, cssH: H } = s

      s.pulseT = (s.pulseT + dt * 0.5) % 1   // 2s pulse cycle

      ctx.clearRect(0, 0, W, H)

      const iW = W - PAD.l - PAD.r
      const iH = H - PAD.t - PAD.b

      // History points only — live dot added separately so it never enters the spline
      const tNow = Date.now()
      const raw  = s.history.map(pt => ({ t: pt.timestamp, p: pt.price }))
      const tStart   = raw.length > 0 ? Math.max(raw[0].t, tNow - WINDOW_MS) : tNow - WINDOW_MS
      const windowMs = tNow - tStart
      const data     = raw.filter(d => d.t >= tStart)
      if (data.length < 1) { rafRef.current = requestAnimationFrame(draw); return }

      const toX = (t: number) => PAD.l + ((t - tStart) / Math.max(windowMs, 1)) * iW

      // Price domain — include live price so domain doesn't jump when it arrives
      const allP = [...data.map(d => d.p), s.currentPrice]
      if (s.strikePrice > 0) allP.push(s.strikePrice)
      const pMin = Math.min(...allP), pMax = Math.max(...allP)
      const pRange  = Math.max(pMax - pMin, 150)
      const pCenter = (pMax + pMin) / 2
      const yMin    = pCenter - pRange * 0.7
      const yMax    = pCenter + pRange * 0.7
      const toY = (p: number) => PAD.t + iH - ((p - yMin) / (yMax - yMin)) * iH

      const above = s.strikePrice > 0 && s.currentPrice > s.strikePrice
      const [cr, cg, cb] = above ? [58, 158, 114] : [224, 111, 160]
      const lineColor = `rgb(${cr},${cg},${cb})`

      // ── Grid lines ────────────────────────────────────────────────────────
      const step = (yMax - yMin) > 1000 ? 500 : (yMax - yMin) > 500 ? 200 : 100
      ctx.setLineDash([2, 6])
      ctx.strokeStyle = 'rgba(0,0,0,0.07)'
      ctx.lineWidth   = 1
      for (let p = Math.ceil(yMin / step) * step; p <= yMax; p += step) {
        if (s.strikePrice > 0 && Math.abs(p - s.strikePrice) < step / 2) continue
        const y = toY(p)
        if (y < PAD.t || y > PAD.t + iH) continue
        ctx.beginPath(); ctx.moveTo(PAD.l, y); ctx.lineTo(PAD.l + iW, y); ctx.stroke()
      }
      ctx.setLineDash([])

      // ── Strike line ───────────────────────────────────────────────────────
      if (s.strikePrice > 0) {
        const sy = toY(s.strikePrice)
        ctx.strokeStyle = '#d4872c'
        ctx.lineWidth   = 1.5
        ctx.setLineDash([5, 3])
        ctx.beginPath(); ctx.moveTo(PAD.l, sy); ctx.lineTo(PAD.l + iW, sy); ctx.stroke()
        ctx.setLineDash([])
        ctx.fillStyle = '#d4872c'
        ctx.font      = '9px ui-monospace, monospace'
        ctx.textAlign = 'right'
        ctx.fillText('STRIKE', PAD.l + iW, sy - 5)
      }

      // ── Spline through history; straight line to live dot ────────────────
      const histPts: [number, number][] = data.map(d => [toX(d.t), toY(d.p)])
      const lx = toX(tNow)
      const ly = toY(s.currentPrice)

      // Full path: spline history + straight lineTo live dot
      function fullPath() {
        if (histPts.length >= 2) {
          linePath(ctx, histPts)
          ctx.lineTo(lx, ly)
        } else if (histPts.length === 1) {
          ctx.moveTo(histPts[0][0], histPts[0][1])
          ctx.lineTo(lx, ly)
        } else {
          ctx.moveTo(lx, ly)
        }
      }

      const firstX = histPts.length > 0 ? histPts[0][0] : lx
      const firstY = histPts.length > 0 ? histPts[0][1] : ly

      // ── Gradient fill ─────────────────────────────────────────────────────
      const grad = ctx.createLinearGradient(0, PAD.t, 0, PAD.t + iH)
      grad.addColorStop(0,   `rgba(${cr},${cg},${cb},0.20)`)
      grad.addColorStop(0.7, `rgba(${cr},${cg},${cb},0.04)`)
      grad.addColorStop(1,   `rgba(${cr},${cg},${cb},0)`)
      ctx.beginPath()
      fullPath()
      ctx.lineTo(lx, PAD.t + iH)
      ctx.lineTo(firstX, PAD.t + iH)
      ctx.closePath()
      ctx.fillStyle = grad
      ctx.fill()

      // ── Price line ────────────────────────────────────────────────────────
      ctx.beginPath()
      fullPath()
      ctx.strokeStyle = lineColor
      ctx.lineWidth   = 2.5
      ctx.lineJoin    = 'round'
      ctx.lineCap     = 'round'
      ctx.stroke()

      // ── Live dot + two expanding pulse rings ──────────────────────────────
      for (let ring = 0; ring < 2; ring++) {
        const phase = (s.pulseT + ring * 0.45) % 1
        const r     = 5 + phase * 18
        const alpha = (1 - phase) * 0.45
        ctx.beginPath()
        ctx.arc(lx, ly, r, 0, Math.PI * 2)
        ctx.strokeStyle = `rgba(${cr},${cg},${cb},${alpha})`
        ctx.lineWidth   = 1.5
        ctx.stroke()
      }
      ctx.beginPath(); ctx.arc(lx, ly, 4.5, 0, Math.PI * 2)
      ctx.fillStyle = lineColor; ctx.fill()
      ctx.beginPath(); ctx.arc(lx, ly, 2, 0, Math.PI * 2)
      ctx.fillStyle = '#fff';    ctx.fill()

      // ── Y-axis labels ─────────────────────────────────────────────────────
      ctx.fillStyle = 'rgba(0,0,0,0.30)'
      ctx.font      = '9px ui-monospace, monospace'
      ctx.textAlign = 'right'
      for (let p = Math.ceil(yMin / step) * step; p <= yMax; p += step) {
        const y = toY(p)
        if (y < PAD.t || y > PAD.t + iH) continue
        ctx.fillText(`$${(p / 1000).toFixed(1)}k`, PAD.l - 6, y + 3)
      }

      // ── X-axis time labels ────────────────────────────────────────────────
      ctx.textAlign = 'center'
      const labelN  = 5
      for (let i = 0; i <= labelN; i++) {
        const t = tStart + (i / labelN) * windowMs
        const x = toX(t)
        const label = new Date(t).toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit' })
        ctx.fillText(label, x, PAD.t + iH + 18)
      }

      rafRef.current = requestAnimationFrame(draw)
    }

    rafRef.current = requestAnimationFrame(draw)
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current)
      ro.disconnect()
    }
  }, [])   // empty — all data flows through live ref

  const above     = strikePrice > 0 && currentPrice > strikePrice
  const lineColor = above ? '#3a9e72' : '#e06fa0'

  return (
    <div className="card" style={{ padding: '18px 14px 10px 14px', display: 'flex', flexDirection: 'column', width: '100%', boxSizing: 'border-box' }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14, padding: '0 4px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
          <div>
            <div style={{ fontSize: 10, color: 'var(--text-muted)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 2 }}>BTC / USD</div>
            <div style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 28, fontWeight: 800, color: lineColor, letterSpacing: '-0.02em', transition: 'color 0.4s ease' }}>
              {currentPrice > 0 ? `$${currentPrice.toLocaleString('en-US', { maximumFractionDigits: 0 })}` : '—'}
            </div>
          </div>
          <div style={{
            padding: '6px 12px', borderRadius: 9,
            background: above ? 'rgba(58,158,114,0.10)' : 'rgba(224,111,160,0.10)',
            border: `1px solid ${above ? 'rgba(58,158,114,0.25)' : 'rgba(224,111,160,0.25)'}`,
            transition: 'all 0.4s ease',
          }}>
            <div style={{ fontSize: 11, fontWeight: 800, color: lineColor }}>
              {above ? '↑ ABOVE STRIKE' : '↓ BELOW STRIKE'}
            </div>
          </div>
        </div>
        {strikePrice > 0 && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 11, color: 'var(--text-muted)' }}>
            <div style={{ width: 18, height: 2, background: 'var(--amber)', borderRadius: 1 }} />
            <span>Strike ${strikePrice.toLocaleString('en-US', { maximumFractionDigits: 0 })}</span>
          </div>
        )}
      </div>

      {/* Canvas — 60fps RAF loop */}
      <div ref={containerRef} style={{ width: '100%', height: 300, position: 'relative' }}>
        <canvas ref={canvasRef} style={{ display: 'block', borderRadius: 8 }} />
        {currentPrice <= 0 && (
          <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <div style={{ fontSize: 10, letterSpacing: '0.07em', color: 'var(--text-muted)', textTransform: 'uppercase' }}>Collecting price data…</div>
          </div>
        )}
      </div>
    </div>
  )
}
