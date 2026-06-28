# regime_btc_crowd — BTC regime + crowdedness regime + BTC influence

1) BTC up/down (20d) -> forward equal-weight market return: regime SEPARATES forward returns (info), but
   CONTRARIAN this tape: BTC-UP fwd5d -1.77% vs BTC-DOWN -0.58% (market reverted every push above trend).
   ⚠️ DIRECTION IS SAMPLE-DEPENDENT (single -44% down tape); in a bull, BTC-up -> trend-up. So the
   regime->return relationship FLIPS sign bear vs bull => use regime as a GATE on validated setups, not a
   standalone directional rule. Explains regime_basket REFUTED + why crash_continue/rally GATE on regime.
2) Crowdedness (aggregate-funding z, market-wide): crowded-LONG (z>=1.5) fwd3d -0.91%; crowded-SHORT
   (z<=-1.5) fwd3d +5.73%/100% win. RIGHT contrarian direction (matches premium_fade) but n=6/n=4 = NOT
   validated. Trade it per-coin (premium_fade n=150 robust), not as a market-timing aggregate.
3) BTC influence: median alt beta 1.36, median R^2 0.36 -> ~36% of an alt's daily variance IS BTC
   (range 0.22-2.0x). BTC dominates the cross-section -> all-long alt book = leveraged BTC bet
   (concentration risk); quantitative case for BTC-residual / market-neutral. See [[project_edge_profile]].
