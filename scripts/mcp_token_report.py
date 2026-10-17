#!/usr/bin/env python3
"""Measure the pathiel MCP server's token footprint: tools/list size and the
response size of common tool calls.

Deterministic space, not latent space: this is a same-input-same-output
measurement, so it is a script, not a judgment call made fresh every time
someone asks "did the overhaul help."

Usage:
    scripts/mcp_token_report.py                      # measure the current server
    scripts/mcp_token_report.py --server PATH         # measure a different copy
    scripts/mcp_token_report.py --compare OLD NEW     # before/after, side by side
    scripts/mcp_token_report.py --json out.json       # also write the raw numbers

Token counts are chars/4, the same rough estimator the overhaul brief uses
(no tokenizer dependency; good enough for "did this get smaller," not for
billing).

Every network call a "common call" needs is faked -- this reports on
payload SHAPE, not on live market data, so the same server always reports
the same numbers. It never touches the network, by design (reproducible
input, reproducible output) and because the mcp agent is not permitted to.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import types
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SERVER = ROOT / "scripts" / "pathiel-mcp-server.py"

# ── canned /info responses, shaped from real Hyperliquid reads on 2026-10-17 ─
# Used to drive "common calls" without ever touching the network. Keyed by
# the /info `type` field the way the server's own `_info(typ, **kw)` is.
_UNIVERSE = [{"name": "BTC", "szDecimals": 5, "maxLeverage": 40},
             {"name": "ETH", "szDecimals": 4, "maxLeverage": 25}]
_CTX = [{"funding": "0.0000125", "openInterest": "27824.85", "dayNtlVlm": "3274603594.46",
         "prevDayPx": "67000", "markPx": "68000", "midPx": "68000.5"},
        {"funding": "0.0000098", "openInterest": "90120.1", "dayNtlVlm": "900000000",
         "prevDayPx": "3200", "markPx": "3300", "midPx": "3300.2"}]
_HIP3_UNIVERSE = [{"name": "xyz:CL", "szDecimals": 3, "maxLeverage": 20}]
_HIP3_CTX = [{"funding": "0.00000625", "openInterest": "1556705.1", "dayNtlVlm": "230444746.79",
              "prevDayPx": "95.841", "markPx": "99.357", "midPx": "99.318"}]
_CANDLE = {"t": 1_700_000_000_000, "T": 1_700_003_599_999, "s": "BTC", "i": "1h",
          "o": "67000.0", "c": "67500.0", "h": "67800.0", "l": "66900.0",
          "v": "188.15985", "n": 3462}


def _fake_http_post_strict(path: str, payload: Dict[str, Any], timeout: int = 5) -> Any:
    """Drop-in for pathiel.client.hl_client._http_post_strict.

    Patched at the hl_client module level (not just on the server module's
    own `_info`) because some common calls (get_all_mids -> hyperfeed's
    market_get_mids -> hl_client.fetch_all_mids) route through hl_client's
    OWN _http_post, which calls hl_client's OWN _http_post_strict in its own
    module globals -- patching only the server's `_info` does not reach that
    path, and the first version of this script quietly made a real network
    call because of exactly that gap.
    """
    return _fake_info(payload.get("type"), **{k: v for k, v in payload.items() if k != "type"})


def _fake_info(typ: str, **kw: Any) -> Any:
    dex = kw.get("dex")
    if typ == "meta":
        return {"universe": _HIP3_UNIVERSE if dex else _UNIVERSE}
    if typ == "metaAndAssetCtxs":
        return [{"universe": _HIP3_UNIVERSE}, _HIP3_CTX] if dex else [{"universe": _UNIVERSE}, _CTX]
    if typ == "allMids":
        return {"xyz:CL": "99.318"} if dex else {"BTC": "68000.5", "ETH": "3300.2"}
    if typ == "candleSnapshot":
        return [_CANDLE] * 24
    if typ == "clearinghouseState":
        return {"marginSummary": {"accountValue": "1000", "totalNtlPos": "0", "totalMarginUsed": "0"},
                "assetPositions": [], "withdrawable": "1000"}
    if typ == "spotClearinghouseState":
        return {"balances": []}
    if typ == "frontendOpenOrders":
        return []
    if typ == "userFills":
        return []
    if typ == "perpDexs":
        return [None, {"name": "xyz"}]
    if typ == "predictedFundings":
        return [["BTC", [["HlPerp", {"fundingRate": "0.0000125", "fundingIntervalHours": 1,
                                      "nextFundingTime": 1_700_003_600_000}]]]]
    if typ == "userFunding":
        return []
    if typ == "userNonFundingLedgerUpdates":
        return []
    return None


def load_server(path: Path) -> types.ModuleType:
    """Import a copy of the MCP server module from an arbitrary path, the
    same way tests/test_mcp_server_hardening.py does, so an old snapshot
    (e.g. checked out from git to a temp file) can be measured too.

    The server file resolves its own project root from `__file__` two
    directories up, which is wrong for a copy sitting somewhere else (e.g.
    a scratch dir). Prepending THIS repo's real root to sys.path first
    means `pathiel`/`contracts` still resolve from here regardless -- an
    old snapshot's own (wrong) sys.path insert just becomes a harmless
    extra entry.
    """
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    spec = importlib.util.spec_from_file_location(f"mcp_report_{abs(hash(str(path)))}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # Two interception points, not one: `_info` for the server's own direct
    # calls, and hl_client's `_http_post_strict` for everything that goes
    # through a pathiel.client helper (fetch_all_mids, fetch_hl_candles, ...).
    # Both are real; faking only one leaves the other hitting the network.
    import pathiel.client.hl_client as hlc
    hlc._http_post_strict = _fake_http_post_strict
    mod._info = _fake_info  # type: ignore[attr-defined]
    mod._TOOL_HANDLERS = None  # rebuild handlers against the faked _info
    return mod


# name -> params, for tools present (under some name) in both old and new
# servers, read-only, no live-money tool among them.
COMMON_CALLS: Dict[str, Dict[str, Any]] = {
    "get_price": {"coin": "BTC"},
    "get_candles": {"coin": "BTC", "interval": "1h", "count": 24},
    "get_all_mids": {},
    "get_asset_context": {"coin": "xyz:CL"},
    "get_max_leverage": {"coin": "xyz:CL"},
    "get_open_interest": {"coin": "xyz:CL"},
    "state": {},
    "get_assets": {},
}


def _tools_list_metrics(mod: types.ModuleType) -> Dict[str, Any]:
    payload = json.dumps({"tools": mod.TOOLS})
    return {"tool_count": len(mod.TOOLS), "chars": len(payload),
           "tokens_est": round(len(payload) / 4)}


def _call(mod: types.ModuleType, name: str, args: Dict[str, Any]) -> "Dict[str, Any] | None":
    handlers = mod._tool_handlers()
    handler = handlers.get(name)
    if handler is None:
        return None
    text = handler(args)
    if not isinstance(text, str):
        text = json.dumps(text, default=str)
    return {"chars": len(text), "tokens_est": round(len(text) / 4)}


def _common_call_metrics(mod: types.ModuleType) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for name, args in COMMON_CALLS.items():
        try:
            out[name] = _call(mod, name, args)
        except Exception as e:  # a removed/renamed tool in an old snapshot
            out[name] = {"error": f"{type(e).__name__}: {e}"}
    return out


def report(path: Path) -> Dict[str, Any]:
    mod = load_server(path)
    return {"path": str(path), "tools_list": _tools_list_metrics(mod),
           "common_calls": _common_call_metrics(mod)}


def _print_single(r: Dict[str, Any]) -> None:
    tl = r["tools_list"]
    print(f"\n{r['path']}")
    print(f"  tools/list: {tl['tool_count']} tools, {tl['chars']} chars, "
          f"~{tl['tokens_est']} tokens")
    print("  common calls (chars / ~tokens):")
    for name, m in r["common_calls"].items():
        if m is None:
            print(f"    {name:<20} (not found)")
        elif "error" in m:
            print(f"    {name:<20} ERROR: {m['error']}")
        else:
            print(f"    {name:<20} {m['chars']:>6} / ~{m['tokens_est']}")


def _print_compare(before: Dict[str, Any], after: Dict[str, Any]) -> None:
    bt, at = before["tools_list"], after["tools_list"]
    print(f"\n{'':<20} {'before':>12} {'after':>12} {'delta':>12}")
    print(f"{'tool count':<20} {bt['tool_count']:>12} {at['tool_count']:>12} "
          f"{at['tool_count'] - bt['tool_count']:>+12}")
    print(f"{'tools/list chars':<20} {bt['chars']:>12} {at['chars']:>12} "
          f"{at['chars'] - bt['chars']:>+12}")
    print(f"{'tools/list ~tokens':<20} {bt['tokens_est']:>12} {at['tokens_est']:>12} "
          f"{at['tokens_est'] - bt['tokens_est']:>+12}")
    print("\ncommon calls, chars (before -> after, delta):")
    for name in COMMON_CALLS:
        b, a = before["common_calls"].get(name), after["common_calls"].get(name)
        bc = b["chars"] if b and "chars" in b else None
        ac = a["chars"] if a and "chars" in a else None
        if bc is None or ac is None:
            print(f"  {name:<20} before={b} after={a}")
            continue
        print(f"  {name:<20} {bc:>6} -> {ac:>6}  ({ac - bc:+d})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--server", type=Path, default=DEFAULT_SERVER,
                    help="server file to measure (default: the repo's own)")
    ap.add_argument("--compare", nargs=2, metavar=("OLD", "NEW"),
                    help="measure two server files and print a before/after table")
    ap.add_argument("--json", type=Path, help="also write the raw numbers to this path")
    args = ap.parse_args()

    if args.compare:
        before = report(Path(args.compare[0]))
        after = report(Path(args.compare[1]))
        _print_compare(before, after)
        out = {"before": before, "after": after}
    else:
        out = report(args.server)
        _print_single(out)

    if args.json:
        args.json.write_text(json.dumps(out, indent=2))
        print(f"\nraw numbers written to {args.json}")


if __name__ == "__main__":
    main()
