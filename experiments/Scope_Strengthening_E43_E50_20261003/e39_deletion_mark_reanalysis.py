#!/usr/bin/env python3
"""Deletion-mark reference and terminating-Pod revision accounting for the extended-delay
target-binding study (E39-A).  Descriptive; not part of the pre-specified analysis.

Usage: python3 e39_deletion_mark_reanalysis.py <export_dir> <out_dir>
  <export_dir> = unpacked e39_EXPORT_e39-20260926t1515z (contains RAW_TRIALS.jsonl, pod_events.jsonl,
                 analysis_final/oracle_trials_E39.csv)
Definition (same as ops_scripts/running_pod_report.py of the controlled-delay study): an accepted
DELETE is invalid at the deletion mark when the first committed write's revision precedes the first
Pod revision carrying deletionTimestamp and the post-write validity is 0.
Self-check: the same rule with the DELETED revision must reproduce the primary INVALID_EXECUTED counts.
"""
import json, os, sys
from collections import defaultdict
import numpy as np, pandas as pd

src, out = sys.argv[1], sys.argv[2]
os.makedirs(out, exist_ok=True)
ev = defaultdict(list)
for l in open(os.path.join(src, "pod_events.jsonl")):
    e = json.loads(l)
    if e.get("uid"):
        ev[e["uid"]].append(e)
raw = {}
for l in open(os.path.join(src, "RAW_TRIALS.jsonl")):
    r = json.loads(l)
    raw[r["trial_id"]] = r
o = pd.read_csv(os.path.join(src, "analysis_final", "oracle_trials_E39.csv"), low_memory=False)
A = o[(o.experiment == "A") & o.condition.astype(str).str.startswith("async")]
rows = []
for _, t in A.iterrows():
    r = raw[t.trial_id]
    d = r.get("delete") or {}
    evs = ev.get(d.get("expected_uid"), [])
    acc = next((e for e in evs if e["type"] in ("MODIFIED", "ADDED", "LIST") and e.get("deletion_timestamp")), None)
    dl = [e for e in evs if e["type"] == "DELETED"]
    w = r.get("writer") or {}
    rows.append(dict(
        trial=t.trial_id, arm=t.arm_c, cond=t.condition_c, delta=t.delta_ms, primary=t.primary_class,
        reason=t.primary_reason, accepted=d.get("http_status") in (200, 202), wrv=t.first_write_rv, v_post=t.v_post,
        mark_rv=int(acc["rv"]) if acc else np.nan, del_rv=int(dl[0]["rv"]) if len(dl) == 1 else np.nan,
        mark_to_deleted_ms=(dl[0]["t_recv_wall_ns"] - acc["t_recv_wall_ns"]) / 1e6 if acc and len(dl) == 1 else np.nan,
        writer_status=w.get("http_status"),
        writer_rv=float(w["resulting_resource_version"]) if w.get("resulting_resource_version") else np.nan,
        on_terminating="on_terminating_pod" in str(t["flags"]),
        n_mark_revisions=sum(1 for e in evs if e.get("deletion_timestamp") and e["type"] != "DELETED")))
T = pd.DataFrame(rows)
T = T[T.primary != "UNKNOWN"].copy()                      # primary denominator
committed = T.accepted & T.wrv.notna() & (T.v_post == 0)
T["inv_final_check"] = committed & (T.wrv < T.del_rv)
T["inv_mark"] = committed & (T.wrv < T.mark_rv)
T["inv_primary"] = T.primary == "INVALID_EXECUTED"
assert (T.inv_final_check == T.inv_primary).all(), "self-check failed: final-removal rule does not reproduce primary labels"
cells = T.groupby(["arm", "cond", "delta"]).agg(n=("trial", "size"), invalid_final=("inv_primary", "sum"),
                                                invalid_mark=("inv_mark", "sum")).reset_index()
cells.to_csv(os.path.join(out, "E39A_deletion_mark_cells.csv"), index=False)


def wa(g, col):
    g = g.sort_values("delta")
    d, p = g.delta.values, (g[col] / g.n).values
    return float(sum((d[j] - d[j - 1]) * (p[j] + p[j - 1]) / 2 for j in range(1, len(d))))


summary = {"accepted_async": int(T.accepted.sum()),
           "accepted_with_mark_and_single_DELETED": int((T.accepted & T.mark_rv.notna() & T.del_rv.notna()).sum()),
           "by_arm": {}, "window_area_ms": {}, "A4d": {}}
for arm, g in T.groupby("arm"):
    summary["by_arm"][arm] = {"n": len(g), "invalid_final": int(g.inv_primary.sum()), "invalid_mark": int(g.inv_mark.sum()),
                              "mark_to_deleted_ms_median": round(float(g[g.accepted].mark_to_deleted_ms.median()), 2)}
for (arm, cond), g in cells.groupby(["arm", "cond"]):
    summary["window_area_ms"][f"{arm}/{cond}"] = {"final": round(wa(g, "invalid_final"), 2), "mark": round(wa(g, "invalid_mark"), 2)}
d = T[T.arm == "A4d"]
term = d[d.on_terminating]
summary["A4d"] = {
    "contested_409": int(d.reason.astype(str).str.contains("denier_read_post_write").sum()),
    "writer_404": int(d.reason.astype(str).str.contains("404").sum()),
    "writer_on_terminating_pod": len(term),
    "terminating_writer_http_200": int((term.writer_status == 200).sum()),
    "terminating_writer_rv_equals_mark_rv": int((term.writer_rv == term.mark_rv).sum()),
    "terminating_exactly_one_mark_revision": int((term.n_mark_revisions == 1).sum()),
    "terminating_deleted_minus_mark_revisions": {str(int(k)): int(v) for k, v in (term.del_rv - term.mark_rv).value_counts().items()},
    "by_delta": {str(k): {"contested": int(g.reason.astype(str).str.contains("denier_read_post_write").sum()),
                          "terminating": int(g.on_terminating.sum()),
                          "writer_404": int(g.reason.astype(str).str.contains("404").sum())}
                 for k, g in d.groupby("delta")}}
json.dump(summary, open(os.path.join(out, "E39A_deletion_mark_summary.json"), "w"), indent=1)
print(json.dumps({k: v for k, v in summary.items() if k != "A4d"}, indent=1))
print(json.dumps({k: v for k, v in summary["A4d"].items() if k != "by_delta"}, indent=1))
