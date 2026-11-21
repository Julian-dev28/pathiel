"""Trade and read a Hyperliquid sub-account with the master's own signer.

Why: Hyperliquid nets one position per coin per account, so a second
strategy on a coin the desk already trades (the fib sleeve on xyz:BRENTOIL,
2026-11-16) needs its own account. A sub-account has no private key. The
venue's rule, quoted from the official docs:

  - Exchange endpoint, "Subaccounts and vaults": "To perform actions on
    behalf of a subaccount or vault signing should be done by the master
    account and the vaultAddress field should be set to the address of the
    subaccount or vault."
  - "Nonces and API wallets": "A master account can approve API wallets to
    sign on behalf of the master account or any of the sub-accounts." and
    "To query the account data associated with a master or sub-account, you
    must pass in the actual address of that account."
  - "Account abstraction modes": in unified account mode "USDC balance is
    the single source for validator-operated perps, XYZ perps, and spot",
    balances and holds show "in the spot clearinghouse state" and
    "Individual perp dex user states are not meaningful."

and from the installed SDK (hyperliquid-python-sdk 0.24.0): an `Exchange`
built with `vault_address` puts that address into the signed action hash
(`utils/signing.action_hash`) and into the posted `vaultAddress`
(`Exchange._post_action`) for orders, bulk orders, cancels and leverage.
An order on a HIP-3 dex (xyz:BRENTOIL) is the same action with the dex's
asset id; the docs name no sub-account restriction for it.

`AccountExchange(address)` is `pathiel.client.exchange` bound to one
sub-account: every call runs inside `exchange.account_target(address)`, so
`pathiel.agents.bracket` takes it as its `ex` and the whole bracket path
(one-position check, 2R gate, leverage rule, attached stop and target,
sweep, set_stop) acts on the sub-account. Nothing here is used unless a
caller builds one: the default account's path is untouched.

Where it would break: the first live order is the proof that this API
wallet may act for this sub-account (a venue refusal comes back as the
order's error, nothing is placed); two processes signing with ONE API
wallet share its nonce set (the docs recommend one API wallet per
sub-account; the SDK's nonce is the millisecond clock, so a clash needs two
actions in the same millisecond).
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from pathiel.client import exchange as _exchange

# Everything that signs. A read-only facade refuses these by name, so a
# dry run cannot reach the venue's /exchange endpoint through any code path.
MUTATING = frozenset({
    "place_hl_order", "place_hl_trigger_order", "place_hl_limit_order", "place_hl_bracket_order",
    "cancel_orders", "cancel_open_orders_for_coin", "cancel_stranded_reduce_only_orders",
    "set_leverage", "_make_exchange",
})

# Account modes whose money sits in the spot balance, not in a perp dex.
UNIFIED_MODES = frozenset({"unifiedAccount", "portfolioMargin"})


class ReadOnlyError(RuntimeError):
    """A dry run asked for a signed action."""


class AccountExchange:
    """`pathiel.client.exchange` bound to sub-account `address`.

    Attribute access returns the module's own attribute; a function is
    wrapped so it runs inside `exchange.account_target(address)`.
    `read_only=True` refuses every signing function (MUTATING) with
    ReadOnlyError before anything is built or sent. `ex`: the exchange
    module, injectable for tests."""

    def __init__(self, address: str, *, read_only: bool = False, ex: Any = None) -> None:
        self._ex = ex if ex is not None else _exchange
        with self._ex.account_target(address) as target:   # validates the address
            self.address: str = target
        self.read_only = bool(read_only)

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._ex, name)
        if not callable(attr) or isinstance(attr, type):
            return attr
        if self.read_only and name in MUTATING:
            def refused(*_a: Any, **_k: Any) -> Any:
                raise ReadOnlyError(f"{name} refused: this exchange facade is read-only (dry run)")
            return refused

        def bound(*args: Any, **kwargs: Any) -> Any:
            with self._ex.account_target(self.address):
                return attr(*args, **kwargs)
        bound.__name__ = name
        return bound


def _post_strict() -> Callable[[str, Dict[str, Any]], Any]:
    from pathiel.client.hl_client import _http_post_strict
    return _http_post_strict


def account_role(user: str, post: Optional[Callable[[str, Dict[str, Any]], Any]] = None) -> Dict[str, Any]:
    """Hyperliquid's own answer to "what is this address": {"role":
    "subAccount", "master": "0x..."} for a sub-account, {"role": "user"} for
    a plain account. {"role": None, "error": ...} when the read failed."""
    post = post or _post_strict()
    try:
        raw = post("/info", {"type": "userRole", "user": user})
    except Exception as e:   # noqa: BLE001 - a failed read is an answer, not a crash
        return {"role": None, "error": f"{type(e).__name__}: {e}"}
    if not isinstance(raw, dict):
        return {"role": None, "error": "no answer"}
    data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    out: Dict[str, Any] = {"role": raw.get("role")}
    if data.get("master"):
        out["master"] = str(data["master"]).lower()
    return out


def _f(x: Any) -> float:
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def account_equity(user: str, coin: str,
                   post: Optional[Callable[[str, Dict[str, Any]], Any]] = None) -> Dict[str, Any]:
    """Equity of `user` that can margin `coin`, and the account's open
    positions on the main dex and the coin's dex. Every read is strict: a
    read that did not land gives {"ok": False, "error": ...}, never a $0
    account (a $0 read would size nothing and would look like a 100%
    drawdown to a kill switch).

    Standard mode: the coin's own dex holds the money (HIP-3 funds on xyz
    back only xyz), so equity = that dex's accountValue.
    Unified account / portfolio margin: the money is the spot USDC balance
    and the per-dex accountValue is not meaningful (docs, "Account
    abstraction modes"), so equity = spot USDC total + the unrealized P&L
    of the open positions. Flat, that is exactly the spot balance."""
    post = post or _post_strict()
    dex = coin.split(":", 1)[0] if ":" in coin else ""
    try:
        mode = post("/info", {"type": "userAbstraction", "user": user})
        states: Dict[str, Any] = {"": post("/info", {"type": "clearinghouseState", "user": user})}
        if dex:
            states[dex] = post("/info", {"type": "clearinghouseState", "user": user, "dex": dex})
        spot = post("/info", {"type": "spotClearinghouseState", "user": user})
    except Exception as e:   # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    if not all(isinstance(s, dict) for s in states.values()) or not isinstance(spot, dict):
        return {"ok": False, "error": "account read returned no state"}
    mode = mode if isinstance(mode, str) else "unknown"
    positions: List[Dict[str, Any]] = []
    for name, st in states.items():
        for row in st.get("assetPositions") or []:
            pos = (row or {}).get("position") or {}
            szi = _f(pos.get("szi"))
            if szi == 0:
                continue
            c = str(pos.get("coin") or "")
            if name and c and ":" not in c:
                c = f"{name}:{c}"
            lev = pos.get("leverage")
            positions.append({"coin": c, "szi": szi, "entry_px": _f(pos.get("entryPx")),
                              "unrealized_pnl": _f(pos.get("unrealizedPnl")),
                              "margin_used": _f(pos.get("marginUsed")),
                              "leverage": (lev or {}).get("value") if isinstance(lev, dict) else lev})
    spot_usdc = sum(_f(b.get("total")) for b in spot.get("balances") or [] if b.get("coin") == "USDC")
    dex_value = _f((states[dex].get("marginSummary") or {}).get("accountValue"))
    unrealized = sum(p["unrealized_pnl"] for p in positions)
    if mode in UNIFIED_MODES:
        equity, source = spot_usdc + unrealized, "spot USDC + unrealized P&L (unified account)"
    else:
        equity, source = dex_value, f"accountValue on the {dex or 'main'} dex"
    return {"ok": True, "user": user, "coin": coin, "mode": mode, "equity": equity, "source": source,
            "spot_usdc": spot_usdc, "dex_account_value": dex_value, "unrealized_pnl": unrealized,
            "positions": positions}
