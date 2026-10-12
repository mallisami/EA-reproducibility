#!/usr/bin/env python3
"""
E26 -- Independent-Cluster Replication of E21's Primary Confirmatory
Condition (idle load, sequential acquisition, all 3 conditions).

Runs against a genuinely separate kind cluster (flashir-e26, created fresh
for this experiment, not a restart of flashir-e13) through its own
kubectl proxy on a distinct port. Imports the locked, unmodified
flashir_core engine exactly as E13/E21/E23/E25 do.
"""
import copy, json, random, sys, time
from pathlib import Path
from urllib.request import urlopen, Request

ROOT = Path(__file__).resolve().parents[1]
PKG_ROOT = ROOT.parents[1]
sys.path.insert(0, str(PKG_ROOT / "02_locked_methodology_v8.1.1" / "deterministic"))
from flashir_core.dcji import derive_critical_predicates
from flashir_core.expr import commit_components

PROTOCOL_PATH = ROOT / "E26_PROTOCOL.json"
PROTOCOL_HASH_PATH = ROOT / "E26_PROTOCOL.sha256"
SCENARIO = json.loads((ROOT / "scenarios" / "F5_PHYS.json").read_text())
PROXY = "http://127.0.0.1:8002"
NS = "flashir-e21"
FLAGS = ["flag_a", "flag_b", "flag_c", "flag_d", "flag_e", "flag_f"]
RESOURCE_OF = {"flag_a": "obs-a", "flag_b": "obs-b", "flag_c": "obs-c",
               "flag_d": "obs-d", "flag_e": "obs-e", "flag_f": "obs-f"}
ATTACK_FLAGS = ["flag_a", "flag_c", "flag_e"]
EXPECTED_DECISION = {"stable": "EXECUTE", "ea_attack": "ABORT_REPLAN", "irrelevant_mutation": "EXECUTE"}


def verify_protocol_hash():
    import hashlib
    actual = hashlib.sha256(PROTOCOL_PATH.read_bytes()).hexdigest()
    recorded = PROTOCOL_HASH_PATH.read_text().split()[0]
    if actual != recorded:
        raise SystemExit(f"PROTOCOL HASH MISMATCH: file={actual} recorded={recorded} -- refusing to run")
    return json.loads(PROTOCOL_PATH.read_text())


def cm_url(resource):
    return f"{PROXY}/api/v1/namespaces/{NS}/configmaps/{resource}"


def get_observation(flag, retries=3):
    resource = RESOURCE_OF[flag]
    last_err = None
    for attempt in range(retries):
        try:
            t0 = time.perf_counter_ns()
            with urlopen(cm_url(resource), timeout=5) as response:
                body = response.read()
            t1 = time.perf_counter_ns()
            obj = json.loads(body)
            return {"flag": flag, "resource": resource,
                    "value": obj["data"].get("valid") == "true",
                    "latency_ns": t1 - t0, "payload_bytes": len(body)}
        except Exception as e:
            last_err = e
            time.sleep(0.1)
    raise RuntimeError(f"get_observation({flag}) failed after {retries} attempts: {last_err}")


def set_observation(flag, value, retries=5):
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
            time.sleep(0.2 * (attempt + 1))
    raise RuntimeError(f"set_observation({flag}) failed after {retries} attempts: {last_err}")


def reset_all_true():
    for f in FLAGS:
        set_observation(f, True)


def apply_condition(condition):
    reset_all_true()
    time.sleep(0.05)
    if condition == "ea_attack":
        for flag in ATTACK_FLAGS:
            set_observation(flag, False)
    elif condition == "irrelevant_mutation":
        body = json.dumps({"data": {"note": f"mut-{time.time()}"}}).encode()
        req = Request(cm_url("obs-irrelevant"), data=body, method="PATCH",
                      headers={"Content-Type": "application/merge-patch+json"})
        urlopen(req, timeout=5).read()
    time.sleep(0.05)


def acquire(flags_to_query):
    t0 = time.perf_counter_ns()
    records = [get_observation(f) for f in flags_to_query]
    t1 = time.perf_counter_ns()
    obs = {r["flag"]: r["value"] for r in records}
    return obs, records, (t1 - t0)


def evaluate_gate(flags_to_query):
    cached = copy.deepcopy(SCENARIO["initial_state"])
    obs, records, refresh_ns = acquire(flags_to_query)
    state = copy.deepcopy(cached)
    for f in FLAGS:
        state["facts"][f] = obs[f] if f in flags_to_query else cached["facts"][f]
    tp0 = time.perf_counter_ns()
    comp = commit_components(SCENARIO, state, "balanced")
    tp1 = time.perf_counter_ns()
    decision = "EXECUTE" if comp["valid"] else "ABORT_REPLAN"
    return {
        "decision": decision, "request_count": len(flags_to_query),
        "refresh_ms": refresh_ns / 1e6, "policy_ms": (tp1 - tp0) / 1e6,
        "gate_ms": (refresh_ns + (tp1 - tp0)) / 1e6,
        "payload_bytes": sum(r["payload_bytes"] for r in records),
        "unsafe": decision == "EXECUTE" and not comp["valid"],
    }


def main():
    protocol = verify_protocol_hash()
    print("Protocol hash verified OK. Running against cluster:", protocol["cluster_name"])

    plan = derive_critical_predicates(SCENARIO, 5.0, "C2", "balanced")
    dcji_qids = plan["critical_predicates"]
    full_qids = FLAGS
    print("DCJI plan:", dcji_qids, "cost", plan["query_cost_units"])

    out_dir = ROOT / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = out_dir / "raw_trials.jsonl"
    if raw_path.exists():
        raise SystemExit(f"{raw_path} already exists; refusing to overwrite.")

    rng = random.Random(protocol["random_seed"])
    n_warmup = protocol["warmup_pairs_per_condition"]
    n_measured = protocol["measured_pairs_per_condition"]

    trial_counter = 0
    rows = []
    with open(raw_path, "w", encoding="utf-8") as f:
        for condition in protocol["conditions"]:
            print(f"=== {condition}: warm-up ({n_warmup} pairs) ===")
            for _ in range(n_warmup):
                apply_condition(condition)
                order = rng.choice(["DCJI_FIRST", "FULL_FIRST"])
                if order == "DCJI_FIRST":
                    evaluate_gate(dcji_qids); evaluate_gate(full_qids)
                else:
                    evaluate_gate(full_qids); evaluate_gate(dcji_qids)

            print(f"=== {condition}: {n_measured} measured pairs ===")
            for rep in range(1, n_measured + 1):
                trial_counter += 1
                apply_condition(condition)
                order = rng.choice(["DCJI_FIRST", "FULL_FIRST"])
                if order == "DCJI_FIRST":
                    d = evaluate_gate(dcji_qids); fr = evaluate_gate(full_qids)
                else:
                    fr = evaluate_gate(full_qids); d = evaluate_gate(dcji_qids)
                row = {
                    "trial_id": trial_counter, "condition": condition, "rep": rep, "guard_order": order,
                    "dcji_request_count": d["request_count"], "full_request_count": fr["request_count"],
                    "dcji_refresh_ms": d["refresh_ms"], "full_refresh_ms": fr["refresh_ms"],
                    "dcji_gate_ms": d["gate_ms"], "full_gate_ms": fr["gate_ms"],
                    "dcji_payload_bytes": d["payload_bytes"], "full_payload_bytes": fr["payload_bytes"],
                    "dcji_decision": d["decision"], "full_decision": fr["decision"],
                    "decision_discordant": d["decision"] != fr["decision"],
                    "expected_decision": EXPECTED_DECISION[condition],
                    "dcji_matches_expected": d["decision"] == EXPECTED_DECISION[condition],
                    "full_matches_expected": fr["decision"] == EXPECTED_DECISION[condition],
                }
                rows.append(row)
                f.write(json.dumps(row) + "\n")
                f.flush()
            print(f"  {condition} done ({trial_counter} total trials so far)")

    n = len(rows)
    discordant = sum(r["decision_discordant"] for r in rows)
    mismatched = sum(not r["dcji_matches_expected"] or not r["full_matches_expected"] for r in rows)
    diffs = [r["dcji_refresh_ms"] - r["full_refresh_ms"] for r in rows]
    boot_reps = protocol["bootstrap_replicates"]
    rng2 = random.Random(protocol["random_seed"] + 1)
    boot_means = []
    for _ in range(boot_reps):
        sample = [diffs[rng2.randrange(n)] for _ in range(n)]
        boot_means.append(sum(sample) / n)
    boot_means.sort()
    ci_lo = boot_means[int(0.025 * (boot_reps - 1))]
    ci_hi = boot_means[int(0.975 * (boot_reps - 1))]

    summary = {
        "n_total": n, "discordant": discordant, "mismatched_expected": mismatched,
        "mean_dcji_request_count": sum(r["dcji_request_count"] for r in rows) / n,
        "mean_full_request_count": sum(r["full_request_count"] for r in rows) / n,
        "mean_paired_refresh_diff_ms": sum(diffs) / n,
        "bootstrap_ci95_refresh_diff_ms": [ci_lo, ci_hi],
        "median_dcji_gate_ms": sorted(r["dcji_gate_ms"] for r in rows)[n // 2],
        "median_full_gate_ms": sorted(r["full_gate_ms"] for r in rows)[n // 2],
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print("\nSUMMARY:", json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
