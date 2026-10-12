#!/usr/bin/env python3
"""
E21 -- Independent Physical Revalidation Cost Benchmark.

Frozen protocol: E21_PROTOCOL.json (hash in E21_PROTOCOL.sha256, verified at
startup). Imports the locked, UNMODIFIED flashir_core engine
(derive_critical_predicates, commit_components) exactly as run_live_multi.py
(E13 v9 extension) does. Does not modify anything under
02_locked_methodology_v8.1.1/ or the existing v9 F5.LIVE evidence.

Design (see E21_PROTOCOL.json for the frozen numbers):
  - 6 independent Kubernetes ConfigMaps (obs-a..obs-f), each its own API
    resource -- fixes F5.LIVE's flaw where 6 labels shared one GET.
  - Every physical query is a direct HTTP GET through a long-lived
    `kubectl proxy`, not a `kubectl get` subprocess, so CLI startup does not
    contaminate the measured latency.
  - Conditions: stable (EXECUTE expected), ea_attack (ABORT_REPLAN expected),
    irrelevant_mutation (EXECUTE expected).
  - Acquisition modes: sequential (one GET after another) and parallel
    (ThreadPoolExecutor, all required GETs issued concurrently).
  - Load levels: idle (L0) and moderate background API traffic (L1, ~22 rps
    against an unrelated object, background_load.py).
  - Guard order (DCJI first vs FullRefresh first) is randomized per trial
    with a fixed seed and logged per row, to rule out cache-warming order
    effects.
  - 20 warm-up paired trials per (condition, mode, load) cell, discarded.
  - 3 blocks x 100 measured paired trials per (condition, mode, load) cell.
"""
import copy, itertools, json, random, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
PKG_ROOT = ROOT.parents[1]
sys.path.insert(0, str(PKG_ROOT / "02_locked_methodology_v8.1.1" / "deterministic"))
from flashir_core.dcji import derive_critical_predicates
from flashir_core.expr import commit_components

PROTOCOL_PATH = ROOT / "E21_PROTOCOL.json"
PROTOCOL_HASH_PATH = ROOT / "E21_PROTOCOL.sha256"
SCENARIO = json.loads((ROOT / "scenarios" / "F5_PHYS.json").read_text())
OUT = ROOT / "results"
NS = "flashir-e21"
PROXY = "http://127.0.0.1:8001"

FLAGS = ["flag_a", "flag_b", "flag_c", "flag_d", "flag_e", "flag_f"]
RESOURCE_OF = {"flag_a": "obs-a", "flag_b": "obs-b", "flag_c": "obs-c",
                "flag_d": "obs-d", "flag_e": "obs-e", "flag_f": "obs-f"}
ATTACK_FLAGS = ["flag_a", "flag_c", "flag_e"]


def verify_protocol_hash():
    import hashlib
    actual = hashlib.sha256(PROTOCOL_PATH.read_bytes()).hexdigest()
    recorded = PROTOCOL_HASH_PATH.read_text().split()[0]
    if actual != recorded:
        raise SystemExit(f"PROTOCOL HASH MISMATCH: file={actual} recorded={recorded} -- refusing to run")
    return json.loads(PROTOCOL_PATH.read_text())


def cm_url(resource):
    return f"{PROXY}/api/v1/namespaces/{NS}/configmaps/{resource}"


def get_observation(flag):
    resource = RESOURCE_OF[flag]
    t0 = time.perf_counter_ns()
    with urlopen(cm_url(resource), timeout=5) as response:
        body = response.read()
        status = response.status
    t1 = time.perf_counter_ns()
    obj = json.loads(body)
    return {
        "flag": flag, "resource": resource,
        "value": obj["data"].get("valid") == "true",
        "latency_ns": t1 - t0,
        "payload_bytes": len(body),
        "http_status": status,
        "resource_version": obj["metadata"]["resourceVersion"],
    }


def set_observation(flag, value, retries=5):
    """Setup call (not timed as part of any trial measurement) -- retried on
    transient proxy hiccups so a single dropped connection does not abort a
    multi-hour run."""
    resource = RESOURCE_OF[flag]
    body = json.dumps({"data": {"valid": "true" if value else "false"}}).encode()
    import urllib.request
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(cm_url(resource), data=body, method="PATCH",
                                          headers={"Content-Type": "application/merge-patch+json"})
            with urllib.request.urlopen(req, timeout=5) as r:
                r.read()
            return
        except Exception as e:
            last_err = e
            time.sleep(0.2 * (attempt + 1))
    raise RuntimeError(f"set_observation({flag}) failed after {retries} attempts: {last_err}")


def reset_all_true():
    for f in FLAGS:
        set_observation(f, True)


def acquire(flags_to_query, mode):
    """Returns (observations dict flag->bool, per-request records, refresh_latency_ns)."""
    t0 = time.perf_counter_ns()
    records = []
    if mode == "sequential":
        for f in flags_to_query:
            records.append(get_observation(f))
    else:  # parallel
        with ThreadPoolExecutor(max_workers=len(flags_to_query)) as ex:
            records = list(ex.map(get_observation, flags_to_query))
    t1 = time.perf_counter_ns()
    obs = {r["flag"]: r["value"] for r in records}
    return obs, records, (t1 - t0)


def evaluate_gate(flags_to_query, mode):
    cached = copy.deepcopy(SCENARIO["initial_state"])
    obs, records, refresh_ns = acquire(flags_to_query, mode)
    state = copy.deepcopy(cached)
    for f in FLAGS:
        state["facts"][f] = obs[f] if f in flags_to_query else cached["facts"][f]
    tp0 = time.perf_counter_ns()
    comp = commit_components(SCENARIO, state, "balanced")
    tp1 = time.perf_counter_ns()
    decision = "EXECUTE" if comp["valid"] else "ABORT_REPLAN"
    return {
        "decision": decision,
        "request_count": len(flags_to_query),
        "queried": list(flags_to_query),
        "refresh_ms": refresh_ns / 1e6,
        "policy_ms": (tp1 - tp0) / 1e6,
        "gate_ms": (refresh_ns + (tp1 - tp0)) / 1e6,
        "payload_bytes": sum(r["payload_bytes"] for r in records),
        "per_request": records,
        "unsafe": decision == "EXECUTE" and not comp["valid"],
    }


def run_paired_trial(dcji_qids, full_qids, mode, rng):
    order = rng.choice(["DCJI_FIRST", "FULL_FIRST"])
    if order == "DCJI_FIRST":
        d = evaluate_gate(dcji_qids, mode)
        f = evaluate_gate(full_qids, mode)
    else:
        f = evaluate_gate(full_qids, mode)
        d = evaluate_gate(dcji_qids, mode)
    return d, f, order


def apply_condition(condition):
    reset_all_true()
    time.sleep(0.05)
    if condition == "ea_attack":
        for flag in ATTACK_FLAGS:
            set_observation(flag, False)
    elif condition == "irrelevant_mutation":
        import urllib.request
        body = json.dumps({"data": {"note": f"mut-{time.time()}"}}).encode()
        req = urllib.request.Request(cm_url("obs-irrelevant"), data=body, method="PATCH",
                                      headers={"Content-Type": "application/merge-patch+json"})
        urllib.request.urlopen(req, timeout=5).read()
    time.sleep(0.05)


EXPECTED_DECISION = {"stable": "EXECUTE", "ea_attack": "ABORT_REPLAN", "irrelevant_mutation": "EXECUTE"}


def main():
    protocol = verify_protocol_hash()
    print("Protocol hash verified OK.")

    plan = derive_critical_predicates(SCENARIO, 5.0, "C2", "balanced")
    dcji_qids = plan["critical_predicates"]
    full_qids = FLAGS
    print("DCJI plan:", dcji_qids, "cost", plan["query_cost_units"])
    assert dcji_qids == protocol["planner_derived_dcji_predicates"], "planner output does not match frozen protocol expectation"

    OUT.mkdir(parents=True, exist_ok=True)
    raw_path = OUT / "raw_trials.jsonl"
    requests_path = OUT / "per_request_log.csv"
    infra_fail_path = OUT / "infra_failures.jsonl"

    completed_cells = set()
    trial_counter = 0
    if raw_path.exists():
        from collections import Counter
        existing = [json.loads(l) for l in open(raw_path, encoding="utf-8")]
        n_per_block = protocol["measured_pairs_per_block"]
        n_blocks = protocol["blocks"]
        counts = Counter((r["load"], r["mode"], r["condition"]) for r in existing)
        completed_cells = {k for k, v in counts.items() if v == n_per_block * n_blocks}
        trial_counter = max((r["trial_id"] for r in existing), default=0)
        print(f"RESUMING: {len(existing)} existing trials found, "
              f"{len(completed_cells)} cells already complete: {sorted(completed_cells)}")

    rng = random.Random(protocol["random_seed"])
    n_warmup = protocol["warmup_pairs_per_condition"]
    n_per_block = protocol["measured_pairs_per_block"]
    n_blocks = protocol["blocks"]
    conditions = protocol["conditions"]
    modes = protocol["acquisition_modes"]
    loads = protocol["load_levels"]

    from background_load import BackgroundLoad
    bl = BackgroundLoad(rps=protocol["background_load_rate_rps"])

    need_csv_header = not (requests_path.exists() and requests_path.stat().st_size > 0)
    raw_f = open(raw_path, "a", encoding="utf-8")
    req_f = open(requests_path, "a", encoding="utf-8")
    infra_f = open(infra_fail_path, "a", encoding="utf-8")
    if need_csv_header:
        req_f.write("trial_id,guard,resource,status,latency_ms,payload_bytes,resource_version\n")

    cell_summaries = []
    MAX_TRIAL_RETRIES = 5

    def with_retry(fn, *args, label=""):
        last_err = None
        for attempt in range(MAX_TRIAL_RETRIES):
            try:
                return fn(*args)
            except Exception as e:
                last_err = e
                infra_f.write(json.dumps({"time": time.time(), "label": label, "attempt": attempt,
                                           "error": str(e)}) + "\n")
                infra_f.flush()
                time.sleep(0.5 * (attempt + 1))
        raise RuntimeError(f"{label} failed after {MAX_TRIAL_RETRIES} attempts: {last_err}")

    try:
        for load in loads:
            if load == "moderate":
                bl.start()
                time.sleep(1.0)
            try:
                for mode in modes:
                    for condition in conditions:
                        cell_key = f"{load}/{mode}/{condition}"
                        if (load, mode, condition) in completed_cells:
                            print(f"SKIP (already complete): {cell_key}")
                            continue
                        print(f"\n=== CELL {cell_key} : warm-up ({n_warmup} pairs) ===")
                        for _ in range(n_warmup):
                            with_retry(apply_condition, condition, label=f"{cell_key} warmup apply_condition")
                            with_retry(run_paired_trial, dcji_qids, full_qids, mode, rng,
                                       label=f"{cell_key} warmup trial")

                        print(f"=== CELL {cell_key} : {n_blocks} x {n_per_block} measured pairs ===")
                        cell_rows = []
                        for block in range(1, n_blocks + 1):
                            for rep in range(1, n_per_block + 1):
                                trial_counter += 1
                                with_retry(apply_condition, condition, label=f"{cell_key} trial {trial_counter} apply_condition")
                                d, f, order = with_retry(run_paired_trial, dcji_qids, full_qids, mode, rng,
                                                          label=f"{cell_key} trial {trial_counter}")
                                row = {
                                    "trial_id": trial_counter, "load": load, "mode": mode,
                                    "condition": condition, "block": block, "rep": rep,
                                    "guard_order": order,
                                    "dcji_queries": d["queried"], "dcji_request_count": d["request_count"],
                                    "dcji_refresh_ms": d["refresh_ms"], "dcji_policy_ms": d["policy_ms"],
                                    "dcji_gate_ms": d["gate_ms"], "dcji_payload_bytes": d["payload_bytes"],
                                    "dcji_decision": d["decision"], "dcji_unsafe": d["unsafe"],
                                    "full_queries": f["queried"], "full_request_count": f["request_count"],
                                    "full_refresh_ms": f["refresh_ms"], "full_policy_ms": f["policy_ms"],
                                    "full_gate_ms": f["gate_ms"], "full_payload_bytes": f["payload_bytes"],
                                    "full_decision": f["decision"], "full_unsafe": f["unsafe"],
                                    "decision_discordant": d["decision"] != f["decision"],
                                    "expected_decision": EXPECTED_DECISION[condition],
                                    "dcji_matches_expected": d["decision"] == EXPECTED_DECISION[condition],
                                    "full_matches_expected": f["decision"] == EXPECTED_DECISION[condition],
                                }
                                raw_f.write(json.dumps(row) + "\n")
                                raw_f.flush()
                                for rec in d["per_request"]:
                                    req_f.write(f"{trial_counter},DCJI,{rec['resource']},{rec['http_status']},"
                                                f"{rec['latency_ns']/1e6:.4f},{rec['payload_bytes']},{rec['resource_version']}\n")
                                for rec in f["per_request"]:
                                    req_f.write(f"{trial_counter},FullRefresh,{rec['resource']},{rec['http_status']},"
                                                f"{rec['latency_ns']/1e6:.4f},{rec['payload_bytes']},{rec['resource_version']}\n")
                                req_f.flush()
                                cell_rows.append(row)
                            print(f"  block {block}/{n_blocks} done ({trial_counter} total trials so far)")

                        n = len(cell_rows)
                        cell_summaries.append({
                            "cell": cell_key, "n": n,
                            "dcji_mean_gate_ms": sum(r["dcji_gate_ms"] for r in cell_rows) / n,
                            "full_mean_gate_ms": sum(r["full_gate_ms"] for r in cell_rows) / n,
                            "dcji_mean_request_count": sum(r["dcji_request_count"] for r in cell_rows) / n,
                            "full_mean_request_count": sum(r["full_request_count"] for r in cell_rows) / n,
                            "discordant": sum(r["decision_discordant"] for r in cell_rows),
                            "dcji_mismatched_expected": sum(not r["dcji_matches_expected"] for r in cell_rows),
                            "full_mismatched_expected": sum(not r["full_matches_expected"] for r in cell_rows),
                        })
                        print(f"  CELL SUMMARY: {json.dumps(cell_summaries[-1], indent=2)}")
            finally:
                if load == "moderate":
                    sent = bl.stop()
                    print(f"background load sent {sent} requests during '{load}' cell group")
    finally:
        raw_f.close()
        req_f.close()
        infra_f.close()

    summaries_path = OUT / "cell_summaries.json"
    prior_summaries = []
    if summaries_path.exists():
        prior_summaries = json.loads(summaries_path.read_text())
    new_keys = {s["cell"] for s in cell_summaries}
    merged = [s for s in prior_summaries if s["cell"] not in new_keys] + cell_summaries
    summaries_path.write_text(json.dumps(merged, indent=2))
    print(f"\nDONE. Total measured trials: {trial_counter}. Wrote {raw_path} and {requests_path}.")


if __name__ == "__main__":
    main()
