#!/usr/bin/env python3
"""E38v2 stage-2 top-up list (pre-registered rule; deterministic).

Rule: after stage 1, every ASYNC-condition cell (arm x condition x delta) with
      0.05 < p_hat(INVALID_EXECUTED, primary oracle) < 0.95 (strict inequalities)
      is topped up to n = 93 scheduled trials (i.e. 93 - n_planned_stage1 new trials).
      p_hat undefined (no analyzable stage-1 trial) -> NOT topped up (inequality not satisfied);
      listed under "undefined_cells" for transparency.
      no_write and uid_rebind_control cells are never topped up.
Ordering: ARMS order, CONDITIONS order, delta ascending (fully deterministic).

Input : results JSON produced by analyze_e38v2.py --stage 1 (must have passed the completeness gate).
Output: stage2_cells_E38v2.json and .csv
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from e38v2_common import (ASYNC_CONDITIONS, N_TOPUP_TARGET, RESULTS_SCHEMA, TOPUP_HI, TOPUP_LO,
                          cell_sort_key, delta_label, norm_delta, sha256_file, topup_decision)

TOPUP_SCHEMA = "e38v2.stage2_topup.v1"


def topup_from_cells(cells):
    """cells: iterable of dicts with arm, condition, delta_ms, n_planned, p_hat (stage-1 values).
    Returns the ordered list of stage-2 cells."""
    out = []
    for c in cells:
        if c["condition"] not in ASYNC_CONDITIONS:
            continue
        if not topup_decision(c.get("p_hat")):
            continue
        n_add = N_TOPUP_TARGET - int(c["n_planned"])
        if n_add <= 0:
            continue
        out.append({"arm": c["arm"], "condition": c["condition"], "delta_ms": norm_delta(c["delta_ms"]),
                    "n_stage1_planned": int(c["n_planned"]), "p_hat_stage1": c.get("p_hat"),
                    "n_topup": n_add, "n_target": N_TOPUP_TARGET, "flag": ""})
    out.sort(key=lambda r: cell_sort_key((r["arm"], r["condition"], r["delta_ms"])))
    for i, r in enumerate(out):
        r["order"] = i
    return out


def undefined_cells(cells):
    """Async cells with undefined stage-1 p_hat (not topped up; reported)."""
    out = [{"arm": c["arm"], "condition": c["condition"], "delta_ms": norm_delta(c["delta_ms"])}
           for c in cells if c["condition"] in ASYNC_CONDITIONS and c.get("p_hat") is None]
    out.sort(key=lambda r: cell_sort_key((r["arm"], r["condition"], r["delta_ms"])))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="E38v2 stage-2 top-up list")
    ap.add_argument("--results", required=True, help="stage-1 results_E38v2.json")
    ap.add_argument("--out", required=True, help="output directory")
    a = ap.parse_args(argv)
    res = json.loads(Path(a.results).read_text(encoding="utf-8"))
    if res.get("schema") != RESULTS_SCHEMA:
        print("REFUSED: not an e38v2 results file", file=sys.stderr)
        return 2
    if res.get("stage_analyzed") != 1:
        print("REFUSED: top-up must be computed from stage-1-only results (stage_analyzed=1)", file=sys.stderr)
        return 2
    if res.get("completeness_gate", {}).get("passed") is not True:
        print("REFUSED: stage-1 results did not pass the completeness gate", file=sys.stderr)
        return 2
    cells = [{"arm": c["arm"], "condition": c["condition"], "delta_ms": c["delta_ms"],
              "n_planned": c["n_planned"], "p_hat": c["primary"]["p_hat"]} for c in res["cells"]]
    lst = topup_from_cells(cells)
    outd = Path(a.out)
    outd.mkdir(parents=True, exist_ok=True)
    doc = {"schema": TOPUP_SCHEMA, "run_id": res.get("run_id"),
           "rule": "async cells with %g < p_hat(INVALID_EXECUTED, primary) < %g after stage 1 -> top up to n=%d"
                   % (TOPUP_LO, TOPUP_HI, N_TOPUP_TARGET),
           "source_results_sha256": sha256_file(a.results),
           "n_cells": len(lst), "n_new_trials": sum(r["n_topup"] for r in lst), "cells": lst,
           "undefined_cells": undefined_cells(cells)}
    (outd / "stage2_cells_E38v2.json").write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    cols = ["order", "arm", "condition", "delta_ms", "n_stage1_planned", "p_hat_stage1", "n_topup", "n_target", "flag"]
    with open(outd / "stage2_cells_E38v2.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, lineterminator="\n")
        w.writeheader()
        for r in lst:
            rr = dict(r)
            rr["delta_ms"] = delta_label(r["delta_ms"])
            w.writerow({k: rr.get(k, "") for k in cols})
    print("stage-2 top-up: %d cells, %d new trials" % (doc["n_cells"], doc["n_new_trials"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
