# Quant Roadmap: From Signals to Sustainable Edge

## Current State

HERMES_TRADER Trader is a functional live trading system with three components:
1. **Agent** -- AI-driven BTC-PERP swing trader (60s loop, verdict → execute → repeat)
2. **Scanner** -- 230+ perps + 294 spot pairs, statistical triggers → AI analysis → risk gates → auto-trade
3. **Dashboard** -- Live charting and manual analysis

The scanner has four triggers: `pctMoveSpike`, `volumeSpike`, `breakout`, `rangeCompression`, with weighted composite scoring. The agent uses a 4h-entry / 1d-trend strategy with EMA crossovers, RSI filtering, and ATR-based stops.

### What Works
- Trigger engine fires on real statistical anomalies
- Risk gates collect all failures (no short-circuit) -- good audit trail
- DRY mode exists for paper trading
- Backtest system has 5,040 combo parameter sweeps
- Walk-forward testing with train/test splits
- Regime filtering via ADX, volume floor, daily EMA alignment
- Realistic transaction costs modeled in backtest

### What's Missing (Edge Killers)
1. **No feedback loop** -- Live trade results never flow back to improve the system
2. **No trigger attribution** -- Which triggers actually make money? Unknown
3. **No AI calibration** -- "LONG 72% confidence" is unverified noise
4. **No live vs. backtest reconciliation** -- You don't know if edge is being captured or lost
5. **No regime adaptation** -- System runs the same parameters regardless of market state
6. **No portfolio-level risk** -- 3 concurrent positions with unknown correlation

---

## Phase 1: Measurement System (Foundation)

**Build the infrastructure to measure everything.**

### 1.1 Trade Journal ✅ (Built)
- Persistent JSON file at `data/trade-journal.json`
- Schema: triggers → verdicts → trades → exits → predictions → calibration → regimes
- Writer functions in `lib/quant/journal.ts`
- Analysis CLI in `scripts/analyze-journal.mjs`

### 1.2 Analysis Dashboard (Next)
- `scripts/analyze-journal.mjs all` → comprehensive report
- Output: Trigger profitability, AI calibration, regime edge, live vs. backtest skew
- Use this daily to understand what's working

### 1.3 Seed with Historical Data
- Use seed-journal.ts to populate with realistic synthetic data
- Run actual backtest results as "predictions" for historical periods

**Deliverable:** Daily quant report showing exactly which triggers are profitable, how calibrated the AI is, and whether live performance matches backtest.

---

## Phase 2: Edge Validation

**Prove or disprove that any component has positive expected value.**

### 2.1 Trigger Attribution
- Link every scanner trigger to eventual trade outcome
- Calculate per-trigger: win rate, profit factor, avg P&L, sharpe
- Identify which triggers to emphasize/block in production

### 2.2 AI Confidence Calibration
- Track: AI says "X% confidence" → actual outcome
- Build calibration curve over 50+ trades
- If 80% confidence = 52% win rate → system is overconfident
- Adjust sizing based on actual calibration, not stated confidence

### 2.3 Live vs. Backtest Reconciliation
- For every live trade, run the backtest with same params at entry time
- Measure: Predicted WR vs. Actual WR
- Calculate skew across different regimes, coins, time periods
- Identify where edge degrades: slippage, timing, regime shift

### 2.4 Walk-Forward Rolling Analysis
- Train 180 days → test 30 days → slide forward 30 days → repeat
- Not just one train/test split, but rolling validation
- Track parameter stability across windows
- If parameters change dramatically between windows → overfitting

**Deliverable:** Quant scorecard:
```
TRIGGER PERFORMANCE
  pctMoveSpike:    WR=62%, PF=1.23, n=24, sharpe=0.34  ✅
  volumeSpike:     WR=40%, PF=0.87, n=12, sharpe=-0.12 ⚠️ 
  breakout:        WR=51%, PF=1.05, n=31, sharpe=0.08  ✅
  rangeCompression:WR=58%, PF=1.19, n=18, sharpe=0.21  ✅

AI CALIBRATION
  80-90% confidence → actual WR: 52% (⚠️ overconfident by 28%)
  90-100% confidence → actual WR: 48% (⚠️ overconfident by 42%)
  60-70% confidence → actual WR: 71% (✅ underconfident by 5%)

LIVE vs BACKTEST SKEW
  Overall: Live +$5.55 vs BT +$0.77 → skew +$4.78
  BTC: In line, ETH: Underperform, SOL: Degraded
```

---

## Phase 3: Optimization Loop

**Use measurement to improve the system systematically.**

### 3.1 Dynamic Parameter Adjustment
- If `volumeSpike` has PF < 1.0 for 30+ trades → reduce its composite weight
- If `pctMoveSpike` performs best in trending regimes → increase its weight when trending
- Auto-adjust trigger thresholds based on recent performance

### 3.2 Regime-Adaptive Sizing
- Increase size when in regimes with proven edge
- Decrease size or block entries in regimes where system loses money
- Example: If "ranging_high_vol" has 66% WR and "ranging_low_vol" has 33% → adjust exposure

### 3.3 AI Prompt Iteration with Validation
- When prompt changes → run walk-forward on new prompt
- Track: Which prompt versions generalize best across windows
- If new prompt improves in-sample but degrades out-of-sample → reject it

### 3.4 Correlation Management
- Track correlation between concurrent positions
- If 3 positions all have β > 0.8 to BTC → effective leverage is 3x, not 1x
- Block correlated entries or reduce size when correlation is high

**Deliverable:** Self-optimizing system that:
1. Measures what works
2. Adjusts parameters weekly based on performance
3. Blocks entries in regimes where it historically loses
4. Scales down when AI confidence is poorly calibrated

---

## Implementation Priority

1. ✅ Trade Journal (done)
2. ✅ Analysis CLI (done) 
3. 🔄 Backtest Reconciliation (in progress)
4. 📤 Seed with real data (need actual scanner runs)
5. 📊 Daily Quant Report (automated via cron)
6. 🔧 Dynamic Trigger Weight Adjustment
7. 📈 Regime-Adaptive Position Sizing
8. 🤖 AI Confidence Calibration System
9. 📉 Portfolio Correlation Tracking
10. 🔄 Walk-Forward Parameter Validation

---

## Rules for Edge Building

1. **Never deploy without measurement** -- Every change must be tracked in the journal
2. **Trust data over intuition** -- If trigger shows negative PF for 50+ trades, it's dead
3. **Validate out-of-sample** -- In-sample performance is meaningless alone
4. **Respect regime boundaries** -- A strategy that works in trends will die in ranges
5. **Correlation is hidden leverage** -- 3 correlated positions = 3x risk
6. **AI confidence is uncalibrated** -- Until proven otherwise, treat it as noise

---

## File Structure

```
lib/quant/
  ├── journal.ts              # Write/read trade journal
  └── backtest-reconciler.ts  # Live vs. backtest reconciliation

scripts/
  ├── analyze-journal.mjs     # CLI: all analyses
  └── seed-journal.ts         # Populate with sample data

data/
  └── trade-journal.json      # Persistent journal (gitignored)

docs/
  ├── journal-schema.md       # Database schema
  ├── trigger-analysis.md     # Per-trigger profitability reports
  ├── ai-calibration.md       # AI confidence calibration reports
  └── quant-roadmap.md        # This file
```