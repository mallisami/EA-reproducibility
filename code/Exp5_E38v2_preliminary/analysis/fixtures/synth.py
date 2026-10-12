#!/usr/bin/env python3
"""Synthetic E38v2 data generator (offline; for tests of the oracle and analysis only).

Simulates, per trial, a kube-apiserver/etcd timeline (fixture reset writes, gate reads, async
ConfigMap writer, UID-preconditioned Pod DELETE, optional validating webhook) and emits files in the
exact interface-spec formats: RAW_TRIALS.jsonl (e38v2.trial.v1), webhook_log.jsonl (e38v2.webhook.v1),
audit.log (audit.k8s.io/v1 Event lines, RequestReceived + ResponseComplete), SCHEDULE.json and a LOCK.
It also writes TRUTH.jsonl: the simulator's own ground truth (true commit order), used by the tests to
check the oracle. resourceVersions are assigned from a global etcd-like revision counter in true
commit order.

CLI:
  synth.py --out DIR [--design full|mini] [--seed N] [--no-edge-cases]
  synth.py --out DIR --stage2-from stage2_cells_E38v2.json      (append stage 2 to an existing run)
  synth.py --edge-cases-out DIR                                  (small hand-built edge-case set)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from e38v2_common import (ARMS, ASYNC_CONDITIONS, DELTAS_MS, N_STAGE1_ASYNC, N_STAGE1_NO_WRITE,  # noqa: E402
                          N_STAGE1_UID, N_TOPUP_TARGET, POLICY_OBJECTS, WRITER_OBJECT,
                          format_k8s_time_us, v_core, parse_bool)

MS = 1_000_000
US = 1_000
BASE_WALL = 1_790_402_400 * 10**9          # 2026-09-26T06:00:00Z
MONO_OFFSET = 1_790_000_000 * 10**9        # wall - mono (mono is an unrelated epoch)
EPOCH_OBJ = "e38-epoch"
ALL_OBJS = POLICY_OBJECTS + (EPOCH_OBJ,)

GATE_READS = {
    "A0_native_uid": [],
    "A1_dcji": ["evidence-a", "evidence-c", "evidence-e", "response-scope", "application-policy", EPOCH_OBJ],
    "A2_fullrefresh": ["evidence-a", "evidence-b", "evidence-c", "evidence-d", "evidence-e", "evidence-f",
                       "response-scope", "application-policy", EPOCH_OBJ],
    "A3_dcji_webhook": ["evidence-a", "evidence-c", "evidence-e", "response-scope", "application-policy", EPOCH_OBJ],
}
WEBHOOK_READS = list(POLICY_OBJECTS)
NEW_VALUE = {"async_scope": "not allowed", "async_app_policy": "false", "async_evidence_last_literal": "false"}

DEFAULT_P = dict(read_step=0.30 * MS, read_srv=0.12 * MS, read_rtt=0.25 * MS, ssar=0.30 * MS,
                 rel_jit_mean=40 * US, rel_jit_sd=20 * US, w_net=0.15 * MS,
                 w_commit_mean=0.60 * MS, w_commit_sd=0.25 * MS, w_commit_min=0.20 * MS,
                 d_send=0.05 * MS, d_net=0.15 * MS, wh_net=0.25 * MS, wh_first=0.10 * MS, wh_read_step=0.20 * MS,
                 wh_done=0.15 * MS, wh_read_half=0.08 * MS, d_commit_mean=0.50 * MS, d_commit_sd=0.15 * MS, d_commit_min=0.20 * MS,
                 rc_after=0.05 * MS, resp_after=0.20 * MS, rebind_after=0.02 * MS)


class Ctx:
    def __init__(self, run_id="e38v2-synth", seed=38260926, start_rv=5000):
        self.run_id = run_id
        self.namespace = "e38v2-" + run_id
        self.seed = seed
        self.rv = start_rv
        self.wall = BASE_WALL
        self.base = BASE_WALL
        self.pod_counter = 0

    def uid(self, tag):
        h = hashlib.sha256(("%s/%s" % (self.run_id, tag)).encode()).hexdigest()
        return "%s-%s-%s-%s-%s" % (h[:8], h[8:12], h[12:16], h[16:20], h[20:32])


def mono(wall_ns):
    return int(wall_ns - MONO_OFFSET)


def _cm_data(obj, value):
    if obj in ("response-scope", "application-policy"):
        return {"allowed": value}
    if obj == EPOCH_OBJ:
        return {"sequence": value}
    return {"value": value}


def audit_event(ctx, audit_id, stage, verb, resource, name, received_ns, stage_ns, code=None,
                request_obj=None, response_obj=None):
    ev = {"kind": "Event", "apiVersion": "audit.k8s.io/v1", "level": "RequestResponse",
          "auditID": audit_id, "stage": stage,
          "requestURI": "/api/v1/namespaces/%s/%s/%s" % (ctx.namespace, resource, name),
          "verb": verb, "user": {"username": "system:serviceaccount:%s:e38v2-runner" % ctx.namespace,
                                 "groups": ["system:serviceaccounts", "system:authenticated"]},
          "sourceIPs": ["172.18.0.1"], "userAgent": "e38v2-runner/1.0",
          "objectRef": {"resource": resource, "namespace": ctx.namespace, "name": name, "apiVersion": "v1"},
          # times passed in are relative to the trial base (float-safe); convert to absolute wall ns
          "requestReceivedTimestamp": format_k8s_time_us(ctx.base + int(round(received_ns))),
          "stageTimestamp": format_k8s_time_us(ctx.base + int(round(stage_ns)))}
    if request_obj is not None:
        ev["requestObject"] = request_obj
    if stage == "ResponseComplete":
        ev["responseStatus"] = {"metadata": {}, "code": code}
        if response_obj is not None:
            ev["responseObject"] = response_obj
        ev["annotations"] = {"authorization.k8s.io/decision": "allow", "authorization.k8s.io/reason": "RBAC"}
    return ev


def cm_object(ctx, name, value, rv):
    return {"kind": "ConfigMap", "apiVersion": "v1",
            "metadata": {"name": name, "namespace": ctx.namespace, "uid": ctx.uid("cm/" + name),
                         "resourceVersion": str(rv)}, "data": _cm_data(name, value)}


def status_object(code, reason, message):
    return {"kind": "Status", "apiVersion": "v1", "metadata": {}, "status": "Failure",
            "message": message, "reason": reason, "code": code}


def initial_values(cond):
    iv = {o: "true" for o in POLICY_OBJECTS}
    if cond == "async_evidence_last_literal":
        iv["evidence-c"] = "false"
        iv["evidence-e"] = "false"
    return iv


def _state_bool(values):
    return {o: parse_bool(values[o]) for o in POLICY_OBJECTS}


def simulate_trial(ctx, e, rng, P=None, jitter=True):
    """Simulate one scheduled trial. Returns (trial, audit_events, webhook_entries, truth)."""
    P = dict(DEFAULT_P, **(P or {}))
    arm, cond, delta = e["arm"], e["condition"], e["delta_ms"]
    tid = e["trial_id"]
    g = (lambda m, sd, lo: max(lo, rng.gauss(m, sd))) if jitter else (lambda m, sd, lo: max(lo, m))
    ctx.base = ctx.wall
    W0 = 0.0                      # simulate in relative ns (float-safe); absolute = A(x)
    A = lambda x: ctx.base + int(round(x))
    M = lambda x: A(x) - MONO_OFFSET
    iv = initial_values(cond)
    iv_all = dict(iv)
    iv_all[EPOCH_OBJ] = str(tid + 1)
    commits = []            # (time, kind, obj/None)
    audit = []
    # fixture reset: one update per ConfigMap, then pod (re)create
    for i, obj in enumerate(ALL_OBJS):
        commits.append((W0 + i * 0.2 * MS, "fixture", obj))
    commits.append((W0 + 2.2 * MS, "pod_create", None))
    ctx.pod_counter += 1
    pod_uid = ctx.uid("pod/%d" % ctx.pod_counter)
    pod_name = "e38v2-target"
    # gate
    G0 = W0 + 10 * MS          # gate starts well after fixture reset so a -5 ms writer never precedes the reset
    objs = GATE_READS[arm]
    reads = []
    for i, obj in enumerate(objs):
        send = G0 + i * P["read_step"]
        reads.append({"object": obj, "send": send, "srv": send + P["read_srv"], "recv": send + P["read_rtt"]})
    T = (reads[-1]["recv"] + P["ssar"]) if reads else G0 + 0.1 * MS
    # writer
    writer = None
    wobj = WRITER_OBJECT.get(cond)
    if cond in ASYNC_CONDITIONS:
        intended = T + delta * MS
        actual = intended + g(P["rel_jit_mean"], P["rel_jit_sd"], 0)
        w_recv = actual + P["w_net"]
        w_commit = w_recv + g(P["w_commit_mean"], P["w_commit_sd"], P["w_commit_min"])
        writer = {"intended": intended, "actual": actual, "recv": w_recv, "commit": w_commit,
                  "rc": w_commit + P["rc_after"], "tresp": w_commit + P["resp_after"]}
        commits.append((w_commit, "writer", wobj))

    def value_at(obj, t):
        if writer is not None and obj == wobj and writer["commit"] < t:
            return NEW_VALUE[cond]
        return iv_all[obj]

    # gate decision
    decision = "EXECUTE"
    truth_note = ""
    if reads:
        first, last = reads[0]["srv"], reads[-1]["srv"]
        wread = [r for r in reads if r["object"] == wobj]
        if writer is not None and first < writer["commit"] < last:
            decision = "UNKNOWN_COHERENCE"
        else:
            st = _state_bool({o: value_at(o, reads[0]["srv"]) if o in objs else iv_all[o] for o in POLICY_OBJECTS})
            if wread and not v_core(st)[0]:
                decision = "ABORT_REPLAN"
    # delete
    dispatched = decision == "EXECUTE"
    dl = {"dispatched": dispatched, "t_send": None, "R": None, "code": None, "rc": None, "tresp": None, "C": None}
    wh = None
    rebind_t = None
    if dispatched:
        d_send = T + P["d_send"]
        R = d_send + P["d_net"]
        dl.update(t_send=d_send, R=R)
        if cond == "uid_rebind_control":
            rebind_t = T + P["rebind_after"]
            commits.append((rebind_t, "pod_delete_old", None))
            commits.append((rebind_t + 0.01 * MS, "pod_create_new", None))
        base = R
        if arm == "A3_dcji_webhook":
            wr = R + P["wh_net"]
            wreads = [{"object": o, "srv": wr + P["wh_first"] + i * P["wh_read_step"]} for i, o in enumerate(WEBHOOK_READS)]
            done = wreads[-1]["srv"] + P["wh_done"]
            st = _state_bool({r["object"]: value_at(r["object"], r["srv"]) for r in wreads})
            ok = v_core(st)[0]
            wh = {"received": wr, "reads": wreads, "done": done, "decision": "allow" if ok else "deny"}
            base = done
        if wh is not None and wh["decision"] == "deny":
            dl.update(code=403, rc=base + 0.1 * MS)
            dl["tresp"] = dl["rc"] + 0.15 * MS
        elif cond == "uid_rebind_control":
            dl.update(code=409, rc=base + 0.2 * MS)
            dl["tresp"] = dl["rc"] + 0.15 * MS
        else:
            C = base + g(P["d_commit_mean"], P["d_commit_sd"], P["d_commit_min"])
            dl.update(code=200, C=C, rc=C + P["rc_after"], tresp=C + P["resp_after"])
            commits.append((C, "delete", None))
    # assign resourceVersions in true commit order
    commits.sort(key=lambda c: (c[0], c[1]))
    rv_of = {}
    obj_hist = {o: [] for o in ALL_OBJS}
    for t, kind, obj in commits:
        ctx.rv += 1
        rv_of[(kind, obj, t)] = ctx.rv
        if obj is not None:
            obj_hist[obj].append((t, ctx.rv))

    def rv_at(obj, t):
        best = None
        for tt, rv in obj_hist[obj]:
            if tt < t:
                best = rv
        return best

    w_rv = rv_of[("writer", wobj, writer["commit"])] if writer else None
    d_rv = rv_of[("delete", None, dl["C"])] if dl["C"] is not None else None
    # audit: fixture writes
    for i, obj in enumerate(ALL_OBJS):
        if obj != EPOCH_OBJ:        # keep file sizes small: only the epoch bump is emitted as audit noise
            continue
        t = W0 + i * 0.2 * MS
        aid = "fx-%s-%d-%d" % (ctx.run_id, tid, i)
        rv = rv_of[("fixture", obj, t)]
        audit.append(audit_event(ctx, aid, "RequestReceived", "update", "configmaps", obj, t - 0.3 * MS, t - 0.3 * MS))
        audit.append(audit_event(ctx, aid, "ResponseComplete", "update", "configmaps", obj, t - 0.3 * MS, t + P["rc_after"],
                                 200, cm_object(ctx, obj, iv_all[obj], None), cm_object(ctx, obj, iv_all[obj], rv)))
    w_aid = d_aid = None
    if writer:
        w_aid = "aud-%s-%d-w" % (ctx.run_id, tid)
        audit.append(audit_event(ctx, w_aid, "RequestReceived", "update", "configmaps", wobj, writer["recv"], writer["recv"]))
        audit.append(audit_event(ctx, w_aid, "ResponseComplete", "update", "configmaps", wobj, writer["recv"], writer["rc"],
                                 200, cm_object(ctx, wobj, NEW_VALUE[cond], None), cm_object(ctx, wobj, NEW_VALUE[cond], w_rv)))
    if dispatched:
        d_aid = "aud-%s-%d-d" % (ctx.run_id, tid)
        req = {"kind": "DeleteOptions", "apiVersion": "v1", "preconditions": {"uid": pod_uid}}
        audit.append(audit_event(ctx, d_aid, "RequestReceived", "delete", "pods", pod_name, dl["R"], dl["R"], request_obj=req))
        if dl["code"] == 200:
            resp = {"kind": "Pod", "apiVersion": "v1",
                    "metadata": {"name": pod_name, "namespace": ctx.namespace, "uid": pod_uid,
                                 "resourceVersion": str(d_rv), "deletionTimestamp": format_k8s_time_us(A(dl["C"]))[:19] + "Z",
                                 "deletionGracePeriodSeconds": 0}}
        elif dl["code"] == 403:
            resp = status_object(403, "Forbidden", 'admission webhook "e38v2.validate.dcji" denied the request: V_core false')
        else:
            resp = status_object(409, "Conflict", "Precondition failed: UID in precondition: %s, UID in object meta: %s"
                                 % (pod_uid, ctx.uid("pod/%d-replacement" % ctx.pod_counter)))
        audit.append(audit_event(ctx, d_aid, "ResponseComplete", "delete", "pods", pod_name, dl["R"], dl["rc"],
                                 dl["code"], req, resp))
    # webhook log entry
    wh_entries = []
    wh_uid = None
    if wh is not None:
        wh_uid = ctx.uid("whreq/%d" % tid)
        wh_entries.append({"schema": "e38v2.webhook.v1", "request_uid": wh_uid, "pod_name": pod_name,
                           "pod_uid": pod_uid if cond != "uid_rebind_control" else ctx.uid("pod/%d-replacement" % ctx.pod_counter),
                           "received_wall_ns": A(wh["received"]),
                           "reads": [{"object": r["object"], "resource_version": str(rv_at(r["object"], r["srv"])),
                                      "value": value_at(r["object"], r["srv"]),
                                      "t_send_wall_ns": A(r["srv"] - P["wh_read_half"]),
                                      "t_recv_wall_ns": A(r["srv"] + P["wh_read_half"])} for r in wh["reads"]],
                           "decision": wh["decision"], "reason": "V_core=%s" % ("true" if wh["decision"] == "allow" else "false"),
                           "completed_wall_ns": A(wh["done"])})
    # trial record
    trial = {"schema": "e38v2.trial.v1", "run_id": ctx.run_id, "trial_id": tid, "stage": e["stage"],
             "block": e["block"], "arm": arm, "condition": cond, "delta_ms": delta, "rep": e["rep"],
             "schedule_seed": e["schedule_seed"],
             "fixture": {"reset_ok": True, "pod_uid_decision": pod_uid, "initial_values": dict(iv_all)},
             "gate": {"decision": decision, "t_start_mono_ns": M(G0), "t_end_mono_ns": M(T), "t_end_wall_ns": A(T),
                      "reads": [{"object": r["object"], "resource_version": str(rv_at(r["object"], r["srv"])),
                                 "value": value_at(r["object"], r["srv"]),
                                 "t_send_mono_ns": M(r["send"]), "t_recv_mono_ns": M(r["recv"])} for r in reads],
                      "ssar_allowed": True if reads else None},
             "writer": None if writer is None else {
                 "object": wobj, "new_value": NEW_VALUE[cond], "intended_release_mono_ns": M(writer["intended"]),
                 "actual_release_mono_ns": M(writer["actual"]), "t_resp_mono_ns": M(writer["tresp"]),
                 "http_status": 200, "resulting_resource_version": str(w_rv), "audit_id": w_aid},
             "delete": {"dispatched": dispatched,
                        "t_send_mono_ns": M(dl["t_send"]) if dispatched else None,
                        "t_send_wall_ns": A(dl["t_send"]) if dispatched else None,
                        "t_resp_mono_ns": M(dl["tresp"]) if dispatched else None,
                        "http_status": dl["code"], "reason": {200: None, 403: "Forbidden", 409: "Conflict"}.get(dl["code"]),
                        "audit_id": d_aid, "expected_uid": pod_uid,
                        "deleted_object_resource_version": str(d_rv) if d_rv is not None else None},
             "effect": {"verified": dl["code"] == 200, "t_verified_mono_ns": M(dl["tresp"] + 2 * MS) if dl["code"] == 200 else None},
             # like the live runner: the AdmissionReview uid is not visible to the client; the oracle
             # joins the webhook entry on pod_uid == delete.expected_uid
             "webhook_request_uid": None, "status": "COMPLETE", "error": None}
    # truth (true commit order)
    v_post = None
    if writer is not None:
        st = _state_bool(iv_all)
        st[wobj] = parse_bool(NEW_VALUE[cond])
        v_post = v_core(st)[0]
    if decision == "UNKNOWN_COHERENCE":
        truth = "UNKNOWN"
    elif decision == "ABORT_REPLAN":
        seen = writer is not None and writer["commit"] < [r for r in reads if r["object"] == wobj][0]["srv"]
        truth = "CORRECT_ABORT" if (seen and not v_post) else "FALSE_ABORT"
    elif dl["code"] == 409:
        truth = "UID_BLOCKED"
    elif dl["code"] == 403:
        if writer is None:
            truth = "FALSE_ABORT"
        else:
            srv = [r for r in wh["reads"] if r["object"] == wobj][0]["srv"]
            truth = "CORRECT_ABORT" if (writer["commit"] < srv and not v_post) else "FALSE_ABORT"
    else:
        truth = "INVALID_EXECUTED" if (writer is not None and writer["commit"] < dl["C"] and not v_post) else "VALID_EXECUTED"
    end = max([c[0] for c in commits] + [T, dl["tresp"] or 0, writer["tresp"] if writer else 0])
    ctx.wall = A(end + 20 * MS)
    return trial, audit, wh_entries, {"trial_id": tid, "truth_primary": truth, "injected": ""}


# ----------------------------------------------------------------------------- schedules
def stage1_schedule(seed, design="full"):
    rng = random.Random(seed)
    deltas = DELTAS_MS if design == "full" else (-2.0, 0.0, 1.0, 10.0)
    n_async = N_STAGE1_ASYNC if design == "full" else 4
    n_nw = N_STAGE1_NO_WRITE if design == "full" else 4
    n_uid = N_STAGE1_UID if design == "full" else 2
    entries, tid = [], 0
    for b in range(max(n_async, n_nw, n_uid)):
        blk = []
        for arm in ARMS:
            if b < n_nw:
                blk.append((arm, "no_write", None))
            if b < n_uid:
                blk.append((arm, "uid_rebind_control", None))
            if b < n_async:
                for c in ASYNC_CONDITIONS:
                    for d in deltas:
                        blk.append((arm, c, d))
        rng.shuffle(blk)
        for arm, c, d in blk:
            entries.append({"trial_id": tid, "stage": 1, "block": b, "arm": arm, "condition": c,
                            "delta_ms": d, "rep": b, "schedule_seed": seed})
            tid += 1
    return entries


def stage2_schedule(seed, cells, first_trial_id, first_block):
    rng = random.Random(seed * 7 + 2)
    entries, tid = [], first_trial_id
    maxadd = max([c["n_topup"] for c in cells] or [0])
    for k in range(maxadd):
        blk = [(c["arm"], c["condition"], c["delta_ms"], c["n_stage1_planned"] + k) for c in cells if k < c["n_topup"]]
        rng.shuffle(blk)
        for arm, cond, d, rep in blk:
            entries.append({"trial_id": tid, "stage": 2, "block": first_block + k, "arm": arm, "condition": cond,
                            "delta_ms": d, "rep": rep, "schedule_seed": seed})
            tid += 1
    return entries


# ----------------------------------------------------------------------------- edge-case injection
def inject_edge_cases(trials, audits, whs, truths, ctx):
    """Mutates generated data in place to include every hard case. Returns {name: trial_id}."""
    by = {t["trial_id"]: t for t in trials}
    tru = {x["trial_id"]: x for x in truths}
    picks = {}

    def pick(pred, name):
        for t in trials:
            if t["trial_id"] in picks.values():
                continue
            if pred(t):
                picks[name] = t["trial_id"]
                return t
        return None

    # 1 INFRA_ERROR (runner reported)
    for k in range(3):
        t = pick(lambda t: t["arm"] == "A2_fullrefresh" and t["condition"] == "async_scope" and t["delta_ms"] == 5.0, "infra_%d" % k)
        if t:
            t["status"] = "INFRA_ERROR"
            t["error"] = "injected: apiserver connection reset during fixture reset"
            tru[t["trial_id"]].update(truth_primary="INFRA_ERROR", injected="infra_error")
    # 2 runner UNKNOWN
    t = pick(lambda t: t["arm"] == "A1_dcji" and t["condition"] == "no_write", "runner_unknown")
    if t:
        t["status"] = "UNKNOWN"
        t["error"] = "injected: effect verification timed out"
        tru[t["trial_id"]].update(truth_primary="UNKNOWN", injected="runner_unknown")
    # 3 missing writer audit event (primary falls back to trial rv; secondary UNKNOWN)
    t = pick(lambda t: t["arm"] == "A0_native_uid" and t["condition"] == "async_app_policy"
             and t["delete"]["http_status"] == 200 and t["writer"] is not None, "missing_writer_audit")
    if t:
        aid = t["writer"]["audit_id"]
        audits[:] = [a for a in audits if a.get("auditID") != aid]
        tru[t["trial_id"]]["injected"] = "missing_writer_audit"
    # 4 equal timestamps (writer ResponseComplete == DELETE requestReceived)
    t = pick(lambda t: t["arm"] == "A0_native_uid" and t["condition"] == "async_scope"
             and t["delete"]["http_status"] == 200, "equal_timestamps")
    if t:
        d_rr = [a for a in audits if a.get("auditID") == t["delete"]["audit_id"]][0]["requestReceivedTimestamp"]
        for a in audits:
            if a.get("auditID") == t["writer"]["audit_id"] and a["stage"] == "ResponseComplete":
                a["stageTimestamp"] = d_rr
        tru[t["trial_id"]]["injected"] = "equal_timestamps"
    # 5 webhook deny with valid state (false abort) in no_write
    for k in range(2):
        t = pick(lambda t: t["arm"] == "A3_dcji_webhook" and t["condition"] == "no_write", "webhook_false_abort_%d" % k)
        if t:
            for w in whs:
                if w["pod_uid"] == t["delete"]["expected_uid"]:
                    w["decision"] = "deny"
                    w["reason"] = "injected: fail-closed on read timeout"
            t["delete"].update(http_status=403, reason="Forbidden", deleted_object_resource_version=None)
            t["effect"] = {"verified": False, "t_verified_mono_ns": None}
            for a in audits:
                if a.get("auditID") == t["delete"]["audit_id"] and a["stage"] == "ResponseComplete":
                    a["responseStatus"]["code"] = 403
                    a["responseObject"] = status_object(403, "Forbidden", "admission webhook denied: fail-closed")
            tru[t["trial_id"]].update(truth_primary="FALSE_ABORT", injected="webhook_false_abort")
    return picks


# ----------------------------------------------------------------------------- run writer
def write_jsonl(path, recs, mode="w"):
    with open(path, mode, encoding="utf-8", newline="\n") as fh:
        for r in recs:
            fh.write(json.dumps(r, sort_keys=True) + "\n")


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def simulate_entries(ctx, entries, jitter=True):
    trials, audits, whs, truths = [], [], [], []
    for e in entries:
        rng = random.Random("%d/%d" % (ctx.seed, e["trial_id"]))
        t, a, w, tr = simulate_trial(ctx, e, rng, jitter=jitter)
        trials.append(t)
        audits += a
        whs += w
        truths.append(tr)
    return trials, audits, whs, truths


def generate_run(root, design="full", seed=38260926, run_id="synthrun1", edge_cases=True):
    """Creates <root>/results/<run_id>/{SCHEDULE.json,RAW_TRIALS.jsonl,webhook_log.jsonl,audit.log,TRUTH.jsonl}
    and <root>/protocol/{PROTOCOL_E38v2.txt,ANALYSIS_PLAN_E38v2.txt,LOCK_E38v2.json}. Returns dict of paths."""
    root = Path(root)
    rd = root / "results" / run_id
    pd = root / "protocol"
    rd.mkdir(parents=True, exist_ok=True)
    pd.mkdir(parents=True, exist_ok=True)
    ctx = Ctx(run_id=run_id, seed=seed)
    entries = stage1_schedule(seed, design)
    sched = {"schema": "e38v2.schedule.v1", "run_id": run_id, "schedule_seed": seed, "stage": 1,
             "design": design, "trials": entries}
    (rd / "SCHEDULE.json").write_text(json.dumps(sched, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    trials, audits, whs, truths = simulate_entries(ctx, entries)
    picks = inject_edge_cases(trials, audits, whs, truths, ctx) if edge_cases else {}
    write_jsonl(rd / "RAW_TRIALS.jsonl", trials)
    write_jsonl(rd / "audit.log", audits)
    write_jsonl(rd / "webhook_log.jsonl", whs)
    write_jsonl(rd / "TRUTH.jsonl", truths)
    (pd / "PROTOCOL_E38v2.txt").write_text("synthetic protocol placeholder for tests\n", encoding="utf-8")
    (pd / "ANALYSIS_PLAN_E38v2.txt").write_text("synthetic analysis plan placeholder for tests\n", encoding="utf-8")
    lock = {"status": "LOCKED", "run_id": run_id, "schedule_seed": seed,
            "files": {"protocol/PROTOCOL_E38v2.txt": sha(pd / "PROTOCOL_E38v2.txt")},
            "schedule_sha256": sha(rd / "SCHEDULE.json"),
            "analysis_plan_sha256": sha(pd / "ANALYSIS_PLAN_E38v2.txt")}
    (pd / "LOCK_E38v2.json").write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    state = {"ctx_rv": ctx.rv, "ctx_wall": ctx.wall, "ctx_pods": ctx.pod_counter, "next_trial_id": len(entries),
             "next_block": max(e["block"] for e in entries) + 1, "seed": seed, "run_id": run_id}
    (rd / "SYNTH_STATE.json").write_text(json.dumps(state) + "\n", encoding="utf-8")
    return {"root": root, "run_dir": rd, "lock": pd / "LOCK_E38v2.json", "picks": picks}


def append_stage2(root, run_id, stage2_cells_json):
    rd = Path(root) / "results" / run_id
    st = json.loads((rd / "SYNTH_STATE.json").read_text())
    cells = json.loads(Path(stage2_cells_json).read_text())["cells"]
    ctx = Ctx(run_id=run_id, seed=st["seed"], start_rv=st["ctx_rv"])
    ctx.wall, ctx.pod_counter = st["ctx_wall"], st["ctx_pods"]
    entries = stage2_schedule(st["seed"], cells, st["next_trial_id"], st["next_block"])
    sched = {"schema": "e38v2.schedule.v1", "run_id": run_id, "schedule_seed": st["seed"], "stage": 2, "trials": entries}
    (rd / "SCHEDULE_STAGE2.json").write_text(json.dumps(sched, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    trials, audits, whs, truths = simulate_entries(ctx, entries)
    write_jsonl(rd / "RAW_TRIALS.jsonl", trials, "a")
    write_jsonl(rd / "audit.log", audits, "a")
    write_jsonl(rd / "webhook_log.jsonl", whs, "a")
    write_jsonl(rd / "TRUTH.jsonl", truths, "a")
    return rd / "SCHEDULE_STAGE2.json", len(entries)


# ----------------------------------------------------------------------------- hand-built scenarios
def scenario(arm, cond, delta, P=None, trial_id=1, jitter=False):
    """One deterministic trial (no jitter) for unit tests. Returns (trial, audit, webhook, truth)."""
    ctx = Ctx(run_id="unit", seed=38260926)
    # distinct Pod UIDs per scenario trial id (the webhook log is joined on pod_uid, as live)
    ctx.pod_counter = 1000 * int(trial_id)
    e = {"trial_id": trial_id, "stage": 1, "block": 0, "arm": arm, "condition": cond, "delta_ms": delta,
         "rep": 0, "schedule_seed": ctx.seed}
    return simulate_trial(ctx, e, random.Random(1), P=P, jitter=jitter)


EDGE_CASES = {
    # name: (arm, condition, delta, P overrides, expected primary, expected secondary)
    "write_just_before_delete_commit": ("A1_dcji", "async_scope", 0.0, {"d_commit_mean": 0.60 * MS}, "INVALID_EXECUTED", "VALID_EXECUTED"),
    "write_just_after_delete_commit": ("A1_dcji", "async_scope", 0.0, {"d_commit_mean": 0.58 * MS}, "VALID_EXECUTED", "VALID_EXECUTED"),
    "write_well_before_delete": ("A0_native_uid", "async_app_policy", -1.0, None, "INVALID_EXECUTED", "INVALID_EXECUTED"),
    "evidence_last_literal_invalid": ("A2_fullrefresh", "async_evidence_last_literal", -1.0, None, "INVALID_EXECUTED", "INVALID_EXECUTED"),
    "fence_negative_delta": ("A1_dcji", "async_scope", -2.0, None, "UNKNOWN", "UNKNOWN"),
    "gate_sees_write_correct_abort": ("A1_dcji", "async_scope", -5.0, None, "CORRECT_ABORT", "CORRECT_ABORT"),
    "webhook_catches_write": ("A3_dcji_webhook", "async_scope", -1.0, None, "CORRECT_ABORT", "CORRECT_ABORT"),
    # webhook log carries per-read timestamps -> time rule uses the send time of the scope read -> agreement
    # (with only received_wall_ns the time rule was conservative and disagreed: FALSE_ABORT)
    "webhook_catches_write_time_rule_per_read": ("A3_dcji_webhook", "async_scope", 0.5, None, "CORRECT_ABORT", "CORRECT_ABORT"),
    "webhook_residual_window": ("A3_dcji_webhook", "async_scope", 1.0, None, "INVALID_EXECUTED", "VALID_EXECUTED"),
    "late_write_valid": ("A3_dcji_webhook", "async_app_policy", 50.0, None, "VALID_EXECUTED", "VALID_EXECUTED"),
    "uid_block_409": ("A1_dcji", "uid_rebind_control", None, None, "UID_BLOCKED", "UID_BLOCKED"),
    "no_write_valid": ("A3_dcji_webhook", "no_write", None, None, "VALID_EXECUTED", "VALID_EXECUTED"),
}


def write_edge_cases(outdir):
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    trials, audits, whs, exp = [], [], [], []
    for i, (name, (arm, cond, d, P, ep, es)) in enumerate(sorted(EDGE_CASES.items())):
        t, a, w, tr = scenario(arm, cond, d, P=P, trial_id=i)
        trials.append(t)
        audits += a
        whs += w
        exp.append({"trial_id": i, "case": name, "expected_primary": ep, "expected_secondary": es})
    write_jsonl(out / "RAW_TRIALS.jsonl", trials)
    write_jsonl(out / "audit.log", audits)
    write_jsonl(out / "webhook_log.jsonl", whs)
    write_jsonl(out / "EXPECTED.jsonl", exp)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    ap.add_argument("--design", choices=("full", "mini"), default="full")
    ap.add_argument("--seed", type=int, default=38260926)
    ap.add_argument("--run-id", default="synthrun1")
    ap.add_argument("--no-edge-cases", action="store_true")
    ap.add_argument("--stage2-from")
    ap.add_argument("--edge-cases-out")
    a = ap.parse_args(argv)
    if a.edge_cases_out:
        print("edge cases ->", write_edge_cases(a.edge_cases_out))
    if a.out and a.stage2_from:
        p, n = append_stage2(a.out, a.run_id, a.stage2_from)
        print("stage 2: %d trials, schedule %s" % (n, p))
    elif a.out:
        r = generate_run(a.out, a.design, a.seed, a.run_id, not a.no_edge_cases)
        print("run ->", r["run_dir"], "lock ->", r["lock"], "edge picks:", r["picks"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
