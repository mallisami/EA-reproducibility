#!/usr/bin/env python3
"""E35: reduced E21 replication on a different physical host.

Uses the locked planner/commit policy, 3 conditions, 20 warm-up pairs per
condition, and 100 measured randomized-order pairs per condition.
"""
from __future__ import annotations

import copy
import csv
import hashlib
import json
import random
import statistics
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
PKG_ROOT = ROOT.parents[1]
sys.path.insert(0, str(PKG_ROOT / "02_locked_methodology_v8.1.1" / "deterministic"))
from flashir_core.dcji import derive_critical_predicates  # noqa: E402
from flashir_core.expr import commit_components  # noqa: E402

PROTOCOL_PATH = ROOT / "E35_PROTOCOL.json"
PROTOCOL_HASH_PATH = ROOT / "E35_PROTOCOL.sha256"
SCENARIO = json.loads((ROOT / "scenarios" / "F5_PHYS.json").read_text())
PROTOCOL = json.loads(PROTOCOL_PATH.read_text())
PROXY = f"http://127.0.0.1:{PROTOCOL['proxy_port']}"
NS = PROTOCOL["namespace"]
FLAGS = ["flag_a", "flag_b", "flag_c", "flag_d", "flag_e", "flag_f"]
RESOURCE_OF = {f"flag_{x}": f"obs-{x}" for x in "abcdef"}
ATTACK_FLAGS = ["flag_a", "flag_c", "flag_e"]
EXPECTED = {"stable": "EXECUTE", "ea_attack": "ABORT_REPLAN", "irrelevant_mutation": "EXECUTE"}


def verify_protocol_hash():
    actual = hashlib.sha256(PROTOCOL_PATH.read_bytes()).hexdigest()
    recorded = PROTOCOL_HASH_PATH.read_text().split()[0]
    if actual != recorded:
        raise SystemExit(f"E35 protocol hash mismatch: {actual} != {recorded}")


def cm_url(name):
    return f"{PROXY}/api/v1/namespaces/{NS}/configmaps/{name}"


def get_observation(flag, guard, trial_id, reqlog):
    resource = RESOURCE_OF[flag]
    last_err = None
    for attempt in range(1, 4):
        try:
            t0 = time.perf_counter_ns()
            with urlopen(cm_url(resource), timeout=5) as response:
                body = response.read()
                status = getattr(response, "status", 200)
            t1 = time.perf_counter_ns()
            obj = json.loads(body)
            rec = {
                "trial_id": trial_id, "guard": guard, "flag": flag, "resource": resource,
                "status": status, "latency_ms": (t1 - t0) / 1e6,
                "payload_bytes": len(body), "resource_version": obj["metadata"].get("resourceVersion")
            }
            reqlog.append(rec)
            return obj["data"].get("valid") == "true", rec
        except Exception as e:
            last_err = e
            time.sleep(0.1 * attempt)
    raise RuntimeError(f"get_observation({flag}) failed: {last_err}")


def patch_cm(resource, patch, retries=5):
    body = json.dumps(patch).encode()
    last = None
    for attempt in range(1, retries + 1):
        try:
            req = Request(cm_url(resource), data=body, method="PATCH", headers={"Content-Type": "application/merge-patch+json"})
            with urlopen(req, timeout=5) as r:
                r.read()
            return
        except Exception as e:
            last = e
            time.sleep(0.1 * attempt)
    raise RuntimeError(f"patch_cm({resource}) failed: {last}")


def set_flag(flag, value):
    patch_cm(RESOURCE_OF[flag], {"data": {"valid": "true" if value else "false"}})


def reset_all():
    for f in FLAGS:
        set_flag(f, True)


def apply_condition(condition):
    reset_all()
    time.sleep(0.05)
    if condition == "ea_attack":
        for f in ATTACK_FLAGS:
            set_flag(f, False)
    elif condition == "irrelevant_mutation":
        patch_cm("obs-irrelevant", {"data": {"note": f"mut-{time.time_ns()}"}})
    time.sleep(0.05)


def evaluate_guard(flags_to_query, guard, trial_id, reqlog):
    cached = copy.deepcopy(SCENARIO["initial_state"])
    t0 = time.perf_counter_ns()
    obs = {}
    payload = 0
    for f in flags_to_query:
        value, rec = get_observation(f, guard, trial_id, reqlog)
        obs[f] = value
        payload += rec["payload_bytes"]
    t1 = time.perf_counter_ns()
    state = copy.deepcopy(cached)
    for f in FLAGS:
        state["facts"][f] = obs[f] if f in obs else cached["facts"][f]
    p0 = time.perf_counter_ns()
    comp = commit_components(SCENARIO, state, "balanced")
    p1 = time.perf_counter_ns()
    decision = "EXECUTE" if comp["valid"] else "ABORT_REPLAN"
    return {
        "decision": decision,
        "request_count": len(flags_to_query),
        "refresh_ms": (t1 - t0) / 1e6,
        "policy_ms": (p1 - p0) / 1e6,
        "gate_ms": ((t1 - t0) + (p1 - p0)) / 1e6,
        "payload_bytes": payload,
    }


def percentile(sorted_values, p):
    if not sorted_values:
        return None
    idx = int(round((len(sorted_values) - 1) * p))
    return sorted_values[max(0, min(idx, len(sorted_values) - 1))]


def bootstrap_ci(diffs, seed, reps):
    rng = random.Random(seed)
    n = len(diffs)
    means = []
    for _ in range(reps):
        means.append(sum(diffs[rng.randrange(n)] for _ in range(n)) / n)
    means.sort()
    return [percentile(means, 0.025), percentile(means, 0.975)]


def summarize_group(rows):
    diffs = [r["dcji_refresh_ms"] - r["full_refresh_ms"] for r in rows]
    return {
        "n": len(rows),
        "mean_refresh_diff_ms": statistics.fmean(diffs) if diffs else None,
        "median_refresh_diff_ms": statistics.median(diffs) if diffs else None,
        "decision_discordances": sum(r["decision_discordant"] for r in rows),
        "expected_decision_mismatches": sum((not r["dcji_matches_expected"]) or (not r["full_matches_expected"]) for r in rows),
    }


def main():
    verify_protocol_hash()
    plan = derive_critical_predicates(SCENARIO, 5.0, "C2", "balanced")
    if plan["status"] != "EXPLOITABLE" or len(plan["critical_query_ids"]) != 3:
        raise SystemExit(f"Unexpected E35 plan: {plan}")
    dcji_flags = []
    qid_to_flag = {f"query:/facts/{f}": f for f in FLAGS}
    for qid in plan["critical_query_ids"]:
        dcji_flags.append(qid_to_flag[qid])
    full_flags = FLAGS[:]

    out = ROOT / "results"
    out.mkdir(exist_ok=True)
    raw_path = out / "raw_trials.jsonl"
    req_path = out / "per_request_log.csv"
    summary_path = out / "summary.json"
    if raw_path.exists() and raw_path.stat().st_size > 0:
        raise SystemExit(f"Refusing to overwrite existing E35 results: {raw_path}")

    rng = random.Random(PROTOCOL["random_seed"])
    reqlog = []
    rows = []
    trial_id = 0
    with raw_path.open("w", encoding="utf-8") as fh:
        for condition in PROTOCOL["conditions"]:
            print(f"E35 warm-up: {condition}")
            for _ in range(PROTOCOL["warmup_pairs_per_condition"]):
                apply_condition(condition)
                order = rng.choice(["DCJI_FIRST", "FULL_FIRST"])
                warm_req = []
                if order == "DCJI_FIRST":
                    evaluate_guard(dcji_flags, "DCJI", -1, warm_req); evaluate_guard(full_flags, "FULL_REFRESH", -1, warm_req)
                else:
                    evaluate_guard(full_flags, "FULL_REFRESH", -1, warm_req); evaluate_guard(dcji_flags, "DCJI", -1, warm_req)

            for rep in range(1, PROTOCOL["measured_pairs_per_condition"] + 1):
                trial_id += 1
                apply_condition(condition)
                order = rng.choice(["DCJI_FIRST", "FULL_FIRST"])
                if order == "DCJI_FIRST":
                    d = evaluate_guard(dcji_flags, "DCJI", trial_id, reqlog)
                    f = evaluate_guard(full_flags, "FULL_REFRESH", trial_id, reqlog)
                else:
                    f = evaluate_guard(full_flags, "FULL_REFRESH", trial_id, reqlog)
                    d = evaluate_guard(dcji_flags, "DCJI", trial_id, reqlog)
                expected = EXPECTED[condition]
                row = {
                    "trial_id": trial_id, "condition": condition, "rep": rep, "guard_order": order,
                    "dcji_request_count": d["request_count"], "full_request_count": f["request_count"],
                    "dcji_refresh_ms": d["refresh_ms"], "full_refresh_ms": f["refresh_ms"],
                    "dcji_gate_ms": d["gate_ms"], "full_gate_ms": f["gate_ms"],
                    "dcji_payload_bytes": d["payload_bytes"], "full_payload_bytes": f["payload_bytes"],
                    "dcji_decision": d["decision"], "full_decision": f["decision"],
                    "expected_decision": expected,
                    "dcji_matches_expected": d["decision"] == expected,
                    "full_matches_expected": f["decision"] == expected,
                    "decision_discordant": d["decision"] != f["decision"],
                }
                rows.append(row)
                fh.write(json.dumps(row, sort_keys=True) + "\n")
                fh.flush()
                print(f"E35 {trial_id:03d}/300 {condition:20s} order={order:10s} diff_ms={row['dcji_refresh_ms']-row['full_refresh_ms']:+.3f}")

    with req_path.open("w", newline="", encoding="utf-8") as fh:
        fields = ["trial_id", "guard", "flag", "resource", "status", "latency_ms", "payload_bytes", "resource_version"]
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader(); w.writerows(reqlog)

    diffs = [r["dcji_refresh_ms"] - r["full_refresh_ms"] for r in rows]
    thresholds = {}
    for t in PROTOCOL["gate_thresholds_ms"]:
        thresholds[str(t)] = {
            "dcji_miss_rate": sum(r["dcji_gate_ms"] > t for r in rows) / len(rows),
            "fullrefresh_miss_rate": sum(r["full_gate_ms"] > t for r in rows) / len(rows),
        }
    by_condition = {c: summarize_group([r for r in rows if r["condition"] == c]) for c in PROTOCOL["conditions"]}
    by_order = {o: summarize_group([r for r in rows if r["guard_order"] == o]) for o in ["DCJI_FIRST", "FULL_FIRST"]}
    by_condition_order = {
        f"{c}|{o}": summarize_group([r for r in rows if r["condition"] == c and r["guard_order"] == o])
        for c in PROTOCOL["conditions"] for o in ["DCJI_FIRST", "FULL_FIRST"]
    }

    summary = {
        "experiment": "E35",
        "n_pairs": len(rows),
        "planner_certificate_query_ids": plan["critical_query_ids"],
        "mean_dcji_request_count": statistics.fmean(r["dcji_request_count"] for r in rows),
        "mean_full_request_count": statistics.fmean(r["full_request_count"] for r in rows),
        "decision_discordances": sum(r["decision_discordant"] for r in rows),
        "expected_decision_mismatches": sum((not r["dcji_matches_expected"]) or (not r["full_matches_expected"]) for r in rows),
        "mean_paired_refresh_diff_ms": statistics.fmean(diffs),
        "median_paired_refresh_diff_ms": statistics.median(diffs),
        "bootstrap_ci95_refresh_diff_ms": bootstrap_ci(diffs, PROTOCOL["random_seed"] + 1, PROTOCOL["bootstrap_replicates"]),
        "direction_negative_overall": statistics.fmean(diffs) < 0,
        "by_condition": by_condition,
        "by_guard_order": by_order,
        "by_condition_and_guard_order": by_condition_order,
        "gate_thresholds_ms": thresholds,
        "pass_criteria": {
            "decision_discordances": 0,
            "expected_decision_mismatches": 0,
            "dcji_request_count": PROTOCOL["expected_dcji_request_count"],
            "fullrefresh_request_count": PROTOCOL["expected_fullrefresh_request_count"]
        },
        "pass": (
            all(r["dcji_request_count"] == PROTOCOL["expected_dcji_request_count"] for r in rows)
            and all(r["full_request_count"] == PROTOCOL["expected_fullrefresh_request_count"] for r in rows)
            and not any(r["decision_discordant"] for r in rows)
            and not any((not r["dcji_matches_expected"]) or (not r["full_matches_expected"]) for r in rows)
        )
    }
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True))
    print("\nE35 SUMMARY\n" + json.dumps(summary, indent=2, sort_keys=True))
    if not summary["pass"]:
        raise SystemExit("E35 completed but failed a structural/safety criterion; preserve and return results for analysis.")


if __name__ == "__main__":
    main()
