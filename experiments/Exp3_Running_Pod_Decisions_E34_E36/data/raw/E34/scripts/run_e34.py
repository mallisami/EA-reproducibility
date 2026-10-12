#!/usr/bin/env python3
"""E34: independent-host running-workload end-to-end validation.

Runs only against the disposable kind cluster named in E34_PROTOCOL.json.
Uses the locked DCJI planner and commit evaluator from the V26 package.
No LANL data and no external human validator are required.
"""
from __future__ import annotations

import copy
import hashlib
import json
import random
import sys
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
PKG_ROOT = ROOT.parents[1]
sys.path.insert(0, str(PKG_ROOT / "02_locked_methodology_v8.1.1" / "deterministic"))
from flashir_core.dcji import derive_critical_predicates  # noqa: E402
from flashir_core.expr import commit_components  # noqa: E402

PROTOCOL_PATH = ROOT / "E34_PROTOCOL.json"
PROTOCOL_HASH_PATH = ROOT / "E34_PROTOCOL.sha256"
SCENARIO_PATH = ROOT / "scenarios" / "E34_RUNNING_WORKLOAD.json"
PROTOCOL = json.loads(PROTOCOL_PATH.read_text())
SCENARIO = json.loads(SCENARIO_PATH.read_text())
PROXY = f"http://127.0.0.1:{PROTOCOL['proxy_port']}"
NS = PROTOCOL["namespace"]
POD_NAME = "flashir-running-target"
DEADLINE_S = float(PROTOCOL["deadline_s"])
N_REPS = int(PROTOCOL["repetitions_per_guard_condition"])
IMAGE = PROTOCOL["workload_image"]


def verify_protocol_hash() -> None:
    actual = hashlib.sha256(PROTOCOL_PATH.read_bytes()).hexdigest()
    recorded = PROTOCOL_HASH_PATH.read_text().split()[0]
    if actual != recorded:
        raise SystemExit(f"E34 protocol hash mismatch: {actual} != {recorded}")


def now_ns() -> int:
    return time.monotonic_ns()


def pod_url() -> str:
    return f"{PROXY}/api/v1/namespaces/{NS}/pods/{POD_NAME}"


def pods_url() -> str:
    return f"{PROXY}/api/v1/namespaces/{NS}/pods"


def request_json(url: str, *, method: str = "GET", body=None, content_type: str = "application/json", timeout: float = 10.0):
    data = None if body is None else json.dumps(body).encode()
    req = Request(url, data=data, method=method, headers={"Content-Type": content_type})
    with urlopen(req, timeout=timeout) as r:
        raw = r.read()
        return (json.loads(raw) if raw else None), getattr(r, "status", 200), len(raw)


def get_pod(*, allow_missing: bool = False):
    try:
        obj, _, _ = request_json(pod_url())
        return obj
    except HTTPError as e:
        if allow_missing and e.code == 404:
            return None
        raise


def pod_ready(obj) -> bool:
    if not obj or obj.get("status", {}).get("phase") != "Running":
        return False
    for c in obj.get("status", {}).get("conditions", []):
        if c.get("type") == "Ready" and c.get("status") == "True":
            return True
    return False


def wait_ready(timeout_s: float = 60.0):
    end = time.monotonic() + timeout_s
    last = None
    while time.monotonic() < end:
        try:
            last = get_pod(allow_missing=True)
            if pod_ready(last):
                return last
        except Exception:
            pass
        time.sleep(0.15)
    raise RuntimeError(f"Pod did not become Running+Ready within {timeout_s}s; last={last}")


def wait_gone(timeout_s: float = 60.0):
    """Return monotonic timestamp when Pod absence is first observed, else None.

    The observation time is a conservative upper bound on the first externally
    effective deletion effect; the actual server-side effect may occur earlier.
    """
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        if get_pod(allow_missing=True) is None:
            return now_ns()
        time.sleep(0.08)
    return None


def pod_manifest() -> dict:
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": POD_NAME,
            "namespace": NS,
            "labels": {
                "app": POD_NAME,
                "incident": "true",
                "response-allowed": "true",
                "irrelevant": "base",
            },
        },
        "spec": {
            "containers": [{
                "name": "web",
                "image": IMAGE,
                "imagePullPolicy": "IfNotPresent",
                "ports": [{"containerPort": 80}],
                "readinessProbe": {
                    "httpGet": {"path": "/", "port": 80},
                    "initialDelaySeconds": 1,
                    "periodSeconds": 1,
                    "timeoutSeconds": 1,
                    "failureThreshold": 15,
                },
            }],
            "restartPolicy": "Never",
        },
    }


def delete_pod(*, expected_uid=None, tolerate_missing: bool = True):
    body = {"apiVersion": "v1", "kind": "DeleteOptions", "gracePeriodSeconds": 0}
    if expected_uid is not None:
        body["preconditions"] = {"uid": expected_uid}
    t0 = now_ns()
    try:
        _, status, _ = request_json(pod_url(), method="DELETE", body=body)
        t1 = now_ns()
        return {"accepted": True, "status": status, "http_error": None, "dispatch_ns": t0, "response_ns": t1}
    except HTTPError as e:
        t1 = now_ns()
        if tolerate_missing and e.code == 404:
            return {"accepted": True, "status": 404, "http_error": "already_missing", "dispatch_ns": t0, "response_ns": t1}
        return {"accepted": False, "status": e.code, "http_error": str(e), "dispatch_ns": t0, "response_ns": t1}


def create_running_pod(timeout_s: float = 30.0):
    obj, status, _ = request_json(pods_url(), method="POST", body=pod_manifest())
    if status not in (200, 201, 202):
        raise RuntimeError(f"Unexpected Pod create status {status}: {obj}")
    return wait_ready(timeout_s)


def reset_running_pod():
    existing = get_pod(allow_missing=True)
    if existing is not None:
        delete_pod(tolerate_missing=True)
        if wait_gone(60) is None:
            raise RuntimeError("Could not remove prior test Pod during reset")
    obj = create_running_pod(60)
    return obj


def patch_labels(labels: dict):
    body = {"metadata": {"labels": labels}}
    obj, status, _ = request_json(
        pod_url(), method="PATCH", body=body,
        content_type="application/merge-patch+json"
    )
    if status not in (200, 201):
        raise RuntimeError(f"Unexpected patch status {status}")
    return obj


def recheck_platform_authorization():
    """Re-evaluate current Kubernetes permission to delete Pods in this namespace.

    This check is intentionally separate from the optimized DCJI observation
    certificate: platform authorization is mandatory at commit time.
    """
    body = {
        "apiVersion": "authorization.k8s.io/v1",
        "kind": "SelfSubjectAccessReview",
        "spec": {
            "resourceAttributes": {
                "namespace": NS,
                "verb": "delete",
                "resource": "pods",
            }
        },
    }
    url = f"{PROXY}/apis/authorization.k8s.io/v1/selfsubjectaccessreviews"
    t0 = now_ns()
    obj, status, payload_bytes = request_json(url, method="POST", body=body)
    t1 = now_ns()
    allowed = bool((obj or {}).get("status", {}).get("allowed", False))
    return {
        "allowed": allowed,
        "status": status,
        "payload_bytes": payload_bytes,
        "latency_ms": (t1 - t0) / 1e6,
        "reason": (obj or {}).get("status", {}).get("reason"),
    }


def live_observation():
    t0 = now_ns()
    obj, status, payload_bytes = request_json(pod_url())
    t1 = now_ns()
    labels = obj.get("metadata", {}).get("labels", {})
    return {
        "uid": obj["metadata"]["uid"],
        "resource_version": obj["metadata"].get("resourceVersion"),
        "scope_ok": labels.get("response-allowed") == "true",
        "phase": obj.get("status", {}).get("phase"),
        "ready": pod_ready(obj),
        "payload_bytes": payload_bytes,
        "refresh_ms": (t1 - t0) / 1e6,
    }


def evaluate_gate(decision_uid: str, obs: dict, guard: str):
    scenario = copy.deepcopy(SCENARIO)
    scenario["predicates"]["p_binding"]["value"] = decision_uid
    state = copy.deepcopy(scenario["initial_state"])
    state["facts"]["current_uid"] = obs["uid"]
    state["facts"]["scope_ok"] = bool(obs["scope_ok"])
    state["facts"]["action_authorized"] = bool(obs["authorization_allowed"])
    t0 = now_ns()
    comp = commit_components(scenario, state, "balanced")
    t1 = now_ns()
    decision = "EXECUTE" if comp["valid"] else "ABORT_REPLAN"
    return decision, (t1 - t0) / 1e6, comp


def apply_condition(condition: str, decision_uid: str):
    t0 = now_ns()
    if condition == "stable":
        pass
    elif condition == "irrelevant_mutation":
        patch_labels({"irrelevant": f"mut-{time.time_ns()}"})
    elif condition == "scope_drift":
        patch_labels({"response-allowed": "false"})
    elif condition == "binding_rebind":
        d = delete_pod(tolerate_missing=False)
        if not d["accepted"]:
            raise RuntimeError(f"Could not delete target for rebinding transition: {d}")
        if wait_gone(60) is None:
            raise RuntimeError("Old Pod did not disappear during rebinding transition")
        create_running_pod(60)
    else:
        raise ValueError(condition)
    t1 = now_ns()
    current = get_pod()
    return {
        "transition_duration_s": (t1 - t0) / 1e9,
        "post_transition_uid": current["metadata"]["uid"],
        "uid_changed": current["metadata"]["uid"] != decision_uid,
    }


def normal_guard_trial(guard: str, condition: str):
    initial = reset_running_pod()
    decision_uid = initial["metadata"]["uid"]
    decision_rv = initial["metadata"].get("resourceVersion")
    t_decision = now_ns()
    transition = apply_condition(condition, decision_uid)
    auth = recheck_platform_authorization()
    obs = live_observation()
    obs["authorization_allowed"] = auth["allowed"]
    decision, policy_ms, comp = evaluate_gate(decision_uid, obs, guard)
    t_gate = now_ns()

    action = None
    effect_observed_ns = None
    effect_verified = False
    affected_uid = None
    if decision == "EXECUTE":
        # The protocol does not inject another mutation after the gate. We still
        # record the pre-dispatch UID and conservatively observe the actual delete
        # effect (Pod absence) rather than treating client dispatch as actuation.
        pre_action = get_pod()
        affected_uid = pre_action["metadata"]["uid"]
        action = delete_pod(tolerate_missing=False)
        if not action["accepted"]:
            raise RuntimeError(f"Accepted gate but delete action failed: {action}")
        effect_observed_ns = wait_gone(60)
        effect_verified = effect_observed_ns is not None

    expected = "ABORT_REPLAN" if condition in {"binding_rebind", "scope_drift"} else "EXECUTE"
    wrong_target_affected = bool(
        decision == "EXECUTE" and affected_uid is not None and affected_uid != decision_uid
    )
    if decision == "EXECUTE":
        horizon_reference_ns = effect_observed_ns if effect_observed_ns is not None else action["response_ns"]
        horizon_semantics = "decision_to_effect_observation_upper_bound"
    else:
        horizon_reference_ns = t_gate
        horizon_semantics = "decision_to_gate_for_abort"
    horizon_elapsed_s = (horizon_reference_ns - t_decision) / 1e9
    return {
        "guard": guard,
        "condition": condition,
        "decision_time_uid": decision_uid,
        "decision_time_resource_version": decision_rv,
        "post_transition_uid": transition["post_transition_uid"],
        "uid_changed": transition["uid_changed"],
        "transition_duration_s": transition["transition_duration_s"],
        "refresh_ms": obs["refresh_ms"],
        "authorization_recheck_ms": auth["latency_ms"],
        "authorization_allowed_at_gate": auth["allowed"],
        "authorization_http_status": auth["status"],
        "policy_ms": policy_ms,
        "gate_elapsed_from_decision_s": (t_gate - t_decision) / 1e9,
        "horizon_elapsed_s": horizon_elapsed_s,
        "deadline_s": DEADLINE_S,
        "horizon_feasible": horizon_elapsed_s <= DEADLINE_S,
        "decision": decision,
        "expected_decision": expected,
        "matches_expected": decision == expected,
        "commit_valid": bool(comp["valid"]),
        "action_sent": action is not None,
        "action_http_status": None if action is None else action["status"],
        "action_dispatch_elapsed_from_decision_s": None if action is None else (action["dispatch_ns"] - t_decision) / 1e9,
        "action_response_elapsed_from_decision_s": None if action is None else (action["response_ns"] - t_decision) / 1e9,
        "effect_observed_elapsed_from_decision_s": None if effect_observed_ns is None else (effect_observed_ns - t_decision) / 1e9,
        "horizon_semantics": horizon_semantics,
        "action_effect_verified": effect_verified,
        "affected_uid": affected_uid,
        "wrong_target_affected": wrong_target_affected,
        "running_ready_at_refresh": bool(obs["phase"] == "Running" and obs["ready"]),
        "scope_ok_at_refresh": obs["scope_ok"],
    }


def native_precondition_trial(condition: str):
    initial = reset_running_pod()
    decision_uid = initial["metadata"]["uid"]
    t_decision = now_ns()
    transition = apply_condition(condition, decision_uid)
    # Re-evaluate platform authorization separately; the UID precondition is the
    # atomic object-identity guard, not a replacement for authorization.
    auth = recheck_platform_authorization()
    # The server-side UID precondition is the guard; scope_drift is intentionally not applicable.
    before = get_pod()
    current_uid = before["metadata"]["uid"]
    if not auth["allowed"]:
        raise RuntimeError("Commit-time Kubernetes SelfSubjectAccessReview denied Pod deletion; the disposable kind experiment requires delete permission.")
    action = delete_pod(expected_uid=decision_uid, tolerate_missing=False)
    effect_observed_ns = None
    if action["accepted"]:
        effect_observed_ns = wait_gone(60)
        effect_verified = effect_observed_ns is not None
        decision = "EXECUTE"
    else:
        effect_verified = False
        decision = "ABORT_REPLAN" if action["status"] in (409, 422) else "ERROR"
    expected = "ABORT_REPLAN" if condition == "binding_rebind" else "EXECUTE"
    wrong_target_affected = bool(action["accepted"] and current_uid != decision_uid)
    if action["accepted"]:
        horizon_reference_ns = effect_observed_ns if effect_observed_ns is not None else action["response_ns"]
        horizon_semantics = "decision_to_effect_observation_upper_bound"
    else:
        horizon_reference_ns = action["response_ns"]
        horizon_semantics = "decision_to_server_precondition_response"
    elapsed_s = (horizon_reference_ns - t_decision) / 1e9
    return {
        "guard": "NATIVE_UID_PRECONDITION",
        "condition": condition,
        "decision_time_uid": decision_uid,
        "post_transition_uid": transition["post_transition_uid"],
        "uid_changed": transition["uid_changed"],
        "transition_duration_s": transition["transition_duration_s"],
        "authorization_recheck_ms": auth["latency_ms"],
        "authorization_allowed_at_gate": auth["allowed"],
        "authorization_http_status": auth["status"],
        "gate_elapsed_from_decision_s": elapsed_s,
        "horizon_elapsed_s": elapsed_s,
        "deadline_s": DEADLINE_S,
        "horizon_feasible": elapsed_s <= DEADLINE_S,
        "decision": decision,
        "expected_decision": expected,
        "matches_expected": decision == expected,
        "action_sent": True,
        "action_http_status": action["status"],
        "action_dispatch_elapsed_from_decision_s": (action["dispatch_ns"] - t_decision) / 1e9,
        "action_response_elapsed_from_decision_s": (action["response_ns"] - t_decision) / 1e9,
        "effect_observed_elapsed_from_decision_s": None if effect_observed_ns is None else (effect_observed_ns - t_decision) / 1e9,
        "horizon_semantics": horizon_semantics,
        "action_effect_verified": effect_verified,
        "affected_uid": current_uid if action["accepted"] else None,
        "wrong_target_affected": wrong_target_affected,
        "scope_ok_at_refresh": before.get("metadata", {}).get("labels", {}).get("response-allowed") == "true",
        "note": "Native UID precondition is not evaluated for scope_drift because UID-only atomicity does not encode the scope predicate."
    }


def main():
    verify_protocol_hash()
    plan = derive_critical_predicates(SCENARIO, DEADLINE_S, "C2", "balanced")
    if plan["status"] != "EXPLOITABLE":
        raise SystemExit(f"Unexpected E34 planner status: {plan}")
    expected_qids = {"query:/facts/current_uid", "query:/facts/scope_ok"}
    if set(plan["critical_query_ids"]) != expected_qids:
        raise SystemExit(f"Unexpected E34 certificate: {plan}")

    out = ROOT / "results"
    out.mkdir(exist_ok=True)
    raw = out / "raw_trials.jsonl"
    summary_path = out / "summary.json"
    if raw.exists() and raw.stat().st_size > 0:
        raise SystemExit(f"Refusing to overwrite existing E34 results: {raw}")

    jobs = []
    for guard in PROTOCOL["guards"]:
        for condition in PROTOCOL["conditions"]:
            for rep in range(1, N_REPS + 1):
                jobs.append((guard, condition, rep))
    for condition in PROTOCOL["native_precondition_conditions"]:
        for rep in range(1, N_REPS + 1):
            jobs.append(("NATIVE_UID_PRECONDITION", condition, rep))
    rng = random.Random(PROTOCOL["random_seed"])
    rng.shuffle(jobs)

    rows = []
    with raw.open("w", encoding="utf-8") as fh:
        for i, (guard, condition, rep) in enumerate(jobs, 1):
            if guard == "NATIVE_UID_PRECONDITION":
                row = native_precondition_trial(condition)
            else:
                row = normal_guard_trial(guard, condition)
            row.update({"trial_index": i, "rep": rep, "planner_certificate_query_ids": plan["critical_query_ids"]})
            rows.append(row)
            fh.write(json.dumps(row, sort_keys=True) + "\n")
            fh.flush()
            print(f"E34 {i:03d}/{len(jobs)} {guard:24s} {condition:20s} decision={row['decision']:12s} match={row['matches_expected']} horizon={row['horizon_feasible']} wrong_target={row['wrong_target_affected']}")

    # Leave one clean running Pod at the end for post-run inspection.
    reset_running_pod()

    mismatches = [r for r in rows if not r["matches_expected"]]
    wrong = [r for r in rows if r["wrong_target_affected"]]
    execute_rows = [r for r in rows if r["decision"] == "EXECUTE"]
    failed_effects = [r for r in execute_rows if not r["action_effect_verified"]]
    abort_rows = [r for r in rows if r["decision"] == "ABORT_REPLAN"]
    sent_after_abort = [r for r in abort_rows if r["action_sent"] and r["guard"] != "NATIVE_UID_PRECONDITION"]
    horizon_misses = [r for r in rows if not r["horizon_feasible"]]
    execute_effect_horizon_misses = [r for r in rows if r["decision"] == "EXECUTE" and not r["horizon_feasible"]]
    abort_or_reject_horizon_misses = [r for r in rows if r["decision"] != "EXECUTE" and not r["horizon_feasible"]]
    by_guard_condition = {}
    for r in rows:
        k = f"{r['guard']}|{r['condition']}"
        s = by_guard_condition.setdefault(k, {"n": 0, "mismatches": 0, "wrong_target_effects": 0, "horizon_misses": 0})
        s["n"] += 1
        s["mismatches"] += int(not r["matches_expected"])
        s["wrong_target_effects"] += int(r["wrong_target_affected"])
        s["horizon_misses"] += int(not r["horizon_feasible"])

    summary = {
        "experiment": "E34",
        "n_trials": len(rows),
        "planner_certificate_query_ids": plan["critical_query_ids"],
        "decision_mismatches": len(mismatches),
        "wrong_target_effects": len(wrong),
        "accepted_actions_without_verified_effect": len(failed_effects),
        "client_guard_actions_sent_after_abort": len(sent_after_abort),
        "horizon_misses": len(horizon_misses),
        "execute_effect_horizon_misses": len(execute_effect_horizon_misses),
        "abort_or_reject_horizon_misses": len(abort_or_reject_horizon_misses),
        "horizon_interpretation": "EXECUTE rows use decision-to-observed-Pod-absence as a conservative upper bound on first effective deletion; client ABORT rows use decision-to-gate, and rejected native-precondition rows use decision-to-server-response.",
        "by_guard_condition": by_guard_condition,
        "pass_criteria": {
            "decision_mismatches": 0,
            "wrong_target_effects": 0,
            "accepted_actions_without_verified_effect": 0,
            "client_guard_actions_sent_after_abort": 0
        },
        "pass": not mismatches and not wrong and not failed_effects and not sent_after_abort,
        "scope_note": "Native UID precondition is intentionally not run for scope_drift because UID-only atomicity does not encode the scope predicate."
    }
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True))
    print("\nE34 SUMMARY\n" + json.dumps(summary, indent=2, sort_keys=True))
    if not summary["pass"]:
        raise SystemExit("E34 completed but failed one or more safety/effect criteria; preserve results and return them for analysis.")


if __name__ == "__main__":
    main()
