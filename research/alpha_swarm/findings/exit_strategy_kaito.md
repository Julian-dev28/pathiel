# exit_strategy_kaito — exit-policy test for KAITO-like momentum-breakout LONGS

Trigger for the study: KAITO long closed by DSL `floor_breach` at +4.16% spot (+20.6% ROE 5x).
Question: was the tight profit-floor optimal, or does a looser/different exit bank more?

Method: 5m candles, breakout-long entries (new 4h high + >=1.2% burst + volume confirm, not
already +20% extended), fill next-bar open, walk forward 12h, apply each exit intrabar, net 6bps,
OOS both halves. n=296 entries, mean MFE 4.16% (matches KAITO's move).

Result (net mean % / trade):
  floor gb=.10 (≈LIVE)  +0.231  win .74   <-- BEST, dominates
  hold 12h              +0.082
  tp +3/5/8%            -0.02..-0.03
  floor gb=.35/.50/.65  -0.09..-0.25
  atr 1.5/2.5/4x        -0.22..-0.25
  scaleout 50%+trail    -0.229

VERDICT: the tight profit-floor (live floor_breach) is the single best exit and it is not close;
every looser variant is -EV. Momentum breakouts pop then mean-revert fast, so banking quickly is
correct — loosening the floor / trailing wider / holding gives the gain back and goes negative.
The KAITO floor_breach exit was optimal. DO NOT loosen exits for breakout longs.

Caveats: edge is thin (+0.23%/trade net 6bps), OOS h2 slightly negative (scalp-like, regime-dependent
per [[project_exit_config_is_the_lever]]); the breakout-long ENTRY class is only marginally +EV — the
tight exit does the work. 5m survivor universe ~3wk = upper bound.
