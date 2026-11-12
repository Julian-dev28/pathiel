"""Online, read-only: what the public default network actually offers.

Three /info reads against Hyperliquid TESTNET only. No signing path is
imported here and no mainnet endpoint is touched.

    .venv/bin/python -m pytest -q -m online tests/test_testnet_online.py

Deselected by default (pyproject addopts).
"""
from __future__ import annotations

import pytest
import requests

from contracts.runtime import TESTNET_API_URL

pytestmark = pytest.mark.online

INFO = f"{TESTNET_API_URL}/info"


def _info(payload):
    r = requests.post(INFO, json=payload, timeout=(5, 15))
    r.raise_for_status()
    return r.json()


def test_testnet_lists_xyz():
    names = [d["name"] for d in _info({"type": "perpDexs"}) if isinstance(d, dict) and d.get("name")]
    assert "xyz" in names, names[:20]


def test_testnet_xyz_has_cl():
    meta = _info({"type": "meta", "dex": "xyz"})
    names = {u.get("name") for u in meta.get("universe", [])}
    assert "xyz:CL" in names, sorted(n for n in names if n)[:40]


def test_max_builder_fee_is_an_int():
    # Two throwaway addresses: nobody approved anything, the answer is 0.
    body = _info({"type": "maxBuilderFee", "user": "0x" + "12" * 20, "builder": "0x" + "34" * 20})
    assert type(body) is int and body >= 0
