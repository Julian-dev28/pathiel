#!/usr/bin/env python3
"""Run the A/B replay over a RANDOM 100-prompt sample (owner request 2026-10-11).

Monkeypatches llm_ab_replay.COHORT to scratch/_ab_cohort_sample100.jsonl so the
resumable replay/audit/score steps all operate on the same sample. Results files
are the normal _ab_<arm>_results.jsonl (keyed by perception_id, resumable).
"""
import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import llm_ab_replay as ab  # noqa: E402

ab.COHORT = REPO / "scratch" / "_ab_cohort_sample100.jsonl"

ap = argparse.ArgumentParser()
ap.add_argument("--arms", required=True, help="comma list")
ap.add_argument("--concurrency", type=int, default=2)
args = ap.parse_args()

for arm in args.arms.split(","):
    ns = argparse.Namespace(arm=arm, limit=0, concurrency=args.concurrency)
    print(f"\n===== SAMPLE100 arm={arm} =====", flush=True)
    ab.cmd_replay(ns)
    ab.cmd_audit(argparse.Namespace(arm=arm))
