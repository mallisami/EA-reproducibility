#!/usr/bin/env python3
"""E39 INDEPENDENT recomputation of the headline counts (sub-experiments A, B, E, F, G).

Deliberately written from protocol/ANALYSIS_PLAN_E39.txt section 2 only, with NO import from the analysis
code (oracle_e39.py, e39_common.py, analyze_e39.py): standard library only. It re-derives, per cell,
k = #INVALID_EXECUTED (primary commit-order rule) and n = primary denominator (classifiable trials plus
fail-closed gate UNKNOWN_COHERENCE trials as non-events), and optionally compares them with
results_E39.json (exit 3 on any difference). Simplified exclusion rules (reported per reason): status not
COMPLETE, fixture reset not ok, a write not committed (non-2xx; except A4d 404 after an accepted DELETE),
RV missing / non-numeric / audit-vs-trial conflict for a write or an accepted action, RV tie,
denial without numeric decider evidence is NOT re-derived (denials count as non-events).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict

VERSION = "e39.independent.v1"
OBJS = ["evidence-a", "evidence-b", "evidence-c", "evidence-d", "evidence-e", "evidence-f",
        "response-scope", "application-policy"]
HEADLINE = ("A", "B", "E", "F", "G")


def arm_of(raw, impl_field):
    s = str(raw or "").strip()
    impl = "python"
    low = s.lower()
    for suf in ("_go", "-go"):
        if low.endswith(suf):
            low, impl = low[: -len(suf)], "client-go"
    if str(impl_field or "").lower() in ("client-go", "go"):
        impl = "client-go"
    table = {"a1_dcji": "A1", "a1": "A1", "a2_fullrefresh": "A2", "a2": "A2", "a3_dcji_webhook": "A3", "a3": "A3",
             "a4d_commit_direct": "A4d", "a4d": "A4d", "a4m_commit_mirror": "A4m", "a4m": "A4m"}
    return table.get(low), impl


def cond_of(raw):
    s = str(raw or "").strip().lower()
    return {"poisson_scope": "async_scope", "out_of_model_bdf": "out_of_model_cut_bdf",
            "in_model_ace": "in_model_cut_ace"}.get(s, s)


def obj_of(name):
    s = str(name or "").strip().lower()
    if s in ("e38.scope", "scope"):
        return "response-scope"
    if s in ("e38.app-policy", "app_policy", "app-policy"):
        return "application-policy"
    if s.startswith("e38.evidence/"):
        return "evidence-" + s[-1]
    if "/" in s:
        s = s.rsplit("/", 1)[1]
    if len(s) == 1 and s in "abcdef":
        return "evidence-" + s
    return s


def truth(v):
    s = str(v).strip().lower()
    if s in ("true", "allowed", "allow", "yes", "1"):
        return True
    if s in ("false", "not allowed", "not_allowed", "denied", "deny", "no", "0"):
        return False
    return None


def valid(st):
    j = (st["evidence-a"] and st["evidence-b"]) or (st["evidence-c"] and st["evidence-d"]) or \
        (st["evidence-e"] and st["evidence-f"])
    return bool(j and st["response-scope"] and st["application-policy"])


def num(x):
    if x is None:
        return None
    s = str(x).strip()
    return int(s) if s.isdigit() else "bad"


def fl(x):
    return None if x in (None, "") else float(x)


def audit_rc(audit):
    out = {"__uid__": {}, "__terminating__": set()}
    for ev in audit:
        if ev.get("stage") != "ResponseComplete" or not ev.get("auditID"):
            continue
        ro = ev.get("responseObject") or {}
        md = {} if ro.get("kind") == "Status" else (ro.get("metadata") or {})
        out.setdefault(ev["auditID"], []).append(md.get("resourceVersion"))
        if md.get("deletionTimestamp"):
            out["__terminating__"].add(ev["auditID"])
        if md.get("uid"):
            out["__uid__"].setdefault(ev["auditID"], []).append(md.get("uid"))
    return out


def pick_rv(aud, aid, trial_rv):
    a = aud.get(aid) if aid else None
    arv = None
    if a:
        if len(set(a)) > 1:
            return "bad"
        arv = num(a[0])
    trv = num(trial_rv)
    if arv == "bad" or trv == "bad":
        return "bad"
    if arv is not None and trv is not None and arv != trv:
        return "bad"
    return arv if arv is not None else trv


def key_of(t):
    x = str(t.get("experiment") or "").upper()
    arm, impl = arm_of(t.get("arm"), t.get("client_impl"))
    c = cond_of(t.get("condition"))
    if x == "A":
        return ("A", arm, impl, c, fl(t.get("delta_ms")))
    if x == "B":
        return ("B", arm, c, fl(t.get("lambda_per_s")))
    if x == "E":
        return ("E", arm, c, fl(t.get("delta_ms")))
    if x == "F":
        return ("F", arm, impl, c, fl(t.get("delta_ms")))
    if x == "G":
        return ("G", arm, c)
    return None


def classify(t, aud):
    """-> ("event"|"nonevent"|"excluded", reason)."""
    if t.get("status") != "COMPLETE" or not (t.get("fixture") or {}).get("reset_ok"):
        return "excluded", "status_or_reset"
    arm, _ = arm_of(t.get("arm"), t.get("client_impl"))
    cond = cond_of(t.get("condition"))
    st0 = {}
    for k, v in ((t.get("fixture") or {}).get("initial_values") or {}).items():
        vv = v.get("value", v.get("allowed")) if isinstance(v, dict) else v
        st0[obj_of(k)] = truth(vv)
    if any(st0.get(o) is None for o in OBJS):
        return "excluded", "fixture_state"
    ws = []
    if isinstance(t.get("writer"), dict):
        ws.append(t["writer"])
    for w in t.get("writer_arrivals") or []:
        ws.append(w)
    for w in t.get("cut_writes") or []:        # E39-G pre-gate cut writes (plan 2.3: they are writes in W)
        if isinstance(w, dict):
            ws.append(w)
    cut_in_fixture = False
    if not valid(st0):
        cuts = {"out_of_model_cut_bdf": {"evidence-b", "evidence-d", "evidence-f"},
                "in_model_cut_ace": {"evidence-a", "evidence-c", "evidence-e"}}.get(cond)
        if cuts and {o for o in OBJS if st0[o] is False} == cuts:
            for o in cuts:
                st0[o] = True          # fixture recorded with the cut applied; the cut itself is judged below
            if not ws:
                cut_in_fixture = True
        else:
            return "excluded", "fixture_invalid"
    gate = t.get("gate") or {}
    if str(t.get("action_type") or "pod_delete") == "netpol_create":
        act = t.get("netpol") or {}
        disp = bool(act) and (act.get("t_send_mono_ns") is not None or act.get("http_status") is not None)
        act_rv_trial = act.get("created_resource_version")
        ok = (200, 201)
    else:
        act = t.get("delete") or {}
        disp = act.get("dispatched") is True
        act_rv_trial = act.get("deleted_object_resource_version")
        ok = (200, 202)
    code = act.get("http_status")
    accepted = disp and code is not None and int(code) in ok
    if not disp:
        if gate.get("decision") == "UNKNOWN_COHERENCE":
            return "nonevent", "fail_closed"
        return "nonevent", "gate_abort"
    committed = []
    writes_at = {}
    for w in ws:
        hs = w.get("http_status")
        if hs is None:
            return "excluded", "write_status_missing"
        if not (200 <= int(hs) < 300):
            if int(hs) == 404 and arm == "A4d" and accepted:
                continue
            return "excluded", "write_not_committed"
        if arm == "A4d" and w.get("audit_id") in aud["__terminating__"]:
            # Pod PATCH answered with a Pod already marked for deletion: applied after the DELETE's deletion
            # mark and never persisted (plan 2.3 (f)); only consistent with an accepted DELETE
            if accepted:
                continue
            return "excluded", "write_on_terminating_pod_action_not_accepted"
        rv = pick_rv(aud, w.get("audit_id"), w.get("resulting_resource_version"))
        if rv is None or rv == "bad":
            return "excluded", "write_rv"
        raw_o = str(w.get("object") or "")
        if raw_o.startswith("pod-annotations:"):
            objs = [obj_of(k) for k in raw_o.split(":", 1)[1].split(",") if k]
        else:
            o = obj_of(raw_o) if raw_o else None
            if o not in OBJS:
                o = {"async_scope": "response-scope", "async_app_policy": "application-policy",
                     "async_evidence_last_literal": "evidence-a"}.get(cond, o)
            objs = [o]
        nv = w.get("new_value")
        if nv is None:
            nv = "not allowed" if objs[0] == "response-scope" else "false"
        for o in objs:
            committed.append((rv, o, truth(nv)))
        writes_at.setdefault(rv, []).append(tuple(sorted((o, truth(nv)) for o in objs)))
    for rv, lst in writes_at.items():
        if len(set(lst)) > 1:          # different writes at one revision must be identical no-op PATCHes
            return "excluded", "same_rv_conflicting_writes"
    if not accepted:
        return "nonevent", "denied_%s" % code
    if cond == "uid_rebind_control":
        uids = aud.get("__uid__", {}).get(act.get("audit_id"))
        if not uids:
            return "excluded", "deleted_uid"
        return ("event", "wrong_object") if uids[0] != act.get("expected_uid") else ("nonevent", "right_object")
    if cut_in_fixture:
        return "event", "cut_in_fixture"
    arv = pick_rv(aud, act.get("audit_id"), act_rv_trial)
    if not committed:
        return "nonevent", "no_committed_write"
    if arv is None or arv == "bad":
        return "excluded", "action_rv"
    if any(rv == arv for rv, _, _ in committed):
        return "excluded", "rv_tie"
    st = dict(st0)
    for rv, o, v in sorted(committed):
        if rv < arv:
            st[o] = v
    return ("event", "invalid_at_commit") if not valid(st) else ("nonevent", "valid_at_commit")


def recompute(raws, audit, planned_ids=None):
    aud = audit_rc(audit)
    cells = defaultdict(lambda: {"k": 0, "n": 0, "excluded": defaultdict(int)})
    for t in raws:
        if planned_ids is not None and t.get("trial_id") not in planned_ids:
            continue
        if str(t.get("experiment") or "").upper() not in HEADLINE:
            continue
        k = key_of(t)
        cls, why = classify(t, aud)
        c = cells[k]
        if cls == "excluded":
            c["excluded"][why] += 1
            continue
        c["n"] += 1
        if cls == "event":
            c["k"] += 1
    return cells


def _load(path):
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                out.append(json.loads(line))
    return out


def _norm(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v


def compare(cells, results):
    diffs = []
    main = {}
    for c in results.get("cells", []):
        if c["cell"][0] in HEADLINE:
            main[tuple(_norm(v) for v in c["cell"])] = (c["primary"]["k_invalid_executed"], c["primary"]["n_primary_denominator"])
    ind = {tuple(_norm(v) for v in k): (v["k"], v["n"]) for k, v in cells.items()}
    for k in sorted(set(main) | set(ind), key=str):
        if main.get(k) != ind.get(k):
            diffs.append({"cell": list(k), "main": main.get(k), "independent": ind.get(k)})
    return diffs


def main(argv=None):
    ap = argparse.ArgumentParser(description="E39 independent headline recomputation (A, B, E, F, G)")
    ap.add_argument("--raw", required=True)
    ap.add_argument("--audit", required=True)
    ap.add_argument("--schedule", action="append", help="restrict to scheduled trial ids (all files given)")
    ap.add_argument("--compare", help="results_E39.json of analyze_e39.py")
    ap.add_argument("--out", help="write JSON here")
    a = ap.parse_args(argv)
    ids = None
    if a.schedule:
        ids = set()
        for p in a.schedule:
            s = json.load(open(p, encoding="utf-8"))
            for e in (s["trials"] if isinstance(s, dict) else s):
                ids.add(e["trial_id"])
    cells = recompute(_load(a.raw), _load(a.audit), ids)
    doc = {"version": VERSION, "cells": [{"cell": list(k), "k": v["k"], "n": v["n"], "excluded": dict(v["excluded"])}
                                         for k, v in sorted(cells.items(), key=lambda kv: str(kv[0]))]}
    rc = 0
    if a.compare:
        res = json.load(open(a.compare, encoding="utf-8"))
        d = compare(cells, res)
        doc["comparison"] = {"n_differences": len(d), "differences": d}
        rc = 3 if d else 0
        print("independent vs main: %d cell differences" % len(d))
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2, sort_keys=True)
            fh.write("\n")
    for c in doc["cells"]:
        print("%-60s k=%4d n=%4d" % ("/".join("NA" if v is None else str(v) for v in c["cell"]), c["k"], c["n"]))
    return rc


if __name__ == "__main__":
    sys.exit(main())
