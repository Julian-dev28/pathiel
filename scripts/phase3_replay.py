#!/usr/bin/env python3
"""Phase-3 no-lookahead post-mortem replay (READ-ONLY — no orders, no writes).

Drives the REAL exit engine (hermes_trader.agents.dsl_exit.DSLTracker.check) against
REAL historical 15m OHLCV from HL candleSnapshot. No indicator peeks at future bars:
each bar is processed in time order, peak updated from the bar HIGH then the stop/
trailing floor tested against the bar LOW (the within-bar worst case for a long) then
the close — exactly how the 60s live loop would see the path intra-bar.

Per token answers: (a) never-entered / (b) entered-but-whipsawed-out / (c) rode it,
with the intra-run drawdown map, any whipsaw candle+price+rule, a liquidation check,
and % of the move KEPT after fees + funding at the stated leverage.

Usage: python3 scripts/phase3_replay.py GRASS 0.47055 3 7.27   # coin entry lev spot_move%
       (spot_move% optional, just annotates the target)
"""
import sys, time, json
from hermes_trader.client.hl_client import fetch_hl_candles  # paced via the shared limiter
from hermes_trader.agents.dsl_exit import DSLTracker, ExitPolicy, RetraceTier
from hermes_trader.agents.config_store import read_agent_config

INTERVAL = "1m"   # match the ~60s live loop cadence: one mark per tick, true time order
BARS = 1500       # ~25h of 1m (covers a 24h run + lead-in)


def _atr_pct(candles, idx, period=14):
    """ATR% of price at bar idx using ONLY bars <= idx (no lookahead)."""
    lo = max(1, idx - period)
    trs = []
    for j in range(lo, idx + 1):
        h, l, pc = candles[j].h, candles[j].l, candles[j - 1].c
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    atr = sum(trs) / len(trs) if trs else 0.0
    return (atr / candles[idx].c * 100) if candles[idx].c > 0 else 0.0


_atr4h_cache = {}
def entry_atr_pct_4h(coin, entry_ts_ms, period=14):
    """4h ATR% at the entry time — MATCHES the live system's
    get_hl_atr('4h',14) that DSLTracker.entry_atr_pct is captured from. Using 1m
    ATR (≈8x tighter) for the noise band would make Patch A inert by accident."""
    key = coin
    c4 = _atr4h_cache.get(key)
    if c4 is None:
        c4 = fetch_hl_candles(coin, "4h", 120) or []
        _atr4h_cache[key] = c4
    if len(c4) < period + 2:
        return 0.0
    # last 4h bar at/before entry (no lookahead)
    idx = 0
    for i, c in enumerate(c4):
        if c.t <= entry_ts_ms:
            idx = i
    idx = max(period, idx)
    return _atr_pct(c4, idx, period)


def _policy(cfg, noise_band=False, noise_band_atr_mult=1.0):
    d = cfg.get("dsl_exit", {}) or {}
    tiers = d.get("phase2_tiers")
    tiers = [RetraceTier(**t) for t in tiers] if tiers else None
    return ExitPolicy(
        noise_band_enabled=noise_band,
        noise_band_atr_mult=noise_band_atr_mult,
        max_loss_pct=d.get("max_loss_pct", 1.0),
        max_loss_roe_pct=d.get("max_loss_roe_pct", 8.0),
        protect_pct=d.get("protect_pct", 1.5),
        retrace_threshold=d.get("retrace_threshold", 0.30),
        hard_timeout_minutes=d.get("hard_timeout_minutes", 1800.0),
        breakeven_trigger_pct=d.get("breakeven_trigger_pct", 0.0),
        breakeven_lock_pct=d.get("breakeven_lock_pct", 0.0),
        stale_flat_timeout_minutes=float(d.get("stale_flat_timeout_minutes", 0.0) or 0.0),
        phase2_tiers=tiers if tiers else ExitPolicy().phase2_tiers,
    )


def replay(coin, entry_px, leverage):
    cfg = read_agent_config()
    candles = fetch_hl_candles(coin, INTERVAL, BARS)
    if not candles or len(candles) < 50:
        print(f"  !! no candle data for {coin} ({len(candles) if candles else 0} bars)")
        return None
    # Locate the run PEAK = global high (these are recent named runs, so the
    # window's high IS the run top). Anchor ENTRY at the run BASE: the LAST bar
    # before the peak whose low touches entry_px (the most-recent fill of the
    # given entry, not an old same-price bar days earlier). No lookahead: entry
    # is fixed before we replay any forward bar.
    hi_i = max(range(len(candles)), key=lambda i: candles[i].h)
    entry_i = 0
    for i in range(hi_i, -1, -1):
        if candles[i].l <= entry_px <= candles[i].h or candles[i].l <= entry_px:
            entry_i = i
            break
    run_lo, run_hi = candles[entry_i].l, candles[hi_i].h
    run_pct = (run_hi - run_lo) / run_lo * 100
    eatr = _atr_pct(candles, entry_i)
    pol = _policy(cfg)
    tr = DSLTracker(coin, "long", entry_px, entry_time=candles[entry_i].t / 1000.0,
                    policy=pol, leverage=leverage, entry_atr_pct=eatr)
    # Liquidation (cross, approx): long liq ~ entry*(1 - 1/lev).
    liq_px = entry_px * (1 - 1.0 / max(1, leverage))

    # ── Faithful exit replay ─────────────────────────────────────────────────
    # Trailing/stop engine driven by SEQUENTIAL 1m CLOSES (one mark per tick, in
    # true time order) — this is what the 60s live loop actually sees; it does NOT
    # synthesize an intra-bar high→low ordering (that produced false bar+1
    # whipsaws on 15m data). The hard stop + liquidation are PRICE LEVELS that
    # fire intra-bar server-side, so those we test against each bar's LOW (the
    # wick) separately and report whichever (trailing-on-close vs wick-stop)
    # would fire FIRST in time.
    import hermes_trader.agents.dsl_exit as dmod
    _real_time = time.time
    peak_run = entry_px
    max_dd_pct = 0.0
    dd_events = []
    exit_rec = None
    liq_hit = None
    hard_stop_px = entry_px * (1 - min(pol.max_loss_pct, pol.max_loss_roe_pct / max(1, leverage)) / 100.0)
    prev_dd_logged = 0
    for i in range(entry_i + 1, len(candles)):
        c = candles[i]
        # intra-run drawdown map from running peak (uses highs/lows for the map only)
        peak_run = max(peak_run, c.h)
        dd = (peak_run - c.l) / peak_run * 100
        if dd >= 3.0 and (i - prev_dd_logged) > 4:
            dd_events.append((c.t, round(dd, 2), c.l)); prev_dd_logged = i
        max_dd_pct = max(max_dd_pct, dd)
        # wick-level hard-stop + liquidation (server-side, fire intra-bar)
        if liq_hit is None and c.l <= liq_px:
            liq_hit = (c.t, c.l)
        if exit_rec is None and c.l <= hard_stop_px:
            exit_rec = {"ts": c.t, "px": hard_stop_px, "reason": f"hard_stop@{hard_stop_px:.6g} (wick low {c.l:.6g})",
                        "unrl_pct": round((hard_stop_px - entry_px) / entry_px * 100, 2),
                        "bars_after_entry": i - entry_i, "kind": "wick_stop"}
            break
        # trailing engine on the close, in time order
        dmod.time.time = (lambda t=c.t: t / 1000.0)
        try:
            v = tr.check(c.c)
        finally:
            dmod.time.time = _real_time
        if v.exit:
            exit_rec = {"ts": c.t, "px": c.c, "reason": v.reason,
                        "unrl_pct": round(v.unrealized_pct, 2),
                        "bars_after_entry": i - entry_i, "kind": "trailing"}
            break

    return {
        "coin": coin, "leverage": leverage, "entry_px": entry_px, "entry_atr_pct": round(eatr, 2),
        "run_lo": run_lo, "run_hi": run_hi, "run_pct": round(run_pct, 1),
        "bars": len(candles), "entry_bar": entry_i, "peak_bar": hi_i,
        "stop_spot_pct": round(min(pol.max_loss_pct, pol.max_loss_roe_pct / max(1, leverage)), 2),
        "liq_px": round(liq_px, 6), "liq_hit": liq_hit, "max_dd_pct": round(max_dd_pct, 2),
        "dd_events": dd_events, "exit": exit_rec,
    }


def ablate(coin, entry_px, lev, A, B, cfg, nb_mult=1.0, cooldown_min=30.0, reentry_above_pct=1.0):
    """State-machine replay over the run: first entry at the run base, then exit
    (real engine; A = noise-band on) and RE-ENTRY (B = cooldown + no-buy-above-
    last-exit). 'captured%' = net spot PnL across all legs / max capturable spot
    move from the first entry. No lookahead: each bar uses only prior state."""
    import hermes_trader.agents.dsl_exit as dmod
    candles = fetch_hl_candles(coin, INTERVAL, BARS)
    if not candles or len(candles) < 50:
        return None
    hi_i = max(range(len(candles)), key=lambda i: candles[i].h)
    entry_i = 0
    for i in range(hi_i, -1, -1):
        if candles[i].l <= entry_px:
            entry_i = i; break
    run_move = (candles[hi_i].h - entry_px) / entry_px * 100
    eatr = entry_atr_pct_4h(coin, candles[entry_i].t)  # 4h ATR, matches live
    pol = _policy(cfg, noise_band=A, noise_band_atr_mult=nb_mult)
    hard_frac = min(pol.max_loss_pct, pol.max_loss_roe_pct / max(1, lev)) / 100.0
    FEE = 0.05  # round-trip taker, spot %
    _rt = time.time
    legs = []
    pos = None
    last_exit_px = last_exit_ts = None
    for i in range(entry_i, len(candles)):
        c = candles[i]
        if pos is None:
            if last_exit_px is None:
                do_enter = (i == entry_i)
            else:
                do_enter = True
                if B:
                    if (c.t - last_exit_ts) / 60000.0 < cooldown_min:
                        do_enter = False
                    elif c.c > last_exit_px * (1 + reentry_above_pct / 100.0):
                        do_enter = False
            if do_enter:
                pos = DSLTracker(coin, "long", c.c, entry_time=c.t / 1000.0,
                                 policy=pol, leverage=lev, entry_atr_pct=eatr)
        else:
            hard_px = pos.entry_px * (1 - hard_frac)
            exited = None
            if c.l <= hard_px:
                exited = (hard_px, "hard_stop")
            else:
                dmod.time.time = (lambda t=c.t: t / 1000.0)
                try:
                    v = pos.check(c.c)
                finally:
                    dmod.time.time = _rt
                if v.exit:
                    exited = (c.c, v.reason.split(" ")[0])
            if exited:
                xpx, reason = exited
                spot = (xpx - pos.entry_px) / pos.entry_px * 100 - FEE
                legs.append((round(pos.entry_px, 6), round(xpx, 6), round(spot, 2), reason))
                last_exit_px, last_exit_ts, pos = xpx, c.t, None
    if pos is not None:
        xpx = candles[-1].c
        spot = (xpx - pos.entry_px) / pos.entry_px * 100 - FEE
        legs.append((round(pos.entry_px, 6), round(xpx, 6), round(spot, 2), "open_end"))
    net = sum(l[2] for l in legs)
    return {"net_spot": round(net, 2), "run_move": round(run_move, 2),
            "captured_pct": round(net / run_move * 100, 1) if run_move > 0 else 0.0,
            "n_legs": len(legs), "legs": legs}


def run_ablation(tokens):
    print("\n=== ABLATION: % of run captured (net spot, after fees) ===")
    print(f"{'token':>7} {'lev':>4} {'run%':>7} | {'baseline':>9} {'A only':>8} {'B only':>8} {'A+B':>8}  (legs base→A+B)")
    cfg = read_agent_config()
    for coin, entry, lev in tokens:
        cells = {}
        for name, (A, B) in [("base", (False, False)), ("A", (True, False)),
                              ("B", (False, True)), ("AB", (True, True))]:
            r = ablate(coin, entry, lev, A, B, cfg)
            cells[name] = r
        if not cells["base"]:
            print(f"{coin:>7} no data"); continue
        rm = cells["base"]["run_move"]
        def cap(n): return f"{cells[n]['captured_pct']:>7}%"
        print(f"{coin:>7} {lev:>3}x {rm:>6}% | {cap('base')} {cap('A')} {cap('B')} {cap('AB')}  "
              f"({cells['base']['n_legs']}→{cells['AB']['n_legs']})")


def main():
    if sys.argv[1] == "ablate":
        TOKENS = [("GRASS", 0.47055, 3), ("WLD", 0.59006, 10),
                  ("CHIP", 0.042073, 3), ("ZEC", 490.08, 10)]
        run_ablation(TOKENS)
        return
    coin = sys.argv[1]
    entry_px = float(sys.argv[2])
    lev = int(sys.argv[3])
    r = replay(coin, entry_px, lev)
    if not r:
        return
    print(f"\n=== {coin} {lev}x  entry {entry_px}  (ATR@entry {r['entry_atr_pct']}%) ===")
    print(f"  run: {r['run_lo']:.6g} -> {r['run_hi']:.6g}  (+{r['run_pct']}%)  "
          f"entry_bar {r['entry_bar']} peak_bar {r['peak_bar']} of {r['bars']}")
    print(f"  stop width (spot): {r['stop_spot_pct']}%  | liq~{r['liq_px']}  liq_hit={r['liq_hit']}")
    print(f"  max intra-run drawdown from running peak: {r['max_dd_pct']}%")
    big = sorted(r['dd_events'], key=lambda e: -e[1])[:5]
    for ts, dd, low in big:
        print(f"    pullback -{dd}%  low {low:.6g}  @ {time.strftime('%m-%d %H:%M', time.gmtime(ts/1000))}")
    if r['exit']:
        e = r['exit']
        print(f"  >>> EXIT @ bar+{e['bars_after_entry']} ({time.strftime('%m-%d %H:%M', time.gmtime(e['ts']/1000))}) "
              f"px~{e['px']:.6g}  reason: {e['reason']}  (unrl {e['unrl_pct']}%)")
    else:
        print(f"  >>> NO EXIT in window — rode to peak (or still open at data end)")


if __name__ == "__main__":
    main()
