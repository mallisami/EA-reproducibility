#!/usr/bin/env python3
"""Task 7: re-created E12 / E12b / E12c scaling harness (no original script exists in the
package; only results/e12*_scalability.json, the frozen scenario files e12_scenarios/ and
e12bc_scenarios/, and the POSTRUN records survive).

Definitions reproduced from the frozen artefacts:
  E12  : e12_scenarios/E12_S{10,25,50,100,250,500}.json, deadline = frozen row deadline_s,
         metric = derive_critical_predicates runtime (frozen field planning_runtime_s).
  E12b : e12bc_scenarios/E12B_D{3..9}.json, deadline = W2 commit deadline.
  E12c : e12bc_scenarios/E12C_Q{2..16}.json, deadline = W2 commit deadline;
         extended here to n=20,24 by adding bases e12c_q17.. to a copy of E12C_Q16.
Caps: max_expansions=100000, max_subset_evals=100000 (frozen), plus cap=1000 for n=20/24.
Each run is a fresh subprocess (scaling_worker.py) so peak RSS is attributable.
Semantic fields of replicate runs are checked against frozen replicate-1 rows.
"""
import os, sys, json, glob, copy, subprocess, statistics, csv, platform

W = os.path.expanduser("~/dcji_reanalysis_20260926")
F8 = W + "/inputs/frozen/02_locked_methodology_v8.1.1/deterministic"
OUT = W + "/out/offline/t7_scaling"
GEN = W + "/work/offline/e12_generated"
os.makedirs(OUT, exist_ok=True); os.makedirs(GEN, exist_ok=True)
PY = W + "/venv/bin/python"
WORKER = W + "/work/offline/scripts/scaling_worker.py"
REPS = 5
SEM = ["status", "expanded", "goal_count", "subset_evals", "candidate_query_count", "critical_query_count", "query_cost_units"]


def run(core, path, deadline, cap, aslimit=None, timeout=900):
    cmd = ["nice", "-n", "10", PY, WORKER, core, path, str(deadline), str(cap)] + ([str(aslimit)] if aslimit else [])
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"core": core, "error": f"timeout>{timeout}s"}
    if out.returncode != 0:
        return {"core": core, "error": "rc=%d %s" % (out.returncode, out.stderr.strip().splitlines()[-1] if out.stderr.strip() else "")}
    return json.loads(out.stdout.strip().splitlines()[-1])


frozen = {"E12": json.load(open(F8 + "/results/e12_scalability.json")),
          "E12b": json.load(open(F8 + "/results/e12b_branching_scalability.json")),
          "E12c": json.load(open(F8 + "/results/e12c_query_scalability.json"))}


def frozen_ref(exp, key, val):
    rows = [r for r in frozen[exp] if r.get(key) == val]
    return rows


def frozen_median(rows):
    k = "planning_runtime_s" if "planning_runtime_s" in rows[0] else "runtime_s"
    return statistics.median(r[k] for r in rows)


configs = []
for n in (10, 25, 50, 100, 250, 500):
    rows = frozen_ref("E12", "state_target", n)
    configs.append(("E12", n, F8 + f"/e12_scenarios/E12_S{n}.json", rows[0]["deadline_s"], rows))
for d in range(3, 10):
    p = F8 + f"/e12bc_scenarios/E12B_D{d}.json"
    configs.append(("E12b", d, p, json.load(open(p))["worlds"]["W2"]["commit_deadline_s"], frozen_ref("E12b", "depth", d)))
for q in (2, 4, 6, 8, 10, 12, 14, 16):
    p = F8 + f"/e12bc_scenarios/E12C_Q{q}.json"
    configs.append(("E12c", q, p, json.load(open(p))["worlds"]["W2"]["commit_deadline_s"], frozen_ref("E12c", "query_count", q)))

# extended E12c instances (generated, NOT frozen)
base = json.load(open(F8 + "/e12bc_scenarios/E12C_Q16.json"))
for n in (20, 24):
    s = copy.deepcopy(base)
    s["scenario_id"] = f"E12C_Q{n}_OFFLINE"
    for i in range(17, n + 1):
        nm = f"e12c_q{i}"
        s["initial_state"]["facts"][nm] = True
        s["predicates"][nm] = {"path": f"/facts/{nm}", "op": "eq", "value": True, "evidence_class": "L",
                               "description": f"Synthetic independent live basis {i} (offline extension)."}
        s["policy_profiles"]["balanced"]["justification"]["any"].append({"predicate": nm})
        s["transitions"][0]["effects"].append({"op": "set", "path": f"/facts/{nm}", "value": False})
        q = f"query:/facts/{nm}"
        s["refresh_model"]["query_costs"][q] = 1.0
        s["refresh_model"]["predicate_query_ids"][nm] = q
        s["refresh_model"]["query_observed_at_s"][q] = 0.0
    assert len(s["policy_profiles"]["balanced"]["justification"]["any"]) == n
    p = f"{GEN}/E12C_Q{n}_OFFLINE.json"
    json.dump(s, open(p, "w"), indent=1)
    configs.append(("E12c_ext", n, p, s["worlds"]["W2"]["commit_deadline_s"], []))

rows = []
for exp, x, path, dl, fr in configs:
    for core in ("orig", "patched"):
        caps = [100000] + ([1000] if exp == "E12c_ext" else [])
        for cap in caps:
            reps = REPS
            aslimit = None
            if exp == "E12c_ext" and core == "orig":
                reps = 1 if x == 24 else REPS
                aslimit = 5120 if x == 24 else None   # 5 GiB address-space cap protects the shared VM
            res = [run(core, path, dl, cap, aslimit) for _ in range(reps)]
            ok = [r for r in res if not r.get("error")]
            sem_match = None
            if fr and ok:
                ref = fr[0]
                sem_match = all(all(r.get(k) == ref.get(k) for k in SEM if k in ref and k != "expanded") and
                                r.get("expanded") == ref.get("expanded", ref.get("expanded_states")) for r in ok)
            row = {"experiment": exp, "size": x, "core": core, "cap": cap, "deadline_s": dl, "reps": reps,
                   "errors": sorted({r["error"] for r in res if r.get("error")}),
                   "median_runtime_s": statistics.median(r["runtime_s"] for r in ok) if ok else None,
                   "median_maxrss_mb": round(statistics.median(r["maxrss_kb"] for r in ok) / 1024, 1) if ok else None,
                   "frozen_median_runtime_s": frozen_median(fr) if fr else None,
                   "semantic_fields_match_frozen": sem_match}
            if ok:
                for k in SEM + ["full_refresh_rejects_all_goals"]:
                    row[k] = ok[0].get(k)
            rows.append(row)
            print(json.dumps(row), flush=True)

hw = {"nproc": os.cpu_count(), "platform": platform.platform(), "python": platform.python_version()}
try:
    hw["cpu_model"] = [l.split(":", 1)[1].strip() for l in open("/proc/cpuinfo") if l.startswith("model name")][0]
    hw["mem_total_kb"] = [l.split()[1] for l in open("/proc/meminfo") if l.startswith("MemTotal")][0]
except Exception:
    pass
json.dump({"hardware": hw, "rows": rows}, open(OUT + "/t7_scaling.json", "w"), indent=1)
keys = []
for r in rows:
    for k in r:
        if k not in keys:
            keys.append(k)
with open(OUT + "/t7_scaling.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=keys); w.writeheader()
    for r in rows:
        w.writerow({k: json.dumps(v) if isinstance(v, list) else v for k, v in r.items()})
print(json.dumps(hw))
