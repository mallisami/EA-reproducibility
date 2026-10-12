#!/usr/bin/env python3
"""
E25 -- Physical All-Predicate Multi-Cut Benchmark (Symmetric Threat Model).

Physically re-derives, via live Kubernetes trials, the E22 theoretical
finding that under a symmetric attacker model (either member of each F5
pair may be cut) the minimum certificate is all six predicates and DCJI
predicts NO reduction over FullRefresh. Imports the locked, unmodified
flashir_core engine exactly as E13/E21/E23 do. Uses a dedicated namespace
(flashir-e25), not E21's or E22's.
"""
import copy, json, sys, time
from pathlib import Path
from urllib.request import urlopen, Request

ROOT = Path(__file__).resolve().parents[1]
PKG_ROOT = ROOT.parents[1]
sys.path.insert(0, str(PKG_ROOT / "02_locked_methodology_v8.1.1" / "deterministic"))
from flashir_core.dcji import derive_critical_predicates
from flashir_core.expr import commit_components

PROTOCOL_PATH = ROOT / "E25_PROTOCOL.json"
PROTOCOL_HASH_PATH = ROOT / "E25_PROTOCOL.sha256"
SCENARIO = json.loads((ROOT / "scenarios" / "F5_PHYS_SYM.json").read_text())
PROXY = "http://127.0.0.1:8001"
NS = "flashir-e25"
FLAGS = ["flag_a", "flag_b", "flag_c", "flag_d", "flag_e", "flag_f"]
RESOURCE_OF = {"flag_a": "obs-a", "flag_b": "obs-b", "flag_c": "obs-c",
               "flag_d": "obs-d", "flag_e": "obs-e", "flag_f": "obs-f"}
RESOURCE_TO_FLAG = {v: k for k, v in RESOURCE_OF.items()}


def verify_protocol_hash():
    import hashlib
    actual = hashlib.sha256(PROTOCOL_PATH.read_bytes()).hexdigest()
    recorded = PROTOCOL_HASH_PATH.read_text().split()[0]
    if actual != recorded:
        raise SystemExit(f"PROTOCOL HASH MISMATCH: file={actual} recorded={recorded} -- refusing to run")
    return json.loads(PROTOCOL_PATH.read_text())


def cm_url(resource):
    return f"{PROXY}/api/v1/namespaces/{NS}/configmaps/{resource}"


def get_obs(flag, retries=3):
    resource = RESOURCE_OF[flag]
    last_err = None
    for attempt in range(retries):
        try:
            t0 = time.perf_counter_ns()
            with urlopen(cm_url(resource), timeout=5) as r:
                body = r.read()
            t1 = time.perf_counter_ns()
            obj = json.loads(body)
            return {"flag": flag, "resource": resource,
                    "value": obj["data"].get("valid") == "true",
                    "latency_ns": t1 - t0, "payload_bytes": len(body)}
        except Exception as e:
            last_err = e
            time.sleep(0.1)
    raise RuntimeError(f"get_obs({flag}) failed after {retries} attempts: {last_err}")


def set_obs(flag, value, retries=3):
    resource = RESOURCE_OF[flag]
    body = json.dumps({"data": {"valid": "true" if value else "false"}}).encode()
    last_err = None
    for attempt in range(retries):
        try:
            req = Request(cm_url(resource), data=body, method="PATCH",
                          headers={"Content-Type": "application/merge-patch+json"})
            with urlopen(req, timeout=5) as r:
                r.read()
            return
        except Exception as e:
            last_err = e
            time.sleep(0.1)
    raise RuntimeError(f"set_obs({flag}) failed after {retries} attempts: {last_err}")


def reset_all_true():
    for f in FLAGS:
        set_obs(f, True)


def apply_cut(cut_resources):
    for resource in cut_resources:
        set_obs(RESOURCE_TO_FLAG[resource], False)


def acquire(flags_to_query):
    t0 = time.perf_counter_ns()
    records = [get_obs(f) for f in flags_to_query]
    t1 = time.perf_counter_ns()
    return {r["flag"]: r["value"] for r in records}, records, (t1 - t0) / 1e6


def evaluate_gate(flags_to_query):
    cached = copy.deepcopy(SCENARIO["initial_state"])
    obs, records, latency_ms = acquire(flags_to_query)
    state = copy.deepcopy(cached)
    for f in FLAGS:
        state["facts"][f] = obs[f] if f in flags_to_query else cached["facts"][f]
    comp = commit_components(SCENARIO, state, "balanced")
    decision = "EXECUTE" if comp["valid"] else "ABORT_REPLAN"
    return {
        "decision": decision, "request_count": len(flags_to_query),
        "latency_ms": latency_ms,
        "payload_bytes": sum(r["payload_bytes"] for r in records),
    }


def main():
    protocol = verify_protocol_hash()
    print("Protocol hash verified OK.")

    plan = derive_critical_predicates(SCENARIO, 5.0, "C2", "balanced")
    dcji_qids = plan["critical_predicates"]
    full_qids = FLAGS
    print("DCJI plan:", dcji_qids, "cost", plan["query_cost_units"])
    assert set(dcji_qids) == set(FLAGS), \
        f"Expected symmetric threat model to force the full 6-predicate certificate; got {dcji_qids}"

    out_dir = ROOT / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = out_dir / "raw_trials.jsonl"
    if raw_path.exists():
        raise SystemExit(f"{raw_path} already exists; refusing to overwrite.")

    n_reps = protocol["repetitions_per_condition"]
    rows = []
    with open(raw_path, "w", encoding="utf-8") as f:
        for cond in protocol["conditions"]:
            for rep in range(1, n_reps + 1):
                reset_all_true()
                time.sleep(0.05)
                apply_cut(cond["cut"])
                time.sleep(0.05)

                dcji = evaluate_gate(dcji_qids)
                full = evaluate_gate(full_qids)

                row = {
                    "condition": cond["id"], "rep": rep,
                    "dcji_request_count": dcji["request_count"],
                    "full_request_count": full["request_count"],
                    "dcji_latency_ms": dcji["latency_ms"],
                    "full_latency_ms": full["latency_ms"],
                    "dcji_decision": dcji["decision"], "full_decision": full["decision"],
                    "expected_decision": cond["expected_decision"],
                    "dcji_matches_expected": dcji["decision"] == cond["expected_decision"],
                    "full_matches_expected": full["decision"] == cond["expected_decision"],
                    "decision_discordant": dcji["decision"] != full["decision"],
                    "request_count_reduction": full["request_count"] - dcji["request_count"],
                }
                rows.append(row)
                f.write(json.dumps(row) + "\n")
                f.flush()
                print(f"{cond['id']:10s} rep {rep:2d}: DCJI={dcji['decision']:12s}({dcji['request_count']}) "
                      f"FULL={full['decision']:12s}({full['request_count']}) "
                      f"reduction={row['request_count_reduction']} discordant={row['decision_discordant']}")

    reset_all_true()

    n = len(rows)
    summary = {
        "n_total": n,
        "mismatches": sum(not r["dcji_matches_expected"] or not r["full_matches_expected"] for r in rows),
        "discordant": sum(r["decision_discordant"] for r in rows),
        "mean_dcji_request_count": sum(r["dcji_request_count"] for r in rows) / n,
        "mean_full_request_count": sum(r["full_request_count"] for r in rows) / n,
        "mean_request_count_reduction": sum(r["request_count_reduction"] for r in rows) / n,
        "trials_with_nonzero_reduction": sum(r["request_count_reduction"] != 0 for r in rows),
        "planner_certificate": dcji_qids,
        "planner_cost": plan["query_cost_units"],
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print("\nSUMMARY:", json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
