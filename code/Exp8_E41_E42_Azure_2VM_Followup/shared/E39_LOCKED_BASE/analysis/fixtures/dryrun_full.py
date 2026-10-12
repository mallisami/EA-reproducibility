#!/usr/bin/env python3
"""Offline dry run of the complete E39 analysis on a FULL-design synthetic run (all sub-experiments, strict
design check, B = 2000 bootstrap replicates) + independent recomputation. Reports wall-clock times.
Usage: dryrun_full.py --out DIR [--reps 2000]"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import analyze_e39 as an  # noqa: E402
import e39_testkit as kit  # noqa: E402
import recompute_independent_e39 as ind  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--reps", type=int, default=2000)
    a = ap.parse_args(argv)
    t0 = time.time()
    p = kit.make_run(a.out, list("ABCDEFG"), mini=False)
    t1 = time.time()
    print("synthetic full design: %d trials, %d audit events (%.1f s)" % (len(p["raws"]), len(p["audit"]), t1 - t0))
    out = Path(a.out) / "analysis"
    rc = an.main(kit.analyze_args(p, out, "--w-external", "A2=2.5", design="strict", reps=a.reps))
    t2 = time.time()
    print("analyze_e39 rc=%d (%.1f s)" % (rc, t2 - t1))
    rc2 = ind.main(["--raw", str(p["run"] / "RAW_TRIALS.jsonl"), "--audit", str(p["run"] / "audit.log"),
                    "--schedule", str(p["run"] / "SCHEDULE.json"), "--compare", str(out / "results_E39.json"),
                    "--out", str(out / "independent_E39.json")])
    print("independent rc=%d (%.1f s)" % (rc2, time.time() - t2))
    return rc or rc2


if __name__ == "__main__":
    sys.exit(main())
