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
  { num: '02', name: 'Live Price Feed',    desc: 'BTC spot from Coinbase. Live YES/NO bid/ask from Kalshi. Strike price and seconds-to-close assembled into a structured snapshot.' },
  { num: '03', name: 'Context Assembly',   desc: 'Spot vs. strike delta, YES/NO ask prices, and time-to-expiry are packaged into a structured prompt context. AOMI reasons over numbers, not raw ticks.' },
  { num: '04', name: 'AOMI Agent',         desc: 'AOMI calls brave_search for live BTC news and sentiment, synthesises with the market snapshot, then streams its reasoning and verdict.' },
  { num: '05', name: 'Risk Gate',          desc: 'Kelly criterion sizes the position from your live Kalshi balance. Orders execute only at confidence ≥ 55%. PASS on weak or ambiguous signals.' },
  { num: '06', name: 'Kalshi Execution',   desc: 'Auto Mode places the order directly — zero clicks. Manual Mode surfaces a trade button on every BUY YES / BUY NO response. Real orders, real settlement.' },
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
    body: 'Kalshi BTC 15-min markets open around the clock. Auto Mode watches every window and executes trades so you don\'t have to.',
  },
  {
    icon: '⟁',
    title: 'Web-search grounded',
    body: 'Every AOMI response is anchored in a live Brave web search — BTC news, sentiment, and momentum pulled seconds before the verdict.',
  },
  {
    icon: '⊕',
    title: 'Your keys, your orders',
    body: 'Your Kalshi credentials live in your server environment. AOMI never holds funds or keys. Auto Mode executes when confident; Manual Mode keeps you in the loop.',
  },
]

const CODE = [
  { k: 'market',       v: 'KXBTC15M-…',  c: '// active window ticker' },
  { k: 'btc_spot',     v: '$97,420',      c: '// Coinbase live price' },
  { k: 'strike',       v: '$97,000',      c: '// Kalshi floor strike' },
  { k: 'delta',        v: '+$420',        c: '// spot above strike',      hi: 'green' },
  { k: 'yes_ask',      v: '63¢',          c: '// implied P(YES)' },
  { k: 'no_ask',       v: '40¢',          c: '// implied P(NO)' },
  { k: 'secs_left',    v: '612',          c: '// ~10 min remaining',      hi: 'amber' },
  { k: 'kelly_count',  v: '4 contracts',  c: '// 5% of balance @ 63¢',   hi: 'green' },
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
            96 windows open every day. The average trader catches 20 —
            the ones that happen to align with their schedule.
            AOMI covers all of them. Search → analyze → execute.
            One slider is the only configuration.
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
          { num: '96',  accent: '',   label: 'windows per day',       desc: 'Most traders catch 20. AOMI catches all 96 — while you sleep, work, or live your life.' },
          { num: '4.8', accent: '×', label: 'more at-bats',         desc: 'Same strategy. Same edge. 4.8× more opportunities to compound it.' },
          { num: '0',   accent: ' clicks', label: 'after setup',    desc: 'Analysis, sizing, order placement, retry on PASS — all runs without you.' },
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
            From window open to executed order in a single agent cycle.
            AOMI searches the web, reasons over live market data, and executes — or hands you the trade button.
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
          <p className={`${s.label} ${r()}`}>What AOMI Sees</p>
          <div className={s.signalsGrid}>
            <div>
              <h2 className={`${s.signalsHeadline} ${r(s.d1)}`}>
                Context built.<br />Agent searches.
              </h2>
              <p className={`${s.signalsBody} ${r(s.d2)}`}>
                A structured market snapshot is assembled in TypeScript
                before every AOMI call. The agent then searches the web
                for live news and sentiment, synthesises both, and
                streams a direct verdict — BUY YES, BUY NO, or PASS.
              </p>
              <ul className={`${s.signalsList} ${r(s.d2)}`}>
                {[
                  'BTC spot vs. Kalshi floor strike — direction and dollar delta',
                  'YES / NO ask prices — live implied probability from the orderbook',
                  'Seconds-to-close — urgency weighting in the agent\'s reasoning',
                  'Brave web search — breaking BTC news and current market sentiment',
                  'Bid/ask spread — liquidity and cost-of-entry signal',
                  'Kelly criterion — position sizing from your live Kalshi balance',
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
            96 windows a day. You were catching 20.<br />
            One slider. Everything else runs without you.
          </p>
          <div className={`${s.ctaBtns} ${r(s.d2)}`}>
            <Link href="/dashboard" className={s.btnPrimary}>Open Dashboard →</Link>
            <Link href="/agent"     className={s.btnSecondary}>Open Agent →</Link>
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer className={s.footer}>
        <span className={s.footerBrand}>AOMI Trader · KXBTC15M · Powered by aomi-labs</span>
        <div className={s.footerLinks}>
          <Link href="/dashboard" className={s.footerLink}>Dashboard</Link>
          <Link href="/agent"     className={s.footerLink}>Agent</Link>
          <a href="https://github.com/aomi-labs/aomi" target="_blank" rel="noopener noreferrer" className={s.footerLink}>AOMI SDK</a>
        </div>
      </footer>

    </div>
  )
}
