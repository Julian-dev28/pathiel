"""memecheck: deterministic red-flag report for a pump.fun / Solana token.

Fetch (DexScreener pairs, RugCheck report + insider graph) is split from
analyze(), which is pure: same three JSON blobs + same clock -> same report.
Read only. No keys. Nothing here touches pathiel's Hyperliquid account.
"""
from __future__ import annotations

import datetime as dt
import re
from collections import Counter

import httpx

BURN = "11111111111111111111111111111111"
DEX_URL = "https://api.dexscreener.com/latest/dex/tokens/{mint}"
RUG_URL = "https://api.rugcheck.xyz/v1/tokens/{mint}/report"
GRAPH_URL = "https://api.rugcheck.xyz/v1/tokens/{mint}/insiders/graph"

# Thresholds. Each one names the pattern it catches.
FAST_GRAD_S = 300        # curve -> AMM under 5 min: bundled launch or sniper wall
THIN_LIQ_PCT = 20.0      # pool USD under 20% of market cap: small sells move price hard
TOP10_PCT = 30.0         # top 10 real wallets (ex pool, ex burn) above 30%
TOP1_PCT = 10.0          # one wallet above 10%
STD_QUOTES = {"SOL", "WSOL", "USDC", "USDT"}

_MINT_RE = re.compile(r"[1-9A-HJ-NP-Za-km-z]{32,44}")


def parse_mint(arg: str) -> str:
    """Mint from a bare address or any pump.fun / dexscreener / solscan URL."""
    hits = _MINT_RE.findall(arg.strip())
    if not hits:
        raise ValueError(f"no Solana address in {arg!r}")
    return hits[-1]


def _get(client: httpx.Client, url: str):
    try:
        r = client.get(url, timeout=20)
        r.raise_for_status()
        return r.json()
    except Exception as e:  # one dead source must not kill the report
        return {"_error": f"{type(e).__name__}: {e}"}


def fetch(mint: str) -> tuple:
    with httpx.Client(headers={"User-Agent": "Mozilla/5.0"}) as c:
        return tuple(_get(c, u.format(mint=mint)) for u in (DEX_URL, RUG_URL, GRAPH_URL))


def top_pair(dex: dict):
    """Deepest graduated pool; the bonding curve only if nothing graduated."""
    pairs = [] if "_error" in dex else (dex.get("pairs") or [])
    curve = next((p for p in pairs if p.get("dexId") == "pumpfun"), None)
    amms = sorted((p for p in pairs if p.get("dexId") != "pumpfun"),
                  key=lambda p: (p.get("liquidity") or {}).get("usd") or 0, reverse=True)
    return curve, (amms[0] if amms else curve)


def quote_usd_from(dex_of_quote: dict) -> float | None:
    """USD price of a quote token from its own DexScreener pairs: the deepest
    pair's priceUsd. None when there is no priced pair."""
    priced = [p for p in (dex_of_quote.get("pairs") or []) if p.get("priceUsd")]
    if not priced:
        return None
    best = max(priced, key=lambda p: (p.get("liquidity") or {}).get("usd") or 0)
    return float(best["priceUsd"])


def fetch_quote_usd(dex: dict) -> float | None:
    """Only called when DexScreener gave no USD price (curve quoted in an
    unusual asset, e.g. a tokenized stock)."""
    _, top = top_pair(dex)
    if not top or top.get("priceUsd"):
        return None
    with httpx.Client(headers={"User-Agent": "Mozilla/5.0"}) as c:
        return quote_usd_from(_get(c, DEX_URL.format(mint=top["quoteToken"]["address"])))


def _ts(ms) -> dt.datetime:
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc)


def analyze(mint: str, dex: dict, rug: dict, graph, now: dt.datetime,
            quote_usd: float | None = None) -> dict:
    """Facts plus a list of (flag, why) red flags. Pure.

    quote_usd: USD price of the pair's quote token, used only when DexScreener
    gives no priceUsd (it leaves curves quoted in odd assets unpriced)."""
    rep: dict = {"mint": mint, "at": now.isoformat(timespec="seconds"), "flags": [], "errors": []}
    flag = lambda k, why: rep["flags"].append((k, why))  # noqa: E731

    if "_error" in dex:
        rep["errors"].append(f"dexscreener: {dex['_error']}")
    curve, top = top_pair(dex)
    rep["name"] = ((rug.get("tokenMeta") or {}).get("name")
                   or ((top or {}).get("baseToken") or {}).get("name"))

    if top:
        quote = (top.get("quoteToken") or {}).get("symbol")
        price = float(top.get("priceUsd") or 0)
        if not price and quote_usd:
            price = float(top.get("priceNative") or 0) * quote_usd
        supply_ui = ((rug.get("token") or {}).get("supply") or 0) / 10 ** (rug.get("token") or {}).get("decimals", 6)
        mc = top.get("marketCap") or (price * supply_ui if price and supply_ui else None)
        liq = (top.get("liquidity") or {}).get("usd")
        tx5 = (top.get("txns") or {}).get("m5") or {}
        links = top.get("info") or {}
        rep["market"] = {
            "venue": top.get("dexId"), "quote": quote, "price_usd": price or None,
            "on_curve": top is curve,
            "mc": mc, "liq_usd": liq, "liq_pct": round(liq / mc * 100, 1) if mc and liq else None,
            "volume": top.get("volume") or {}, "change": top.get("priceChange") or {},
            "txns_m5": tx5, "txns_h1": (top.get("txns") or {}).get("h1") or {},
            "age_min": round((now - _ts(top["pairCreatedAt"])).total_seconds() / 60),
            "socials": [s.get("url") for s in links.get("socials") or []],
            "websites": [s.get("url") for s in links.get("websites") or []],
        }
        if curve and top is not curve:
            grad = round((_ts(top["pairCreatedAt"]) - _ts(curve["pairCreatedAt"])).total_seconds())
            rep["market"]["graduated_s"] = grad
            if grad < FAST_GRAD_S:
                flag("fast_graduation", f"left the bonding curve in {grad}s: bundle/sniper launch")
        if quote not in STD_QUOTES:
            flag("exotic_quote", f"priced in {quote}, not SOL/USDC: you must hold {quote} to trade, "
                 "and its issuer's controls apply to the pool's reserve")
        if not mc:
            flag("no_usd_price", "no USD price from any source")
        if mc and liq and liq / mc * 100 < THIN_LIQ_PCT:
            flag("thin_liquidity", f"pool ${liq:,.0f} is {liq / mc * 100:.1f}% of ${mc:,.0f} mc")
        b, s = tx5.get("buys") or 0, tx5.get("sells") or 0
        if b and s >= b:
            flag("sellers_match_buyers", f"last 5 min {b} buys vs {s} sells: early holders exiting into the bid")
        if not rep["market"]["websites"]:
            flag("no_website", "no website listed")

    if "_error" in rug:
        rep["errors"].append(f"rugcheck: {rug['_error']}")
        return rep

    tok = rug.get("token") or {}
    supply = tok.get("supply") or 1
    dec = tok.get("decimals", 6)
    ext = rug.get("token_extensions") or {}
    rep["contract"] = {
        "mint_authority": tok.get("mintAuthority"), "freeze_authority": tok.get("freezeAuthority"),
        "meta_mutable": (rug.get("tokenMeta") or {}).get("mutable"),
        "transfer_hook": ext.get("transferHook"), "permanent_delegate": ext.get("permanentDelegate"),
        "transfer_fee_pct": (rug.get("transferFee") or {}).get("pct"),
        "rugcheck_score": rug.get("score_normalised"),
        "rugcheck_risks": [r.get("name") for r in rug.get("risks") or []],
        "rugged": rug.get("rugged"),
        "lp_locked": [(m.get("marketType"), (m.get("lp") or {}).get("lpLockedPct"))
                      for m in rug.get("markets") or []],
    }
    c = rep["contract"]
    if c["mint_authority"]:
        flag("mint_authority", "creator can mint more supply")
    if c["freeze_authority"]:
        flag("freeze_authority", "creator can freeze your wallet")
    if c["meta_mutable"]:
        flag("mutable_metadata", "name/image can be changed")
    if c["transfer_hook"] or c["permanent_delegate"] or c["transfer_fee_pct"]:
        flag("token_extension", "transfer hook, permanent delegate or transfer fee present")
    if c["rugged"]:
        flag("rugged", "RugCheck marks it rugged")
    if c["lp_locked"] and (c["lp_locked"][0][1] or 0) < 100:
        flag("lp_unlocked", f"main pool LP locked {c['lp_locked'][0][1]}%")

    rep["creator"] = {"address": rug.get("creator"), "balance": rug.get("creatorBalance")}
    if rug.get("creatorBalance") == 0:
        flag("creator_out", "creator wallet holds 0: dev already sold or moved the bag")
    desc = (rug.get("fileMeta") or {}).get("description")
    rep["description"] = desc
    if not desc:
        flag("no_description", "empty token description")

    known = rug.get("knownAccounts") or {}
    pools = {m.get("pubkey") for m in rug.get("markets") or []}
    pools |= {k for k, v in known.items() if (v or {}).get("type") == "AMM"}
    hs = rug.get("topHolders") or []
    real = [h for h in hs if h["owner"] not in pools and h["owner"] != BURN]
    rep["holders"] = {
        "count": rug.get("totalHolders"),
        "top10_pct": round(sum(h["pct"] for h in real[:10]), 2),
        "top1_pct": round(real[0]["pct"], 2) if real else 0.0,
        "burned_pct": round(sum(h["pct"] for h in hs if h["owner"] == BURN), 2),
        "insider_flagged_pct": round(sum(h["pct"] for h in real if h.get("insider")), 2),
    }
    if rep["holders"]["top10_pct"] > TOP10_PCT:
        flag("concentrated", f"top 10 wallets hold {rep['holders']['top10_pct']}%")
    if rep["holders"]["top1_pct"] > TOP1_PCT:
        flag("whale", f"one wallet holds {rep['holders']['top1_pct']}%")

    nets = [{"id": n.get("id"), "wallets": n.get("size"), "type": n.get("type"),
             "pct": round((n.get("currentHolding") or 0) / supply * 100, 2)}
            for n in rug.get("insiderNetworks") or []]
    rep["insiders"] = {"wallets": rug.get("graphInsidersDetected") or 0, "networks": nets, "split_buys": []}
    if rep["insiders"]["wallets"]:
        flag("insider_networks", f"{rep['insiders']['wallets']} linked wallets in {len(nets)} network(s), "
             f"{sum(n['pct'] for n in nets):.2f}% of supply")
    if isinstance(graph, list):
        amts = Counter(nd["holdings"] for net in graph for nd in net.get("nodes") or [] if nd.get("holdings"))
        rep["insiders"]["split_buys"] = sorted(
            ({"wallets": n, "tokens": a / 10 ** dec} for a, n in amts.items() if n > 1),
            key=lambda d: -d["tokens"])
        for sb in rep["insiders"]["split_buys"]:
            flag("split_buy", f"{sb['wallets']} wallets hold exactly {sb['tokens']:,.2f} tokens: one buyer")
    elif isinstance(graph, dict) and "_error" in graph:
        rep["errors"].append(f"insider graph: {graph['_error']}")
    return rep


def render(rep: dict) -> str:
    L = [f"# {rep.get('name')} ({rep['mint']})  @ {rep['at']}"]
    m = rep.get("market")
    if m:
        usd = lambda v: f"${v:,.0f}" if v else "n/a"  # noqa: E731
        L.append(f"mc {usd(m['mc'])} | liq {usd(m['liq_usd'])} ({m['liq_pct']}%) | venue {m['venue']}"
                 f"{' (bonding curve)' if m['on_curve'] else ''} | quote {m['quote']} | age {m['age_min']} min"
                 + (f" | curve->AMM {m['graduated_s']}s" if "graduated_s" in m else ""))
        L.append(f"chg m5 {m['change'].get('m5')}% h1 {m['change'].get('h1')}% h24 {m['change'].get('h24')}% | "
                 f"vol h1 ${m['volume'].get('h1', 0):,.0f} m5 ${m['volume'].get('m5', 0):,.0f} | "
                 f"m5 {m['txns_m5'].get('buys')}b/{m['txns_m5'].get('sells')}s")
        L.append(f"socials {m['socials'] or '-'} | sites {m['websites'] or '-'}")
    if "contract" in rep:
        c, h = rep["contract"], rep["holders"]
        L.append(f"contract: mint {c['mint_authority']} freeze {c['freeze_authority']} mutable {c['meta_mutable']} "
                 f"LP {c['lp_locked']} rugcheck {c['rugcheck_score']} {c['rugcheck_risks']}")
        L.append(f"holders {h['count']} | top10 {h['top10_pct']}% top1 {h['top1_pct']}% burned {h['burned_pct']}% | "
                 f"creator balance {rep['creator']['balance']}")
    L.append(f"RED FLAGS ({len(rep['flags'])}):")
    L += [f"  - {k}: {why}" for k, why in rep["flags"]] or ["  none"]
    L += [f"ERROR {e}" for e in rep["errors"]]
    return "\n".join(L)
