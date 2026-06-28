# TA / AI Verdict Forensic Audit — did our skips and PASSes cost alpha?

Agent: `ta_ai_audit` (read-only). Date 2026-06-28.
Window: events with `ts` in [now−15d, now−72h] = **2026-06-13 → 2026-06-25** (every event gets a full 72h forward).
Forward returns: HL 1h candles fetched live, ref price = close of the candle containing the event ts.
Source: `~/.hermes-trader-session-log.jsonl`. Script: `scratchpad/analyze_audit.py` (+ `candle_cache.json`, `audit_events.json`).

Dedupe: research events deduped per (coin, verdict, hour-bucket); TA-skips deduped per (coin, signal, hour) then random-sampled.
185 unique coins, all candle fetches succeeded.

## REGIME CONTEXT (read this before the tables)
**BTC fell −10.2% over the window** (66,881 → 60,081). This is a DOWN-drift window. Every verdict bucket
has a negative mean end-return because the whole tape drifted down. So judge the AI on *relative separation*
(did LONG beat PASS?) and on *max-up* (did vetoed coins still run?), not on absolute end-return.

---

## A) AI RESEARCH VERDICTS — forward returns (every researched coin was already TA-confirmed)

| verdict | n | ret24 mean | ret72 mean | maxup72 mean | maxdn72 mean | ran ≥8% | ran ≥15% | ended up |
|---|---|---|---|---|---|---|---|---|
| **PASS** (AI vetoed the long) | 2641 | −0.13% | **−1.53%** | **+9.63%** | −8.18% | 41.7% | 20.9% | 40.1% |
| **LONG** (AI greenlit) | 1211 | −2.13% | **−4.53%** | **+6.75%** | −10.40% | 28.7% | 14.7% | 28.2% |
| **SHORT** | 585 | −1.63% | −5.08% | +5.10% | −9.94% | 19.7% | 7.7% | 23.4% |

### AI directional hit-rate (72h end-return)
- **LONG correct (ended up): 28.2%** (n=1211)
- SHORT correct (ended down): 76.6% (n=585) — but this is mostly down-market beta, not skill (everything drifted down).

### THE HEADLINE — the AI decision layer is INVERTED
The coins the AI **vetoed (PASS)** outperformed the coins it **greenlit (LONG)** on *every* metric:
- maxup72: PASS **+9.63%** vs LONG +6.75%
- end-ret72: PASS **−1.53%** vs LONG **−4.53%**
- ended-up rate: PASS 40.1% vs LONG 28.2%

If you had taken the AI's PASS list as your long book instead of its LONG list, you'd have done **+3.0pp better
on end-return and +2.9pp better on max-up.** The AI is anti-selecting: it greenlights the worse coins.

**Confidence is also inverted** (LONG bucket):

| conf band | n | ret72 mean | up-rate | maxup72 |
|---|---|---|---|---|
| 0.65–0.72 | 123 | −2.35% | 36% | +8.50% |
| ≥0.72 | 1087 | **−4.79%** | **27%** | +6.54% |

Higher AI confidence → *worse* forward returns. The confidence score has negative predictive value here.

### PASS opportunity cost (the suspected "AI vetoes TA-confirmed breakouts" leak)
- **41.7%** of PASS'd coins ran **≥+8%** within 72h (avg max-up of that subset **+18.2%**); **20.9% ran ≥+15%**.
- Distinct coins: **92 distinct coins ran ≥8% after a PASS, 57 ran ≥15%.**
- Top PASS misses: `xyz:BIRD` (+75%, +74%, +73%… researched-and-PASSed repeatedly through its whole run),
  `RESOLV` (+69%, +67%, …), `JTO` +58%, `EIGEN`. The bot looked at these, the TA confirmed them, the AI said PASS,
  and they ran 50-75%.

### Missed-alpha $ estimate (order of magnitude, heavily caveated)
Sum of best-episode max-up across the 92 distinct PASS-coins that ran ≥8% = **19.9x summed (1989%)**.
Realizable upside depends entirely on the trailing exit (max-up is a high-water mark, not a fill).

| notional / trade | trailing capture of max-up | missed over the 12-day window |
|---|---|---|
| $10 | 30% | ~$60 |
| $10 | 50% | ~$99 |
| $30 | 30% | ~$179 |
| $30 | 50% | ~$298 |

At the live ~$10-30 notional and a realistic 30-50% trailing capture, the PASS layer left **~$60-300 of upside
on the table in 12 days** — on the order of the entire ~$190 account. **Caveat:** PASS coins' *end-return* is
−1.53%, so this is only real upside *with a good trailing exit that banks the spike*; buy-and-hold-72h of the
PASS list loses money. (Memory says the exit engine catches 55-84% from base, which is what makes this capturable.)

---

## B) TA-SKIP events — did skipped coins stay quiet (right) or run (wrong)?

Sampled the genuinely-TA skip reasons (excluded ENTRY_PREFLIGHT / RESEARCH_THROTTLE / HELD_THROTTLE — those are
capital/token gating, not TA judgments). n=113 deduped TA-filter rejections with 72h forward.

| skip signal | n | maxup72 mean | ret72 mean | ran ≥8% | ran ≥15% | stayed quiet (<8%) |
|---|---|---|---|---|---|---|
| **PRE_RESEARCH_RUNNER_GATE** | 70 | **+14.53%** | −0.21% | **65.7%** | **50.0%** | 34.3% |
| **REJECTED** | 38 | +11.75% | −3.38% | 55.3% | 31.6% | 44.7% |
| NO_WILLIAMS_SETUP | 5 | +2.51% | −14.85% | 0% | 0% | 100% |
| **ALL TA-skips** | 113 | — | — | **59.3%** | — | 40.7% |

**The runner-gate is vetoing movers too.** 65.7% of runner-gate-blocked coins ran ≥8% and **half ran ≥15%**
within 72h. Top skip misses: `MET` (+45%, +38%, +35%, repeatedly gate-blocked through its run), `EIGEN`
(+36%, +35%, +31%), `JTO` +47%. Same signature as the AI PASS: the gate said "momentum not confirmed" and the
coin then ran 30-45%. Avg end-ret ≈ flat (−0.21%), so — same as PASS — the spike is real but only capturable
with a trailing exit.

`NO_WILLIAMS_SETUP` (n=5) correctly kept the bot out of dead coins (avg ret72 −14.85%) — but tiny sample.

---

## VERDICT

**Both veto layers are vetoing winners, not filtering losers — this confirms the entry-latency / AI-decision-layer leak.**

1. **AI PASS layer is inverted.** PASS'd (vetoed) coins outperformed LONG'd (greenlit) coins on max-up, end-return,
   and up-rate. AI confidence has negative predictive value. The AI is not adding directional skill on top of the
   TA confirm — it is subtracting it. This is the same finding as memory's "AI was blind to signals" /
   "Entry latency is the real leak", now re-confirmed on a fresh 12-day window with forward returns.
2. **Runner-gate is over-tight.** Half of gate-blocked coins ran ≥15%. It's filtering momentum that then moons.
3. **The captured-alpha is exit-conditioned.** Vetoed/skipped coins spike (high max-up) but mean-revert to roughly
   flat/negative end-returns in this down regime. The missed alpha is only bankable with the trailing exit that
   already exists. So the fix is *let more of these breakouts through* (relax PASS bias + runner-gate), not change exits.

### Caveats
- **Survivorship.** Coins that later delisted/died are absent from the live candle fetch, so a skip that dodged a
  death looks neutral and the veto layers look slightly *worse* than reality. But the miss magnitude (41.7% of PASS
  ran ≥8%; BIRD/RESOLV/MET/EIGEN are live, real runs) is far too large for survivorship to explain away.
- **Down regime.** BTC −10.2% over the window inflates SHORT "correctness" (beta, not skill) and depresses all
  end-returns. The relative finding (PASS > LONG) is regime-robust; the absolute $ figure is a down-window snapshot.
- **Max-up ≠ realized.** $ estimate assumes a 30-50% trailing capture; it is order-of-magnitude, not a backtest.
- n for the genuine TA-skip slice is small (113); RUNNER_GATE n=70 is the most reliable skip cell.
