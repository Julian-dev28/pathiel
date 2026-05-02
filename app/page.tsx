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
  { num: '01', name: 'Live Market Data',    desc: 'AOMI calls get_all_mids and get_l2_book — live BTC mid price, bid/ask spread, and full order book depth from Hyperliquid.' },
  { num: '02', name: 'Position Check',      desc: 'AOMI calls get_clearinghouse_state with your wallet address — live account equity, open positions, margin used, unrealized PnL.' },
  { num: '03', name: 'Web Search',          desc: 'brave_search fires for live BTC news, technical signals, and market sentiment. Agent reasons from what\'s happening right now, not training data.' },
  { num: '04', name: 'AOMI Analysis',       desc: 'Agent synthesizes Hyperliquid order book + account state + search results. Streams a direct LONG / SHORT / PASS verdict with confidence %.' },
  { num: '05', name: 'Risk Gate',           desc: 'Kelly criterion sizes the position from your live equity. 5× leverage. Orders execute only at confidence ≥ 55%. PASS on weak signals.' },
  { num: '06', name: 'HL Execution',        desc: 'Auto Mode places the order directly — EIP-712 signed IOC limit order on Hyperliquid. Zero clicks after setup. Real orders, real settlement.' },
]

const FEATURES = [
  {
    icon: '⬡',
    title: 'Live Hyperliquid data',
    body: 'Before every decision: live BTC price, order book depth, your account equity and open positions — all from Hyperliquid via AOMI tools.',
  },
  {
    icon: '⊛',
    title: 'Continuous 24/7 loop',
    body: 'BTC-PERP never closes. Auto Mode runs every 90 seconds — Hyperliquid data + web search → verdict → execute → hold → repeat.',
  },
  {
    icon: '⟁',
    title: 'Grounded in live data',
    body: 'Every verdict is anchored in a live Brave web search and Hyperliquid order book query — pulled seconds before the decision.',
  },
  {
    icon: '⊕',
    title: 'Your keys, your orders',
    body: 'Your wallet key lives in your server environment. AOMI never holds funds. Auto Mode executes when confident; Manual Mode keeps you in the loop.',
  },
]

const CODE = [
  { k: 'btc_price',   v: '$78,243.50',   c: '// Hyperliquid mid price' },
  { k: 'bid_depth',   v: '22.2 BTC',     c: '// at $78,243',           hi: 'green' },
  { k: 'ask_depth',   v: '1.19 BTC',     c: '// at $78,244' },
  { k: 'equity',      v: '$10.00',       c: '// your HL account value', hi: 'amber' },
  { k: 'position',    v: 'FLAT',         c: '// no open position' },
  { k: 'confidence',  v: '68%',          c: '// agent certainty',       hi: 'green' },
  { k: 'leverage',    v: '5×',           c: '// cross margin' },
  { k: 'verdict',     v: 'LONG',         c: '// execute',               hi: 'green' },
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
        <Link href="/agent" className={s.navCta}>Open Agent →</Link>
      </nav>

      {/* Hero */}
      <section className={s.hero}>
        <div className={s.heroInner}>
          <p className={s.heroEyebrow}>BTC-PERP · Hyperliquid · Powered by AOMI</p>
          <h1 className={s.heroHeadline}>
            TRADE<br />
            <span className={s.heroAccent}>BTC</span><br />
            PERP
          </h1>
          <p className={s.heroSub}>
            BTC-PERP runs 24/7. Before every trade, AOMI queries live Hyperliquid
            price and order book data, checks your position, and searches the web
            for current BTC news. LONG, SHORT, or PASS — grounded in what's
            happening right now.
          </p>
          <div className={s.heroCtas}>
            <Link href="/agent" className={s.btnPrimary}>Open Agent →</Link>
            <a href="#how"      className={s.btnSecondary}>How it works</a>
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
          { num: '24/7',  accent: '',    label: 'continuous market',   desc: 'BTC-PERP never closes. AOMI watches it continuously — no windows, no gaps, no missed signals.' },
          { num: '5',     accent: '×',   label: 'leverage',            desc: 'Cross-margin 5× leverage. Kelly-sized from your live equity. PASS on weak or ambiguous signals.' },
          { num: '0',     accent: ' clicks', label: 'after setup',     desc: 'Analysis, sizing, EIP-712 signing, order placement — all runs without you in Auto Mode.' },
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
            From analysis trigger to executed order in a single agent cycle.
            AOMI queries Hyperliquid, searches the web, reasons, and executes — or hands you the trade button.
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
                Live data queried.<br />Agent searches.
              </h2>
              <p className={`${s.signalsBody} ${r(s.d2)}`}>
                AOMI calls Hyperliquid tools before every decision — live price,
                order book depth, your account state. Then searches the web for
                current BTC news and sentiment. Synthesises both and streams
                a direct verdict: LONG, SHORT, or PASS.
              </p>
              <ul className={`${s.signalsList} ${r(s.d2)}`}>
                {[
                  'BTC mid price from Hyperliquid — get_all_mids, updated every cycle',
                  'Order book depth — get_l2_book, bids and asks with full size levels',
                  'Account equity and open positions — get_clearinghouse_state',
                  'Brave web search — live BTC news, technicals, and market sentiment',
                  'Funding rate — directional bias signal from the perpetual market',
                  'Kelly criterion — position sizing from your live Hyperliquid equity',
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
          <h2 className={`${s.headline} ${r(s.d1)}`}>Built for traders<br />who can&apos;t watch 24/7.</h2>
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
            BTC-PERP. 24/7. Live Hyperliquid data before every trade.<br />
            One slider. Everything else runs without you.
          </p>
          <div className={`${s.ctaBtns} ${r(s.d2)}`}>
            <Link href="/agent" className={s.btnPrimary}>Open Agent →</Link>
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer className={s.footer}>
        <span className={s.footerBrand}>AOMI Trader · BTC-PERP · Powered by aomi-labs</span>
        <div className={s.footerLinks}>
          <Link href="/agent"     className={s.footerLink}>Agent</Link>
          <a href="https://github.com/Julian-dev28/aomi-trader" target="_blank" rel="noopener noreferrer" className={s.footerLink}>GitHub</a>
          <a href="https://aomi.dev" target="_blank" rel="noopener noreferrer" className={s.footerLink}>AOMI</a>
        </div>
      </footer>

    </div>
  )
}
