#!/usr/bin/env python3
"""Standalone shadow runner for the external-alpha edges. Polls smart_money + basis_gap
every INTERVAL minutes, journals each fresh signal with the perp's price at signal time
to .external-alpha-shadow.jsonl. No AI, no live orders — pure live-mechanics validation
(does the wired module fire correctly, and do the signals' forward outcomes match the
backtest?). Score outcomes later by joining the logged price to the perp's price N hours
on. Run in the background; Ctrl-C to stop.
"""
import json
import os
import time

from hermes_trader.agents.config_store import read_agent_config
from hermes_trader.agents.external_alpha import external_alpha_signals
from hermes_trader.client.hl_client import fetch_all_mids

JOURNAL = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".external-alpha-shadow.jsonl")
INTERVAL_MIN = 10


def main():
    print(f"[shadow] external-alpha shadow runner — every {INTERVAL_MIN}min -> {JOURNAL}")
    while True:
        try:
            cfg = read_agent_config()
            sigs = external_alpha_signals(cfg)
            mids = {}
            try:
                mids = fetch_all_mids(include_hip3=True)
            except Exception:
                pass
            for s in sigs:
                rec = {"ts": int(time.time()), "coin": s["coin"], "side": s["side"],
                       "source": s["source"], "reason": s["reason"],
                       "strength": round(float(s.get("strength", 0)), 3),
                       "perp_px": float(mids.get(s["coin"], 0) or 0)}
                with open(JOURNAL, "a") as f:
                    f.write(json.dumps(rec) + "\n")
                print(f"[shadow] {s['source']:12s} {s['coin']:12s} {s['side']:5s} @ {rec['perp_px']} — {s['reason'][:60]}")
        except Exception as e:
            print(f"[shadow] cycle error: {e}")
        time.sleep(INTERVAL_MIN * 60)


if __name__ == "__main__":
    main()
