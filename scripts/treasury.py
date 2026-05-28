#!/usr/bin/env python3
"""Treasury — move USDC / USDH / USDT between spot, main perp, and HIP-3 dexes.

Replaces the manual HL-frontend dance:
  spot USDC → swap to USDH on @230 → bridge to km perp
  spot USDC → swap to USDT0 on @166 → bridge to cash perp
  main perp USDC ↔ xyz/vntl perp USDC (no swap needed)

Subcommands:
    treasury.py status                          # show all balances
    treasury.py move --from spot --to xyz --amount 50      # send_asset
    treasury.py move --from "" --to spot --amount 25       # main perp → spot
    treasury.py spot-perp --direction perp --amount 50     # usd_class_transfer
    treasury.py swap --to USDH --amount 30                 # spot order USDC→USDH
    treasury.py fund-km  --amount 20      # USDC main → spot → USDH → km dex
    treasury.py fund-cash --amount 10     # USDC main → spot → USDT0 → cash dex

The script uses HYPERLIQUID_PRIVATE_KEY (which must be the master key —
agent wallets can't sign transfers/swaps). Permission errors surface clearly.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
_env = _REPO / ".env.local"
if _env.exists():
    for line in _env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

from hermes_trader.client.hl_client import _http_post, fetch_account_state, resolve_user_address

# HL spot pair indices (verified live from /info spotMeta on 2026-05-28).
SPOT_PAIR_USDH_USDC = "@230"   # USDH/USDC
SPOT_PAIR_USDT_USDC = "@166"   # USDT0/USDC

# Token names used by send_asset (case-sensitive per HL SDK).
TOKEN_USDC = "USDC"
TOKEN_USDH = "USDH"
TOKEN_USDT = "USDT0"


def _exchange():
    """Sign transfers + swaps with the MASTER key (HYPERLIQUID_MASTER_PRIVATE_KEY).

    Agent wallets (used by the trading loop) can sign orders only — HL
    rejects transfers with "Must deposit before performing actions"
    because the agent address has no on-chain balance. Treasury operations
    must be signed by the master, which actually holds the funds. The
    trading loop continues using HYPERLIQUID_PRIVATE_KEY (the agent key);
    only this CLI touches the master.
    """
    pk = os.environ.get("HYPERLIQUID_MASTER_PRIVATE_KEY", "").strip()
    if not pk:
        print("error: HYPERLIQUID_MASTER_PRIVATE_KEY missing from .env.local")
        print("       transfers require the master key — agent keys can't sign them.")
        sys.exit(2)
    if pk.startswith("0x"):
        pk = pk[2:]
    from hyperliquid.exchange import Exchange
    from eth_account import Account
    acct = Account.from_key(pk)
    return Exchange(acct, base_url="https://api.hyperliquid.xyz")


def _master_address() -> str:
    return os.environ.get("HYPERLIQUID_MASTER_ADDRESS") or os.environ.get("HYPERLIQUID_WALLET_ADDRESS", "")


def cmd_status(_args) -> int:
    user = resolve_user_address()
    if not user:
        print("error: no wallet address resolved")
        return 2

    state = fetch_account_state(user, include_hip3=True)

    # Spot balances
    spot = _http_post("/info", {"type": "spotClearinghouseState", "user": user}) or {}
    spot_balances = spot.get("balances", []) or []

    print(f"\n=== Treasury · {user} ===\n")
    print(f"{'location':<22} {'token':<8} {'amount':>14}")
    print("-" * 50)

    # Spot row(s)
    for b in spot_balances:
        amt = float(b.get("total", 0) or 0)
        if amt < 0.001: continue
        print(f"  spot                  {b.get('coin','?'):<8} {amt:>14.4f}")

    # Per-dex (main + HIP-3)
    dex_equity = state.get("dex_equity", {})
    for dex, eq in dex_equity.items():
        if eq < 0.5 and dex != "": continue
        label = "main perp" if dex == "" else f"{dex} perp"
        # We don't know the token per-dex from our cached state, so we re-query.
        # For status display only; cheap enough.
        payload = {"type": "clearinghouseState", "user": user}
        if dex:
            payload["dex"] = dex
        cs = _http_post("/info", payload) or {}
        # marginSummary.accountValue is USD-equivalent; for native token amount
        # check balances list inside `marginSummary` or the raw response.
        print(f"  {label:<22} {'(USD)':<8} {eq:>14.2f}")

    print()
    print(f"  Total aggregated:     {'':<8} ${state['equity']:>13.2f}")
    print(f"  Free (initial margin):{'':<8} ${state['available_aggregated']:>13.2f}")
    return 0


def cmd_move(args) -> int:
    """send_asset between any pair of {spot, "" (main perp), HIP-3 dex name}."""
    src = args.src
    dst = args.dst
    if src == dst:
        print(f"error: source and destination are both '{src}'")
        return 2

    user = _master_address()
    ex = _exchange()
    print(f"  move ${args.amount:.2f} {args.token} : "
          f"{src or 'main'} → {dst or 'main'}")
    result = ex.send_asset(
        destination=user,
        source_dex=src,
        destination_dex=dst,
        token=args.token,
        amount=float(args.amount),
    )
    print(f"  response: {result}")
    if isinstance(result, dict) and result.get("status") == "ok":
        print("  ✓ sent")
        return 0
    print("  ✗ failed (likely permission error — confirm HYPERLIQUID_PRIVATE_KEY is the master key)")
    return 1


def cmd_spot_perp(args) -> int:
    """USDC main perp ↔ spot via usd_class_transfer."""
    ex = _exchange()
    to_perp = (args.direction == "perp")
    print(f"  usd_class_transfer ${args.amount:.2f} → {'main perp' if to_perp else 'spot'}")
    result = ex.usd_class_transfer(float(args.amount), to_perp=to_perp)
    print(f"  response: {result}")
    if isinstance(result, dict) and result.get("status") == "ok":
        print("  ✓ transferred")
        return 0
    print("  ✗ failed")
    return 1


def cmd_swap(args) -> int:
    """Buy USDH or USDT0 on the spot market, paying with USDC. IOC limit
    crossing the ask (1% buffer to guarantee fill on these stable pairs)."""
    ex = _exchange()
    if args.to == "USDH":
        pair = SPOT_PAIR_USDH_USDC
        token = "USDH"
    elif args.to == "USDT" or args.to == "USDT0":
        pair = SPOT_PAIR_USDT_USDC
        token = "USDT0"
    else:
        print(f"error: --to must be USDH or USDT, got {args.to}")
        return 2

    # Get current ask price for the pair
    book = _http_post("/info", {"type": "l2Book", "coin": pair}) or {}
    levels = book.get("levels", [[], []])
    if not levels[1]:
        print(f"error: no ask side for {pair}")
        return 2
    ask_px = float(levels[1][0]["px"])
    # IOC limit at 1% above ask = guaranteed fill at touch
    limit_px = round(ask_px * 1.005, 6)
    # Stablecoin pair price is ~1.0; size in TOKEN units = amount_usd / ask_px
    # ~1:1 stable, so size ≈ amount in USDC
    size = round(float(args.amount) / ask_px, 4)

    print(f"  spot swap: buy {size} {token} on {pair} @ {limit_px} (ask {ask_px})")
    print(f"             paying ~{args.amount:.2f} USDC")
    result = ex.order(pair, True, size, limit_px,
                      {"limit": {"tif": "Ioc"}}, reduce_only=False)
    print(f"  response: {result}")
    if isinstance(result, dict) and result.get("status") == "ok":
        statuses = result.get("response", {}).get("data", {}).get("statuses", [])
        if statuses and statuses[0].get("filled"):
            print("  ✓ filled")
            return 0
        if statuses and statuses[0].get("error"):
            print(f"  ✗ {statuses[0]['error']}")
            return 1
    print("  ✗ failed (response unparseable)")
    return 1


def cmd_fund_dex(args, *, target_token: str, target_dex: str, spot_pair: str) -> int:
    """Pipeline: USDC main perp → spot → swap to target_token → send to target_dex."""
    amount = float(args.amount)
    ex = _exchange()
    user = _master_address()

    print(f"\n=== Fund {target_dex} ({target_token}) with ${amount:.2f} ===\n")

    # 1. Move USDC main perp → spot
    print(f"[1/3] USDC main perp → spot")
    r1 = ex.usd_class_transfer(amount, to_perp=False)
    print(f"      response: {r1}")
    if not (isinstance(r1, dict) and r1.get("status") == "ok"):
        print("      ✗ aborting")
        return 1
    time.sleep(1)

    # 2. Swap USDC → target_token on spot
    print(f"[2/3] swap USDC → {target_token} on {spot_pair}")
    book = _http_post("/info", {"type": "l2Book", "coin": spot_pair}) or {}
    levels = book.get("levels", [[], []])
    if not levels[1]:
        print(f"      ✗ no ask on {spot_pair}")
        return 1
    ask_px = float(levels[1][0]["px"])
    limit_px = round(ask_px * 1.005, 6)
    size = round(amount / ask_px, 4)
    r2 = ex.order(spot_pair, True, size, limit_px,
                  {"limit": {"tif": "Ioc"}}, reduce_only=False)
    print(f"      response: {r2}")
    statuses = (r2 or {}).get("response", {}).get("data", {}).get("statuses", [])
    if not (statuses and statuses[0].get("filled")):
        print(f"      ✗ swap didn't fill: {statuses}")
        return 1
    filled_sz = float(statuses[0]["filled"].get("totalSz", size))
    time.sleep(1)

    # 3. Send target_token from spot → target_dex
    print(f"[3/3] send {filled_sz:.4f} {target_token} : spot → {target_dex}")
    r3 = ex.send_asset(
        destination=user, source_dex="spot", destination_dex=target_dex,
        token=target_token, amount=filled_sz,
    )
    print(f"      response: {r3}")
    if isinstance(r3, dict) and r3.get("status") == "ok":
        print(f"\n✓ funded {target_dex} with ~${amount:.2f} (${filled_sz:.2f} {target_token})")
        return 0
    print("      ✗ send_asset failed")
    return 1


def cmd_fund_km(args) -> int:
    return cmd_fund_dex(args, target_token="USDH", target_dex="km",
                       spot_pair=SPOT_PAIR_USDH_USDC)


def cmd_fund_cash(args) -> int:
    return cmd_fund_dex(args, target_token="USDT0", target_dex="cash",
                       spot_pair=SPOT_PAIR_USDT_USDC)


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status")

    p_move = sub.add_parser("move", help="send_asset between any pair (spot/main/HIP-3)")
    p_move.add_argument("--from", dest="src", required=True,
                        help='source: "spot", "" for main perp, or HIP-3 dex name (xyz/vntl/km/etc)')
    p_move.add_argument("--to", dest="dst", required=True)
    p_move.add_argument("--amount", type=float, required=True)
    p_move.add_argument("--token", default="USDC", help="USDC / USDH / USDT0")

    p_sp = sub.add_parser("spot-perp", help="USDC main-perp ↔ spot")
    p_sp.add_argument("--direction", choices=["perp", "spot"], required=True)
    p_sp.add_argument("--amount", type=float, required=True)

    p_sw = sub.add_parser("swap", help="spot swap USDC → USDH or USDT0")
    p_sw.add_argument("--to", choices=["USDH", "USDT"], required=True)
    p_sw.add_argument("--amount", type=float, required=True)

    p_km = sub.add_parser("fund-km", help="end-to-end: USDC main → spot → USDH → km perp")
    p_km.add_argument("--amount", type=float, required=True)

    p_cash = sub.add_parser("fund-cash", help="end-to-end: USDC main → spot → USDT0 → cash perp")
    p_cash.add_argument("--amount", type=float, required=True)

    args = ap.parse_args()

    handlers = {
        "status": cmd_status,
        "move": cmd_move,
        "spot-perp": cmd_spot_perp,
        "swap": cmd_swap,
        "fund-km": cmd_fund_km,
        "fund-cash": cmd_fund_cash,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
