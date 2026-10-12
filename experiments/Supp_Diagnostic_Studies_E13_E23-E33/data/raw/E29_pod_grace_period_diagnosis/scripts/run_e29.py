#!/usr/bin/env python3
"""
E29 -- Diagnosing the true cause of F1.1/F1.2's original 39-80s live
transition duration.

The locked E13 manifests (f1_1_pod_benign.yaml, f1_2_statefulset.yaml,
f4_1_pods.yaml) all set `nodeName: flashir-nonexistent` -- a node that
never exists in the cluster. These Pods are therefore deliberately never
scheduled, never pull an image, and never start a container. This rules
out image-pull and container-start latency as the cause of the original
39-80s delay, leaving Kubernetes' default 30s pod termination grace
period as the leading hypothesis. This experiment tests that hypothesis
directly: using the byte-identical locked Pod manifest (unmodified,
requires no changes to any locked file), it measures real delete+recreate
wall-clock time under (a) default grace period (matching the original
E13 harness's plain `kubectl delete`) and (b) forced immediate deletion
(`--grace-period=0 --force`).

The first run of this script completed all 10 `default_grace_period`
repetitions (each taking tens of seconds to minutes, per the hypothesis)
but then hit a transient `etcdserver: request timed out` error on the
second `forced_immediate` repetition -- a real infrastructure hiccup on
this long-running cluster, not a logic bug. The 10 completed
`default_grace_period` measurements are real, valid data (captured from
that run's own stdout, reproduced verbatim below as PRIOR_DEFAULT_GRACE_ROWS)
and are not re-measured, since re-running ten 30-80s+ deletions purely to
regenerate already-valid numbers would waste significant time for no
scientific benefit. Retries were added to `run()` and results are now
written incrementally so a future transient failure cannot lose progress.
"""
import json, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "manifest.yaml"
NS = "flashir-e29"
CONTEXT = "kind-flashir-e13"
N_REPS = 10

# Verbatim from the first run's stdout (before the transient etcd timeout
# aborted the forced_immediate phase). Real, unaltered measurements.
PRIOR_DEFAULT_GRACE_ROWS = [
    {"rep": 1, "delete_s": 78.073, "create_s": 0.439, "total_s": 78.512},
    {"rep": 2, "delete_s": 18.786, "create_s": 0.563, "total_s": 19.349},
    {"rep": 3, "delete_s": 39.051, "create_s": 0.518, "total_s": 39.569},
    {"rep": 4, "delete_s": 38.740, "create_s": 0.422, "total_s": 39.163},
    {"rep": 5, "delete_s": 38.743, "create_s": 0.304, "total_s": 39.047},
    {"rep": 6, "delete_s": 39.044, "create_s": 0.336, "total_s": 39.379},
    {"rep": 7, "delete_s": 40.934, "create_s": 0.337, "total_s": 41.271},
    {"rep": 8, "delete_s": 433.608, "create_s": 0.879, "total_s": 434.488},
    {"rep": 9, "delete_s": 18.300, "create_s": 0.324, "total_s": 18.624},
    {"rep": 10, "delete_s": 39.180, "create_s": 0.311, "total_s": 39.491},
]


def run(args, check=True, retries=3):
    last = None
    for attempt in range(retries):
        p = subprocess.run(args, text=True, capture_output=True)
        if not check or p.returncode == 0:
            return p
        last = p
        time.sleep(1.0 * (attempt + 1))
    raise RuntimeError(f"command failed after {retries} attempts: {' '.join(args)} :: {last.stderr.strip()}")


def ensure_exists():
    run(["kubectl", "--context", CONTEXT, "apply", "-f", str(MANIFEST)])


def delete_forced():
    t0 = time.perf_counter()
    run(["kubectl", "--context", CONTEXT, "-n", NS, "delete", "pod", "flashir-target",
         "--ignore-not-found=true", "--grace-period=0", "--force"])
    t1 = time.perf_counter()
    return t1 - t0


def create():
    t0 = time.perf_counter()
    run(["kubectl", "--context", CONTEXT, "apply", "-f", str(MANIFEST)])
    t1 = time.perf_counter()
    return t1 - t0


def main():
    out_dir = ROOT / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    forced_path = out_dir / "forced_immediate_rows.jsonl"

    forced_rows = []
    if forced_path.exists():
        forced_rows = [json.loads(l) for l in open(forced_path, encoding="utf-8")]
        print(f"RESUMING: {len(forced_rows)} forced_immediate rows already recorded.")

    with open(forced_path, "a", encoding="utf-8") as f:
        for rep in range(len(forced_rows) + 1, N_REPS + 1):
            ensure_exists()
            time.sleep(0.3)
            t_delete = delete_forced()
            t_create = create()
            total = t_delete + t_create
            row = {"rep": rep, "delete_s": t_delete, "create_s": t_create, "total_s": total}
            forced_rows.append(row)
            f.write(json.dumps(row) + "\n")
            f.flush()
            print(f"forced_immediate rep {rep}: delete={t_delete:.3f}s create={t_create:.3f}s total={total:.3f}s")

    results = {"default_grace_period": PRIOR_DEFAULT_GRACE_ROWS, "forced_immediate": forced_rows}
    out = {}
    for label, rows in results.items():
        totals = [r["total_s"] for r in rows]
        deletes = [r["delete_s"] for r in rows]
        out[label] = {
            "n": len(rows),
            "mean_total_s": sum(totals) / len(totals),
            "median_total_s": sorted(totals)[len(totals) // 2],
            "min_total_s": min(totals),
            "max_total_s": max(totals),
            "mean_delete_s": sum(deletes) / len(deletes),
            "rows": rows,
        }
    (ROOT / "results" / "summary.json").write_text(json.dumps(out, indent=2))
    print("\nSUMMARY:")
    for label, s in out.items():
        print(f"  {label}: mean_total={s['mean_total_s']:.3f}s median={s['median_total_s']:.3f}s "
              f"(delete mean={s['mean_delete_s']:.3f}s) range=[{s['min_total_s']:.3f}, {s['max_total_s']:.3f}]")


if __name__ == "__main__":
    main()
