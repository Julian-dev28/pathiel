#!/usr/bin/env python3
"""Eval harness for the copycat book: real reads, zero orders, ever.

copycat mirrors 10 Hyperliquid leaderboard wallets (pathiel/agents/
copycat_live.py) at real money, UNTESTED (no backtest; W-CP1 already
REFUTED copying one of these same 10 wallets, 0xe282, at every leverage
tested). Before it is ever enabled for real this answers the mechanical
question a backtest can't: does the live code path actually run clean
against our real account, for all 10 leaders, without ever touching the
exchange?

What this does:
  - Reads our REAL equity/positions (read-only clearinghouseState /
    spotClearinghouseState via fetch_account_state) and each leader's REAL
    state via copycat_live.LiveVenue's read methods.
  - Drives `copycat_live.maybe_run` for real, with `DryRunVenue` standing in
    for the exchange: every read is real (inherited unchanged from the
    book's own LiveVenue), every write (set_leverage / market / close) is
    INTERCEPTED, recorded as an intent, and answered with a synthetic
    success. The real exchange write functions (place_hl_order,
    set_leverage, close_position_market) are never imported into this call
    path, let alone invoked.
  - Book state lives in a throwaway temp dir (`--cycles`/`--interval` run
    N cycles so the diff logic actually exercises adds/reduces/closes, not
    just a cold start) — never `.copycat.json`, the live file. NOTE:
    copycat's default `only_new_positions` is False, unlike copy_trade's
    True — so cycle 0 is NOT a quiet baseline here. A leader's entire
    current book is a live candidate to copy from the first cycle, subject
    to every other gate (claims, margin room, worse-entry, min order).
  - `PATHIEL_STATE_DIR` is redirected to that SAME temp dir for the whole
    process, before any `pathiel.agents.*` import, so the cross-book claims
    registry (`.rebalancer_claims.json`, shared with every other live book)
    can never be touched either. A "dry run" that claims a coin in the real
    registry would block a real book from trading that coin — a side
    effect, not a dry run.
  - `copycat.enabled` is forced True IN MEMORY ONLY (a deep copy of the
    config); `.agent-config.json` on disk is never written.

What this is NOT: a backtest, or evidence the rule has edge. It is the
"does the plumbing work" gate copy_trade_live and drawdown_ladder_live never
got before going live, and copycat deserves at least that much.

    .venv/bin/python scripts/copycat_dry_run.py
    .venv/bin/python scripts/copycat_dry_run.py --cycles 3 --interval 20

Exit 0 only if every leader read OK or skipped with a reason, zero
exceptions, and every intended order is >= $10.50 and <= its coin's max
leverage. Writes /tmp/copycat_dry_run/report.json.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _state_env  # noqa: E402

# ── Isolate ALL repo state before the first pathiel.agents import ──────────
# pathiel.agents.rebalancer_owned reads PATHIEL_STATE_DIR at import time and
# every book's state_file() (claims registry, OwnedPositions, this book's own
# .copycat.json) routes through it. Set this BEFORE importing
# pathiel.agents.copycat_live (or anything else under pathiel.agents), or a
# module already imported with the live root baked in would still write
# there. This is a dry run; nothing it does may be visible to a live book.
_STATE_TMP = tempfile.mkdtemp(prefix="copycat_dry_run_state_")
os.environ["PATHIEL_STATE_DIR"] = _STATE_TMP

_state_env.load_env_local(ROOT)

import pathiel.agents.copycat_live as copycat_live  # noqa: E402

GREEN, RED, AMBER, DIM, OFF = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"
REPORT_DIR = "/tmp/copycat_dry_run"
REPORT_PATH = os.path.join(REPORT_DIR, "report.json")
MIN_ORDER_USD = 10.50


def _jsafe(d: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    for k, v in d.items():
        try:
            json.dumps(v)
            out[k] = v
        except TypeError:
            out[k] = str(v)
    return out


class DryRunVenue(copycat_live.LiveVenue):
    """The book's real LiveVenue, with every WRITE method intercepted.

    Every READ method (leader_extras / mid / max_leverage / size_for) is
    inherited UNCHANGED from `copycat_live.LiveVenue` — real HTTP, real
    data, no stub, no mock. `leader_state` is overridden ONLY to observe
    (call the real inherited method, record ok/fail per leader, return its
    result untouched) -- see `leader_state` below for why. `set_leverage` /
    `market` / `close` are overridden to record intent and return a
    synthetic success; none of them import or call
    `pathiel.client.exchange`'s write functions or
    `pathiel.agents.executor.close_position_market`. That is the whole
    contract this class exists to guarantee, and
    tests/test_copycat_dry_run.py pins it with the real write functions
    monkeypatched to raise.
    """

    def __init__(self) -> None:
        super().__init__()
        self.intents: List[Dict[str, Any]] = []
        self.leader_reads: Dict[str, bool] = {}

    def leader_state(self, user: str) -> Optional[Dict[str, Any]]:
        # Pure observer, not a stub: calls the real inherited read and passes
        # its result through UNCHANGED. This is the only way to know "every
        # leader read OK or explicitly skipped with a reason" straight from
        # the source -- maybe_run's own skip dict is keyed by COIN for every
        # gate past the read itself (worse-entry, min-order, claimed), so a
        # leader whose every candidate coin gets skipped on one of those
        # never has its address appear anywhere in maybe_run's own output,
        # even though its read genuinely succeeded.
        result = super().leader_state(user)
        self.leader_reads[user] = result is not None
        return result

    def set_leverage(self, coin: str, leverage: int, is_cross: Optional[bool]) -> Dict[str, Any]:
        # Matches LiveVenue.set_leverage's exact signature (is_cross is
        # positional, no default) so this slots in wherever maybe_run calls
        # it. `is_cross` here is the REQUESTED margin mode; a real isolated-
        # only market would force it False regardless -- a dry run can't see
        # that exchange-side behavior without calling the exchange, so it
        # reports the request as applied and flags it as such.
        self.intents.append({"call": "set_leverage", "coin": coin,
                             "leverage": leverage, "is_cross_requested": is_cross})
        return {"ok": True, "is_cross": is_cross, "dry_run": True}

    def market(self, coin: str, is_buy: bool, size: float, mid: float,
               reduce_only: bool = False, **kwargs: Any) -> Dict[str, Any]:
        notional = round(abs(size) * mid, 2)
        self.intents.append({"call": "market", "coin": coin, "is_buy": is_buy,
                             "size": size, "mid": mid, "notional_usd": notional,
                             "reduce_only": reduce_only, "extra": _jsafe(kwargs)})
        return {"ok": True, "avg_px": mid, "total_sz": size,
                "order_id": "dryrun", "dry_run": True}

    def close(self, coin: str, *args: Any, **kwargs: Any) -> bool:
        self.intents.append({"call": "close", "coin": coin, "extra": _jsafe(kwargs)})
        return True


def _read_real_daily_pnl(root: str) -> Dict[str, Any]:
    """Read-only peek at the live `.agent-memory.json` for today's tracked
    daily PnL, WITHOUT importing `pathiel.agents.memory` (importing that
    module instantiates its singleton, which reads AND can write the live
    file on its own schedule — a risk this script has no reason to take for
    one number). If the file's day-start isn't today (UTC), the real
    tracker would reset on its own next tick, so this reports a fresh 0.0
    rather than a stale day's figure."""
    path = os.path.join(root, ".agent-memory.json")
    if not os.path.exists(path):
        return {"daily_pnl": 0.0, "source": "no .agent-memory.json on disk"}
    try:
        with open(path) as fh:
            mem = json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        return {"daily_pnl": 0.0, "source": f"unreadable, treating as fresh day: {e}"}
    day_start_ts = int(mem.get("dayStartTs") or 0)
    today_start = int(time.time() // 86400) * 86400
    if day_start_ts == today_start:
        return {"daily_pnl": float(mem.get("dailyPnl") or 0.0),
                "source": ".agent-memory.json, today's live tracked value"}
    stale_day = time.strftime("%Y-%m-%d", time.gmtime(day_start_ts)) if day_start_ts else "never"
    return {"daily_pnl": 0.0,
            "source": f".agent-memory.json day-start is stale ({stale_day} UTC, "
                      f"not today) -- reporting a fresh day's 0.0, matching what "
                      f"the real tracker would show on its own next tick"}


def _check_order(coin: str, notional: float, leverage: Optional[int],
                 max_leverage: Optional[int]) -> List[str]:
    problems = []
    if notional < MIN_ORDER_USD - 1e-6:
        problems.append(f"{coin}: order notional ${notional:.2f} < ${MIN_ORDER_USD} minimum")
    if leverage is not None and max_leverage is not None and leverage > max_leverage:
        problems.append(f"{coin}: leverage {leverage}x > coin max {max_leverage}x")
    return problems


def run(cycles: int, interval: float, sizing_mode_override: Optional[str] = None) -> Dict[str, Any]:
    from pathiel.agents.config_store import read_agent_config
    from pathiel.agents.risk_gates import effective_daily_loss_limit
    from pathiel.client.hl_client import fetch_account_state, resolve_user_address

    user = resolve_user_address()
    if not user:
        return {"ok": False, "fatal": "resolve_user_address() returned empty "
                "(.env.local HYPERLIQUID_MASTER_ADDRESS / HYPERLIQUID_WALLET_ADDRESS)"}

    live_cfg = read_agent_config()
    cfg = json.loads(json.dumps(live_cfg))          # deep copy; disk config never touched
    cc_cfg = dict(cfg.get("copycat") or {})
    was_enabled = cc_cfg.get("enabled")
    cc_cfg["enabled"] = True                         # in-memory override only
    if sizing_mode_override:
        cc_cfg["sizing_mode"] = sizing_mode_override  # also in-memory only
    cfg["copycat"] = cc_cfg

    try:
        settings = copycat_live.settings(cfg)
    except Exception as e:
        settings = {"error": f"copycat_live.settings(cfg) raised: {e}", **cc_cfg}

    leaders = settings.get("leaders") if isinstance(settings, dict) else None
    leaders = leaders if isinstance(leaders, list) else cc_cfg.get("leaders") or []

    pnl_info = _read_real_daily_pnl(ROOT)
    daily_pnl = pnl_info["daily_pnl"]

    book_tmp = tempfile.mkdtemp(prefix="copycat_dry_run_book_", dir=_STATE_TMP)
    state_path = os.path.join(book_tmp, ".copycat.json")
    equity_log_path = os.path.join(book_tmp, ".copycat_equity.jsonl")

    venue = DryRunVenue()
    exceptions: List[str] = []
    leader_skips: Dict[str, str] = {}
    cycles_out: List[Dict[str, Any]] = []
    first_dex_available: Optional[Dict[str, float]] = None

    for i in range(cycles):
        try:
            acct = fetch_account_state(user, include_hip3=True)
        except Exception as e:
            exceptions.append(f"cycle {i}: fetch_account_state raised: {type(e).__name__}: {e}")
            break
        equity = float(acct.get("equity") or 0)
        positions = acct.get("asset_positions") or []
        dex_available = dict(acct.get("dex_available") or {"": acct.get("available", 0.0)})
        if first_dex_available is None:
            first_dex_available = dict(dex_available)   # pre-order baseline, for the margin report
        daily_loss_limit = effective_daily_loss_limit(cfg, equity, daily_pnl)

        events: List[Dict[str, Any]] = []
        n_intents_before = len(venue.intents)
        out: Dict[str, Any] = {}
        try:
            out = copycat_live.maybe_run(
                cfg, positions, equity, dex_available, daily_pnl, daily_loss_limit,
                allow_entries=True, now_ms=int(time.time() * 1000), venue=venue,
                log_event=events.append, state_path=state_path,
                equity_log_path=equity_log_path,
            )
        except Exception as e:
            import traceback
            exceptions.append(f"cycle {i}: maybe_run raised: {type(e).__name__}: {e}\n"
                             + traceback.format_exc())

        skipped = out.get("skipped") if isinstance(out, dict) else None
        if isinstance(skipped, dict):
            leader_skips.update({str(k): v for k, v in skipped.items()})

        cycles_out.append({
            "cycle": i, "equity": round(equity, 4), "n_positions": len(positions),
            "dex_available": {k: round(v, 4) for k, v in dex_available.items()},
            "daily_pnl": round(daily_pnl, 4), "daily_loss_limit": round(daily_loss_limit, 4),
            "result": out, "events": events,
            "new_intents": venue.intents[n_intents_before:],
        })
        if exceptions:
            break
        if i < cycles - 1 and interval > 0:
            time.sleep(interval)

    # Effective sizing_mode + leverage per intended order, straight from
    # copycat_live's own log_event payload for "open"/"add" -- it already
    # records exactly this (see copycat_live.py `_add`), so this is read,
    # not re-derived. Liquidation distance at the leverage ACTUALLY used
    # (not the coin's max) uses the same approximation as W-CC1's findings
    # doc: maintenance margin ~= half the initial margin fraction, so the
    # adverse move to liquidate at isolated leverage L is ~= 1/(2L).
    copies_report: List[Dict[str, Any]] = []
    for c in cycles_out:
        for ev in c.get("events", []):
            if ev.get("event") == "copycat" and ev.get("action") in ("open", "add"):
                lev = ev.get("leverage")
                notional = float(ev.get("notional") or 0)
                margin = round(notional / lev, 4) if lev else None
                copies_report.append({
                    "cycle": c["cycle"], "coin": ev.get("coin"), "leader": ev.get("leader"),
                    "action": ev.get("action"), "side": ev.get("side"),
                    "sizing_mode": ev.get("sizing_mode"), "leverage": lev,
                    "is_cross": ev.get("is_cross"), "notional_usd": notional,
                    "margin_usd": margin,
                    "liquidation_distance_pct": round(100.0 / (2 * lev), 3) if lev else None,
                })

    dex_margin_used: Dict[str, float] = {}
    for c in copies_report:
        if c["margin_usd"] is None:
            continue
        coin = c["coin"] or ""
        dex = coin.split(":", 1)[0] if ":" in coin else ""
        dex_margin_used[dex] = dex_margin_used.get(dex, 0.0) + c["margin_usd"]
    margin_summary = {
        "available_before_this_run": {k: round(v, 4) for k, v in (first_dex_available or {}).items()},
        "margin_used_by_intended_opens": {k: round(v, 4) for k, v in dex_margin_used.items()},
        "margin_remaining": {
            dex: round(float((first_dex_available or {}).get(dex, 0.0)) - used, 4)
            for dex, used in dex_margin_used.items()
        },
    }

    # Every intended non-reduce-only market order must clear the exchange
    # floor and respect the coin's max leverage. A reduce-only close can be
    # smaller than the minimum (HL allows shrinking below it), so only
    # entries/adds are checked here.
    order_problems: List[str] = []
    for intent in venue.intents:
        if intent["call"] != "market" or intent.get("reduce_only"):
            continue
        lev_intents = [x for x in venue.intents if x["call"] == "set_leverage"
                       and x["coin"] == intent["coin"]]
        lev = lev_intents[-1]["leverage"] if lev_intents else None
        try:
            max_lev = venue.max_leverage(intent["coin"])
        except Exception:
            max_lev = None
        order_problems.extend(_check_order(intent["coin"], intent["notional_usd"], lev, max_lev))

    # The task's pass bar is "every leader read OK or explicitly skipped
    # with a reason" -- i.e. every leader's read was ATTEMPTED and its
    # outcome is known, success or failure. `venue.leader_reads` is the
    # direct record of that (see DryRunVenue.leader_state): a leader
    # missing from it entirely means maybe_run never even tried to read it,
    # a real gap. A leader present but False (read failed) still counts as
    # "explicitly accounted for" -- maybe_run's own skip dict already
    # reports that case as "leader read failed".
    unexplained_leaders = [addr for addr in leaders if addr not in venue.leader_reads]
    failed_leader_reads = [addr for addr, ok in venue.leader_reads.items() if not ok]

    passed = not exceptions and not order_problems and not unexplained_leaders
    return {
        "ok": passed,
        "generated_ms": int(time.time() * 1000),
        "leaders": leaders,
        "settings": settings,
        "daily_pnl_source": pnl_info["source"],
        "cycles": cycles_out,
        "intents": venue.intents,
        "exceptions": exceptions,
        "order_problems": order_problems,
        "leader_skip_reasons": leader_skips,
        "unexplained_leaders": unexplained_leaders,
        "failed_leader_reads": failed_leader_reads,
        "leader_reads": dict(venue.leader_reads),
        "copies": copies_report,
        "margin_summary": margin_summary,
        "sizing_mode_override": sizing_mode_override,
        "state_dir": _STATE_TMP,
        "config_enabled_override": {"was": was_enabled, "forced_in_memory": True,
                                    "disk_config_touched": False},
    }


def _print_report(r: Dict[str, Any]) -> None:
    print(f"\n{DIM}copycat dry run -- real reads, zero orders, state in {r.get('state_dir')}{OFF}\n")
    if r.get("fatal"):
        print(f"{RED}{r['fatal']}{OFF}")
        return

    if r.get("cycles"):
        print(f"  leaders: {len(r['leaders'])}   daily_pnl: ${r['cycles'][0]['daily_pnl']:.2f} "
              f"({r['daily_pnl_source']})")
    else:
        print("  no cycles ran")
    sm = r.get("settings") or {}
    override_tag = f" {AMBER}(--sizing-mode override){OFF}" if r.get("sizing_mode_override") else ""
    fixed_lev_tag = f" (fixed leverage={sm.get('leverage')})" if sm.get("leverage_mode") == "fixed" else ""
    print(f"  sizing_mode: {sm.get('sizing_mode')}{override_tag}   "
          f"leverage_mode: {sm.get('leverage_mode')}{fixed_lev_tag}   "
          f"fixed_margin_usd: ${sm.get('fixed_margin_usd')}   "
          f"fixed_notional_usd: ${sm.get('fixed_notional_usd')}")

    print(f"\n  {'cycle':>5} {'equity':>10} {'positions':>9} {'opened':>7} {'added':>6} "
          f"{'reduced':>8} {'closed':>7} {'skips':>6}")
    for c in r.get("cycles", []):
        res = c.get("result") or {}
        print(f"  {c['cycle']:>5} {c['equity']:>10.2f} {c['n_positions']:>9} "
              f"{len(res.get('opened', [])):>7} {len(res.get('added', [])):>6} "
              f"{len(res.get('reduced', [])):>8} {len(res.get('closed', [])):>7} "
              f"{len(res.get('skipped', {})):>6}")

    if r.get("intents"):
        print(f"\n  intended actions ({len(r['intents'])}), NONE sent to the exchange:")
        for it in r["intents"]:
            if it["call"] == "market":
                side = "BUY" if it["is_buy"] else "SELL"
                tag = " reduce" if it.get("reduce_only") else ""
                print(f"    {DIM}[intent]{OFF} market {it['coin']:>14} {side} "
                      f"size={it['size']:.6g} @ {it['mid']:.6g} "
                      f"(${it['notional_usd']:.2f}){tag}")
            elif it["call"] == "set_leverage":
                print(f"    {DIM}[intent]{OFF} set_leverage {it['coin']:>14} -> {it['leverage']}x")
            elif it["call"] == "close":
                print(f"    {DIM}[intent]{OFF} close {it['coin']:>14}")
    else:
        print(f"\n  {AMBER}no intended actions this run{OFF} (all leaders flat/unchanged, "
              f"or every candidate skipped -- see reasons below)")

    if r.get("copies"):
        print(f"\n  copies this run would open/add ({len(r['copies'])}), effective "
              f"sizing per order:")
        print(f"    {'coin':>14} {'action':>6} {'mode':>14} {'lev':>4} {'notional':>9} "
              f"{'margin':>7} {'liq dist':>9}  leader")
        for c in r["copies"]:
            print(f"    {c['coin']:>14} {c['action']:>6} {str(c['sizing_mode']):>14} "
                  f"{c['leverage']:>3}x {c['notional_usd']:>9.2f} {c['margin_usd']:>7.2f} "
                  f"{c['liquidation_distance_pct']:>8.2f}%  {c['leader']}")
        ms = r.get("margin_summary") or {}
        print("\n  margin used vs available (before this run), by dex:")
        used_by_dex = ms.get("margin_used_by_intended_opens") or {}
        for dex in sorted(used_by_dex):     # only dexes that actually used margin
            avail = (ms.get("available_before_this_run") or {}).get(dex, 0.0)
            used = used_by_dex[dex]
            remain = (ms.get("margin_remaining") or {}).get(dex, avail)
            tag = dex or "(main)"
            flag = f" {RED}OVER{OFF}" if remain < 0 else ""
            print(f"    {tag:>10}: available ${avail:.4f}  used ${used:.4f}  "
                  f"remaining ${remain:.4f}{flag}")

    if r.get("leader_skip_reasons"):
        print("\n  skip reasons:")
        for k, v in sorted(r["leader_skip_reasons"].items()):
            print(f"    {DIM}{k}:{OFF} {v}")

    print()
    if r["exceptions"]:
        print(f"{RED}FAIL: {len(r['exceptions'])} exception(s){OFF}")
        for e in r["exceptions"]:
            print(f"  {RED}{e}{OFF}")
    if r["order_problems"]:
        print(f"{RED}FAIL: {len(r['order_problems'])} order problem(s){OFF}")
        for p in r["order_problems"]:
            print(f"  {RED}{p}{OFF}")
    if r["unexplained_leaders"]:
        print(f"{RED}FAIL: {len(r['unexplained_leaders'])} leader(s) never read at all{OFF}")
        for addr in r["unexplained_leaders"]:
            print(f"  {RED}{addr}{OFF}")
    if r["failed_leader_reads"]:
        print(f"{AMBER}{len(r['failed_leader_reads'])} leader(s) had a failed read this run "
              f"(explicitly flagged, not a FAIL by itself):{OFF}")
        for addr in r["failed_leader_reads"]:
            print(f"  {AMBER}{addr}{OFF}")
    if r["ok"]:
        print(f"{GREEN}PASS{OFF}: zero exceptions, every intended order >= ${MIN_ORDER_USD} "
              f"and within coin max leverage, every leader read OK or skipped with a reason.")
    print()


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cycles", type=int, default=2, help="cycles to run (default 2); "
                    "with the live config's only_new_positions=false, cycle 0 already "
                    "treats each leader's current book as a candidate, it is not a "
                    "quiet baseline -- a second cycle instead shows the diff logic "
                    "(adds/reduces/closes) against cycle 0's own state")
    ap.add_argument("--interval", type=float, default=30.0, help="seconds between cycles")
    ap.add_argument("--sizing-mode", choices=["proportional", "fixed_margin", "fixed_notional"],
                    default=None, help="override copycat.sizing_mode for this run, IN MEMORY "
                    "ONLY (.agent-config.json is never written). A leader with its own "
                    "leader_overrides.sizing_mode entry still uses that leader's override, "
                    "not this flag -- this only changes the book-wide default.")
    args = ap.parse_args(argv)

    r = run(args.cycles, args.interval, sizing_mode_override=args.sizing_mode)
    _print_report(r)

    os.makedirs(REPORT_DIR, exist_ok=True)
    with open(REPORT_PATH, "w") as fh:
        json.dump(r, fh, indent=2, sort_keys=False, default=str)
    print(f"{DIM}full report: {REPORT_PATH}{OFF}\n")

    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
