"""W-P2 systematic enumeration of 2024-2026 congressional floor votes.

Crawls ALL House roll-call XMLs (clerk.house.gov) and Senate LIS vote menus
(senate.gov), caches vote METADATA only, then applies the pre-committed
keyword filter from findings/W-P2_scheduled_catalyst.md. No price data here.

Usage:
  W-P2_enumerate.py senate          # fetch senate menus + detail times for matches
  W-P2_enumerate.py house 2024      # crawl one year of house rolls (resumable)
  W-P2_enumerate.py filter          # apply keyword filter, print candidate table

READ-ONLY research. Never touches live state. Polite throttle on .gov hosts.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_HOUSE = os.path.join(HERE, "W-P2_cache_house_rolls.json")
CACHE_SENATE = os.path.join(HERE, "W-P2_cache_senate.json")
OUT_CANDIDATES = os.path.join(HERE, "W-P2_candidates.json")

UA = {"User-Agent": "hermes-research w-p2 (contact: team.recoin@gmail.com)"}
ET_TZ = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

SLEEP_S = 0.35          # polite crawl of .gov static XML
TIMEOUT_S = 30


def _get(url: str, retries: int = 3) -> bytes | None:
    for i in range(retries):
        try:
            r = requests.get(url, headers=UA, timeout=TIMEOUT_S)
            if r.status_code == 404:
                return None
            if r.ok:
                return r.content
            time.sleep(2.0 * (i + 1))
        except Exception:
            time.sleep(2.0 * (i + 1))
    return None


def _load(path: str) -> dict:
    return json.load(open(path)) if os.path.exists(path) else {}


def _save(path: str, obj) -> None:
    json.dump(obj, open(path, "w"), indent=0)


# ── House ──────────────────────────────────────────────────────────────

def crawl_house(year: int) -> None:
    cache = _load(CACHE_HOUSE)
    ykey = str(year)
    rolls = cache.get(ykey, {})
    n = max((int(k) for k in rolls), default=0)
    misses = 0
    t0 = time.time()
    while misses < 8:      # rolls are contiguous; 8 straight 404s = end of year
        n += 1
        key = str(n)
        if key in rolls:
            misses = 0
            continue
        raw = _get(f"https://clerk.house.gov/evs/{year}/roll{n:03d}.xml")
        time.sleep(SLEEP_S)
        if raw is None:
            misses += 1
            continue
        misses = 0
        try:
            root = ET.fromstring(raw)
            md = root.find("vote-metadata")
            g = lambda tag: (md.findtext(tag) or "").strip()
            at = md.find("action-time")
            rolls[key] = {
                "legis": g("legis-num"),
                "q": g("vote-question"),
                "desc": g("vote-desc"),
                "result": g("vote-result"),
                "date": g("action-date"),                       # e.g. 8-May-2024
                "time_et": (at.get("time-etz") if at is not None else "") or "",
                "totals": g("yea-count") or "",
            }
            tot = root.find(".//totals-by-vote")
            if tot is not None:
                rolls[key]["yea"] = (tot.findtext("yea-total") or "").strip()
                rolls[key]["nay"] = (tot.findtext("nay-total") or "").strip()
        except Exception as exc:
            rolls[key] = {"error": str(exc)}
        if n % 25 == 0:
            cache[ykey] = rolls
            _save(CACHE_HOUSE, cache)
            done = len([k for k in rolls if "error" not in rolls[k]])
            print(f"[house {year}] roll {n} cached={done} elapsed={time.time()-t0:.0f}s", flush=True)
    cache[ykey] = rolls
    _save(CACHE_HOUSE, cache)
    print(f"[house {year}] DONE: {len(rolls)} rolls", flush=True)


# ── Senate (via GovTrack mirror of LIS; senate.gov Akamai-403s scripts) ─

SENATE_SETS = [(118, "2024"), (119, "2025"), (119, "2026")]


def crawl_senate() -> None:
    cache = _load(CACHE_SENATE)
    menus = cache.setdefault("menus", {})
    for cong, sess in SENATE_SETS:
        mk = f"{cong}_{sess}"
        if mk in menus:
            continue
        votes, offset = [], 0
        while True:
            raw = _get(
                "https://www.govtrack.us/api/v2/vote?congress=%d&chamber=senate"
                "&session=%s&limit=300&offset=%d" % (cong, sess, offset))
            time.sleep(SLEEP_S)
            if raw is None:
                print(f"[senate] {mk} page offset={offset} FETCH FAILED", flush=True)
                break
            page = json.loads(raw)
            objs = page.get("objects", [])
            for v in objs:
                votes.append({
                    "num": v.get("number"),
                    "created_et": v.get("created"),      # ET per GovTrack/LIS
                    "question": v.get("question") or "",
                    "category": v.get("category") or "",
                    "result": v.get("result") or "",
                    "tally": f"{v.get('total_plus')}-{v.get('total_minus')}",
                })
            offset += len(objs)
            if offset >= page.get("meta", {}).get("total_count", 0) or not objs:
                break
        menus[mk] = votes
        print(f"[senate] {mk}: {len(votes)} votes", flush=True)
        _save(CACHE_SENATE, cache)
    _save(CACHE_SENATE, cache)


# ── Bill-title enrichment ──────────────────────────────────────────────
# Short titles hide keywords behind acronyms ("GENIUS Act" has no
# "stablecoin"). For every bill referenced by a vote, pull ALL its titles
# (official long title included) from GovTrack and filter over the union.

CACHE_BILLS = os.path.join(HERE, "W-P2_cache_bills.json")

BILL_RE = re.compile(
    r"\b(H\.? ?R\.?|S\.?|H\.? ?J\.? ?RES\.?|S\.? ?J\.? ?RES\.?|H\.? ?RES\.?"
    r"|H\.? ?CON\.? ?RES\.?|S\.? ?CON\.? ?RES\.?)\s*(\d{1,5})\b", re.IGNORECASE)

_TYPE_SLUG = {
    "HR": "house_bill", "S": "senate_bill",
    "HJRES": "house_joint_resolution", "SJRES": "senate_joint_resolution",
    "HRES": "house_resolution", "SRES": "senate_resolution",
    "HCONRES": "house_concurrent_resolution", "SCONRES": "senate_concurrent_resolution",
}


def _bill_keys(text: str, congress: int) -> list[str]:
    keys = []
    for typ, num in BILL_RE.findall(text or ""):
        slug = _TYPE_SLUG.get(re.sub(r"[^A-Z]", "", typ.upper()))
        if slug:
            keys.append(f"{congress}/{slug}/{int(num)}")
    return keys


def _congress_for(year: int) -> int:
    return 118 if year <= 2024 else 119


def enrich_bills() -> None:
    house = _load(CACHE_HOUSE)
    senate = _load(CACHE_SENATE)
    bills = _load(CACHE_BILLS)
    wanted: set[str] = set()
    for year, rolls in house.items():
        cong = _congress_for(int(year))
        for r in rolls.values():
            if "error" in r:
                continue
            wanted.update(_bill_keys(f"{r.get('legis','')} {r.get('desc','')}", cong))
    for mk, votes in senate.get("menus", {}).items():
        cong = int(mk.split("_")[0])
        for v in votes:
            wanted.update(_bill_keys(v.get("question", ""), cong))
    todo = sorted(k for k in wanted if k not in bills)
    print(f"[bills] unique referenced={len(wanted)} to_fetch={len(todo)}", flush=True)
    for i, key in enumerate(todo):
        cong, slug, num = key.split("/")
        raw = _get(f"https://www.govtrack.us/api/v2/bill?congress={cong}"
                   f"&bill_type={slug}&number={num}&fields=titles,title")
        time.sleep(SLEEP_S)
        titles: list[str] = []
        if raw is not None:
            try:
                objs = json.loads(raw).get("objects", [])
                if objs:
                    t = objs[0]
                    titles = [t.get("title") or ""]
                    for entry in (t.get("titles") or []):
                        # entries are [type, as, text] triples or dicts
                        if isinstance(entry, (list, tuple)) and len(entry) >= 3:
                            titles.append(str(entry[2]))
                        elif isinstance(entry, dict):
                            titles.append(str(entry.get("title") or ""))
            except Exception:
                titles = []
        bills[key] = " | ".join(x for x in titles if x)
        if (i + 1) % 50 == 0:
            _save(CACHE_BILLS, bills)
            print(f"[bills] {i+1}/{len(todo)}", flush=True)
    _save(CACHE_BILLS, bills)
    print(f"[bills] DONE: {len(bills)} cached", flush=True)


# ── Keyword filter (pre-committed in findings file) ────────────────────

TIER1 = re.compile(
    r"\b(digital assets?|digital commodit(?:y|ies)|cryptocurrenc(?:y|ies)|crypto"
    r"|blockchain|stablecoins?|bitcoin|ethereum|virtual currenc(?:y|ies)"
    r"|central bank digital currency|CBDC|SAB\s*121|tokens?"
    r"|Securities and Exchange Commission|Commodity Futures Trading Commission"
    r"|SEC|CFTC|custody|innovation and technology)\b",
    re.IGNORECASE)
TIER2 = re.compile(
    r"\b(financial innovation|fintech|financial technology|Bank Secrecy"
    r"|payment system)\b", re.IGNORECASE)
MINING = re.compile(r"\bmining\b", re.IGNORECASE)
MINING_CTX = re.compile(r"\b(digital|crypto|bitcoin|proof)\b", re.IGNORECASE)


def _match_tier(text: str) -> str | None:
    if TIER1.search(text):
        return "T1"
    if MINING.search(text) and MINING_CTX.search(text):
        return "T1"
    if TIER2.search(text):
        return "T2"
    return None


def house_ts_utc(date_s: str, time_et: str) -> str:
    """'8-May-2024' + '14:11' (ET) -> ISO UTC."""
    if not date_s:
        return ""
    try:
        d = datetime.strptime(date_s, "%d-%b-%Y")
        hh, mm = (time_et or "12:00").split(":")
        dt = d.replace(hour=int(hh), minute=int(mm), tzinfo=ET_TZ)
        return dt.astimezone(UTC).isoformat()
    except Exception:
        return ""


def senate_ts_utc(created_et: str) -> str:
    """GovTrack 'created' '2024-05-16T11:17:00' (ET) -> ISO UTC."""
    try:
        dt = datetime.fromisoformat(created_et).replace(tzinfo=ET_TZ)
        return dt.astimezone(UTC).isoformat()
    except Exception:
        return ""


def run_filter() -> None:
    house = _load(CACHE_HOUSE)
    senate = _load(CACHE_SENATE)
    bills = _load(CACHE_BILLS)

    def _bill_text(text: str, congress: int) -> str:
        return " | ".join(bills.get(k, "") for k in _bill_keys(text, congress))

    cands, n_scanned = [], 0
    for year, rolls in sorted(house.items()):
        cong = _congress_for(int(year))
        for num, r in sorted(rolls.items(), key=lambda kv: int(kv[0])):
            if "error" in r:
                continue
            n_scanned += 1
            base = " ".join([r.get("legis", ""), r.get("q", ""), r.get("desc", "")])
            text = base + " || " + _bill_text(base, cong)
            tier = _match_tier(text)
            if tier:
                cands.append({
                    "src": "house", "year": year, "roll": num, "tier": tier,
                    "legis": r.get("legis"), "q": r.get("q"), "desc": r.get("desc"),
                    "result": r.get("result"), "tally": f"{r.get('yea','')}-{r.get('nay','')}",
                    "ts_utc": house_ts_utc(r.get("date", ""), r.get("time_et", "")),
                })
    for mk, votes in senate.get("menus", {}).items():
        cong = int(mk.split("_")[0])
        for v in votes:
            n_scanned += 1
            base = v.get("question", "")
            text = base + " || " + _bill_text(base, cong)
            tier = _match_tier(text)
            if tier:
                cands.append({
                    "src": "senate", "menu": mk, "num": v["num"], "tier": tier,
                    "q": v.get("question"), "category": v.get("category"),
                    "result": v.get("result"), "tally": v.get("tally"),
                    "ts_utc": senate_ts_utc(v.get("created_et", "")),
                })
    _save(OUT_CANDIDATES, {"n_scanned": n_scanned, "candidates": cands})
    t1 = sum(1 for c in cands if c["tier"] == "T1")
    print(f"scanned={n_scanned} matches={len(cands)} (T1={t1} T2={len(cands)-t1})")
    for c in cands:
        label = c.get("legis") or c.get("issue")
        desc = (c.get("desc") or c.get("title") or "")[:70]
        print(f"  [{c['tier']}] {c['src']} {c.get('year', c.get('menu'))}/{c.get('roll', c.get('num'))} "
              f"{c['ts_utc'] or 'NO-TS'} {label} | {c.get('q','')[:40]} | {desc} | {c['result']} {c['tally']}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "senate":
        crawl_senate()
    elif cmd == "house":
        crawl_house(int(sys.argv[2]))
    elif cmd == "enrich":
        enrich_bills()
    elif cmd == "filter":
        run_filter()
    else:
        print(__doc__)
