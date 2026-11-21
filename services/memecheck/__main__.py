"""python -m services.memecheck <mint or url> [--json] [--save DIR]"""
import argparse
import datetime as dt
import json
import pathlib
import sys

from services.memecheck.memecheck import analyze, fetch, fetch_quote_usd, parse_mint, render


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="memecheck", description=__doc__)
    ap.add_argument("token", help="mint address or pump.fun / dexscreener URL")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    ap.add_argument("--save", metavar="DIR", help="also write the raw API responses here")
    a = ap.parse_args(argv)
    mint = parse_mint(a.token)
    dex, rug, graph = fetch(mint)
    if a.save:
        d = pathlib.Path(a.save)
        d.mkdir(parents=True, exist_ok=True)
        for n, blob in (("dex", dex), ("rug", rug), ("graph", graph)):
            (d / f"{mint[:8]}_{n}.json").write_text(json.dumps(blob))
    rep = analyze(mint, dex, rug, graph, dt.datetime.now(dt.timezone.utc),
                  quote_usd=fetch_quote_usd(dex))
    print(json.dumps(rep, indent=2, default=str) if a.json else render(rep))
    return 1 if rep["errors"] and "market" not in rep and "contract" not in rep else 0


if __name__ == "__main__":
    sys.exit(main())
