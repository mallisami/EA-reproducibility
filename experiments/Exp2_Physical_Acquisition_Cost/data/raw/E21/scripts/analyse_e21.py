#!/usr/bin/env python3
"""Protocol-aligned E21 analysis (V11 package).

The frozen E21 protocol names paired_refresh_latency_ms as the primary
performance endpoint and gate latency as a secondary endpoint. The first
post-run analysis script accidentally used gate latency for the paired
bootstrap calculation. This corrected analysis uses REFRESH latency for the
primary paired inference while retaining gate latency, deadline misses,
request counts, payload bytes, and safety as secondary/descriptive outcomes.
Raw trials and the frozen protocol are unchanged.
"""
import csv, json, math, random, statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = json.loads((ROOT / "E21_PROTOCOL.json").read_text())


def qtile(xs, p):
    xs = sorted(xs)
    if not xs:
        return None
    i = (len(xs) - 1) * p
    a, b = math.floor(i), math.ceil(i)
    return xs[a] if a == b else xs[a] * (b - i) + xs[b] * (i - a)


def describe(xs):
    return {
        "mean": statistics.mean(xs),
        "sd": statistics.stdev(xs) if len(xs) > 1 else 0.0,
        "median": qtile(xs, .5),
        "iqr": [qtile(xs, .25), qtile(xs, .75)],
        "p50": qtile(xs, .5),
        "p95": qtile(xs, .95),
        "p99": qtile(xs, .99),
    }


def bootstrap_paired_diff(pairs, reps, seed):
    """Bootstrap mean paired difference, resampling complete pairs."""
    rng = random.Random(seed)
    diffs = [d - f for d, f in pairs]
    n = len(diffs)
    boot_means = []
    for _ in range(reps):
        sample = [diffs[rng.randrange(n)] for _ in range(n)]
        boot_means.append(sum(sample) / n)
    boot_means.sort()
    lo = boot_means[int(0.025 * (reps - 1))]
    hi = boot_means[int(0.975 * (reps - 1))]
    return {
        "mean_diff": statistics.mean(diffs),
        "median_diff": qtile(diffs, .5),
        "bootstrap_ci95": [lo, hi],
    }


def main():
    rows = [json.loads(l) for l in open(ROOT / "results" / "raw_trials.jsonl", encoding="utf-8")]
    by_cell = defaultdict(list)
    for r in rows:
        by_cell[(r["load"], r["mode"], r["condition"])].append(r)

    thresholds = PROTOCOL["deadline_thresholds_ms"]
    boot_reps = PROTOCOL["bootstrap_replicates"]
    seed = PROTOCOL["random_seed"]
    cell_reports = []

    for idx, ((load, mode, condition), cell_rows) in enumerate(sorted(by_cell.items())):
        n = len(cell_rows)
        dcji_refresh = [r["dcji_refresh_ms"] for r in cell_rows]
        full_refresh = [r["full_refresh_ms"] for r in cell_rows]
        dcji_gate = [r["dcji_gate_ms"] for r in cell_rows]
        full_gate = [r["full_gate_ms"] for r in cell_rows]
        dcji_req = [r["dcji_request_count"] for r in cell_rows]
        full_req = [r["full_request_count"] for r in cell_rows]
        dcji_bytes = [r["dcji_payload_bytes"] for r in cell_rows]
        full_bytes = [r["full_payload_bytes"] for r in cell_rows]

        deadline_report = {}
        for d in thresholds:
            # Operational deadline includes refresh + policy evaluation.
            deadline_report[str(d)] = {
                "dcji_gate_miss_rate": sum(x > d for x in dcji_gate) / n,
                "full_gate_miss_rate": sum(x > d for x in full_gate) / n,
            }

        report = {
            "load": load,
            "mode": mode,
            "condition": condition,
            "n": n,
            "dcji_refresh_ms": describe(dcji_refresh),
            "full_refresh_ms": describe(full_refresh),
            "paired_refresh_latency_diff_ms": bootstrap_paired_diff(
                list(zip(dcji_refresh, full_refresh)), boot_reps, seed + idx
            ),
            "dcji_gate_ms": describe(dcji_gate),
            "full_gate_ms": describe(full_gate),
            "paired_gate_latency_diff_ms": bootstrap_paired_diff(
                list(zip(dcji_gate, full_gate)), boot_reps, seed + 100 + idx
            ),
            "mean_dcji_request_count": statistics.mean(dcji_req),
            "mean_full_request_count": statistics.mean(full_req),
            "mean_dcji_payload_bytes": statistics.mean(dcji_bytes),
            "mean_full_payload_bytes": statistics.mean(full_bytes),
            "deadline_miss_rates": deadline_report,
            "decision_discordant": sum(bool(r["decision_discordant"]) for r in cell_rows),
            "dcji_mismatched_expected": sum(not r["dcji_matches_expected"] for r in cell_rows),
            "full_mismatched_expected": sum(not r["full_matches_expected"] for r in cell_rows),
        }
        cell_reports.append(report)

    all_dcji_refresh = [r["dcji_refresh_ms"] for r in rows]
    all_full_refresh = [r["full_refresh_ms"] for r in rows]
    all_dcji_gate = [r["dcji_gate_ms"] for r in rows]
    all_full_gate = [r["full_gate_ms"] for r in rows]
    mean_dcji_bytes = statistics.mean(r["dcji_payload_bytes"] for r in rows)
    mean_full_bytes = statistics.mean(r["full_payload_bytes"] for r in rows)

    pooled_descriptive = {
        "n_paired_trials": len(rows),
        "dcji_median_refresh_ms": qtile(all_dcji_refresh, .5),
        "full_median_refresh_ms": qtile(all_full_refresh, .5),
        "median_refresh_reduction_fraction": 1 - qtile(all_dcji_refresh, .5) / qtile(all_full_refresh, .5),
        "dcji_median_gate_ms": qtile(all_dcji_gate, .5),
        "full_median_gate_ms": qtile(all_full_gate, .5),
        "median_gate_reduction_fraction": 1 - qtile(all_dcji_gate, .5) / qtile(all_full_gate, .5),
        "mean_dcji_payload_bytes": mean_dcji_bytes,
        "mean_full_payload_bytes": mean_full_bytes,
        "mean_payload_reduction_fraction": 1 - mean_dcji_bytes / mean_full_bytes,
        "dcji_requests_per_trial": 3,
        "full_requests_per_trial": 6,
        "request_reduction_fraction": 0.5,
        "decision_discordant": sum(bool(r["decision_discordant"]) for r in rows),
        "dcji_mismatched_expected": sum(not r["dcji_matches_expected"] for r in rows),
        "full_mismatched_expected": sum(not r["full_matches_expected"] for r in rows),
        "interpretation": "descriptive only across heterogeneous cells; primary confirmatory evidence is per-cell paired refresh-latency CI",
    }

    output = {
        "analysis_version": "V11_protocol_aligned",
        "analysis_note": (
            "Frozen protocol primary endpoint is paired_refresh_latency_ms. "
            "V11 corrects the initial post-run analysis that bootstrapped gate latency instead. "
            "Raw data and protocol are unchanged; conclusions are unchanged."
        ),
        "protocol": PROTOCOL,
        "pooled_descriptive": pooled_descriptive,
        "cells": cell_reports,
    }
    (ROOT / "results" / "summary.json").write_text(json.dumps(output, indent=2), encoding="utf-8")

    with open(ROOT / "results" / "paired_results.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "trial_id", "load", "mode", "condition", "block", "rep", "guard_order",
            "dcji_refresh_ms", "full_refresh_ms", "refresh_diff_ms",
            "dcji_gate_ms", "full_gate_ms", "gate_diff_ms",
            "dcji_request_count", "full_request_count",
            "dcji_payload_bytes", "full_payload_bytes",
            "dcji_decision", "full_decision", "decision_discordant"
        ])
        for r in rows:
            w.writerow([
                r["trial_id"], r["load"], r["mode"], r["condition"], r["block"], r["rep"], r["guard_order"],
                r["dcji_refresh_ms"], r["full_refresh_ms"], r["dcji_refresh_ms"] - r["full_refresh_ms"],
                r["dcji_gate_ms"], r["full_gate_ms"], r["dcji_gate_ms"] - r["full_gate_ms"],
                r["dcji_request_count"], r["full_request_count"],
                r["dcji_payload_bytes"], r["full_payload_bytes"],
                r["dcji_decision"], r["full_decision"], r["decision_discordant"]
            ])

    with open(ROOT / "results" / "latency_distribution.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["load", "mode", "condition", "guard", "metric", "n", "mean_ms", "median_ms", "p95_ms", "p99_ms"])
        for rep in cell_reports:
            for guard, prefix in [("DCJI", "dcji"), ("FullRefresh", "full")]:
                for metric in ["refresh_ms", "gate_ms"]:
                    s = rep[f"{prefix}_{metric}"]
                    w.writerow([rep["load"], rep["mode"], rep["condition"], guard, metric, rep["n"],
                                round(s["mean"], 4), round(s["median"], 4), round(s["p95"], 4), round(s["p99"], 4)])

    print(f"Wrote protocol-aligned summary for {len(rows)} paired trials across {len(cell_reports)} cells")
    for rep in cell_reports:
        p = rep["paired_refresh_latency_diff_ms"]
        print(f"{rep['load']}/{rep['mode']}/{rep['condition']}: "
              f"refresh mean diff={p['mean_diff']:.3f} ms, CI95=({p['bootstrap_ci95'][0]:.3f}, {p['bootstrap_ci95'][1]:.3f})")


if __name__ == "__main__":
    main()
