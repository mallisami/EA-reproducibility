#!/usr/bin/env python3
"""Compare oracle_e39 primary classes with the simulator's TRUTH.jsonl (synthetic runs only)."""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import oracle_e39 as orc  # noqa: E402
from e39_common import load_jsonl  # noqa: E402


def compare(run_dir):
    rd = Path(run_dir)
    raws, _ = load_jsonl(rd / "RAW_TRIALS.jsonl")
    au, _ = load_jsonl(rd / "audit.log")
    wh, _ = load_jsonl(rd / "webhook_log.jsonl")
    tr = {t["trial_id"]: t for t in load_jsonl(rd / "TRUTH.jsonl")[0]}
    rows = orc.run_oracle(raws, au, wh)
    bad = []
    for r in rows:
        t = tr[r["trial_id"]]
        if r["primary_class"] != t["truth_primary"]:
            bad.append((r["experiment"], r["arm_c"], r["condition_c"], r["primary_class"], r["primary_reason"], t["truth_primary"], t["outcome"]))
    return rows, bad


if __name__ == "__main__":
    rows, bad = compare(sys.argv[1])
    print("trials", len(rows), "mismatches", len(bad))
    print(Counter((r["experiment"], r["primary_class"]) for r in rows))
    for b in bad[:30]:
        print(b)
    sys.exit(1 if bad else 0)
