'use client'

import { useEffect, useRef } from 'react'
import Link from 'next/link'
import s from './landing.module.css'

function useReveal() {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const root = ref.current
    if (!root) return
    const els = root.querySelectorAll(`.${s.reveal}`)
    const io = new IntersectionObserver(
      (entries) => entries.forEach((e) => {
        if (e.isIntersecting) { e.target.classList.add(s.visible); io.unobserve(e.target) }
      }),
      { threshold: 0.1, rootMargin: '0px 0px -48px 0px' },
    )
    els.forEach((el) => io.observe(el))
    return () => io.disconnect()
  }, [])
  return ref
}

function r(...extra: (string | undefined)[]) {
  return [s.reveal, ...extra].filter(Boolean).join(' ')
}

const PIPELINE = [
  { num: '01', name: 'Market Discovery',   desc: 'AOMI finds the active KXBTC15M 15-min window. Reads floor strike, close time, and live bid/ask from Kalshi.' },
  { num: '02', name: 'Live Price Feed',    desc: 'BTC spot from Coinbase. 15-min OHLCV candles, 1-min intra-window feed, and Bybit perp funding rate.' },
  { num: '03', name: 'Quant Pre-compute',  desc: 'RSI, MACD, Bollinger %B, Garman-Klass vol, Brownian and log-normal binary priors — all before the agent call.' },
  { num: '04', name: 'AOMI Agent',         desc: 'Natural language prompt to AOMI. The agent reasons over quant signals, orderbook, and time-to-expiry. Streams live.' },
  { num: '05', name: 'Risk Gate',          desc: 'Deterministic Kelly sizing, daily loss cap, drawdown guard. Outputs YES / NO / PASS with a limit price in cents.' },
  { num: '06', name: 'Kalshi Execution',   desc: 'One-click confirmation routes through your Kalshi credentials. AOMI proposes; you approve. Real orders, real settlement.' },
]

const FEATURES = [
  {
    icon: '◈',
    title: 'Chat-driven',
    body: 'Ask "Should I trade this window?" and get a live analysis with a trade recommendation — not a wall of charts.',
  },
  {
    icon: '⊛',
    title: '24/7 coverage',
    body: 'Kalshi BTC 15-min markets open around the clock. AOMI watches every window so you don\'t have to.',
  },
  {
    icon: '⟁',
    title: 'Quant-grounded',
    body: 'Every AOMI response is anchored in pre-computed signals: RSI, GK vol, Black-Scholes digital pricing.',
  },
  {
    icon: '⊕',
    title: 'You sign, you execute',
    body: 'AOMI proposes trades; your Kalshi credentials execute. No custody risk, no private key in the cloud.',
  },
]

const CODE = [
  { k: 'rsi_9',        v: '67.3',    c: '// approaching overbought' },
  { k: 'macd_hist',    v: '+12.4',   c: '// bullish momentum' },
  { k: 'bollinger_%b', v: '0.74',    c: '// upper-band pressure' },
  { k: 'gk_vol_1h',    v: '0.48%',   c: '// annualised σ via OHLC',  hi: 'amber' },
  { k: 'p_brownian',   v: '0.612',   c: '// Brownian P(YES)' },
  { k: 'p_lnBinary',   v: '0.598',   c: '// Black-Scholes digital' },
  { k: 'p_blended',    v: '0.638',   c: '// time-weighted blend' },
  { k: 'edge',         v: '+8.3pp',  c: '// vs market 55.5¢',        hi: 'green' },
]

export default function Landing() {
  const rootRef = useReveal()

  return (
    <div className={s.root} ref={rootRef}>

      {/* Nav */}
      <nav className={s.nav}>
        <a href="/" className={s.navLogo}>
          AOMI <span className={s.navLogoAccent}>TRADER</span>
        </a>
        <div className={s.navLinks}>
          <a href="#how"      className={s.navLink}>How it works</a>
          <a href="#signals"  className={s.navLink}>Signals</a>
          <a href="#features" className={s.navLink}>Features</a>
        </div>
        <Link href="/dashboard" className={s.navCta}>Open App →</Link>
      </nav>

      {/* Hero */}
      <section className={s.hero}>
        <div className={s.heroInner}>
          <p className={s.heroEyebrow}>KXBTC15M · Kalshi Binary Markets · Powered by AOMI</p>
          <h1 className={s.heroHeadline}>
            TRADE<br />
            <span className={s.heroAccent}>EVERY</span><br />
            WINDOW
          </h1>
          <p className={s.heroSub}>
            AOMI watches your Kalshi BTC prediction markets 24/7.
            Ask it what to trade. One click to execute.
            Never miss a 15-minute window again.
          </p>
          <div className={s.heroCtas}>
            <Link href="/dashboard" className={s.btnPrimary}>Open Dashboard →</Link>
            <a href="#how"          className={s.btnSecondary}>How it works</a>
          </div>
        </div>
        <div className={s.scrollCue}>
          <div className={s.scrollLine} />
          Scroll
        </div>
      </section>

      {/* Stats */}
      <div className={s.statsRow}>
        {[
          { num: '15',  accent: 'm', label: 'per Kalshi window',    desc: 'AOMI analyzes each window start-to-finish in real time' },
          { num: '24',  accent: '/7', label: 'agent coverage',      desc: 'Never miss a window while you sleep, work, or live your life' },
          { num: '6',   accent: '+', label: 'quant signals',        desc: 'RSI · MACD · GK vol · Black-Scholes · autocorr · momentum' },
        ].map(({ num, accent, label, desc }, i) => (
          <div className={`${s.statItem} ${r(i > 0 ? s.d1 : undefined)}`} key={label}>
            <div className={s.statNum}>{num}<span className={s.statAccent}>{accent}</span></div>
            <div className={s.statLabel}>{label}</div>
            <div className={s.statDesc}>{desc}</div>
          </div>
        ))}
      </div>

      {/* How it works */}
      <section className={s.section} id="how">
        <div className={s.inner}>
          <p className={`${s.label} ${r()}`}>How It Works</p>
          <h2 className={`${s.headline} ${r(s.d1)}`}>Six steps.<br />One decision.</h2>
          <p className={`${s.sub} ${r(s.d2)}`}>
            From market open to signed order in a single agent cycle.
            Each stage updates live in the dashboard — no waiting for the full pipeline.
          </p>
          <div className={s.pipelineList}>
            {PIPELINE.map((step, i) => (
              <div className={`${s.pipelineItem} ${r(i < 3 ? s.d1 : s.d2)}`} key={step.num}>
                <span className={s.pipelineNum}>{step.num}</span>
                <span className={s.pipelineName}>{step.name}</span>
                <span className={s.pipelineDesc}>{step.desc}</span>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Signals */}
      <section className={s.section} id="signals">
        <div className={s.inner}>
          <p className={`${s.label} ${r()}`}>Quantitative Framework</p>
          <div className={s.signalsGrid}>
            <div>
              <h2 className={`${s.signalsHeadline} ${r(s.d1)}`}>
                The math runs<br />before the agent.
              </h2>
              <p className={`${s.signalsBody} ${r(s.d2)}`}>
                All indicators are pre-computed in TypeScript before
                the AOMI call. The agent reasons about derived
                signals — not raw price data — so responses are faster
                and grounded in real statistics.
              </p>
              <ul className={`${s.signalsList} ${r(s.d2)}`}>
                {[
                  'Garman-Klass volatility — 7.4× more efficient than close-to-close',
                  'Log-normal binary option pricing (Black-Scholes digital)',
                  'Lag-1 autocorrelation for regime detection',
                  'Pressure-weighted orderbook imbalance',
                  'Price velocity + acceleration on 1-min candles',
                  'Dual prior blend — α → 0.70 at expiry',
                ].map(item => (
                  <li className={s.signalItem} key={item}>
                    <span className={s.signalDot} />
                    {item}
                  </li>
                ))}
              </ul>
            </div>
            <div className={`${s.codeBlock} ${r(s.d1)}`}>
              {CODE.map(line => (
                <div className={s.codeLine} key={line.k}>
                  <span className={s.codeKey}>{line.k}</span>
                  <span className={line.hi === 'green' ? s.codeGreen : line.hi === 'amber' ? s.codeAmber : s.codeVal}>
                    {line.v}
                  </span>
                  <span className={s.codeComment}>{line.c}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* Features */}
      <section className={s.section} id="features">
        <div className={s.inner}>
          <p className={`${s.label} ${r()}`}>Why AOMI</p>
          <h2 className={`${s.headline} ${r(s.d1)}`}>Built for traders<br />who can't watch 24/7.</h2>
          <div className={s.modesGrid}>
            {FEATURES.map((f, i) => (
              <div className={`${s.modeItem} ${r(i < 2 ? s.d1 : s.d2)}`} key={f.title}>
                <p className={s.modeName} style={{ fontSize: 20, marginBottom: 6 }}>{f.icon}</p>
                <p className={s.modeName}>{f.title}</p>
                <p className={s.modeDesc}>{f.body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* CTA */}
      <section className={s.cta}>
        <div className={s.ctaInner}>
          <h2 className={`${s.ctaHeadline} ${r()}`}>
            YOUR AGENT<br />IS READY
          </h2>
          <p className={`${s.ctaSub} ${r(s.d1)}`}>
            Live BTC data · Kalshi orderbook · AOMI agent · one-click execution.
          </p>
          <div className={`${s.ctaBtns} ${r(s.d2)}`}>
            <Link href="/dashboard" className={s.btnPrimary}>Open Dashboard →</Link>
            <Link href="/settings"  className={s.btnSecondary}>Connect Kalshi</Link>
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer className={s.footer}>
        <span className={s.footerBrand}>AOMI Trader · KXBTC15M · Powered by aomi-labs</span>
        <div className={s.footerLinks}>
          <Link href="/dashboard" className={s.footerLink}>Dashboard</Link>
          <Link href="/settings"  className={s.footerLink}>Settings</Link>
          <a href="https://github.com/aomi-labs/aomi" target="_blank" rel="noopener noreferrer" className={s.footerLink}>AOMI SDK</a>
        </div>
      </footer>

    </div>
  )
}
