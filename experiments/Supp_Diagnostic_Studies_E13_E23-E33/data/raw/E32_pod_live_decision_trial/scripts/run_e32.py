#!/usr/bin/env python3
"""
E32 -- Full live decision trial on the grace-period-fixed real Pod
mechanism (not a ConfigMap surrogate, not timing-only).

Combines E29's root-cause diagnosis (the original F1.1/F1.2 deadline
infeasibility was caused by Kubernetes' default pod termination grace
period, not scheduling/image-pull, since those Pods are deliberately
never scheduled) with E13's original live decision-gate methodology
(derive_critical_predicates + commit_components against a real live
query). Uses a byte-identical copy of the locked f1_1_pod_benign.yaml
manifest; the ONLY change from the original E13 harness's behavior is
that the delete call uses gracePeriodSeconds=0 instead of the default.

Imports the locked, unmodified flashir_core engine exactly as E13/E23
do. Nothing under 02_locked_methodology_v8.1.1/ or any other experiment
is modified.
"""
import copy, json, sys, time
from pathlib import Path
from urllib.request import urlopen, Request

ROOT = Path(__file__).resolve().parents[1]
PKG_ROOT = ROOT.parents[1]
sys.path.insert(0, str(PKG_ROOT / "02_locked_methodology_v8.1.1" / "deterministic"))
from flashir_core.dcji import derive_critical_predicates
from flashir_core.expr import commit_components

SCENARIO = json.loads((ROOT / "scenarios" / "F1_POD_FIXED.json").read_text())
PROXY = "http://127.0.0.1:8001"
NS = "flashir-e32"
POD_NAME = "flashir-target"
MANIFEST_YAML = (ROOT / "manifests" / "f1_1_pod_benign.yaml").read_text()
DEADLINE_S = 5.0


def pod_url():
    return f"{PROXY}/api/v1/namespaces/{NS}/pods/{POD_NAME}"


def pods_url():
    return f"{PROXY}/api/v1/namespaces/{NS}/pods"


def get_uid(retries=3):
    last_err = None
    for attempt in range(retries):
        try:
            with urlopen(pod_url(), timeout=5) as r:
                obj = json.loads(r.read())
            return obj["metadata"]["uid"]
        except Exception as e:
            last_err = e
            time.sleep(0.2)
    raise RuntimeError(f"get_uid failed after {retries} attempts: {last_err}")


def pod_manifest_json():
    # Minimal, deterministic re-serialization of the locked YAML's fields
    # (avoids adding a YAML dependency; the fields are fixed and known).
    return json.dumps({
        "apiVersion": "v1", "kind": "Pod",
        "metadata": {"name": POD_NAME, "namespace": NS, "labels": {"flashir-role": "benign"}},
        "spec": {"nodeName": "flashir-nonexistent",
                 "containers": [{"name": "hold", "image": "registry.k8s.io/pause:3.10.1"}]},
    }).encode()


def create(retries=3):
    last_err = None
    for attempt in range(retries):
        try:
            req = Request(pods_url(), data=pod_manifest_json(), method="POST",
                          headers={"Content-Type": "application/json"})
            with urlopen(req, timeout=5) as r:
                r.read()
            return
        except Exception as e:
            last_err = e
            time.sleep(0.2)
    raise RuntimeError(f"create failed after {retries} attempts: {last_err}")


def delete_forced(retries=3):
    body = json.dumps({"kind": "DeleteOptions", "apiVersion": "v1", "gracePeriodSeconds": 0}).encode()
    last_err = None
    for attempt in range(retries):
        try:
            req = Request(pod_url(), data=body, method="DELETE",
                          headers={"Content-Type": "application/json"})
            with urlopen(req, timeout=5) as r:
                r.read()
            return
        except Exception as e:
            last_err = e
            if "404" in str(e) or "NotFound" in str(e):
                return  # already gone
            time.sleep(0.2)
    raise RuntimeError(f"delete_forced failed after {retries} attempts: {last_err}")


def patch_irrelevant():
    # F1.1's live harness has no separate irrelevant-mutation resource; we
    # reuse a label patch on the same Pod that does not touch its UID.
    body = json.dumps({"metadata": {"labels": {"irrelevant": f"v{int(time.time())}"}}}).encode()
    req = Request(pod_url(), data=body, method="PATCH",
                  headers={"Content-Type": "application/merge-patch+json"})
    urlopen(req, timeout=5).read()


def evaluate_gate(decision_uid, current_uid):
    """Same pattern as E23's evaluate_gate: the binding predicate's
    comparison target is patched per-trial since a live UID is a fresh
    UUID every run, not a fixed scenario constant."""
    scenario = copy.deepcopy(SCENARIO)
    scenario["predicates"]["p_binding"]["value"] = decision_uid
    state = copy.deepcopy(scenario["initial_state"])
    state["facts"]["current_uid"] = current_uid
    t0 = time.perf_counter_ns()
    comp = commit_components(scenario, state, "balanced")
    t1 = time.perf_counter_ns()
    decision = "EXECUTE" if comp["valid"] else "ABORT_REPLAN"
    return decision, (t1 - t0) / 1e6


def run_trial(condition):
    delete_forced()
    create()
    decision_uid = get_uid()

    transition_duration_s = 0.0
    if condition == "ea_attack":
        t0 = time.perf_counter()
        delete_forced()
        create()
        t1 = time.perf_counter()
        transition_duration_s = t1 - t0
    elif condition == "nc_irrelevant":
        patch_irrelevant()

    t_commit0 = time.perf_counter()
    current_uid = get_uid()
    query_latency_ms = (time.perf_counter() - t_commit0) * 1000

    decision, policy_ms = evaluate_gate(decision_uid, current_uid)
    gate_latency_ms = query_latency_ms + policy_ms
    deadline_feasible = transition_duration_s <= DEADLINE_S if condition == "ea_attack" else None

    return {
        "condition": condition,
        "decision_time_uid": decision_uid,
        "commit_time_uid": current_uid,
        "transition_duration_s": transition_duration_s,
        "deadline_feasible": deadline_feasible,
        "gate_latency_ms": gate_latency_ms,
        "decision": decision,
    }


EXPECTED = {"ea_attack": "ABORT_REPLAN", "nc_stable": "EXECUTE", "nc_irrelevant": "EXECUTE"}


def main():
    plan = derive_critical_predicates(SCENARIO, DEADLINE_S, "C2", "balanced")
    print("Plan:", plan["critical_predicates"], "cost", plan["query_cost_units"])

    out_dir = ROOT / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = out_dir / "raw_trials.jsonl"
    if raw_path.exists():
        raise SystemExit(f"{raw_path} already exists; refusing to overwrite.")

    n_reps = 20
    rows = []
    with open(raw_path, "w", encoding="utf-8") as f:
        for condition in ["ea_attack", "nc_stable", "nc_irrelevant"]:
            for rep in range(1, n_reps + 1):
                r = run_trial(condition)
                r["rep"] = rep
                r["expected_decision"] = EXPECTED[condition]
                r["matches_expected"] = r["decision"] == EXPECTED[condition]
                rows.append(r)
                f.write(json.dumps(r) + "\n")
                f.flush()
                td = r["transition_duration_s"]
                print(f"{condition:14s} rep {rep:2d}: decision={r['decision']:12s} "
                      f"expected={EXPECTED[condition]:12s} match={r['matches_expected']} "
                      f"transition_s={td:.4f} deadline_feasible={r['deadline_feasible']}")

    n = len(rows)
    mismatches = sum(not r["matches_expected"] for r in rows)
    attack_rows = [r for r in rows if r["condition"] == "ea_attack"]
    feasible = sum(bool(r["deadline_feasible"]) for r in attack_rows)
    durations = [r["transition_duration_s"] for r in attack_rows]
    summary = {
        "n_total": n, "mismatches": mismatches,
        "attack_trials": len(attack_rows),
        "deadline_feasible_count": feasible,
        "deadline_feasible_rate": feasible / len(attack_rows),
        "transition_duration_s_min": min(durations),
        "transition_duration_s_max": max(durations),
        "transition_duration_s_mean": sum(durations) / len(durations),
        "deadline_s": DEADLINE_S,
        "planner_certificate": plan["critical_predicates"],
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print("\nSUMMARY:", json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
