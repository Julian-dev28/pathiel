"""Gate tests: fixtures are HUSS (3umHFk...pump) as fetched 2026-11-20 ~17:00 UTC."""
import copy
import datetime as dt
import json
import pathlib

import pytest

from services.memecheck.memecheck import analyze, parse_mint, quote_usd_from, render

FX = pathlib.Path(__file__).parent / "fixtures"
MINT = "3umHFkiMHyphCkLxtbf8Aabie8r5hxn86QeBAovTpump"
NOW = dt.datetime(2026, 11, 20, 17, 0, 19, tzinfo=dt.timezone.utc)


@pytest.fixture
def blobs():
    return tuple(json.loads((FX / f"huss_{n}.json").read_text()) for n in ("dex", "rug", "graph"))


def flags(rep):
    return {k for k, _ in rep["flags"]}


def test_parse_mint_from_url_and_bare():
    assert parse_mint(f"https://pump.fun/coin/{MINT}") == MINT
    assert parse_mint(f"https://pump.fun/coin/{MINT}/") == MINT
    assert parse_mint(MINT) == MINT
    with pytest.raises(ValueError):
        parse_mint("https://pump.fun/")


def test_huss_known_red_flags(blobs):
    rep = analyze(MINT, *blobs, NOW)
    assert rep["name"] == "HUSS"
    # The five things the hand analysis found, now asserted.
    assert {"fast_graduation", "creator_out", "split_buy", "insider_networks",
            "thin_liquidity", "sellers_match_buyers", "no_description", "no_website"} <= flags(rep)
    # Clean contract: none of the contract flags fire.
    assert not flags(rep) & {"mint_authority", "freeze_authority", "mutable_metadata",
                             "token_extension", "rugged", "lp_unlocked", "concentrated", "whale"}
    m = rep["market"]
    assert m["venue"] == "pumpswap"        # deepest non-curve pool, not the $8 meteora one
    assert m["graduated_s"] == 64
    assert m["age_min"] == 50
    assert m["liq_pct"] == 18.9


def test_huss_holders_exclude_pool_and_burn(blobs):
    h = analyze(MINT, *blobs, NOW)["holders"]
    assert h["top10_pct"] == 16.86        # pool (9.27%) and burn (1.73%) excluded
    assert h["top1_pct"] == 2.41
    assert h["burned_pct"] == 1.73


def test_split_buy_detects_identical_holdings(blobs):
    sb = analyze(MINT, *blobs, NOW)["insiders"]["split_buys"]
    assert sb == [{"wallets": 2, "tokens": 4603538.385685}]


def test_contract_flags_fire_when_authorities_present(blobs):
    dex, rug, graph = copy.deepcopy(blobs)
    rug["token"]["mintAuthority"] = "X" * 32
    rug["token"]["freezeAuthority"] = "Y" * 32
    rug["tokenMeta"]["mutable"] = True
    rug["markets"][0]["lp"]["lpLockedPct"] = 0
    f = flags(analyze(MINT, dex, rug, graph, NOW))
    assert {"mint_authority", "freeze_authority", "mutable_metadata", "lp_unlocked"} <= f


def test_whale_and_concentration(blobs):
    dex, rug, graph = copy.deepcopy(blobs)
    rug["topHolders"][1]["pct"] = 25.0
    f = flags(analyze(MINT, dex, rug, graph, NOW))
    assert {"whale", "concentrated"} <= f


def test_dead_sources_report_errors_not_crash(blobs):
    err = {"_error": "ConnectError: boom"}
    rep = analyze(MINT, err, err, err, NOW)
    assert len(rep["errors"]) == 2 and "market" not in rep
    assert "ERROR dexscreener" in render(rep)
    # rugcheck down, dex up: market still reported
    rep = analyze(MINT, blobs[0], err, err, NOW)
    assert rep["market"]["mc"] == 141160 and "contract" not in rep


def test_render_lists_flags(blobs):
    out = render(analyze(MINT, *blobs, NOW))
    assert "RED FLAGS" in out and "fast_graduation" in out and "64s" in out


FRIES = "8ugj3KW6utdRDLwmdkWTRiud5ycZ558D1f1ReA8xpump"
FRIES_NOW = dt.datetime(2026, 11, 20, 17, 34, 1, tzinfo=dt.timezone.utc)


@pytest.fixture
def fries():
    return tuple(json.loads((FX / f"fries_{n}.json").read_text()) for n in ("dex", "rug", "graph"))


def test_quote_usd_takes_deepest_priced_pair():
    sqqq = json.loads((FX / "sqqq_dex.json").read_text())
    assert quote_usd_from(sqqq) == 54.93          # $114k USDC pool, not the $1 meteora one
    assert quote_usd_from({"pairs": []}) is None


def test_curve_quoted_in_tokenized_stock_gets_usd_mc(fries):
    # Regression: DexScreener leaves a curve quoted in SQQQ unpriced; the first
    # version printed "mc $0" and missed that it trades against a tokenized ETF.
    rep = analyze(FRIES, *fries, FRIES_NOW, quote_usd=54.93)
    m = rep["market"]
    assert m["on_curve"] and m["quote"] == "SQQQ"
    assert m["mc"] == pytest.approx(0.0000002254 * 54.93 * 1e9)   # ~ $12.4k
    assert "exotic_quote" in flags(rep) and "no_usd_price" not in flags(rep)
    assert "mc $12,381" in render(rep) and "(bonding curve)" in render(rep)


def test_unpriced_curve_says_so_instead_of_zero(fries):
    rep = analyze(FRIES, *fries, FRIES_NOW)          # quote price lookup failed
    assert rep["market"]["mc"] is None and "no_usd_price" in flags(rep)
    assert "mc n/a" in render(rep)


def test_sol_quoted_pair_has_no_exotic_flag(blobs):
    assert "exotic_quote" not in flags(analyze(MINT, *blobs, NOW))


@pytest.mark.online
def test_live_fetch_shape():
    from services.memecheck.memecheck import fetch
    dex, rug, graph = fetch(MINT)
    assert "pairs" in dex and "token" in rug
