#!/usr/bin/env python3
"""C7: does float summation over a set change certificates when costs are non-dyadic?
Compares locked (fc_orig) vs patched (fc_patched, exact Fractions) derive_critical_predicates on
seeded random scenarios with costs drawn from {0.1,0.2,0.3,0.7} and from dyadic {0.5,1,2,3}."""
import os, sys, json, random
from fractions import Fraction
W = os.path.expanduser("~/dcji_reanalysis_20260926")
sys.path.insert(0, W + "/work/offline/pkgs")
sys.path.insert(0, W + "/work/offline/deterministic_patched/tests")
sys.path.insert(0, W + "/work/offline/deterministic_patched")
import fc_orig.dcji as Od, fc_patched.dcji as Pd
import fc_orig.expr as Ox
import test_offline_patches as T   # reuse the random scenario generator (imports flashir_core from patched copy)

res = {}
for label, pool in (("dyadic", [0.5, 1.0, 2.0, 3.0]), ("non_dyadic", [0.1, 0.2, 0.3, 0.7])):
    rng = random.Random(4242)
    n = diff = diff_cost = diff_ident = 0
    examples = []
    for _ in range(3000):
        s = T.random_scenario(rng, pool)
        if not Ox.commit_components(s, s["initial_state"], "balanced")["valid"]:
            continue
        o = Od.derive_critical_predicates(s, 3.0, "C1", "balanced")
        p = Pd.derive_critical_predicates(s, 3.0, "C1", "balanced")
        n += 1
        ko = (o["status"], o["critical_query_ids"], o["query_cost_units"], o["subset_evals"])
        kp = (p["status"], p["critical_query_ids"], p["query_cost_units"], p["subset_evals"])
        if ko != kp:
            diff += 1
            if o["query_cost_units"] != p["query_cost_units"]:
                diff_cost += 1
            if o["critical_query_ids"] != p["critical_query_ids"]:
                diff_ident += 1
            if len(examples) < 5:
                examples.append({"orig": ko, "patched": kp, "costs": s["refresh_model"]["query_costs"]})
    res[label] = {"scenarios": n, "differ": diff, "cost_value_differs": diff_cost, "certificate_identity_differs": diff_ident,
                  "examples": examples}
print(json.dumps(res, indent=1, default=str))
os.makedirs(W + "/out/offline/tests", exist_ok=True)
json.dump(res, open(W + "/out/offline/tests/c7_float_vs_exact.json", "w"), indent=1, default=str)
