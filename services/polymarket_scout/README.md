# Polymarket Judgment Scout (shadow)

Point the LLM's information-synthesis edge at markets priced by **judgment, not
speed**. This is the deliberate opposite of the gabagool pair-arb, which is a
latency race we measured as **0/60 tradeable on resting books** (see
`research/alpha_swarm/findings/W-Z1_polymarket.md`). Here, being *right* beats
being *fast*.

**Zero trading, zero capital.** It reads public Polymarket data (keyless), asks
the LLM for a YES probability, records the divergence as a PAPER trade filled at
the **touch** (the ask, never the mid), and grades on the market's actual
resolution. Capital only after the gate below clears.

## Pipeline

```
open_markets()  →  is_judgment_market()  →  LLM forecast (opus + web search)
   →  signed_edge vs market mid  →  decide_side (|edge| ≥ threshold)
   →  paper-fill at the touch  →  ledger.record()      [.state/polymarket_scout/signals.jsonl]
                                        ⋯ market resolves ⋯
   →  ledger.grade(resolver)  →  paper EV + Brier(LLM) vs Brier(market)
```

- **Market filter** (`is_judgment_market`): order book on, live, liquid
  (≥ $1k), resolves in **3–21 days**, mid-priced (0.10–0.90 — no edge in a
  near-settled 0.02/0.98), and **not** a crypto up/down latency market.
- **Edge**: `llm_yes − market_yes`. Trade only when `|edge| ≥ 0.12`.
- **Fill realism**: paper-filled at the best **ask** (what you'd actually pay),
  net of a conservative ~1%/fill fee proxy both ways.
- **The verdict that matters**: `brier_llm < brier_mkt`. If the LLM is not better
  calibrated than the market's own price, there is no edge — kill it.

## Run

```bash
python -m services.polymarket_scout.run --dry --limit 12   # funnel only, no LLM
python -m services.polymarket_scout.run --limit 10         # live LLM (opus + search)
```

Grade later from a resolver that maps market_id → (YES won?):
```python
from services.polymarket_scout import ledger
print(ledger.grade(my_resolver))
```

## Go-live gate (pre-registered — W-Z1)

No capital until ALL hold on the shadow ledger:
1. **n ≥ 150** resolved paper trades.
2. **mean paper PnL ≥ +3%/position** at the touch (not the mid).
3. **p < 0.05** vs a matched-null (random side / random divergence-selected).
4. **brier_llm < brier_mkt** — the LLM beats the market's price.
5. **Monotonic**: bigger divergence → bigger realized edge.
6. **Dispute-robust**: edge survives dropping UMA-disputed resolutions.
7. **Access solved**: US order placement is geoblocked on the international CLOB
   — live needs Polymarket US (QCX, CFTC DCM) + operator KYC + an Ed25519 key.

## Known refinements (v1 is a validated core, not final)

- **Novelty/perpetual markets** ("… before GTA VI?") pass the date gate but
  resolve on a moving target — add a category/keyword exclusion. The live dry
  run correctly surfaced the real targets (MI-13 primary markets) alongside
  this noise.
- **Category weighting**: W-Z1 found the soft money in geopolitics/world/culture;
  down-weight sports and headline superforecaster markets.
- **Book-mid vs Gamma price**: the filter uses the Gamma outcomePrice; the
  paper fill uses the live CLOB ask. Consider mid-from-book for the edge calc too.
```
