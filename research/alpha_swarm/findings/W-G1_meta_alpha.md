# W-G1 — Meta-alpha from our own decision exhaust (session log, 2026-05-27 → 2026-07-09)

**Hypothesis:** the bot's PIT-logged decisions (AI verdicts, gate blocks, fills) contain measurable information about what earns and what bleeds — nobody can lookahead on our own exhaust.

**Data:** `~/.hermes-trader-session-log.jsonl` (395,836 lines; 31,400 research / 14,537 execute / 12,287 preflight / 1,016 dsl_exit events). Episodes deduped per (coin, label) with a 6h refractory window: 4,644 research / 4,256 execute / 407 preflight episodes. Fabricated pytest coins (C1/C2/C3) filtered. Fills priced at the NEXT 1h bar open after decision ts (PIT). Forward horizons 1h/6h/24h; costs 12/25 bps round-trip. 4 delisted coins (GRASS, IO, ONDO, W) have no candles and drop out — for the -EV long finding this bias is conservative (dead coins would make longs look worse).

**Script:** `research/alpha_swarm/hypotheses/W-G1_meta_alpha.py` (stages: extract / fetch / analyze / robust; caches in scratchpad).

## 1. AI verdict calibration — LONG confidence is ANTI-calibrated

24h forward, gross %, deduped episodes:

| verdict | conf band | n | mean 24h | win | net@25bps |
|---|---|---|---|---|---|
| LONG | 0.62–0.70 | 168 | −1.21% | 0.46 | −1.46% |
| LONG | 0.70–0.80 | 609 | **−2.13%** | 0.36 | −2.38% |
| LONG | 0.80+ | 53 | +0.42% | 0.66 | +0.17% |
| SHORT | 0.62–0.70 | 199 | +0.34% | 0.50 | +0.09% |
| SHORT | 0.70–0.80 | 540 | +0.62% | 0.56 | +0.37% |
| SHORT | 0.80+ | 16 | +2.62% | 0.56 | +2.37% |

- **LONG:** conf ≥0.75 did 1.53% WORSE than conf <0.70 (MC shuffle p=0.015). The bulk band (0.70–0.80) is −2.13% and negative in BOTH halves (H1 −0.86% / H2 −3.39%), diversified (top coin only 20/609), and −2.22% below a matched-coin random-time null (p=1.000, i.e. significantly worse than random timing). The AI longs coins at exactly the wrong moments.
- **SHORT:** monotone in confidence. Conf ≥0.70 shorts: +0.68% obs vs +0.35% matched null → **+0.33% excess, p=0.069** (marginal); positive in both halves (H1 +1.01 / H2 +0.35), diversified.
- **PASS counterfactual:** PASSed coins as longs = −0.59% @24h (n=2,874) vs executed longs −0.87% (n=343); diff p=0.49. The PASS veto is NOT the leak in this window (revises the old entry-latency prior for this tape).

## 2. Gate counterfactuals @24h gross (execute-stage blocks, side KNOWN)

Baseline EXECUTED: n=488, −0.35%. Ambient all-coin 24h long drift over the span: −0.30% (down-tape).

| gate | n | blocked mean 24h | p (MC vs exec) | verdict |
|---|---|---|---|---|
| shadow_mode | 29 | −6.00% | 0.000 | SAVES hugely |
| counter_regime_conf | 127 | −3.71% | 0.000 | SAVES (both halves ~−3.5%) |
| giveback | 7 | −3.32% | 0.209 | saves (tiny n) |
| reentry_cap | 24 | −1.79% | 0.275 | saves |
| trend_filter | 163 | −1.68% | 0.029 | SAVES |
| max_positions | 169 | −1.30% | 0.103 | saves |
| runner_gate | 213 | −1.08% | 0.178 | saves (H1 −1.63 / H2 −0.52) |
| cooldown | 38 | −0.75% | 0.705 | neutral-saves |
| confidence_floor | 78 | −0.44% | 0.913 | neutral |
| margin | 365 | −0.34% | 0.994 | neutral (blocked ≈ executed) |
| volume_floor | 32 | −0.18% | 0.896 | neutral |
| killswitch | 29 | +1.44% | 0.156 | destroys? (weak n) |
| **thin_short_floor** | **314** | **+1.12%** | **0.001** | **DESTROYS — blocked +EV shorts** |

- **thin_short_floor ("short on thin market") is the one gate provably burning EV**: blocked AI shorts made +1.12% @24h gross, positive in both halves (+1.91 / +0.34), diversified (top coin 12/314), and **+0.69% above the matched-coin random-time short null (p=0.019)**. Net of 25bps still +0.87%.
- **runner_gate prior REVISED:** the old finding (runner gate vetoes breakouts that run) does not hold in this window — blocked candidates lost −1.08%, negative in both halves. Down-tape regime matters.
- Preflight gates (no side logged, long-assumed): loss_cooldown −4.0% (n=24), reentry_cap −6.1% (n=17), notional_room_full −1.36% (n=43) — the churn gates save if the would-be trades were longs; interpret with the direction caveat.

## 3. Execution quality

- **Entry slippage vs signal price** (n=717 matched research→fill pairs, ≤15 min): mean −11.2 bps, median 0.0 bps (negative = filled BETTER than signal). Slippage is NOT a leak at current size; large negative hours (03/05/00 UTC, −65…−75 bps) are price drift between verdict and fill, not cost.
- **Fee-viability by holding time** (n=679 paired round-trips; real measured fee ≈ 5 bps spot per round-trip):

| hold | n | gross | net (real fee) | win | gross−12bps | gross−25bps |
|---|---|---|---|---|---|---|
| 0–15m | 90 | −0.67% | −0.72% | 0.38 | −0.79% | −0.92% |
| 15–60m | 187 | −0.12% | −0.17% | 0.57 | −0.24% | −0.37% |
| 60–180m | 182 | +0.01% | −0.04% | 0.52 | −0.11% | −0.24% |
| 180–360m | 158 | +0.13% | +0.08% | 0.51 | +0.01% | −0.12% |
| 360–1440m | 45 | +0.72% | +0.67% | 0.49 | +0.60% | +0.47% |
| >1440m | 17 | +0.35% | +0.30% | 0.71 | +0.23% | +0.10% |

  Only holds ≥6h are clearly fee-viable; sub-1h holds are the churn tax (−0.67% gross at <15m is mostly instant stop-outs — adverse selection, not fees). Any edge realized via <1h holds needs >37 bps expected move just to break even at 25 bps assumed cost.
- Time-of-day PnL (worst 19/13/06 UTC ≈ −0.75%, best 11/21/08 UTC): n per hour is small (18–53); treat as noise, no action.

## VERDICT

- **LONG-verdict anti-calibration: ROBUST (negative).** Mid-conf AI longs are −EV in both halves and worse than random timing (the number: −2.13% @24h, n=609, p=0.015 inverted-calibration MC).
- **thin_short_floor destroys EV: ROBUST +EV counterfactual** (+0.69% excess vs matched null, p=0.019, n=314, both halves positive). Regime caveat: measured on a net-down tape; survivorship makes the short side an UPPER bound only via missing delisted coins (which would likely have HELPED shorts, so bias is conservative here — but the universe is today's listed set).
- Everything else: gates mostly save; execution/slippage clean; churn tax is exit-adverse-selection not fees.
- Caveats: 6h-dedup episodes still overlap inside 24h windows → effective n is lower than shown, p-values slightly optimistic; span is one 6-week regime.

## Shadow-wire spec (the ONE)

`thin_short_relax` shadow book: when an execute is blocked with "short on thin market" AND the triggering research verdict has conf ≥ 0.70, append a shadow entry (next-bar fill) to `shadow_ledger/thin_short_relax.jsonl`; grade at 24h via `scripts/shadow_status.py`. Promotion bar: ≥30 forward shadow entries, +EV net 25bps, AND a stop-width sweep {8/15/20/25/40%} before any live flip (per the over-refute lesson). Expected: ~+0.87%/trade net@25bps if the 6-week counterfactual holds forward.
