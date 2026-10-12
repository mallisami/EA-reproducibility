#!/usr/bin/env python3
"""
E37 physical-request verification: confirms, from the independently logged
per-request CSV (not from the trial-level summary), that DCJI issued exactly
3 HTTP GETs and FullRefresh exactly 6 on every single trial -- i.e. the
claimed request-count reduction is a directly observed fact, not an
inference from the planner's stated cost.
"""
import csv, json, sys
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parents[1]


def main():
    req_path = ROOT / "results" / "per_request_log.csv"
    raw_path = ROOT / "results" / "raw_trials.jsonl"

    per_trial = defaultdict(lambda: defaultdict(int))
    with open(req_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            per_trial[int(row["trial_id"])][row["guard"]] += 1

    protocol = json.loads((ROOT / "E37_PROTOCOL.json").read_text())
    expected_n = len(protocol["conditions"]) * len(protocol["acquisition_modes"]) * len(protocol["load_levels"]) * protocol["blocks"] * protocol["measured_pairs_per_block"]
    n = len(per_trial)
    dcji_mismatches = sum(1 for t, c in per_trial.items() if c.get("DCJI", 0) != 3)
    full_mismatches = sum(1 for t, c in per_trial.items() if c.get("FullRefresh", 0) != 6)

    rows = [json.loads(l) for l in open(raw_path)]
    discordant = sum(r["decision_discordant"] for r in rows)
    dcji_wrong = sum(not r["dcji_matches_expected"] for r in rows)
    full_wrong = sum(not r["full_matches_expected"] for r in rows)
    raw_ids = {int(r["trial_id"]) for r in rows}
    request_ids = set(per_trial)
    id_sets_match = raw_ids == request_ids

    result = {
        "expected_trials": expected_n,
        "trials_verified": n,
        "trials_with_dcji_request_count_not_3": dcji_mismatches,
        "trials_with_full_request_count_not_6": full_mismatches,
        "trial_id_sets_match_between_raw_and_request_log": id_sets_match,
        "request_count_claim_verified": n == expected_n and id_sets_match and dcji_mismatches == 0 and full_mismatches == 0,
        "total_trial_rows": len(rows),
        "decision_discordant_count": discordant,
        "dcji_decisions_not_matching_expected": dcji_wrong,
        "full_decisions_not_matching_expected": full_wrong,
        "safety_verified": len(rows) == expected_n and len(raw_ids) == expected_n and discordant == 0 and dcji_wrong == 0 and full_wrong == 0,
    }
    (ROOT / "results" / "verification.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    if not result["request_count_claim_verified"]:
        print("\nFAIL: request-count claim not verified by independent per-request log")
        sys.exit(1)
    if not result["safety_verified"]:
        print("\nWARNING: safety/decision-correctness issues found -- see counts above")


if __name__ == "__main__":
    main()
