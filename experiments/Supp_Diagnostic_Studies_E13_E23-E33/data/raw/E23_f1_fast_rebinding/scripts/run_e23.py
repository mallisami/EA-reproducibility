#!/usr/bin/env python3
"""
E23 -- F1 Fast-Rebinding Deadline-Feasibility Replication.

Frozen protocol: E23_PROTOCOL.json (hash verified at startup). Imports the
locked, UNMODIFIED flashir_core engine (derive_critical_predicates,
commit_components), exactly as the original E13 harness and the E21
extension do. Does not modify anything under 02_locked_methodology_v8.1.1/
or any other existing v9 evidence.

Mechanism: identical to F1.1/F1.2 (same-name object rebinding via UID) but
hosted on a Kubernetes ConfigMap rather than a Pod, so the delete/recreate
transition completes in milliseconds instead of the 39-80s a Pod/container
required in the original E13 run -- isolating the identity-rebinding
mechanism's timing from container-scheduling overhead.

For every trial we record the REAL wall-clock duration of the attacker's
delete+recreate transition, not a simulated value, so "deadline-feasible"
is a directly measured fact.
"""
import copy, json, sys, time
from pathlib import Path
from urllib.request import urlopen, Request

ROOT = Path(__file__).resolve().parents[1]
PKG_ROOT = ROOT.parents[1]
sys.path.insert(0, str(PKG_ROOT / "02_locked_methodology_v8.1.1" / "deterministic"))
from flashir_core.dcji import derive_critical_predicates
from flashir_core.expr import commit_components

PROTOCOL_PATH = ROOT / "E23_PROTOCOL.json"
PROTOCOL_HASH_PATH = ROOT / "E23_PROTOCOL.sha256"
SCENARIO = json.loads((ROOT / "scenarios" / "F1_FAST.json").read_text())
PROXY = "http://127.0.0.1:8001"
NS = "flashir-f1fast"
TARGET = "f1-target"
IRRELEVANT = "f1-irrelevant"


def verify_protocol_hash():
    import hashlib
    actual = hashlib.sha256(PROTOCOL_PATH.read_bytes()).hexdigest()
    recorded = PROTOCOL_HASH_PATH.read_text().split()[0]
    if actual != recorded:
        raise SystemExit(f"PROTOCOL HASH MISMATCH: file={actual} recorded={recorded} -- refusing to run")
    return json.loads(PROTOCOL_PATH.read_text())


def cm_url(name):
    return f"{PROXY}/api/v1/namespaces/{NS}/configmaps/{name}"


def get_uid(name):
    with urlopen(cm_url(name), timeout=5) as r:
        obj = json.loads(r.read())
    return obj["metadata"]["uid"]


def delete_cm(name):
    req = Request(cm_url(name), method="DELETE")
    urlopen(req, timeout=5).read()


def create_cm(name, data_key="role", data_val="quarantine-target"):
    body = json.dumps({
        "apiVersion": "v1", "kind": "ConfigMap",
        "metadata": {"name": name, "namespace": NS},
        "data": {data_key: data_val},
    }).encode()
    req = Request(f"{PROXY}/api/v1/namespaces/{NS}/configmaps", data=body, method="POST",
                  headers={"Content-Type": "application/json"})
    urlopen(req, timeout=5).read()


def patch_irrelevant():
    body = json.dumps({"data": {"note": f"mut-{time.time()}"}}).encode()
    req = Request(cm_url(IRRELEVANT), data=body, method="PATCH",
                  headers={"Content-Type": "application/merge-patch+json"})
    urlopen(req, timeout=5).read()


def evaluate_gate(decision_uid, current_uid):
    """Mirrors run_live_multi.py's decision() pattern: project the single
    live-refreshed query into the cached decision-time state and evaluate
    the real, unmodified commit_components().

    F1's binding predicate is "current identity equals THIS decision's
    identity" -- a per-decision value, not a fixed enum like F5.LIVE's
    boolean flags. Since decision_uid is a fresh live UUID every trial (not
    knowable when the static scenario file was authored), the predicate's
    comparison target is patched into a per-trial scenario copy rather than
    hardcoded in F1_FAST.json. This is the live analog of how the locked,
    static F1.1/F1.2 scenarios hardcode their decision-time UID ("U1") as a
    scenario constant -- here that constant is simply supplied at runtime."""
    scenario = copy.deepcopy(SCENARIO)
    scenario["predicates"]["p_binding"]["value"] = decision_uid
    state = copy.deepcopy(scenario["initial_state"])
    state["facts"]["current_uid"] = current_uid
    t0 = time.perf_counter_ns()
    comp = commit_components(scenario, state, "balanced")
    t1 = time.perf_counter_ns()
    decision = "EXECUTE" if comp["valid"] else "ABORT_REPLAN"
    return decision, (t1 - t0) / 1e6


def run_trial(condition, deadline_s):
    # Ensure clean starting state: f1-target exists with a known UID.
    try:
        delete_cm(TARGET)
    except Exception:
        pass
    create_cm(TARGET)
    decision_uid = get_uid(TARGET)

    transition_duration_s = 0.0
    if condition == "ea_attack":
        t0 = time.perf_counter()
        delete_cm(TARGET)
        create_cm(TARGET)
        t1 = time.perf_counter()
        transition_duration_s = t1 - t0
    elif condition == "nc_irrelevant":
        patch_irrelevant()

    t_commit0 = time.perf_counter()
    current_uid = get_uid(TARGET)
    query_latency_ms = (time.perf_counter() - t_commit0) * 1000

    decision, policy_ms = evaluate_gate(decision_uid, current_uid)
    gate_latency_ms = query_latency_ms + policy_ms

    deadline_feasible = transition_duration_s <= deadline_s if condition == "ea_attack" else None

    return {
        "condition": condition,
        "decision_time_uid": decision_uid,
        "commit_time_uid": current_uid,
        "transition_duration_s": transition_duration_s,
        "deadline_feasible": deadline_feasible,
        "query_latency_ms": query_latency_ms,
        "policy_ms": policy_ms,
        "gate_latency_ms": gate_latency_ms,
        "decision": decision,
    }


def main():
    protocol = verify_protocol_hash()
    print("Protocol hash verified OK.")

    plan = derive_critical_predicates(SCENARIO, protocol["deadline_s"], "C2", "balanced")
    print("Plan:", plan["critical_predicates"], "cost", plan["query_cost_units"])

    out_dir = ROOT / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = out_dir / "raw_trials.jsonl"
    if raw_path.exists():
        raise SystemExit(f"{raw_path} already exists; refusing to overwrite.")

    expected = protocol["expected_decision"]
    n_reps = protocol["repetitions_per_condition"]
    deadline_s = protocol["deadline_s"]

    rows = []
    with open(raw_path, "w", encoding="utf-8") as f:
        for condition in protocol["conditions"]:
            for rep in range(1, n_reps + 1):
                r = run_trial(condition, deadline_s)
                r["rep"] = rep
                r["expected_decision"] = expected[condition]
                r["matches_expected"] = r["decision"] == expected[condition]
                rows.append(r)
                f.write(json.dumps(r) + "\n")
                f.flush()
                td = r["transition_duration_s"]
                print(f"{condition:14s} rep {rep:2d}: decision={r['decision']:12s} "
                      f"expected={expected[condition]:12s} match={r['matches_expected']} "
                      f"transition_s={td:.4f} deadline_feasible={r['deadline_feasible']}")

    n = len(rows)
    mismatches = sum(not r["matches_expected"] for r in rows)
    attack_rows = [r for r in rows if r["condition"] == "ea_attack"]
    feasible = sum(bool(r["deadline_feasible"]) for r in attack_rows)
    durations = [r["transition_duration_s"] for r in attack_rows]
    summary = {
        "n_total": n,
        "mismatches": mismatches,
        "attack_trials": len(attack_rows),
        "deadline_feasible_count": feasible,
        "deadline_feasible_rate": feasible / len(attack_rows),
        "transition_duration_s_min": min(durations),
        "transition_duration_s_max": max(durations),
        "transition_duration_s_mean": sum(durations) / len(durations),
        "deadline_s": deadline_s,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print("\nSUMMARY:", json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
