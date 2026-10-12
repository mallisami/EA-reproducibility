#!/usr/bin/env python3
"""Task 6: TTLGuard (B2) re-evaluation with staggered cache ages vs DCJI (B6) and FullRefresh (B4).

Locked data: every query_observed_at_s = 0 and W2/W3 commit at 3-5 s / 0.25-0.4 s, so with the
frozen 2 s TTL every query is stale at W2 (TTL == FullRefresh) and fresh at W3.

Variant A ("pre-snapshot"): cache contents = initial_state (the locked cache model), but each
  query's reading was taken age_q ~ U(0,10) s BEFORE t=0: observed_at_s = -age_q.
Variant B ("in-window"): age_q ~ U(0,10) s measured back from the commit time; the cached value
  of query q is the world state at t_q = max(0, deadline - age_q) along that world's transition
  path (so a recently observed query may already reflect attacker writes). All defenses (B0, B2,
  B4, B6) see this same cache; B6's certificate is still planned from initial_state.
Seeds 0..SEEDS-1; thresholds {0.5,1,2,5,10}; held-out P1 specs (P1-P4 identical), worlds W0-W3.
"""
import os, sys, json, glob, copy, random, csv
from collections import defaultdict

W = os.path.expanduser("~/dcji_reanalysis_20260926")
sys.path.insert(0, W + "/work/offline/pkgs")
import fc_orig.defenses as Of, fc_orig.engine as Oe, fc_orig.expr as Ox, fc_orig.refresh as Or, fc_orig.dcji as Od
import fc_patched.defenses as Pf

F8 = W + "/inputs/frozen/02_locked_methodology_v8.1.1/deterministic"
OUT = W + "/out/offline/t6_ttl"
os.makedirs(OUT, exist_ok=True)
SEEDS = 50
THR = [0.5, 1.0, 2.0, 5.0, 10.0]

specs = {}
for p in sorted(glob.glob(F8 + "/instances/heldout/*_P1.json")):
    wr = json.load(open(p))
    specs[wr["template_id"]] = wr["scenario"]

rows = []
for sid, s in sorted(specs.items()):
    prof = s["primary_policy"]; cap = s["attacker_capability_tier"]
    rq = sorted(Or.refreshable_query_ids(s, prof))
    qpaths = {q: sorted({s["predicates"][p]["path"] for p in Or.material_predicates(s, prof)
                         if Or.query_id_for_predicate(s, p) == q}) for q in rq}
    for wn in ("W0", "W1", "W2", "W3"):
        w = s["worlds"][wn]; dl = w["commit_deadline_s"]
        cs = Oe.replay_path(s, w["transition_path"], dl)["state"]
        truth = Ox.commit_components(s, cs, prof)
        plan_o = Od.derive_critical_predicates(s, dl, cap, prof)
        for seed in range(SEEDS):
            rng = random.Random(f"{sid}|{wn}|{seed}")
            ages = {q: rng.uniform(0, 10) for q in rq}
            for variant in ("A_pre_snapshot", "B_in_window"):
                s2 = copy.deepcopy(s)
                if variant == "A_pre_snapshot":
                    s2["refresh_model"]["query_observed_at_s"].update({q: -ages[q] for q in rq})
                    cache = copy.deepcopy(s["initial_state"])
                else:
                    cache = copy.deepcopy(s["initial_state"])
                    for q in rq:
                        tq = max(0.0, dl - ages[q])
                        s2["refresh_model"]["query_observed_at_s"][q] = dl - ages[q]
                        st_q = Oe.replay_path(s, w["transition_path"], tq)["state"]
                        for path in qpaths[q]:
                            Ox.pointer_set(cache, path, copy.deepcopy(Ox.pointer_get(st_q, path)))
                b0 = "EXECUTE" if Ox.commit_components(s2, cache, prof)["valid"] else "ABORT_REPLAN"   # cached-only check
                b4 = Of.B4_fullrefresh(s2, cache, cs, prof)
                b6 = Of.B6_dcji_gate(s2, cache, cs, prof, deadline_s=dl, critical_plan=plan_o)
                for thr in THR:
                    b2 = Of.B2_ttlguard(s2, cache, cs, prof, ttl_s=thr)
                    rows.append({"spec": sid, "family": s["family"], "world": wn, "deadline_s": dl, "seed": seed,
                                 "variant": variant, "ttl_s": thr, "truth_valid": truth["valid"], "attack_goal": truth["attack_goal"],
                                 "B2": b2["decision"], "B2_cost": b2["query_cost_units"], "B2_n_stale": len(b2["refreshed_predicates"]),
                                 "B4": b4["decision"], "B4_cost": b4["query_cost_units"],
                                 "B6": b6["decision"], "B6_cost": b6["query_cost_units"], "cache_only": b0})

with open(os.path.join(OUT, "t6_ttl_sweep_rows.csv"), "w", newline="") as f:
    wtr = csv.DictWriter(f, fieldnames=list(rows[0]))
    wtr.writeheader(); wtr.writerows(rows)

agg = defaultdict(lambda: defaultdict(float))
for r in rows:
    k = (r["variant"], r["world"], r["ttl_s"])
    a = agg[k]
    a["n"] += 1
    for d in ("B2", "B4", "B6", "cache_only"):
        a[f"{d}_unsafe"] += (r[d] == "EXECUTE" and r["attack_goal"])
        a[f"{d}_false_abort"] += (r[d] == "ABORT_REPLAN" and r["truth_valid"])
    a["B2_cost_sum"] += r["B2_cost"]; a["B6_cost_sum"] += r["B6_cost"]; a["B4_cost_sum"] += r["B4_cost"]
table = []
for (v, wn, thr), a in sorted(agg.items()):
    n = a["n"]
    table.append({"variant": v, "world": wn, "ttl_s": thr, "n": int(n),
                  "B2_unsafe": int(a["B2_unsafe"]), "B2_false_abort": int(a["B2_false_abort"]), "B2_mean_cost": round(a["B2_cost_sum"] / n, 3),
                  "B6_unsafe": int(a["B6_unsafe"]), "B6_false_abort": int(a["B6_false_abort"]), "B6_mean_cost": round(a["B6_cost_sum"] / n, 3),
                  "B4_unsafe": int(a["B4_unsafe"]), "B4_false_abort": int(a["B4_false_abort"]), "B4_mean_cost": round(a["B4_cost_sum"] / n, 3),
                  "cache_only_unsafe": int(a["cache_only_unsafe"]), "cache_only_false_abort": int(a["cache_only_false_abort"])})
with open(os.path.join(OUT, "t6_ttl_sweep_summary.csv"), "w", newline="") as f:
    wtr = csv.DictWriter(f, fieldnames=list(table[0]))
    wtr.writeheader(); wtr.writerows(table)
for t in table:
    if t["world"] in ("W2", "W3"):
        print(t)
