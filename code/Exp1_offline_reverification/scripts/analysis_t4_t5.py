#!/usr/bin/env python3
"""Tasks 4 and 5: independent brute-force minimality over the FULL query universe,
P1-P4 identity check, and stronger offline tests (5a-5e).
Imports the locked core as fc_orig and the patched core as fc_patched (renamed copies
under work/offline/pkgs); independent semantics come from indep.py."""
import os, sys, json, glob, copy, csv, time
from collections import Counter, defaultdict
from fractions import Fraction

W = os.path.expanduser("~/dcji_reanalysis_20260926")
sys.path.insert(0, W + "/work/offline/pkgs")
sys.path.insert(0, W + "/work/offline/scripts")
import indep as I
import fc_orig.dcji as Od, fc_orig.defenses as Of, fc_orig.engine as Oe
import fc_patched.dcji as Pd, fc_patched.defenses as Pf

F8 = W + "/inputs/frozen/02_locked_methodology_v8.1.1/deterministic"
F9 = W + "/inputs/frozen/03_extended_study_v9"
OUT = W + "/out/offline/t4_t5"
os.makedirs(OUT, exist_ok=True)


def wcsv(name, rows):
    if not rows:
        return
    keys = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    with open(os.path.join(OUT, name), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for r in rows:
            w.writerow({k: (json.dumps(v) if isinstance(v, (list, dict)) else v) for k, v in r.items()})


def fstr(x):
    return None if x is None else (str(x) if isinstance(x, Fraction) else x)


# ------------------------------------------------------------------ load specs
held_inst = {}
for p in sorted(glob.glob(F8 + "/instances/heldout/*.json")):
    wr = json.load(open(p))
    held_inst[wr["instance_id"]] = wr
held_P1 = {k: v["scenario"] for k, v in held_inst.items() if v["parameterization_id"] == "P1"}
pilot = {json.load(open(p))["scenario_id"]: json.load(open(p)) for p in sorted(glob.glob(F9 + "/scenarios/pilot/*.json"))}
dev = {json.load(open(p))["scenario_id"]: json.load(open(p)) for p in sorted(glob.glob(F8 + "/scenarios/dev/*.json"))}
print("loaded", len(held_inst), "held-out instances,", len(held_P1), "P1,", len(pilot), "pilot,", len(dev), "dev")

summary = {}

# ------------------------------------------------------------------ Task 4
def t4_one(label, sid, s, horizon_name, deadline):
    prof = s["primary_policy"]
    cap = s["attacker_capability_tier"]
    t0 = time.perf_counter()
    my_goals = I.goals_of(s, deadline, prof, prune_unauth=True)
    my_goals_np = I.goals_of(s, deadline, prof, prune_unauth=False)
    lk = Od.enumerate_attack_goals(s, deadline, cap, prof)
    lk_keys = {I.canon(g["state"]) for g in lk["goals"]}
    plan_o = Od.derive_critical_predicates(s, deadline, cap, prof)
    plan_p = Pd.derive_critical_predicates(s, deadline, cap, prof)
    gstates = [v[0] for v in my_goals.values()]
    allq = set(s["refresh_model"]["query_costs"])
    posq = {q for q in allq if I.qcost(s["refresh_model"]["query_costs"][q]) > 0}
    b_pos = I.brute_min(s, prof, gstates, posq, "material")
    b_all = I.brute_min(s, prof, gstates, allq, "material")
    b_pos_ap = I.brute_min(s, prof, gstates, posq, "allpreds")
    cert = plan_o.get("critical_query_ids")
    dcost = None if plan_o["status"] not in ("EXPLOITABLE", "NOT_EXPLOITABLE") else I.qcost(plan_o["query_cost_units"] or 0)
    cert_sound = None
    if cert is not None:
        cert_sound = all(not I.comps(s, I.hybrid(s, s["initial_state"], g, set(cert), prof), prof)["valid"] for g in gstates)
    row = {
        "set": label, "spec": sid, "horizon": horizon_name, "deadline_s": deadline,
        "my_goals": len(my_goals), "locked_goals": len(lk_keys), "goal_sets_equal": set(my_goals) == lk_keys,
        "my_goals_no_auth_prune": len(my_goals_np),
        "dcji_status_orig": plan_o["status"], "dcji_status_patched": plan_p["status"],
        "cert_orig": cert, "cert_patched": plan_p.get("critical_query_ids"),
        "cost_orig": plan_o.get("query_cost_units"), "cost_patched": plan_p.get("query_cost_units"),
        "evals_orig": plan_o.get("subset_evals"), "evals_patched": plan_p.get("subset_evals"),
        "orig_eq_patched": (plan_o["status"], cert, plan_o.get("query_cost_units"), plan_o.get("subset_evals")) ==
                           (plan_p["status"], plan_p.get("critical_query_ids"), plan_p.get("query_cost_units"), plan_p.get("subset_evals")),
        "candidate_n": len(plan_o.get("candidate_query_ids") or []), "universe_pos_n": len(posq), "universe_all_n": len(allq),
        "subsets_tried_pos": b_pos.get("tried"), "feasible_pos": b_pos.get("feasible"),
        "brute_min_pos": fstr(b_pos.get("min_cost")), "brute_argmin_pos": b_pos.get("argmin"),
        "brute_min_all_incl_zero_cost": fstr(b_all.get("min_cost")), "brute_argmin_all": b_all.get("argmin"),
        "brute_min_pos_allpred_semantics": fstr(b_pos_ap.get("min_cost")),
        "cert_sound_indep": cert_sound,
        "counterexample_pos": (dcost is not None and b_pos.get("min_cost") is not None and b_pos["min_cost"] < dcost),
        "argmin_identity_match": (cert == b_pos.get("argmin")) if cert is not None else None,
        "dcji_none_but_brute_feasible": (cert is None and b_pos.get("min_cost") is not None),
        "secs": round(time.perf_counter() - t0, 4),
    }
    return row


t4 = []
for iid, wr in sorted(held_inst.items()):
    s = wr["scenario"]
    for hn in ("W2", "W3"):
        r = t4_one("heldout", iid, s, hn, s["worlds"][hn]["commit_deadline_s"])
        r["template"] = wr["template_id"]; r["param"] = wr["parameterization_id"]
        t4.append(r)
for sid, s in sorted(pilot.items()):
    for hn in ("W2", "W3"):
        t4.append(t4_one("pilot68", sid, s, hn, s["worlds"][hn]["commit_deadline_s"]))
for sid, s in sorted(dev.items()):
    for hn in ("W2", "W3"):
        t4.append(t4_one("dev", sid, s, hn, s["worlds"][hn]["commit_deadline_s"]))
wcsv("t4_minimality_full_universe.csv", t4)


def agg(rows):
    return {
        "rows": len(rows),
        "exploitable_rows": sum(r["dcji_status_orig"] == "EXPLOITABLE" for r in rows),
        "goal_sets_equal_locked_vs_indep": sum(r["goal_sets_equal"] for r in rows),
        "goal_count_distribution": dict(Counter(r["my_goals"] for r in rows)),
        "rows_where_no_auth_prune_adds_goals": sum(r["my_goals_no_auth_prune"] != r["my_goals"] for r in rows),
        "orig_eq_patched": sum(r["orig_eq_patched"] for r in rows),
        "cert_sound_indep_true": sum(r["cert_sound_indep"] is True for r in rows),
        "counterexamples_full_pos_universe": sum(r["counterexample_pos"] for r in rows),
        "argmin_identity_match": sum(r["argmin_identity_match"] is True for r in rows),
        "dcji_none_but_brute_feasible": sum(r["dcji_none_but_brute_feasible"] for r in rows),
        "zero_cost_universe_strictly_cheaper": sum(
            (r["brute_min_all_incl_zero_cost"] is not None and r["brute_min_pos"] is not None and
             Fraction(r["brute_min_all_incl_zero_cost"]) < Fraction(r["brute_min_pos"])) for r in rows),
        "allpred_semantics_differs": sum(r["brute_min_pos_allpred_semantics"] != r["brute_min_pos"] for r in rows),
        "total_subsets_tried": sum(r["subsets_tried_pos"] or 0 for r in rows),
    }


summary["t4"] = {lab: agg([r for r in t4 if r["set"] == lab]) for lab in ("heldout", "pilot68", "dev")}
summary["t4"]["heldout_P1_only"] = agg([r for r in t4 if r["set"] == "heldout" and r.get("param") == "P1"])

# P1-P4 identity check
ident = []
by_t = defaultdict(dict)
for iid, wr in held_inst.items():
    by_t[wr["template_id"]][wr["parameterization_id"]] = wr["scenario"]
t4_by = defaultdict(dict)
for r in t4:
    if r["set"] == "heldout":
        t4_by[(r["template"], r["horizon"])][r["param"]] = {k: v for k, v in r.items() if k not in ("spec", "param", "secs")}
for tid, ps in sorted(by_t.items()):
    base = ps["P1"]
    prof = base["primary_policy"]
    mats = I.material(base, prof) | {base["action"]["authorization"]["predicate"]}
    struct_keys_diff = set()
    mat_vals_equal = True
    for pid_, s in ps.items():
        for k in set(base) | set(s):
            if k in ("initial_state", "parameterizations"):
                continue
            if base.get(k) != s.get(k):
                struct_keys_diff.add(k)
        for mp in mats:
            path = base["predicates"][mp]["path"]
            if I.jget(base["initial_state"], path) != I.jget(s["initial_state"], path):
                mat_vals_equal = False
        # all transitions' effect paths & precondition predicates identical by struct check
    # behavioural identity in E5 frozen rows
    t4same = all(len({json.dumps(v, sort_keys=True, default=str) for v in t4_by[(tid, h)].values()}) == 1 for h in ("W2", "W3"))
    ident.append({"template": tid, "params": sorted(ps), "non_state_keys_differing": sorted(struct_keys_diff),
                  "material_initial_values_equal": mat_vals_equal, "t4_results_identical_across_P": t4same})
e5 = json.load(open(F8 + "/results/heldout/e5_defense_comparison.json"))
cell = defaultdict(set)
for r in e5:
    cell[(r["template_id"], r["world"], r["defense_id"])].add(json.dumps(
        [r["commit_decision"], r["unsafe_commit"], r["false_abort"], r["query_cost_units"], r["critical_query_ids"],
         r["refreshed_predicates"], r["attack_goal_state_count"], r["valid_at_commit"], r["oracle_attack_goal"]]))
e5_ident = sum(len(v) == 1 for v in cell.values())
summary["p1_p4_identity"] = {
    "templates": len(ident),
    "templates_with_only_initial_state_or_param_metadata_differing": sum(
        set(x["non_state_keys_differing"]) <= {"parameterizations"} for x in ident),
    "non_state_keys_differing_union": sorted(set().union(*[set(x["non_state_keys_differing"]) for x in ident])),
    "material_initial_values_equal": sum(x["material_initial_values_equal"] for x in ident),
    "t4_results_identical_across_P": sum(x["t4_results_identical_across_P"] for x in ident),
    "e5_cells_template_world_defense": len(cell), "e5_cells_identical_across_P1_P4": e5_ident,
}
wcsv("t4_p1_p4_identity.csv", ident)

# ------------------------------------------------------------------ Task 5 helpers
def decide_all(s, prof, commit, plan_o, plan_p, deadline):
    cs = copy.deepcopy(commit)
    cs["timestamp_s"] = deadline
    init = s["initial_state"]
    d = {}
    d["B0"] = Of.B0_oneshot(s, init, cs, prof)["decision"]
    d["B2"] = Of.B2_ttlguard(s, init, cs, prof, ttl_s=2.0)["decision"]
    d["B4"] = Of.B4_fullrefresh(s, init, cs, prof)["decision"]
    d["B6o"] = Of.B6_dcji_gate(s, init, cs, prof, deadline_s=deadline, critical_plan=plan_o)["decision"]
    d["B6p"] = Pf.B6_dcji_gate(s, init, cs, prof, deadline_s=deadline, critical_plan=plan_p)["decision"]
    return d


def tally(s, prof, states, plan_o, plan_p, deadline, extra=None):
    c = Counter()
    for st in states:
        tr = I.comps(s, st, prof)
        d = decide_all(s, prof, st, plan_o, plan_p, deadline)
        c["states"] += 1
        c["valid"] += tr["valid"]
        c["attack_goal"] += tr["goal"]
        c["unauthorized"] += (not tr["auth"])
        for k, v in d.items():
            c[f"{k}_unsafe"] += (v == "EXECUTE" and tr["goal"])
            c[f"{k}_false_abort"] += (v == "ABORT_REPLAN" and tr["valid"])
            c[f"{k}_exec_unauth"] += (v == "EXECUTE" and not tr["auth"])
    return c


def plans(s, deadline):
    prof = s["primary_policy"]; cap = s["attacker_capability_tier"]
    return (Od.derive_critical_predicates(s, deadline, cap, prof), Pd.derive_critical_predicates(s, deadline, cap, prof))


# ---------------- 5a paper counterexample J=(A&B)|(C&D), Sd=(1,1,0,1), G={(0,1,0,1)}
def mk_bool_scenario(sid, init_vals, justification, transitions, costs=None, cap="C1"):
    names = sorted(init_vals)
    s = {"scenario_id": sid, "attacker_capability_tier": cap, "primary_policy": "balanced",
         "initial_state": {"timestamp_s": 0, "facts": dict(init_vals, auth=True)},
         "predicates": {n: {"path": f"/facts/{n}", "op": "eq", "value": True} for n in names},
         "action": {"authorization": {"predicate": "p_auth"}, "execution_precondition": True},
         "policy_profiles": {"balanced": {"justification": justification, "binding": True, "scope": True}},
         "transitions": transitions,
         "refresh_model": {"cost_unit": "normalized_query_unit",
                           "query_costs": {f"q_{n}": (costs or {}).get(n, 1.0) for n in names},
                           "predicate_query_ids": {n: f"q_{n}" for n in names},
                           "query_observed_at_s": {f"q_{n}": 0.0 for n in names}}}
    s["predicates"]["p_auth"] = {"path": "/facts/auth", "op": "eq", "value": True}
    return s


def cut(tid, var, dur=1.0, val=False, cap="C1"):
    return {"transition_id": tid, "actor": "attacker", "capability_tier": cap, "duration_s": dur, "cost_ops": 1,
            "preconditions": True, "effects": [{"op": "set", "path": f"/facts/{var}", "value": val}],
            "adversarially_induced": True, "causal_parent_ids": []}


J = {"any": [{"all": [{"predicate": "A"}, {"predicate": "B"}]}, {"all": [{"predicate": "C"}, {"predicate": "D"}]}]}
cx = mk_bool_scenario("PAPER_CX", {"A": True, "B": True, "C": False, "D": True}, J, [cut("cut_A", "A")])
po, pp = plans(cx, 2.0)
gl = I.goals_of(cx, 2.0, "balanced")
bm = I.brute_min(cx, "balanced", [v[0] for v in gl.values()], set(cx["refresh_model"]["query_costs"]))
allstates = []
import itertools
for bits in itertools.product([True, False], repeat=4):
    st = copy.deepcopy(cx["initial_state"]); st["facts"].update(dict(zip("ABCD", bits)))
    allstates.append(st)
cx_rows = []
for st in allstates:
    tr = I.comps(cx, st, "balanced"); d = decide_all(cx, "balanced", st, po, pp, 2.0)
    cx_rows.append({"A": st["facts"]["A"], "B": st["facts"]["B"], "C": st["facts"]["C"], "D": st["facts"]["D"],
                    "reachable_goal": I.canon(st) in gl, "truth_valid": tr["valid"], **d})
wcsv("t5a_paper_counterexample_all16.csv", cx_rows)
# naive "one query per justification basis" heuristic for comparison: {A or B} x {C or D}
summary["t5a"] = {"goals": len(gl), "goal_states": [v[0]["facts"] for v in gl.values()],
                  "dcji_orig": {k: po.get(k) for k in ("status", "critical_query_ids", "query_cost_units", "candidate_query_ids")},
                  "dcji_patched": {k: pp.get(k) for k in ("status", "critical_query_ids", "query_cost_units")},
                  "brute_full_universe": {"min_cost": fstr(bm["min_cost"]), "argmin": bm["argmin"], "feasible": bm["feasible"], "tried": bm["tried"]},
                  "fullrefresh_cost": Of.B4_fullrefresh(cx, cx["initial_state"], cx["initial_state"], "balanced")["query_cost_units"],
                  "on_goal": {k: v for k, v in decide_all(cx, "balanced", list(gl.values())[0][0], po, pp, 2.0).items()},
                  "unreachable_states_where_B6_executes_but_invalid": sum(1 for r in cx_rows if not r["reachable_goal"] and r["B6o"] == "EXECUTE" and not r["truth_valid"])}

# ---------------- 5b multi-goal F5 variants
def f5_variants(sid, s):
    prof = s["primary_policy"]; cap = s["attacker_capability_tier"]
    inm = I.model_transitions(s)
    written = {e["path"] for t in inm for e in t["effects"]}
    mats = sorted(I.material(s, prof))
    unmod = [p for p in mats if s["predicates"][p]["path"] not in written and
             I.qcost(s["refresh_model"]["query_costs"][s["refresh_model"]["predicate_query_ids"][p]]) > 0]
    dur = min(t["duration_s"] for t in inm)
    out = []
    for k in range(0, len(unmod) + 1):
        v = copy.deepcopy(s)
        for p in unmod[:k]:
            fs = I.falsify(s, p, s["initial_state"])
            v["transitions"].append({"transition_id": f"cut_extra_{p}", "title": "offline multi-goal cut", "actor": "attacker",
                                     "capability_tier": cap, "duration_s": dur, "cost_ops": 1, "preconditions": True,
                                     "effects": [{"op": "set", "path": s["predicates"][p]["path"],
                                                  "value": I.jget(fs, s["predicates"][p]["path"])}],
                                     "adversarially_induced": True, "causal_parent_ids": []})
        out.append((f"{sid}+{k}cuts", v, unmod[:k]))
    return out


t5b = []
f5_bases = {k: v for k, v in held_P1.items() if v["family"] == "F5" and v["scenario_role"] == "positive_ea_toctou"}
f5_bases.update({k: v for k, v in pilot.items() if v["family"] == "F5"})
for sid, s in sorted(f5_bases.items()):
    for vid, v, added in f5_variants(sid, s):
        prof = v["primary_policy"]; dl = v["worlds"]["W2"]["commit_deadline_s"]
        po, pp = plans(v, dl)
        G = I.goals_of(v, dl, prof)
        posq = {q for q, c in v["refresh_model"]["query_costs"].items() if I.qcost(c) > 0}
        bm = I.brute_min(v, prof, [x[0] for x in G.values()], posq)
        R = I.reachable(v, dl, I.model_transitions(v), prune_unauth=False)
        c = tally(v, prof, [x[0] for x in R.values()], po, pp, dl)
        b4c = Of.B4_fullrefresh(v, v["initial_state"], v["initial_state"], prof)["query_cost_units"]
        t5b.append({"variant": vid, "added_cuts": added, "goals": len(G), "locked_goal_count": po.get("goal_count"),
                    "status_o": po["status"], "status_p": pp["status"], "cert": po.get("critical_query_ids"),
                    "cost_o": po.get("query_cost_units"), "cost_p": pp.get("query_cost_units"), "evals_o": po.get("subset_evals"),
                    "evals_p": pp.get("subset_evals"), "brute_min_pos": fstr(bm["min_cost"]), "B4_cost": b4c,
                    "counterexample": bm["min_cost"] is not None and po.get("query_cost_units") is not None and bm["min_cost"] < I.qcost(po["query_cost_units"]),
                    "reachable_states": c["states"], "valid_states": c["valid"], "goal_states": c["attack_goal"],
                    "B6o_unsafe": c["B6o_unsafe"], "B6o_false_abort": c["B6o_false_abort"], "B6p_unsafe": c["B6p_unsafe"],
                    "B6p_false_abort": c["B6p_false_abort"], "B4_unsafe": c["B4_unsafe"], "B4_false_abort": c["B4_false_abort"],
                    "B0_unsafe": c["B0_unsafe"]})
wcsv("t5b_multigoal_f5.csv", t5b)
summary["t5b"] = {"variants": len(t5b), "multi_goal_variants": sum(r["goals"] > 1 for r in t5b),
                  "goal_count_range": [min(r["goals"] for r in t5b), max(r["goals"] for r in t5b)],
                  "counterexamples": sum(r["counterexample"] for r in t5b),
                  "orig_patched_cert_equal": sum((r["status_o"], r["cost_o"], r["evals_o"]) == (r["status_p"], r["cost_p"], r["evals_p"]) for r in t5b),
                  "B6_unsafe_total": sum(r["B6o_unsafe"] for r in t5b), "B6_false_abort_total": sum(r["B6o_false_abort"] for r in t5b),
                  "B4_false_abort_total": sum(r["B4_false_abort"] for r in t5b),
                  "variants_where_cert_cost_equals_B4": sum(r["cost_o"] == r["B4_cost"] for r in t5b if r["goals"] > 1),
                  "multi_goal_variants_total": sum(r["goals"] > 1 for r in t5b)}

# ---------------- 5c valid-state permissiveness over ALL reachable states
t5c = []
spec_sets = [("heldout_P1", held_P1), ("pilot68", pilot), ("dev", dev)]
for lab, specs in spec_sets:
    for sid, s in sorted(specs.items()):
        prof = s["primary_policy"]; dl = s["worlds"]["W2"]["commit_deadline_s"]
        po, pp = plans(s, dl)
        for tsname, tset in (("in_model", I.model_transitions(s)), ("all_transitions", s["transitions"])):
            R = I.reachable(s, dl, tset, prune_unauth=False)
            c = tally(s, prof, [x[0] for x in R.values()], po, pp, dl)
            t5c.append({"set": lab, "spec": sid, "transition_set": tsname, **dict(c)})
wcsv("t5c_reachable_state_permissiveness.csv", t5c)
summary["t5c"] = {}
for lab in ("heldout_P1", "pilot68", "dev"):
    for tsn in ("in_model", "all_transitions"):
        rows = [r for r in t5c if r["set"] == lab and r["transition_set"] == tsn]
        keys = sorted({k for r in rows for k in r if k not in ("set", "spec", "transition_set")})
        summary["t5c"][f"{lab}/{tsn}"] = {k: sum(r.get(k, 0) for r in rows) for k in keys}

# ---------------- 5d out-of-model mutation of unmodeled predicates
t5d = []
for lab, specs in (("heldout_P1", held_P1), ("pilot68", pilot)):
    for sid, s in sorted(specs.items()):
        prof = s["primary_policy"]; dl = s["worlds"]["W2"]["commit_deadline_s"]
        po, pp = plans(s, dl)
        inm = I.model_transitions(s)
        written = {e["path"] for t in inm for e in t["effects"]}
        qmap = s["refresh_model"]["predicate_query_ids"]; qc = s["refresh_model"]["query_costs"]
        unmod = [p for p in sorted(I.material(s, prof)) if s["predicates"][p]["path"] not in written]
        R = I.reachable(s, dl, inm, prune_unauth=True)
        for kind in ("zero_cost", "positive_cost"):
            targets = [p for p in unmod if (I.qcost(qc[qmap[p]]) <= 0) == (kind == "zero_cost")]
            if not targets:
                continue
            c = Counter()
            for r_ in range(1, len(targets) + 1):
                for combo in itertools.combinations(targets, r_):
                    for st, _, _ in R.values():
                        m = st
                        for p in combo:
                            m = I.falsify(s, p, m)
                        tr = I.comps(s, m, prof)
                        d = decide_all(s, prof, m, po, pp, dl)
                        c["mutated_states"] += 1
                        c["attack_goal"] += tr["goal"]
                        c["valid"] += tr["valid"]
                        for k, v in d.items():
                            c[f"{k}_unsafe"] += (v == "EXECUTE" and tr["goal"])
                            c[f"{k}_false_abort"] += (v == "ABORT_REPLAN" and tr["valid"])
            t5d.append({"set": lab, "spec": sid, "mutation_kind": kind, "targets": targets, **dict(c)})
wcsv("t5d_out_of_model_mutation.csv", t5d)
summary["t5d"] = {}
for lab in ("heldout_P1", "pilot68"):
    for kind in ("zero_cost", "positive_cost"):
        rows = [r for r in t5d if r["set"] == lab and r["mutation_kind"] == kind]
        keys = sorted({k for r in rows for k in r if k not in ("set", "spec", "mutation_kind", "targets")})
        summary["t5d"][f"{lab}/{kind}"] = {"specs": len(rows), **{k: sum(r.get(k, 0) for r in rows) for k in keys}}

# ---------------- 5e W0/W1/W3 worlds for the 68 follow-up specs
t5e = []
for sid, s in sorted(pilot.items()):
    prof = s["primary_policy"]
    for wn in ("W0", "W1", "W2", "W3"):
        w = s["worlds"][wn]; dl = w["commit_deadline_s"]
        rep = Oe.replay_path(s, w["transition_path"], dl)
        cs = rep["state"]; tr = I.comps(s, cs, prof)
        init = s["initial_state"]
        b4 = Of.B4_fullrefresh(s, init, cs, prof)
        b6o = Of.B6_dcji_gate(s, init, cs, prof, deadline_s=dl, attacker_capability=s["attacker_capability_tier"])
        b6p = Pf.B6_dcji_gate(s, init, cs, prof, deadline_s=dl, attacker_capability=s["attacker_capability_tier"])
        t5e.append({"spec": sid, "family": s["family"], "world": wn, "deadline_s": dl, "expected": w["expected_commit"],
                    "truth_valid": tr["valid"], "attack_goal": tr["goal"], "path_completed": rep["path_completed"],
                    "B4": b4["decision"], "B4_cost": b4["query_cost_units"], "B6o": b6o["decision"], "B6o_cost": b6o["query_cost_units"],
                    "B6o_mode": b6o["defense_mode"], "B6p": b6p["decision"], "B6p_mode": b6p["defense_mode"]})
wcsv("t5e_pilot_all_worlds.csv", t5e)
summary["t5e"] = {}
for wn in ("W0", "W1", "W2", "W3"):
    rows = [r for r in t5e if r["world"] == wn]
    summary["t5e"][wn] = {
        "n": len(rows),
        "expected_matches_truth": sum((r["expected"] == "EXECUTE") == r["truth_valid"] for r in rows),
        "B6o_correct": sum((r["B6o"] == "EXECUTE") == r["truth_valid"] for r in rows),
        "B6o_unsafe": sum(r["B6o"] == "EXECUTE" and r["attack_goal"] for r in rows),
        "B6o_false_abort": sum(r["B6o"] == "ABORT_REPLAN" and r["truth_valid"] for r in rows),
        "B4_unsafe": sum(r["B4"] == "EXECUTE" and r["attack_goal"] for r in rows),
        "B4_false_abort": sum(r["B4"] == "ABORT_REPLAN" and r["truth_valid"] for r in rows),
        "B6o_eq_B6p": sum(r["B6o"] == r["B6p"] and r["B6o_mode"] == r["B6p_mode"] for r in rows),
        "B6_modes": dict(Counter(r["B6o_mode"] for r in rows)),
        "mean_B6_cost": sum(r["B6o_cost"] for r in rows) / len(rows), "mean_B4_cost": sum(r["B4_cost"] for r in rows) / len(rows),
    }

# validator (patch c) over all specs
viol = {}
for lab, specs in (("heldout_P1", held_P1), ("pilot68", pilot), ("dev", dev)):
    viol[lab] = sum(bool(Pd.zero_cost_observability_violations(s, s["primary_policy"])) for s in specs.values())
summary["validator_zero_cost_write_violations"] = viol

json.dump(summary, open(os.path.join(OUT, "t4_t5_summary.json"), "w"), indent=2, default=str)
print(json.dumps(summary, indent=1, default=str))
