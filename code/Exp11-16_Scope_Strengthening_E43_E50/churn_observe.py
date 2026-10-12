#!/usr/bin/env python3
"""Read-only: watch Pods for a while and report how often each Pod's resourceVersion changes,
and what changed.  Works on any cluster the current kubeconfig can read.

  python3 churn_observe.py --seconds 600 --out runs/churn.json [--namespace NS]
"""
import argparse, json, statistics, time
from collections import defaultdict


def parts(o):
    m = o.get("metadata", {})
    return {"labels": m.get("labels"), "annotations": m.get("annotations"),
            "status": o.get("status"), "spec": o.get("spec"),
            "other_metadata": {k: v for k, v in m.items()
                               if k not in ("labels", "annotations", "resourceVersion", "managedFields")}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=600)
    ap.add_argument("--namespace", default=None)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    from kubernetes import client, config, watch
    config.load_kube_config()
    v1 = client.CoreV1Api()
    fn = v1.list_namespaced_pod if a.namespace else v1.list_pod_for_all_namespaces
    kw = {"namespace": a.namespace} if a.namespace else {}
    last, first_seen, changes = {}, {}, defaultdict(lambda: defaultdict(int))
    t_end = time.time() + a.seconds
    while time.time() < t_end:
        w = watch.Watch()
        try:
            for ev in w.stream(fn, timeout_seconds=max(1, int(t_end - time.time())), **kw):
                o = ev["raw_object"]
                key = o["metadata"]["namespace"] + "/" + o["metadata"]["name"] + "/" + o["metadata"]["uid"]
                p = parts(o)
                if ev["type"] == "MODIFIED" and key in last:
                    changes[key]["total"] += 1
                    for k in p:
                        if p[k] != last[key][k]:
                            changes[key][k] += 1
                last[key] = p
                first_seen.setdefault(key, time.time())
                if ev["type"] == "DELETED":
                    last.pop(key, None)
        except Exception as e:
            print("watch restarted:", repr(e)[:120])
            time.sleep(1)
    now = time.time()
    per_pod = []
    for key, t0 in first_seen.items():
        minutes = max((now - t0) / 60, 1e-9)
        c = changes.get(key, {})
        per_pod.append({"pod": key, "observed_min": round(minutes, 2), "changes": c.get("total", 0),
                        "changes_per_min": round(c.get("total", 0) / minutes, 3),
                        **{k: c.get(k, 0) for k in ("status", "labels", "annotations", "spec", "other_metadata")}})
    rates = sorted(x["changes_per_min"] for x in per_pod)
    summary = {"seconds": a.seconds, "pods_observed": len(per_pod),
               "pods_with_any_change": sum(1 for x in per_pod if x["changes"]),
               "changes_per_min_median": statistics.median(rates) if rates else None,
               "changes_per_min_p95": rates[int(0.95 * (len(rates) - 1))] if rates else None,
               "changes_per_min_max": rates[-1] if rates else None,
               "by_part": {k: sum(x[k] for x in per_pod) for k in ("status", "labels", "annotations", "spec", "other_metadata")}}
    json.dump({"summary": summary, "per_pod": per_pod}, open(a.out, "w"), indent=1)
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
