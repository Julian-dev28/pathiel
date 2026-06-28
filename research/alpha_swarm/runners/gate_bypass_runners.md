# gate_bypass_runners — should we BYPASS gates to catch more runners?

**Read-only.** Data: `movers_dataset.json` (159 native perps + BTC, 1h ~2000 bars, 50 coins with a
>=50%/48h run). Gates tested against the REAL live logic in `executor.py::_runner_entry_block_reason`
+ `_sidestep_extension_block_reason` + `_late_chase_relax_ok`. Entry decided on bars<=i, FILLED i+1
open. Exit = live **tight-floor / scalp** (3.5% hard stop, then trail giving back 0.40 of the
peak-from-entry excursion — the DSL retrace ladder). Net@12bps, OOS first/second time-halves.
Scripts: `gate_bypass_bt.py` (fresh-impulse path), `gate_bypass_chase.py` (continuation-chase path).

## TL;DR verdict

**Do NOT relax any gate to catch runners. The extension cap blocks ZERO *tradeable* runners; its
"missed" runners are all sub-$0.2M-volume micro-cap mirages you cannot fill.** The conf floor and
composite/structure gates are not the binding constraint either — runner early-breakouts already pass
them. The runners we miss die upstream (entry-trigger timing + capital saturation), not at these gates.

---

## Gate 1 — EXTENSION CAP (`override_max_daily_extension_pct=30`)  →  KEEP. Relaxing is a mirage.

The cap exists because chasing extended coins is -EV (gate audit: `sidestep_extension_blocked`
median **-9.9%/24h**). The question: under the tight-floor exit, does relaxing it catch more runners
NET than the blow-off dumps it admits?

**The fresh-impulse runner trigger fires EARLY — the cap almost never binds on it.**
826 fresh-impulse entries (48h-high breakout + 5x volume + up bar) across the universe; **96% (790)
fire at <20% daily extension.** Only 14 fire at >=30% ext. The bot enters runners at the early stage,
*before* the cap is relevant.

**Cap sweep — fresh-impulse path (admit ext<CAP), tight-floor net@12bps:**

| cap | entries | runners | dumps | total net | mean/entry | OOS h1/h2 |
|-----|--------:|--------:|------:|----------:|-----------:|-----------|
| 30% (live) | 812 | 12 | 70 | +592% | +0.73% | +0.77/+0.69 ROBUST |
| 50% | 824 | 14 | 74 | +638% | +0.77% | ROBUST |
| 80% | 825 | 15 | 75 | +634% | +0.77% | ROBUST |
| no-cap | 826 | 15 | 76 | +630% | +0.76% | ROBUST |

Relaxing 30→no-cap adds **3 runners and 6 dumps over the ENTIRE dataset** (~83 days, 159 coins).

**Continuation-CHASE path** (the path the cap actually governs — uptrend + fresh local high on an
already-moving coin; this is the late-chase / sidestep PASS→LONG the cap filters): 3142 entries.
Marginal band admitted by 30→no-cap = **33 entries, +5 runners, +14 dumps, +1.45%/entry**, survives
to 50bps. Looks +EV. It is a **mirage** — here is every extended-band entry:

```
ext>=30% chase entries, by liquidity:
  kNEIRO +26% ($0.2M)  BABY +19% ($0.1M)  ORDI RUNNER ($0.2M)  BIO RUNNER ($0.2M)
  NIL RUNNER ($0.1M)   SAGA RUNNER ($0.1M) ... all winners are $0.1-0.2M VOLUME
  liquid (>=$5M): ZEC +1.3%(no run) WLD +0.3% JTO +0.7%/-3.5% XPL +2.8% — FLAT NOISE, 0 runners
```

Every runner the relaxed cap "catches" is a **$0.1-0.2M daily-volume** coin — untradeable at any real
size (the same untradeable-mirage the liquidity floor exists to avoid). Filter to tradeable
liquidity (>=$5M): the extended band collapses to ZEC/WLD/JTO/XPL = flat, **zero runners, one dump**.

**Direct runner accounting (of the 50 runner coins):**
- **12** are caught EARLY by the fresh-impulse breakout at <30% ext → cap is irrelevant, bot gets them.
- **3** are *only* catchable late (ext>=30%): **HMSTR ($0.2M), ORDI ($0.2M), SAGA ($0.1M)** — all
  micro-cap, untradeable.
- **35** produce no runner-grade fresh-impulse entry at all → missed by entry-trigger timing/horizon
  and capital saturation, **not** by the extension cap.

**Verdict: KEEP the 30% cap.** It blocks 0 tradeable runners and filters real dumps (dump rate
33-43% in the >=30% bands). The only +EV above 30% is micro-cap paper. The existing **20-30% LIQUID
`late_chase_relax` pocket is the correct ceiling** — my data shows the 20-30% band is already flat
(+0.18%/entry, 5% runner, 27% dump), and everything above 30% is thin-coin dumps. This re-confirms the
gate-audit -9.9% finding and the extension-latency memory note.

## Gate 2 — CONFIDENCE FLOOR (`min_confidence=0.65`, sidestep-exempt)  →  already loosened; not a candle gate

AI confidence is a model output — **not reconstructible from candles**, so it can't be swept in this
candle backtest (honest limitation). From the forward gate audit it is the purest entry-latency leak:
**49 distinct coins blocked, +1.0% median 24h** (vs the structure sub-reasons which have *negative*
median). It has already been addressed: floor dropped 0.70→0.65 AND made sidestep-exempt
(`_sidestep_ok` bypasses the conf check entirely for confirmed TA breakouts). The structural test here
confirms runner early-breakouts DO fire the signal (12/50 caught early), so the residual lever is the
AI-confidence layer, not structure — and it's already relaxed. See `validate_conf_floor.md`. No
further candle-side action; do not drop below 0.65 without the AI-layer forward eval.

## Gate 3 — min_composite / TREND FILTER  →  KEEP. Runners pass it; it's not the blocker.

The structural prerequisites (`fresh_impulse = volume_spike AND (breakout OR burst)`, uptrend) ARE
computable, and runners DO satisfy them — 12/50 runners produce a runner-grade fresh-impulse entry at
<30% ext, i.e. the structure/composite gate ADMITS the early runner. The trend filter is RIGHT per the
gate audit (median -2.2%/24h on what it blocks). Relaxing these admits the median dud (the
breakout/structure sub-reasons have negative median forward return). **Do not relax.**

---

## The honest bottom line

Relaxing the extension cap **loses money in practice**: the dumps outnumber the runners (33-43% dump
rate >=30% ext), and the only entries that pay are micro-cap pumps you cannot trade. It nets slightly
positive *on paper* purely because the tight-floor stop truncates each dump to -3.5% while a thin-coin
runner paper-prints +25% — but that +25% is unfillable. **No gate is safe to relax to catch more
runners.** The real leak for the 35 missed runners is upstream: entry-trigger timing/horizon and the
capital-saturation / stranded-xyz constraint (memory: "missed moves = capital saturation"), not the
runner gate. Spend effort there, not on bypassing gates.
