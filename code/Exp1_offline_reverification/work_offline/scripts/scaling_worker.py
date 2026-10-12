#!/usr/bin/env python3
"""One isolated planning run (fresh process => clean peak-RSS measurement).
argv: core(orig|patched) scenario_json deadline max_subset_evals [as_limit_mb]"""
import sys, os, json, time, resource
W = os.path.expanduser("~/dcji_reanalysis_20260926")
core, path, deadline, cap = sys.argv[1], sys.argv[2], float(sys.argv[3]), int(sys.argv[4])
if len(sys.argv) > 5:
    lim = int(sys.argv[5]) * 2 ** 20
    resource.setrlimit(resource.RLIMIT_AS, (lim, lim))
sys.path.insert(0, W + "/work/offline/pkgs")
if core == "orig":
    from fc_orig.dcji import derive_critical_predicates
else:
    from fc_patched.dcji import derive_critical_predicates
s = json.load(open(path))
rss0 = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
t0 = time.perf_counter()
try:
    p = derive_critical_predicates(s, deadline, s["attacker_capability_tier"], s["primary_policy"], 100000, cap)
    err = None
except MemoryError:
    p = {}; err = "MemoryError"
dt = time.perf_counter() - t0
print(json.dumps({"core": core, "runtime_s": dt, "maxrss_kb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                  "baseline_rss_kb": rss0, "error": err, "status": p.get("status"), "expanded": p.get("expanded"),
                  "goal_count": p.get("goal_count"), "subset_evals": p.get("subset_evals"),
                  "candidate_query_count": len(p.get("candidate_query_ids") or []),
                  "critical_query_count": len(p.get("critical_query_ids") or []), "query_cost_units": p.get("query_cost_units"),
                  "full_refresh_rejects_all_goals": p.get("full_refresh_rejects_all_goals")}))
