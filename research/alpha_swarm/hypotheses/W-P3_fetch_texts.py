#!/usr/bin/env python
"""W-P3: rebuild W-P1's 308-event set WITH accession numbers, then fetch and
cache each filing's primary-document text from EDGAR Archives.

Phases (argv; default all; caches idempotent — present entries are skipped):

  events       -> W-P3_cache_events.json        the 308 W-P1 events + accession
  primarydocs  -> W-P3_cache_primary_docs.json  accession -> primaryDocument
  texts        -> W-P3_cache_texts/<accession>.txt + _manifest.json

The event rebuild reuses W-P1's code (imported from W-P1_edgar_backtest.py)
and MUST reproduce W-P1_results.json:events_detail 1:1 on (ticker, acc_iso, r)
or it aborts — same 308 events, same returns, nothing rebuilt from network.

SEC throttle: ~2 req/s, research User-Agent. No HL calls in this script.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from html.parser import HTMLParser

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

_spec = importlib.util.spec_from_file_location(
    "wp1_backtest", os.path.join(HERE, "W-P1_edgar_backtest.py"))
wp1 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wp1)

FIL_CACHE = os.path.join(HERE, "W-P1_cache_filings.json")
HR_CACHE = os.path.join(HERE, "W-P1_cache_1h.json")
WP1_RESULTS = os.path.join(HERE, "W-P1_results.json")

EV_CACHE = os.path.join(HERE, "W-P3_cache_events.json")
PDOC_CACHE = os.path.join(HERE, "W-P3_cache_primary_docs.json")
TEXT_DIR = os.path.join(HERE, "W-P3_cache_texts")
MANIFEST = os.path.join(TEXT_DIR, "_manifest.json")

SEC_UA = {"User-Agent": "hermes-research team.recoin@gmail.com"}
SEC_SLEEP = 0.5           # ~2 req/s
HOUR_MS = 3_600_000
TEXT_CAP = 60_000         # chars kept on disk (prompt truncation happens later)


def _load(path, default):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default


def _save(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=1)
    os.replace(tmp, path)


# ── phase: events — W-P1's exact construction, + accession ───────────────────

def build_events():
    fils = json.load(open(FIL_CACHE))
    bars = json.load(open(HR_CACHE))
    tz = wp1.tz_check(fils)
    print(f"tz check: {tz['verdict']}")
    coins = {c: wp1.Coin(c, rows) for c, rows in bars.items() if rows}

    events = []
    for tick, rec in sorted(fils.items()):
        coin = f"xyz:{tick}"
        co = coins.get(coin)
        if co is None or len(co.t) < 48:
            continue
        seen_entry_bars = set()
        for f in sorted(rec["filings"], key=lambda x: x["acceptanceDateTime"]):
            if not f["acceptanceDateTime"]:
                continue
            acc = wp1.acc_to_utc_ms(f["acceptanceDateTime"], tz["verdict"])
            if acc < co.t[0] or acc > co.t[-1] - 25 * HOUR_MS:
                continue
            i = co.idx_at_or_after(acc)
            if i is None:
                continue
            gap = co.t[i] - acc
            if gap > wp1.ENTRY_GAP_MAX_MS:
                continue
            if i in seen_entry_bars:
                continue
            seen_entry_bars.add(i)
            events.append({
                "ticker": tick, "coin": coin, "cik": rec["cik"],
                "company": rec["name"], "form": f["form"],
                "items": f.get("items", ""),
                "accession": f["accessionNumber"],
                "filingDate": f["filingDate"],
                "acc_ms": acc,
                "acc_iso": datetime.fromtimestamp(
                    acc / 1000, tz=timezone.utc).isoformat(),
                "mkt_hours": wp1.is_market_hours(acc),
                "i_entry": i, "entry_gap_h": gap / HOUR_MS,
                "r": {h: co.ret(i, k) for h, k in wp1.HORIZONS.items()},
            })
    events.sort(key=lambda e: e["acc_ms"])

    # ── 1:1 verification against W-P1's published event table ──
    ref = json.load(open(WP1_RESULTS))["events_detail"]
    assert len(events) == len(ref) == 308, \
        f"event count mismatch: rebuilt {len(events)} vs W-P1 {len(ref)}"
    for e, r_ in zip(events, ref):
        assert e["ticker"] == r_["ticker"] and e["acc_iso"] == r_["acc_iso"], \
            f"event mismatch: {e['ticker']}@{e['acc_iso']} vs {r_['ticker']}@{r_['acc_iso']}"
        for h in ("1h", "4h", "24h"):
            a, b = e["r"][h], r_["r"][h]
            assert (a is None and b is None) or abs(a - b) < 1e-12, \
                f"return mismatch {e['ticker']}@{e['acc_iso']} {h}: {a} vs {b}"
    _save(EV_CACHE, events)
    print(f"events: {len(events)} rebuilt, verified 1:1 vs W-P1 -> {EV_CACHE}")


# ── phase: primarydocs — accession -> primaryDocument filename ───────────────

def fetch_primary_docs():
    events = _load(EV_CACHE, None)
    if events is None:
        raise SystemExit("run `events` phase first")
    cache = _load(PDOC_CACHE, {})
    need = {e["accession"] for e in events} - set(cache)
    ciks = sorted({(e["cik"], e["ticker"]) for e in events
                   if e["accession"] in need})
    print(f"primarydocs: {len(need)} accessions missing, {len(ciks)} CIKs to fetch")
    for cik, tick in ciks:
        url = f"https://data.sec.gov/submissions/CIK{cik:010d}.json"
        try:
            r = requests.get(url, headers=SEC_UA, timeout=30)
            r.raise_for_status()
            rec = r.json().get("filings", {}).get("recent", {})
        except Exception as exc:
            print(f"  {tick}: submissions FAIL {exc}")
            time.sleep(SEC_SLEEP)
            continue
        accs = rec.get("accessionNumber", [])
        docs = rec.get("primaryDocument", [""] * len(accs))
        for a, d in zip(accs, docs):
            if a in need:
                cache[a] = d
        _save(PDOC_CACHE, cache)
        time.sleep(SEC_SLEEP)
    # fallback: aged out of `recent` -> archive folder index.json
    still = {e["accession"]: e for e in events}
    missing = [a for a in still if a not in cache or not cache[a]]
    for a in missing:
        e = still[a]
        nod = a.replace("-", "")
        url = f"https://www.sec.gov/Archives/edgar/data/{e['cik']}/{nod}/index.json"
        try:
            r = requests.get(url, headers=SEC_UA, timeout=30)
            r.raise_for_status()
            items = r.json().get("directory", {}).get("item", [])
            htms = [it["name"] for it in items
                    if it["name"].lower().endswith((".htm", ".html"))
                    and "index" not in it["name"].lower()]
            if htms:
                cache[a] = htms[0]
                print(f"  fallback index.json {e['ticker']} {a} -> {htms[0]}")
        except Exception as exc:
            print(f"  fallback FAIL {a}: {exc}")
        time.sleep(SEC_SLEEP)
        _save(PDOC_CACHE, cache)
    have = sum(1 for e in events if cache.get(e["accession"]))
    print(f"primarydocs: {have}/{len(events)} events mapped -> {PDOC_CACHE}")


# ── phase: texts ─────────────────────────────────────────────────────────────

class _Text(HTMLParser):
    _SKIP = {"script", "style"}
    _BLOCK = {"p", "div", "tr", "br", "table", "li", "h1", "h2", "h3", "h4"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        t = tag.split(":")[-1]
        if t in self._SKIP:
            self._skip += 1
        elif t in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        t = tag.split(":")[-1]
        if t in self._SKIP and self._skip:
            self._skip -= 1
        elif t in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    p = _Text()
    try:
        p.feed(html)
        p.close()
    except Exception:
        pass
    txt = "".join(p.parts)
    txt = txt.replace("\xa0", " ")
    txt = re.sub(r"[ \t]+", " ", txt)
    txt = re.sub(r" ?\n ?", "\n", txt)
    txt = re.sub(r"\n{3,}", "\n\n", txt)
    return txt.strip()


def fetch_texts():
    events = _load(EV_CACHE, None)
    pdocs = _load(PDOC_CACHE, None)
    if events is None or pdocs is None:
        raise SystemExit("run `events` + `primarydocs` phases first")
    os.makedirs(TEXT_DIR, exist_ok=True)
    manifest = _load(MANIFEST, {})
    todo = [e for e in events
            if e["accession"] not in manifest
            or not manifest[e["accession"]].get("ok")]
    print(f"texts: {len(todo)} to fetch ({len(events) - len(todo)} cached)")
    for k, e in enumerate(todo):
        a = e["accession"]
        doc = pdocs.get(a)
        nod = a.replace("-", "")
        if not doc:
            manifest[a] = {"ok": False, "err": "no primaryDocument mapped"}
            _save(MANIFEST, manifest)
            continue
        url = f"https://www.sec.gov/Archives/edgar/data/{e['cik']}/{nod}/{doc}"
        try:
            r = requests.get(url, headers=SEC_UA, timeout=30)
            r.raise_for_status()
            raw = r.text
        except Exception as exc:
            manifest[a] = {"ok": False, "err": str(exc), "url": url}
            print(f"  [{k+1}/{len(todo)}] {e['ticker']} {a}: FAIL {exc}")
            _save(MANIFEST, manifest)
            time.sleep(SEC_SLEEP)
            continue
        txt = html_to_text(raw) if "<" in raw[:2000] else raw
        txt = txt[:TEXT_CAP]
        with open(os.path.join(TEXT_DIR, f"{a}.txt"), "w") as f:
            f.write(txt)
        manifest[a] = {"ok": True, "url": url, "doc": doc,
                       "raw_len": len(raw), "text_len": len(txt)}
        if (k + 1) % 25 == 0 or k + 1 == len(todo):
            print(f"  [{k+1}/{len(todo)}] {e['ticker']} {a}: {len(txt)} chars")
        _save(MANIFEST, manifest)
        time.sleep(SEC_SLEEP)
    ok = sum(1 for a in manifest.values() if a.get("ok"))
    print(f"texts: {ok}/{len({e['accession'] for e in events})} cached -> {TEXT_DIR}")


if __name__ == "__main__":
    phases = sys.argv[1:] or ["events", "primarydocs", "texts"]
    for ph in phases:
        {"events": build_events, "primarydocs": fetch_primary_docs,
         "texts": fetch_texts}[ph]()
