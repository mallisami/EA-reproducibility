#!/usr/bin/env python3
"""E39 stage-2 top-up list (pre-registered rule; deterministic; E39-A ONLY).

Rule: after stage 1, every E39-A async-condition cell (arm x condition x delta) with
      0.05 < p_hat(INVALID_EXECUTED, primary) < 0.95 (strict) is topped up to n = 93 scheduled trials.
      Undefined p_hat -> not topped up (listed). no_write / uid_rebind_control never; B-G never.
Input : results_E39.json from analyze_e39.py --stage 1 (completeness gate passed, experiments incl. A).
Output: stage2_cells_E39.json and .csv (the runner builds SCHEDULE_STAGE2.json from it; trial ids >= 100001).
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from analyze_e39 import topup_from_cells
from e39_common import ASYNC_CONDITIONS, N_TOPUP_TARGET, RESULTS_SCHEMA, TOPUP_HI, TOPUP_LO, sha256_file

TOPUP_SCHEMA = "e39.stage2_topup.v1"


def main(argv=None):
    ap = argparse.ArgumentParser(description="E39-A stage-2 top-up list")
    ap.add_argument("--results", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    res = json.loads(Path(a.results).read_text(encoding="utf-8"))
    if res.get("schema") != RESULTS_SCHEMA:
        print("REFUSED: not an e39 results file", file=sys.stderr)
        return 2
    if res.get("stage_analyzed") != 1:
        print("REFUSED: top-up must be computed from stage-1-only results", file=sys.stderr)
        return 2
    if res.get("completeness_gate", {}).get("passed") is not True or res.get("confirmatory") is not True:
        print("REFUSED: stage-1 results did not pass the completeness gate / strict design check", file=sys.stderr)
        return 2
    if "A" not in (res.get("experiments") or []):
        print("REFUSED: stage-1 results do not include E39-A", file=sys.stderr)
        return 2
    cells_a = [c for c in res["cells"] if c["cell"][0] == "A"]
    lst = topup_from_cells(cells_a)
    undefined = [c["label"] for c in cells_a if c["cell"][3] in ASYNC_CONDITIONS and c["stage1_primary"]["p_hat"] is None]
    doc = {"schema": TOPUP_SCHEMA, "run_id": res.get("run_id"),
           "rule": "E39-A async cells with %g < p_hat < %g after stage 1 -> top up to n=%d" % (TOPUP_LO, TOPUP_HI, N_TOPUP_TARGET),
           "source_results_sha256": sha256_file(a.results), "n_cells": len(lst),
           "n_new_trials": sum(r["n_topup"] for r in lst), "cells": lst, "undefined_cells": undefined}
    outd = Path(a.out)
    outd.mkdir(parents=True, exist_ok=True)
    (outd / "stage2_cells_E39.json").write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    cols = ["order", "arm", "condition", "delta_ms", "n_stage1_planned", "p_hat_stage1", "n_topup", "n_target"]
    with open(outd / "stage2_cells_E39.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, lineterminator="\n")
        w.writeheader()
        for r in lst:
            w.writerow({k: r.get(k, "") for k in cols})
    print("E39-A stage-2 top-up: %d cells, %d new trials" % (doc["n_cells"], doc["n_new_trials"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
