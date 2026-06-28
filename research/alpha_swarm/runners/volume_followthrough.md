# volume_followthrough — operator's eye: 3x breakout candle + 1.5x next-candle CONFIRM -> run

Tested on 5m, 180 small-cap movers. A breakout candle (new 4h high, >=Bx vol, green) split by whether
the NEXT candle holds >=Cx volume (follow-through CONFIRMED) vs vol-dies (UNCONFIRMED). Enter at close of
confirm candle -> next bar open (lookahead-safe), tight-floor exit, net 12bps.

RESULT — the 2nd-candle volume confirm is a REAL quality filter:
  3x+1.5x (operator's exact): CONFIRMED run>=20% 0.5% / EV -0.10%  vs  UNCONFIRMED 0.1% / -0.19%.
  Holds across thresholds: CONFIRMED runs >=20% at ~5x the UNCONFIRMED rate, ~2x better EV.
  => validates "a one-bar spike = pump-and-dump (reverts); a spike CONFIRMED by a 2nd elevated-vol candle
     = real move." The follow-through separates runners from fakes.

BUT not a standalone +EV trigger: even best CONFIRMED is -0.10% net (5m breakouts fizzle too often; >=50%
runs ~0% in the 17d window — MANTA-scale is rare). It's an ENTRY-QUALITY FILTER (avoid unconfirmed pumps),
not a money-printer. Aligns with the live `override_volume_confirm` (1.2x) gate -> a stronger persist-the-volume
version is a legit upgrade. Pairs with the momentum-persistence runner equation (both = "which breakout to take").
