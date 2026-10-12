#!/usr/bin/env python3
"""
E13 v9 extension: a genuinely informative live Kubernetes DCJI-vs-FullRefresh
comparison, using a multi-basis scenario (F5.LIVE) with 6 independently
queryable live Pod labels instead of the original E13's single-query cases
(which are provably identical for DCJI and FullRefresh by construction).

Imports the locked, UNMODIFIED flashir_core engine (derive_critical_predicates,
commit_components) exactly as the original E13 harness does. Does not modify
anything under flashir_complete_v8_1_1.
"""
import copy, json, subprocess, sys, time
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PKG_ROOT / "02_locked_methodology_v8.1.1" / "deterministic"))
from flashir_core.dcji import derive_critical_predicates
from flashir_core.expr import commit_components

ROOT = PKG_ROOT / "03_extended_study_v9" / "e13_live_multi"
SCENARIO = json.loads((ROOT / "scenarios" / "F5_live.json").read_text())
MANIFEST = ROOT / "manifests" / "f5_live_pod.yaml"
OUT = ROOT / "results"
NS = "flashir-e13"
FLAGS = ["flag_a", "flag_b", "flag_c", "flag_d", "flag_e", "flag_f"]
LABEL_OF = {f: f.replace("_", "-") for f in FLAGS}
ATTACK_TYPES = ["cut_all"]
CUT_FLAG = {"cut_all": ["flag_a", "flag_c", "flag_e"]}
# NOTE: the justification policy is an OR across three independent bases
# (a&b) | (c&d) | (e&f). Cutting only ONE basis leaves the other two valid via
# the OR, so justification correctly stays true (EXECUTE) -- that is not a
# bug, it is the same OR semantics as the locked controlled F5 templates.
# To reach the actual EA-TOCTOU goal state (matching the deterministic W2
# world, which applies cut_b1+cut_b2+cut_b3 together) the live attacker must
# cut all three bases' critical members in the same trial.


def run(args, check=True):
    p = subprocess.run(args, text=True, capture_output=True)
    if check and p.returncode:
        raise RuntimeError(f"command failed: {' '.join(args)} :: {p.stderr.strip()}")
    return p


def apply_pod():
    run(["kubectl", "apply", "-f", str(MANIFEST)])
    end = time.time() + 30
    while time.time() < end:
        p = run(["kubectl", "-n", NS, "get", "pod", "flashir-multi", "-o", "json"], check=False)
        if p.returncode == 0:
            obj = json.loads(p.stdout)
            if obj.get("status", {}).get("phase") in ("Running", "Pending"):
                return obj
        time.sleep(0.5)
    raise RuntimeError("timeout waiting for flashir-multi pod")


def get_labels():
    obj = json.loads(run(["kubectl", "-n", NS, "get", "pod", "flashir-multi", "-o", "json"]).stdout)
    labels = obj["metadata"]["labels"]
    return {f: labels.get(LABEL_OF[f], "false") == "true" for f in FLAGS}


def patch_flag(flag, value):
    label = LABEL_OF[flag]
    run(["kubectl", "-n", NS, "label", "pod", "flashir-multi", f"{label}={'true' if value else 'false'}", "--overwrite"])


def reset_all_true():
    for f in FLAGS:
        patch_flag(f, True)


def live_query(flags_to_query):
    obs = get_labels()
    return {f: obs[f] for f in flags_to_query}


def project_commit_state(observed):
    state = copy.deepcopy(SCENARIO["initial_state"])
    for f, v in observed.items():
        state["facts"][f] = v
    return state


def decision(qids_to_refresh):
    """Query exactly the given live labels and evaluate via the locked commit_components,
    exactly mirroring what B4_fullrefresh / B6_dcji_gate do internally (refresh only the
    named queries; unqueried facts retain their decision-time cached value)."""
    cached = copy.deepcopy(SCENARIO["initial_state"])
    observed = live_query(qids_to_refresh)
    state = project_commit_state(observed)
    # unqueried flags keep their cached (decision-time, all-true) value, matching
    # the locked refresh_queries()/hybrid-state semantics used by B4/B6.
    for f in FLAGS:
        if f not in qids_to_refresh:
            state["facts"][f] = cached["facts"][f]
    comp = commit_components(SCENARIO, state, "balanced")
    return ("EXECUTE" if comp["valid"] else "ABORT_REPLAN"), comp


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    out_path = OUT / "e13_live_multi_results.json"
    if out_path.exists():
        raise SystemExit("results file already exists; refusing overwrite")

    run(["kubectl", "create", "namespace", NS], check=False)

    plan = derive_critical_predicates(SCENARIO, 5.0, "C2", "balanced")
    print("DCJI plan:", json.dumps(plan, indent=2))
    dcji_qids = [f.replace("query:/facts/", "") for f in plan["critical_query_ids"]]
    full_qids = FLAGS

    rows = []
    n_reps = 20
    try:
        for rep in range(1, n_reps + 1):
            apply_pod()
            reset_all_true()
            time.sleep(0.3)
            decision_labels = get_labels()
            attack = ATTACK_TYPES[(rep - 1) % len(ATTACK_TYPES)]
            cut_flags = CUT_FLAG[attack]

            t0 = time.time()
            for cf in cut_flags:
                patch_flag(cf, False)
            t1 = time.time()

            dcji_decision, dcji_comp = decision(dcji_qids)
            dcji_cost = float(len(dcji_qids))

            full_decision, full_comp = decision(full_qids)
            full_cost = float(len(full_qids))

            row = {
                "rep": rep, "attack_type": attack, "cut_flags": cut_flags,
                "decision_labels": decision_labels,
                "transition_start_unix": t0, "transition_end_unix": t1,
                "B6_decision": dcji_decision, "B6_query_cost": dcji_cost, "B6_queried": dcji_qids,
                "B6_unsafe": dcji_decision == "EXECUTE" and not dcji_comp["valid"],
                "B4_decision": full_decision, "B4_query_cost": full_cost, "B4_queried": full_qids,
                "B4_unsafe": full_decision == "EXECUTE" and not full_comp["valid"],
            }
            rows.append(row)
            print(f"rep {rep:2d} attack={attack:8s} B6={dcji_decision}(cost={dcji_cost}) "
                  f"B4={full_decision}(cost={full_cost})")
    finally:
        run(["kubectl", "-n", NS, "delete", "pod", "flashir-multi", "--ignore-not-found=true", "--wait=false"], check=False)

    n = len(rows)
    b4_unsafe = sum(r["B4_unsafe"] for r in rows)
    b6_unsafe = sum(r["B6_unsafe"] for r in rows)
    discordant = sum(1 for r in rows if r["B4_unsafe"] != r["B6_unsafe"])
    mean_b4 = sum(r["B4_query_cost"] for r in rows) / n
    mean_b6 = sum(r["B6_query_cost"] for r in rows) / n
    summary = {"n": n, "B4_unsafe": b4_unsafe, "B6_unsafe": b6_unsafe, "discordant": discordant,
               "mean_B4_cost": mean_b4, "mean_B6_cost": mean_b6, "dcji_plan": plan}
    (OUT / "e13_live_multi_results.json").write_text(json.dumps({"rows": rows, "summary": summary}, indent=2))
    print("\nSUMMARY:", json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
