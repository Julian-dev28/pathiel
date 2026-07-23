# W-SOC1 — coverage-surge LONG: ride the attention instead of fading it

**Question (operator, 2026-07-23):** we wired news/coverage surges only as SHORTS and
they bled on the semis. Does the LONG side — go WITH the attention spike — have an edge?
Is social/attention the frontier now that candle-space is dead (Markov memoryless W-MC1,
price-entries no-EV)?

**Method (`hypotheses/W-SOC1_social_long.py`):** untainted (signal_bar_t >= 2026-07-12;
pre-07-12 news ledger is model-faked) surge_x >= 2.0 events from `news_catalyst.jsonl`
(crypto recorder, 07-13..07-18) and `news_surge_short.jsonl` (equity+crypto, 07-20..07-22).
287 events, 55 coins, deduped per coin-hour. Entry = first 1h bar open >= the record `ts`
(lookahead-safe), forward +4h/+24h/+48h open->close, fees 0/25/50bps. Both sides scored
per event (LONG +r-fee, SHORT -r-fee). Matched same-coin random-time null (2000) on LONG
net25 at +24h. OOS = time-halves. 1h candles fetched once from HL (cached, gentle).

## Results (net25, +24h primary)

| cell | n | raw fwd24 | LONG net25 | SHORT net25 | halves (LONG) | null p (LONG) | verdict |
|---|---|---|---|---|---|---|---|
| ALL | 254 | −3.82% | −4.07% | +3.57% | −4.91 / −3.22 | 0.0005 | REFUTED |
| CRYPTO | 219 | −4.55% | −4.80% | **+4.30%** | −4.52 / −5.08 | 0.0005 | REFUTED (long) |
| EQUITY (xyz) | 35 | +0.79% | +0.54% | −1.04% | +0.88 / +0.22 | 0.17 | REFUTED |
| BREAKING | 26 | −2.76% | −3.01% | +2.51% | −5.28 / −0.75 | 0.0065 | REFUTED |

## Reading

1. **LONG coverage-surge is REFUTED — attention is not a long signal.** Every cell's
   LONG net25 is <= 0 and the ALL/CRYPTO cells lose to the random-time null (p=0.0005).
   Buying the attention spike loses.

2. **The mechanism is directional and OPPOSITE by asset class:**
   - **Crypto surge → dump.** raw fwd24 −4.55%; the coverage spike marks a LOCAL TOP —
     news as exit liquidity. SHORT earns +4.30% net25, stable in BOTH halves, beats its
     null (the LONG p=0.0005 is a symmetric short edge). This is a real, currently-untraded
     signal on the `news_catalyst` recorder window.
   - **Equity (xyz semis) surge → continue up.** raw fwd24 +0.79%; the pop persists.
     SHORT loses −1.04%, LONG is right-signed +0.54% both halves — but n=35, one tape
     (the semis rally itself), null p=0.17. Directionally consistent with the momentum/
     dispersion story (W-X2), NOT statistically established.

3. **Independent confirmation of the 2026-07-23 semis-short kill.** The books shadowed
   this session shorted xyz-equity surges; this cell measures equity surge-short at −1.04%
   net25 — the kill removed a losing side. Symmetric confirmation the SHORT belongs on
   crypto surges, not equity ones.

## VERDICT: **REFUTED** (the LONG hypothesis) — attention-spike-LONG has no edge; crypto
surges are exit liquidity, equity surges are momentum but under-powered (n, one tape).

**Byproduct discovery (NOT a promotion):** crypto coverage-surge SHORT (+4.30% net25
fwd24, both halves, beats null) is worth a forward recorder → bounded live test under the
standing rules — BUT it rests on a single 6-day recorder tape (07-13..07-18), so it is a
CANDIDATE, not a wire. The asymmetry (short crypto surges / do-not-short equity surges) is
the tradeable shape, if it survives more tape. Sector must gate the sign.

Caveats: ~10-day total span, one tape, survivor-biased live universe, funding unmodelled,
24/7 perp bars. Artifacts: `hypotheses/W-SOC1_results.json`, cache `scratchpad/W-SOC1_1h.json`.
