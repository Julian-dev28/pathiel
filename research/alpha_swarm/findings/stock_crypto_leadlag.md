# stock_crypto_leadlag — do HIP-3 equities (SP500/SKHX/xyz) LEAD crypto?

Tested xyz:SP500, xyz:SKHX, xyz:NVDA, xyz:XYZ100, xyz:MSTR vs BTC/ETH/SOL at 1h and 5m,
equity-active bars only (drop stale off-hours). contemp = same-bar corr; lead = X[i] vs Y[i+1].

VERDICT: REFUTED — strong CONTEMPORANEOUS co-move, ZERO tradeable lead either direction.
- contemp: SP500 +0.48, XYZ100 +0.50, MSTR +0.70-0.79 (BTC proxy), NVDA +0.39, SKHX +0.33 (weakest).
- lead-lag (1h & 5m): eq->crypto+1 and crypto->eq+1 both +0.02..+0.06, SYMMETRIC = just the same-bar
  correlation bleeding into the adjacent bar, not a direction. Too small to clear fees regardless.
- SP500 does NOT lead crypto; SKHX does NOT lead crypto; no xyz/HIP-3 market leads crypto.
Mechanism: both are risk assets reacting to the same macro simultaneously; HIP-3 equities trade 24/7
so off-hours they're moved BY crypto sentiment (no independent lead), and US-hours stock moves are
already public by bar-close. Matches [[project_alpha_swarm_2026_06_27]] btc_leadlag (contemporaneous, not laggable).
