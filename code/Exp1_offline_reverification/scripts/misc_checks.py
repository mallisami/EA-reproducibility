#!/usr/bin/env python3
"""(1) Dev-matrix scenario-hash mismatch diagnosis; (2) E5 tallies orig vs patched regenerated."""
import os, json, glob, hashlib, itertools, csv
from collections import defaultdict
W = os.path.expanduser("~/dcji_reanalysis_20260926")
F8 = W + "/inputs/frozen/02_locked_methodology_v8.1.1/deterministic"
O = W + "/out/offline"


def sha(x):
    return hashlib.sha256(json.dumps(x, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


frozen = json.load(open(F8 + "/results/dev/deterministic_matrix.json"))
fh = {}
for r in frozen:
    fh.setdefault(r["scenario_id"], set()).add(r["hashes"]["scenario"])
out = {}
for p in sorted(glob.glob(F8 + "/scenarios/dev/*.json")):
    s = json.load(open(p))
    target = fh.get(s["scenario_id"], set())
    found = None
    keys = sorted(s)
    for r in (1, 2, 3):
        for combo in itertools.combinations(keys, r):
            t = {k: v for k, v in s.items() if k not in combo}
            if sha(t) in target:
                found = list(combo); break
        if found:
            break
    out[s["scenario_id"]] = {"current_sha_matches": sha(s) in target, "matches_if_keys_removed": found,
                             "policy_and_transition_hashes_match": None}
    rows = [r for r in frozen if r["scenario_id"] == s["scenario_id"]]
    out[s["scenario_id"]]["policy_and_transition_hashes_match"] = all(
        r["hashes"]["policy"] == sha(s["policy_profiles"][s["primary_policy"]]) and r["hashes"]["transition_model"] == sha(s["transitions"]) for r in rows)
print(json.dumps(out, indent=1))

# E5 tallies
tal = []
for v in ("orig", "patched"):
    e5 = json.load(open(f"{O}/regression_{v}/heldout/e5_defense_comparison.json"))
    agg = defaultdict(lambda: defaultdict(float))
    for r in e5:
        a = agg[(r["defense_id"], r["world"])]
        a["n"] += 1; a["unsafe"] += r["unsafe_commit"]; a["false_abort"] += r["false_abort"]
        a["abort"] += r["commit_decision"] == "ABORT_REPLAN"; a["cost"] += r["query_cost_units"] or 0
    for (d, w), a in sorted(agg.items()):
        tal.append({"core": v, "defense": d, "world": w, "n": int(a["n"]), "abort": int(a["abort"]), "unsafe": int(a["unsafe"]),
                    "false_abort": int(a["false_abort"]), "mean_query_cost": round(a["cost"] / a["n"], 4)})
with open(O + "/regression_e5_tallies.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(tal[0])); w.writeheader(); w.writerows(tal)
for t in tal:
    if t["core"] == "orig":
        print(t)
json.dump(out, open(O + "/dev_matrix_hash_diagnosis.json", "w"), indent=1)
