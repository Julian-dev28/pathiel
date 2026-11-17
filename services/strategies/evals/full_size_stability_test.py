"""Full-size stability eval (2026-11-17): old (buggy) vs new (fixed) "full
size" for xyz:BRENTOIL, replayed over the oil desk-tick log.

BUG (docs/overhaul/... see the fix commit): desk_brief's "Your size" / the
oil desk-tick agent's same-side top-up decision sized a FRESH entry off
`services.risk.size.size_trade(total_account_equity * desk_share, ...)`.
Account equity (Hyperliquid's clearinghouseState accountValue) includes
every open position's own unrealized P&L, so while the oil desk held its
14.79 xyz:BRENTOIL long (opened 2026-11-17T01:53Z at 89.4062), "full size"
chased that SAME position's own mark-to-market swings instead of staying a
stable function of the desk's underlying capital. Observed live: 13.59,
19.62, 7.71 at different ticks on 2026-11-16/17, with the thesis's entry/
stop unchanged between most of them.

Fix: `services.risk.size.equity_excluding_position_pnl` backs the held
position's own unrealized P&L out of total equity BEFORE applying the
desk's equity share, so "full size" sizes from the same basis
`pathiel.agents.bracket.place_bracket` would use for a FRESH entry on this
desk, whether or not the desk currently holds the position.

This script replays the REAL (mid, position, stop) sequence for
xyz:BRENTOIL from /tmp/oil-loop/progress.log (one line per desk tick,
read-only) across the window the 14.79 long was open (2026-11-17T01:53Z
onward), computes the implied unrealized P&L at each tick from the real
mid and the real entry/size HL would report (uPnL = (mid - entry) * size,
Hyperliquid's own linear-perp formula), and reports OLD vs NEW full size
side by side.

CANNOT reproduce the exact 13.59 / 19.62 / 7.71 figures: the log carries
only each tick's one-line summary (mid, position, action), never the full
desk_brief JSON "Your size" payload the operator read those three numbers
from, and the live risk profile (risk_per_trade_pct, desk_equity_share)
lives in ~/.pathiel/.agent-config.json, which this fix must not open. This
eval instead takes the account's "capital aside from this position"
(`--base-equity-usd`) as a parameter -- the mechanism it proves (full size
riding the position's own uPnL, and the fix removing that) does not depend
on getting that one number exactly right, and the CLI default is in the
right order of magnitude for the account the bug report describes (uPnL
~+$24, margin used ~$91 on a 20x, 14.79 @ 89.4062 position at the 16:20Z
tick this script also replays).

    python -m services.strategies.evals.full_size_stability_test \\
        [--log /tmp/oil-loop/progress.log] [--base-equity-usd 70.0]
        [--desk-share 0.90] [--leverage 20] [--out-dir /tmp/full-size-stability]

Writes old_vs_new.csv and report.json under --out-dir. Offline: no network,
no account read, no order ever placed. Pure replay of a local log file.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence

from contracts.risk.models import RiskProfile
from services.risk.derive import VenueRules
from services.risk.size import equity_excluding_position_pnl, size_trade

COIN = "xyz:BRENTOIL"
# The long place_bracket filled 2026-11-17T01:53Z, 5.36R, stop 88.64 tp 93.43
# (progress.log line: "action=PLACED long market at 24h low, 5.36R, stop
# 88.64 tp 93.43"). ENTRY_PX is the real fill price every later tick's
# "pos=14.79@89.4062" repeats; SIZE is the real held size throughout the
# window this script replays.
ENTRY_PX = 89.4062
SIZE = 14.79
# The stop the position carried until the operator raised it 2026-11-17T16:15Z
# ("action=STOP-RAISED 88.64->90.50 on operator yes; covers 14.79, TP 93.43
# unchanged"). A tick's "full size" (a hypothetical FRESH entry right now)
# sizes off whatever stop is in force at that tick.
STOP_BEFORE_RAISE = 88.64
STOP_AFTER_RAISE = 90.50
STOP_RAISE_AT = "2026-11-17T16:15Z"

# Reconstructed operator profile: 20x leverage, risk caps "widened to 100" --
# contracts/risk/models.py's own CONTRACT_VERSION note ("1.1.0 (2026-10-23):
# risk/daily/bump caps widened to 100, operator opts out of risk caps") and
# pathiel/agents/bracket.py's DEFAULT_MAX_STOP_FRAC_OF_LIQ docstring (20x,
# "operator's record") are both committed source, not the live config file.
# With risk_per_trade_pct=100 the leverage cap is always what actually binds
# (target_notional dwarfs leverage x equity at any real stop_pct), so size
# is a clean linear function of equity -- the self-reference this eval is
# about, isolated from any stop-distance noise.
PROFILE = RiskProfile(preset="custom", risk_per_trade_pct=100.0, daily_loss_limit_pct=100.0,
                      max_leverage=20.0, allow_min_order_bump=True, max_bumped_risk_pct=100.0)
VENUE = VenueRules(min_order_notional_usd=10.5)
COIN_MAX_LEVERAGE = 20.0
SZ_DECIMALS = 2

_LINE_RE = re.compile(
    r"DESK-TICK oil (?P<ts>\S+) coin=xyz:BRENTOIL mid=(?P<mid>[\d.]+) pos=(?P<pos>\S+)")
_POS_RE = re.compile(r"^(?P<size>-?[\d.]+)(?:@(?P<entry>[\d.]+))?")


@dataclass(frozen=True)
class Tick:
    ts: str
    mid: float
    size: float             # signed; 0.0 when flat
    entry_px: Optional[float]   # None when the line carried no entry (e.g. "pos=X long")


def parse_log_line(line: str) -> Optional[Tick]:
    """One DESK-TICK oil line for xyz:BRENTOIL -> a Tick, or None (a line for
    another coin, another desk, or a SKIP/NONE line with no position field).
    Tolerant of the log's few position spellings: "pos=flat", "pos=S@E",
    "pos=S long"/"pos=S short" (no entry on the line)."""
    m = _LINE_RE.search(line)
    if not m:
        return None
    pos = m.group("pos")
    if pos == "flat":
        return Tick(ts=m.group("ts"), mid=float(m.group("mid")), size=0.0, entry_px=None)
    pm = _POS_RE.match(pos)
    if not pm:
        return None
    try:
        size = float(pm.group("size"))
    except ValueError:
        return None
    entry_raw = pm.group("entry")
    entry_px: Optional[float] = None
    if entry_raw is not None:
        try:
            entry_px = float(entry_raw)
        except ValueError:
            entry_px = None
    return Tick(ts=m.group("ts"), mid=float(m.group("mid")), size=size, entry_px=entry_px)


def load_ticks(log_path: str, *, coin: str = COIN) -> List[Tick]:
    """Every parseable xyz:BRENTOIL oil tick in `log_path`, in file order.
    Missing file or unreadable line: skipped, never raises (a read-only
    replay of an operational log must degrade, not crash)."""
    out: List[Tick] = []
    path = Path(log_path)
    if not path.is_file():
        return out
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if "coin=xyz:BRENTOIL" not in line or "DESK-TICK oil" not in line:
                continue
            tick = parse_log_line(line)
            if tick is not None:
                out.append(tick)
    return out


def implied_unrealized_pnl(mid: float, entry_px: float, size: float) -> float:
    """Hyperliquid's own linear-perp formula: (mark - entry) x signed size.
    0.0 when flat (size == 0) regardless of entry/mid."""
    if size == 0:
        return 0.0
    return (mid - entry_px) * size


def stop_in_force(ts: str) -> float:
    """The stop a fresh entry sizes against at `ts`: STOP_BEFORE_RAISE until
    the operator's 2026-11-17T16:15Z raise, STOP_AFTER_RAISE from then on."""
    return STOP_AFTER_RAISE if ts >= STOP_RAISE_AT else STOP_BEFORE_RAISE


@dataclass(frozen=True)
class Row:
    ts: str
    mid: float
    held_size: float
    implied_upnl_usd: float
    old_equity_usd: float          # base + this position's own uPnL (the bug)
    new_equity_usd: float          # base alone (the fix)
    old_full_size: Optional[float]
    new_full_size: Optional[float]
    old_topup_room: Optional[float]
    new_topup_room: Optional[float]


def replay(ticks: Sequence[Tick], *, base_equity_usd: float, desk_share: float,
          leverage: float = 20.0) -> List[Row]:
    """OLD (buggy) vs NEW (fixed) full size at every tick. OLD sizes off
    `base_equity_usd + implied uPnL` (what desk_brief/_thesis_sizing read
    before the fix: total account equity, which already contains this
    position's own floating P&L). NEW backs that same P&L back out first
    (services.risk.size.equity_excluding_position_pnl) -- the fix. Both feed
    the SAME desk_share, leverage, stop (stop_in_force) and
    services.risk.size.size_trade place_bracket itself uses, so "full size"
    here is exactly what the brief would have shown."""
    rows: List[Row] = []
    last_entry = ENTRY_PX
    for t in ticks:
        entry_px = t.entry_px if t.entry_px is not None else last_entry
        if t.entry_px is not None:
            last_entry = t.entry_px
        upnl = implied_unrealized_pnl(t.mid, entry_px, t.size)
        old_equity = base_equity_usd + upnl
        new_equity = equity_excluding_position_pnl(old_equity, upnl)   # == base_equity_usd
        stop_px = stop_in_force(t.ts)
        old = size_trade(max(0.0, old_equity) * desk_share, PROFILE, [], entry_px=t.mid, stop_px=stop_px,
                         leverage=leverage, coin_max_leverage=COIN_MAX_LEVERAGE, venue=VENUE,
                         sz_decimals=SZ_DECIMALS)
        new = size_trade(max(0.0, new_equity) * desk_share, PROFILE, [], entry_px=t.mid, stop_px=stop_px,
                         leverage=leverage, coin_max_leverage=COIN_MAX_LEVERAGE, venue=VENUE,
                         sz_decimals=SZ_DECIMALS)
        rows.append(Row(
            ts=t.ts, mid=t.mid, held_size=t.size, implied_upnl_usd=round(upnl, 2),
            old_equity_usd=round(old_equity, 2), new_equity_usd=round(new_equity, 2),
            old_full_size=old.size, new_full_size=new.size,
            old_topup_room=None if old.size is None else round(max(0.0, old.size - abs(t.size)), 6),
            new_topup_room=None if new.size is None else round(max(0.0, new.size - abs(t.size)), 6),
        ))
    return rows


def _swing(values: Sequence[Optional[float]]) -> float:
    nums = [v for v in values if v is not None]
    return (max(nums) - min(nums)) if nums else 0.0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--log", default="/tmp/oil-loop/progress.log")
    ap.add_argument("--base-equity-usd", type=float, default=70.0)
    ap.add_argument("--desk-share", type=float, default=0.90)
    ap.add_argument("--leverage", type=float, default=20.0)
    ap.add_argument("--out-dir", default="/tmp/full-size-stability")
    ap.add_argument("--from-ts", default="2026-11-17T01:53Z",
                    help="first tick to replay (the 14.79 long's fill)")
    ap.add_argument("--to-ts", default="2026-11-17T16:26Z",
                    help="last tick to replay (the bug report's 16:20Z tick plus one)")
    args = ap.parse_args()

    ticks = load_ticks(args.log)
    # Only the window the 14.79 xyz:BRENTOIL long opened 2026-11-17T01:53Z
    # was open: the log's oil coin carried other, unrelated positions on
    # other dates (a different size, a different entry), and replaying
    # those too would mix two positions' uPnL into one swing number.
    ticks = [t for t in ticks if t.size != 0 and args.from_ts <= t.ts <= args.to_ts]
    rows = replay(ticks, base_equity_usd=args.base_equity_usd, desk_share=args.desk_share,
                 leverage=args.leverage)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "old_vs_new.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["ts", "mid", "held_size", "implied_upnl_usd", "old_equity_usd", "new_equity_usd",
                   "old_full_size", "new_full_size", "old_topup_room", "new_topup_room"])
        for r in rows:
            w.writerow([r.ts, r.mid, r.held_size, r.implied_upnl_usd, r.old_equity_usd, r.new_equity_usd,
                       r.old_full_size, r.new_full_size, r.old_topup_room, r.new_topup_room])

    old_swing = _swing([r.old_full_size for r in rows])
    new_swing = _swing([r.new_full_size for r in rows])
    report = {
        "ticks_replayed": len(rows),
        "base_equity_usd": args.base_equity_usd,
        "desk_share": args.desk_share,
        "leverage": args.leverage,
        "old_full_size_swing": round(old_swing, 4),
        "new_full_size_swing": round(new_swing, 4),
        "old_full_size_min_max": [min((r.old_full_size for r in rows), default=None),
                                  max((r.old_full_size for r in rows), default=None)],
        "new_full_size_min_max": [min((r.new_full_size for r in rows), default=None),
                                  max((r.new_full_size for r in rows), default=None)],
        "verdict": ("fixed: full size stays flat across the position's own uPnL swings"
                   if new_swing < old_swing * 0.1 else "full size still swings; investigate"),
    }
    (out_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"replayed {len(rows)} ticks from {args.log}")
    print(f"  old full size: {report['old_full_size_min_max']} (swing {old_swing:.2f})")
    print(f"  new full size: {report['new_full_size_min_max']} (swing {new_swing:.2f})")
    print(f"  {report['verdict']}")
    print(f"  csv: {csv_path}")
    print(f"  report: {out_dir / 'report.json'}")


if __name__ == "__main__":
    main()
