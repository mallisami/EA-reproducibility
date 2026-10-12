#!/usr/bin/env python3
"""Synthetic E39 data generator (offline; for tests of the oracle, analysis and independent recomputation).

Simulates per trial a kube-apiserver/etcd timeline in continuous time (fixture reset, gate reads or the A4
Pod read, single async writer / Poisson arrivals / pre-gate cuts, A4m mirror controller, kubelet Pod status
updates, A3 webhook, Pod DELETE with uid(+resourceVersion) preconditions or NetworkPolicy CREATE) and emits
files in the E39 formats: RAW_TRIALS.jsonl (e39.trial.v1, protocol/E39_RECORD_SCHEMA.json field names),
webhook_log.jsonl, audit.log (audit.k8s.io/v1 ResponseComplete events), SCHEDULE.json and TRUTH.jsonl.
resourceVersions come from one global revision counter assigned in true commit-time order, so TRUTH (derived
from times) is an independent check of the oracle (derived from RVs and audit parsing).

CLI: synth_e39.py --out DIR [--design full|mini] [--seed N] [--experiments A,B,...]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from e39_common import (ACQ_MODES, ARMS_A, ARMS_B, ARMS_D, ARMS_E, ARMS_F, ARMS_G, ASYNC_CONDITIONS,  # noqa: E402
                        C_CONDITIONS, DELTAS_A, DELTAS_SHORT, G_CONDITIONS, G_CUT_OBJECTS, LAMBDAS_B,
                        LATENCIES_C, LOAD_LEVELS, N_A_ASYNC, N_A_NO_WRITE, N_A_UID, N_B, N_C_PAIRS, N_D, N_E,
                        N_F, N_G, PLANNING_MODES, POLICY_OBJECTS, WRITER_OBJECT, format_k8s_time_us, parse_bool,
                        v_core)

MS = 1_000_000.0
US = 1_000.0
BASE_WALL = 1_790_402_400 * 10**9
MONO_OFFSET = 1_790_000_000 * 10**9
RAW_ARM = {("A1", "python"): "A1_dcji", ("A2", "python"): "A2_fullrefresh", ("A3", "python"): "A3_dcji_webhook",
           ("A4d", "python"): "A4d_commit_direct", ("A4m", "python"): "A4m_commit_mirror",
           ("A1", "client-go"): "A1_dcji_go", ("A4d", "client-go"): "A4d_commit_direct_go"}
RAW_COND = {"async_scope": "async_scope", "async_app_policy": "async_app_policy",
            "async_evidence_last_literal": "async_evidence_last_literal", "no_write": "no_write",
            "uid_rebind_control": "uid_rebind_control", "stable": "stable", "ea_attack": "ea_attack",
            "out_of_model_cut_bdf": "out_of_model_bdf", "in_model_cut_ace": "in_model_ace"}
ANN_KEY = {"evidence-" + x: "e38.evidence/" + x for x in "abcdef"}
ANN_KEY.update({"response-scope": "e38.scope", "application-policy": "e38.app-policy"})
NEW_VALUE = {"response-scope": "not allowed", "application-policy": "false"}
DCJI_READS = ["evidence-a", "evidence-c", "evidence-e", "response-scope", "application-policy"]
FULL_READS = list(POLICY_OBJECTS)
LOAD_MULT = {"idle": 1.0, "moderate": 1.6, "high": 2.6}

DEFAULT_P = dict(read_step=0.30, read_srv=0.12, read_rtt=0.25, ssar=0.30, rel_jit=0.04, w_net=0.15,
                 w_commit=0.60, w_commit_sd=0.25, w_commit_min=0.20, d_send=0.05, d_net=0.15, d_commit=0.50,
                 d_commit_sd=0.15, d_commit_min=0.20, rc_after=0.05, resp_after=0.20, wh_net=0.25, wh_step=0.20,
                 wh_srv=0.08, wh_done=0.15, mirror_lag_mean=3.0, mirror_lag_min=0.4, p_status_update=0.0,
                 planning_a1=8.0, planning_a2=2.0, go_d_send=0.02)


def wv(obj):
    return NEW_VALUE.get(obj, "false")


class Ctx:
    def __init__(self, run_id="e39-synth", seed=3926099):
        self.run_id = run_id
        self.namespace = "e39-" + run_id
        self.seed = seed
        self.rv = 7000
        self.wall = BASE_WALL
        self.n = 0

    def uid(self, tag):
        h = hashlib.sha256(("%s/%s" % (self.run_id, tag)).encode()).hexdigest()
        return "%s-%s-%s-%s-%s" % (h[:8], h[8:12], h[12:16], h[16:20], h[20:32])


def _ev(ctx, base, aid, verb, resource, name, recv, done, code, ro=None, message=None, user="e39-runner"):
    ev = {"kind": "Event", "apiVersion": "audit.k8s.io/v1", "level": "RequestResponse", "auditID": aid,
          "stage": "ResponseComplete", "verb": verb,
          "requestURI": "/api/v1/namespaces/%s/%s/%s" % (ctx.namespace, resource, name),
          "user": {"username": "system:serviceaccount:e39-system:" + user},
          "objectRef": {"resource": resource, "namespace": ctx.namespace, "name": name},
          "requestReceivedTimestamp": format_k8s_time_us(base + int(round(recv * MS))),
          "stageTimestamp": format_k8s_time_us(base + int(round(done * MS))),
          "responseStatus": {"metadata": {}, "code": code}}
    if message:
        ev["responseStatus"]["message"] = message
    if ro is not None:
        ev["responseObject"] = ro
    return ev


def initial_values(cond):
    iv = {o: "true" for o in POLICY_OBJECTS}
    if cond == "async_evidence_last_literal":
        iv["evidence-c"] = "false"
        iv["evidence-e"] = "false"
    return iv


def simulate_trial(ctx, e, rng, P=None):
    """One scheduled trial -> (raw record, audit events, webhook entries, truth dict)."""
    P = dict(DEFAULT_P, **(P or {}))
    x, arm, impl, cond = e["experiment"], e["arm_c"], e["client_impl"], e["condition_c"]
    delta, lam = e.get("delta_ms"), e.get("lambda_per_s")
    lat_c = e.get("injected_latency_ms") or 0.0
    acq, plm, load = e.get("acquisition_mode"), e.get("planning_mode"), e.get("load_level") or "idle"
    action_type = "netpol_create" if x == "E" else "pod_delete"
    ctx.n += 1
    base = ctx.wall
    ctx.wall += 400 * int(MS)
    A = lambda t: base + int(round(t * MS))
    M = lambda t: A(t) - MONO_OFFSET
    g = lambda m, sd, lo: max(lo, rng.gauss(m, sd))
    # host / load guard (E39-D)
    throttled = False
    cpu = {"idle": 12.0, "moderate": 48.0, "high": 78.0}[load] + rng.uniform(-5, 5)
    if load == "high" and rng.random() < P.get("p_throttle", 0.05):
        throttled, cpu = True, 88.0
    eff_load = "moderate" if throttled else load
    km = LOAD_MULT[eff_load] if x == "D" else 1.0
    iv = initial_values(cond)
    ann_mode = arm in ("A4d", "A4m")
    # ----- commits: list of dicts {t, kind, obj, value, pod(bool)}
    commits = []
    for i, o in enumerate(POLICY_OBJECTS):
        commits.append({"t": i * 0.2, "kind": "fixture", "obj": o, "value": iv[o], "pod": False})
    commits.append({"t": 2.0, "kind": "pod_create", "obj": None, "pod": True})
    pod_uid = ctx.uid("pod/%d" % ctx.n)
    pod_name = "e39-target"
    t0 = 10.0
    planning_ms = None
    if plm == "per_incident":
        planning_ms = g(P["planning_a1"] if arm == "A1" else P["planning_a2"], 0.5, 0.2)
    G0 = t0 + (planning_ms or 0.0)
    # ----- writes
    writes = []           # {obj, value, release, commit, role, pod}
    wobj = WRITER_OBJECT.get(cond)
    rs = P["read_step"] * km

    def mk_write(obj, release, role):
        c = release + P["w_net"] * km + g(P["w_commit"] * km, P["w_commit_sd"], P["w_commit_min"])
        return {"obj": obj, "value": wv(obj), "release": release, "commit": c, "role": role, "pod": arm == "A4d"}

    if cond in G_CONDITIONS:
        for j, o in enumerate(G_CUT_OBJECTS[cond]):
            writes.append(mk_write(o, G0 - 6.0 + j * 0.8, "cut"))
    # ----- gate reads
    if ann_mode:
        read_objs = []
    elif arm == "A2":
        read_objs = FULL_READS
    else:
        read_objs = DCJI_READS
    reads = []
    extra = lat_c
    for i, o in enumerate(read_objs):
        if acq == "parallel":
            # bounded client concurrency (3 in flight): A1's 5 reads take 2 rounds, A2's 8 reads 3 rounds
            send = G0 + (i // 3) * (rs + extra) + (i % 3) * 0.02
        else:
            send = G0 + i * (rs + extra)
        reads.append({"object": o, "send": send, "srv": send + P["read_srv"] * km + extra, "recv": send + P["read_rtt"] * km + extra})
    pod_read = None
    if ann_mode:
        pod_read = {"send": G0, "srv": G0 + P["read_srv"] * km, "recv": G0 + P["read_rtt"] * km}
        T = pod_read["recv"] + P["ssar"] * km
    else:
        T = (max(r["recv"] for r in reads) + P["ssar"] * km) if reads else G0 + 0.1
    if cond in ASYNC_CONDITIONS and x != "B":
        intended = T + delta
        writes.append(dict(mk_write(wobj, intended + abs(rng.gauss(P["rel_jit"], 0.02)), "writer"), intended=intended))
    # A4m mirror
    for w in writes:
        if arm == "A4m":
            lag = P.get("mirror_lag_fixed")
            if lag is None:
                lag = P["mirror_lag_min"] + rng.expovariate(1.0 / P["mirror_lag_mean"])
            w["mirror_commit"] = w["commit"] + lag

    def val_cm(obj, t, ws):
        v = iv[obj]
        best = -1.0
        for c in ws:
            if c["obj"] == obj and c["commit"] < t and c["commit"] > best:
                best, v = c["commit"], c["value"]
        return v

    def val_ann(obj, t, ws):
        v = iv[obj]
        best = -1.0
        for c in ws:
            ct = c["commit"] if arm == "A4d" else c.get("mirror_commit")
            if ct is not None and c["obj"] == obj and ct < t and ct > best:
                best, v = ct, c["value"]
        return v

    # ----- Poisson arrivals (E39-B): from gate start until action complete + 50 ms; generated lazily below
    def gate_decision(ws):
        if ann_mode:
            st = {o: parse_bool(val_ann(o, pod_read["srv"], ws)) for o in POLICY_OBJECTS}
            return "EXECUTE" if v_core(st)[0] else "ABORT_REPLAN"
        first, last = reads[0]["srv"], reads[-1]["srv"]
        if any(first < c["commit"] < last for c in ws):
            return "UNKNOWN_COHERENCE"
        st = {o: parse_bool(val_cm(o, r["srv"], ws)) for r in reads for o in [r["object"]]}
        for o in POLICY_OBJECTS:
            st.setdefault(o, parse_bool(iv[o]))       # DCJI certificate: unread literals assumed per the model
        return "EXECUTE" if v_core(st)[0] else "ABORT_REPLAN"

    if x == "B":
        t = G0
        horizon = G0 + 200.0
        while True:
            t += rng.expovariate(lam) * 1000.0
            if t > horizon:
                break
            writes.append(mk_write(wobj, t, "arrival"))
    # decision uses writes committed so far (arrivals included; all are time-ordered facts)
    decision = gate_decision(writes)
    dispatched = decision == "EXECUTE"
    d_send = T + (P["go_d_send"] if impl == "client-go" else P["d_send"])
    R = d_send + P["d_net"] * km
    wh = None
    if dispatched and arm == "A3":
        wr = R + P["wh_net"]
        wreads = []
        for i, o in enumerate(FULL_READS):
            s = wr + P["wh_first"] if "wh_first" in P else wr + 0.10
            s = s + i * P["wh_step"]
            wreads.append({"object": o, "send": s, "srv": s + P["wh_srv"]})
        st = {r["object"]: parse_bool(val_cm(r["object"], r["srv"], writes)) for r in wreads}
        wh = {"recv": wr, "reads": wreads, "decision": "allow" if v_core(st)[0] else "deny",
              "done": wreads[-1]["srv"] + P["wh_done"]}
        C = wh["done"] + g(P["d_commit"] * km, P["d_commit_sd"], P["d_commit_min"])
    else:
        C = R + g(P["d_commit"] * km, P["d_commit_sd"], P["d_commit_min"])
    # E39-B arrivals last until the action completes (+50 ms); later draws are not part of the trial
    if x == "B":
        end = (C + P["resp_after"] if dispatched else T) + 50.0
        writes = [w for w in writes if w["role"] != "arrival" or w["release"] <= end]
    # kubelet status updates on the Pod (source of false 409s for A4)
    status_updates = []
    if dispatched and ann_mode and rng.random() < P["p_status_update"]:
        status_updates.append(pod_read["srv"] + rng.uniform(0.01, max(0.02, C - pod_read["srv"] - 0.01)))
    if P.get("force_status_update_at") is not None and dispatched and ann_mode:
        status_updates.append(pod_read["srv"] + P["force_status_update_at"])
    # outcome of the action
    outcome = None
    uid_rebind = cond == "uid_rebind_control"
    if not dispatched:
        outcome = "not_dispatched"
    elif uid_rebind:
        outcome = "409_uid"
    elif arm == "A3" and wh["decision"] == "deny":
        outcome = "403"
    elif ann_mode:
        pod_mods = [c["commit"] for c in writes if arm == "A4d"] + \
                   [c["mirror_commit"] for c in writes if arm == "A4m" and c.get("mirror_commit") is not None] + status_updates
        if any(pod_read["srv"] < m < C for m in pod_mods):
            outcome = "409_rv"
        else:
            outcome = "accept"
    else:
        outcome = "accept"
    # A4d arrivals after the Pod is gone -> 404 (not committed)
    for w in writes:
        w["committed"] = True
        if arm == "A4d" and outcome == "accept" and w["commit"] > C:
            w["committed"] = False
    # ----- assign resource versions in commit-time order
    evs = [dict(c) for c in commits]
    for i, w in enumerate(writes):
        if w["committed"]:
            evs.append({"t": w["commit"], "kind": "write", "i": i, "pod": w["pod"]})
            if w.get("mirror_commit") is not None:
                evs.append({"t": w["mirror_commit"], "kind": "mirror", "i": i, "pod": True})
    for j, s in enumerate(status_updates):
        evs.append({"t": s, "kind": "status", "pod": True})
    if outcome == "accept":
        evs.append({"t": C, "kind": "action", "pod": action_type == "pod_delete"})
    evs.sort(key=lambda z: z["t"])
    last_obj_rv = {}
    pod_mod = []           # (t, rv)
    for z in evs:
        ctx.rv += 1
        z["rv"] = ctx.rv
        if z["kind"] == "fixture":
            last_obj_rv.setdefault(z["obj"], [])
            last_obj_rv[z["obj"]].append((z["t"], z["rv"]))
        elif z["kind"] == "write":
            w = writes[z["i"]]
            w["rv"] = z["rv"]
            if not w["pod"]:
                last_obj_rv.setdefault(w["obj"], []).append((z["t"], z["rv"]))
        elif z["kind"] == "mirror":
            writes[z["i"]]["mirror_rv"] = z["rv"]
        elif z["kind"] == "action":
            action_rv = z["rv"]
        if z["pod"] and z["kind"] != "action":
            pod_mod.append((z["t"], z["rv"]))

    def cm_rv_at(obj, t):
        return max(rv for (tt, rv) in last_obj_rv.get(obj, []) if tt < t)

    def pod_rv_at(t):
        return max(rv for (tt, rv) in pod_mod if tt < t)

    # ----- audit + records
    audit, whlog = [], []
    rec_writes = []
    for i, w in enumerate(writes):
        aid = ctx.uid("w/%d/%d" % (ctx.n, i))
        rec = {"object": (pod_name if w["role"] == "arrival" else ANN_KEY[w["obj"]]) if w["pod"] else w["obj"], "new_value": w["value"],
               "intended_release_mono_ns": M(w.get("intended", w["release"])), "actual_release_mono_ns": M(w["release"]),
               "t_resp_mono_ns": M(w["commit"] + P["resp_after"]), "http_status": 200 if w["committed"] else 404,
               "resulting_resource_version": str(w["rv"]) if w["committed"] else None, "audit_id": aid,
               "planned_offset_ms": w["release"] - G0}
        if w["committed"]:
            if w["pod"]:
                ro = {"kind": "Pod", "apiVersion": "v1", "metadata": {"name": pod_name, "uid": pod_uid,
                      "resourceVersion": str(w["rv"]), "annotations": {ANN_KEY[w["obj"]]: w["value"]}}}
                audit.append(_ev(ctx, base, aid, "patch", "pods", pod_name, w["commit"] - 0.3, w["commit"] + P["rc_after"], 200, ro, user="e39-writer"))
            else:
                ro = {"kind": "ConfigMap", "apiVersion": "v1", "metadata": {"name": w["obj"], "resourceVersion": str(w["rv"])},
                      "data": ({"allowed": w["value"]} if w["obj"] in NEW_VALUE else {"value": w["value"]})}
                audit.append(_ev(ctx, base, aid, "patch", "configmaps", w["obj"], w["commit"] - 0.3, w["commit"] + P["rc_after"], 200, ro, user="e39-writer"))
        else:
            audit.append(_ev(ctx, base, aid, "patch", "pods", pod_name, w["commit"] - 0.3, w["commit"] + P["rc_after"], 404,
                             {"kind": "Status", "code": 404, "message": "pods \"%s\" not found" % pod_name}, user="e39-writer"))
        rec_writes.append(rec)
        if w.get("mirror_rv") is not None:
            maid = ctx.uid("m/%d/%d" % (ctx.n, i))
            ro = {"kind": "Pod", "apiVersion": "v1", "metadata": {"name": pod_name, "uid": pod_uid, "resourceVersion": str(w["mirror_rv"]),
                                                                   "annotations": {ANN_KEY[w["obj"]]: w["value"]}}}
            audit.append(_ev(ctx, base, maid, "patch", "pods", pod_name, w["mirror_commit"] - 0.3, w["mirror_commit"] + P["rc_after"], 200, ro,
                             user="e39-mirror"))
            w["mirror_rec"] = {"source_object": w["obj"], "source_commit_rv": str(w["rv"]), "source_commit_wall_ns": A(w["commit"] + P["rc_after"]),
                               "mirror_commit_rv": str(w["mirror_rv"]), "mirror_commit_wall_ns": A(w["mirror_commit"] + P["rc_after"]),
                               "mirror_audit_id": maid}
    g_reads = [{"object": r["object"], "resource_version": str(cm_rv_at(r["object"], r["srv"])),
                "value": val_cm(r["object"], r["srv"], [w for w in writes if w["committed"]]),
                "t_send_mono_ns": M(r["send"]), "t_recv_mono_ns": M(r["recv"])} for r in reads]
    gate = {"decision": decision, "t_start_mono_ns": M(G0), "t_end_mono_ns": M(T), "t_end_wall_ns": A(T),
            "reads": g_reads, "ssar_allowed": True}
    bound = None
    if ann_mode:
        prv = pod_rv_at(pod_read["srv"])
        bound = {"pod_rv_at_gate": str(prv), "annotations_at_gate": {ANN_KEY[o]: val_ann(o, pod_read["srv"], writes) for o in POLICY_OBJECTS}}
        gate["reads"] = [{"object": pod_name, "resource_version": str(prv), "value": pod_uid, "t_send_mono_ns": M(pod_read["send"]),
                          "t_recv_mono_ns": M(pod_read["recv"])}]
    daid = ctx.uid("d/%d" % ctx.n)
    act_rec = {"dispatched": dispatched, "t_send_mono_ns": M(d_send) if dispatched else None,
               "t_send_wall_ns": A(d_send) if dispatched else None,
               "t_resp_mono_ns": M(C + P["resp_after"]) if dispatched else None, "http_status": None,
               "reason": None, "audit_id": daid if dispatched else None, "expected_uid": pod_uid,
               "deleted_object_resource_version": None}
    msg = None
    if dispatched:
        if outcome == "accept":
            code = 200 if action_type == "pod_delete" else 201
            if action_type == "pod_delete":
                ro = {"kind": "Pod", "apiVersion": "v1", "metadata": {"name": pod_name, "uid": pod_uid, "resourceVersion": str(action_rv)}}
                act_rec["deleted_object_resource_version"] = str(action_rv)
            else:
                ro = {"kind": "NetworkPolicy", "apiVersion": "networking.k8s.io/v1",
                      "metadata": {"name": "isolate-%d" % ctx.n, "uid": ctx.uid("np/%d" % ctx.n), "resourceVersion": str(action_rv)}}
        elif outcome == "409_uid":
            code = 409
            msg = "Operation cannot be fulfilled on pods \"%s\": Precondition failed: UID in precondition: %s, UID in object meta: %s" % (
                pod_name, pod_uid, ctx.uid("rebind/%d" % ctx.n))
            ro = {"kind": "Status", "code": 409, "message": msg}
        elif outcome == "409_rv":
            code = 409
            msg = "Operation cannot be fulfilled on pods \"%s\": Precondition failed: ResourceVersion in precondition: %s, ResourceVersion in object meta: %s" % (
                pod_name, bound["pod_rv_at_gate"], pod_rv_at(C))
            ro = {"kind": "Status", "code": 409, "message": msg}
        else:
            code = 403
            ro = {"kind": "Status", "code": 403, "message": "admission webhook denied the request"}
        act_rec["http_status"] = code
        res_name = "pods" if action_type == "pod_delete" else "networkpolicies"
        verb = "delete" if action_type == "pod_delete" else "create"
        nm = pod_name if action_type == "pod_delete" else "isolate-%d" % ctx.n
        audit.append(_ev(ctx, base, daid, verb, res_name, nm, R, C + P["rc_after"], code, ro, message=msg))
        if wh is not None:
            whlog.append({"schema": "e39.webhook.v1", "request_uid": ctx.uid("ar/%d" % ctx.n),
                          "pod_uid": pod_uid if action_type == "pod_delete" else None,
                          "object_name": None if action_type == "pod_delete" else nm,
                          "received_wall_ns": A(wh["recv"]),
                          "reads": [{"object": r["object"], "resource_version": str(cm_rv_at(r["object"], r["srv"])),
                                     "value": val_cm(r["object"], r["srv"], writes), "t_send_wall_ns": A(r["send"])} for r in wh["reads"]],
                          "decision": wh["decision"], "completed_wall_ns": A(wh["done"])})
    raw = {"schema": "e39.trial.v1", "run_id": ctx.run_id, "trial_id": e["trial_id"], "stage": e["stage"], "block": e["block"],
           "experiment": x, "arm": e["arm"], "client_impl": impl, "action_type": action_type, "condition": e["condition"],
           "delta_ms": delta, "lambda_per_s": lam, "injected_latency_ms": e.get("injected_latency_ms"),
           "acquisition_mode": acq, "planning_mode": plm, "planning_ms": planning_ms,
           "guard_order": e.get("guard_order"), "pair_id": e.get("pair_id"),
           "load_level": eff_load if x == "D" else "idle", "requested_level": load if x == "D" else None, "load_observed_rps": {"idle": 0, "moderate": 50, "high": 150}[load] if x == "D" else 0,
           "host": {"cpu_pct": cpu, "free_ram_mb": 9000.0 - (3000 if load == "high" else 0), "load_throttled": throttled},
           "rep": e["rep"], "schedule_seed": e["schedule_seed"],
           "fixture": {"reset_ok": True, "initial_values": dict(iv, **{o: "false" for o in G_CUT_OBJECTS.get(cond, ())})},
           "gate": gate, "writer": None, "writer_arrivals": None, "bound_state": bound,
           "delete_preconditions": {"uid": pod_uid, "resource_version": bound["pod_rv_at_gate"] if bound else None},
           "mirror": None, "netpol": None, "go_client_version": "v0.37.0" if impl == "client-go" else None,
           "delete": act_rec if action_type == "pod_delete" else {"dispatched": False, "expected_uid": pod_uid},
           "effect": {"verified": outcome == "accept"}, "webhook_request_uid": None, "status": "COMPLETE", "error": None}
    if action_type == "netpol_create":
        raw["netpol"] = None if not dispatched else {"name": "isolate-%d" % ctx.n, "t_send_mono_ns": act_rec["t_send_mono_ns"],
                                                      "t_resp_mono_ns": act_rec["t_resp_mono_ns"], "http_status": act_rec["http_status"],
                                                      "audit_id": daid,
                                                      "created_resource_version": str(action_rv) if outcome == "accept" else None}
    single = [w for w in rec_writes if True]
    if x == "B" or cond in G_CONDITIONS:
        raw["writer_arrivals"] = [{k: v for k, v in w.items() if k in ("planned_offset_ms", "actual_release_mono_ns", "t_resp_mono_ns",
                                                                       "http_status", "resulting_resource_version", "audit_id", "object")}
                                  for w in single]
    elif single:
        raw["writer"] = {k: v for k, v in single[0].items() if k != "planned_offset_ms"}
    if arm == "A4m" and writes and writes[0].get("mirror_rec"):
        raw["mirror"] = writes[0]["mirror_rec"]
    # ----- truth (time domain)
    committed = [w for w in writes if w["committed"]]

    def st_with(ws):
        s = {o: parse_bool(iv[o]) for o in POLICY_OBJECTS}
        for w in sorted(ws, key=lambda w: w["commit"]):
            s[w["obj"]] = parse_bool(w["value"])
        return s

    if decision == "UNKNOWN_COHERENCE":
        truth = "UNKNOWN"
    elif uid_rebind:
        truth = "UID_BLOCKED"
    elif not dispatched:
        # gate: seen = committed before the gate's read of that object (unread objects: before the last read)
        if ann_mode:
            seen = [w for w in committed if (w["commit"] if arm == "A4d" else w.get("mirror_commit", math.inf)) < pod_read["srv"]]
        else:
            rt = {r["object"]: r["srv"] for r in reads}
            last = max(rt.values())
            seen = [w for w in committed if w["commit"] < rt.get(w["obj"], last)]
        truth = "CORRECT_ABORT" if not v_core(st_with(seen))[0] else "FALSE_ABORT"
    elif outcome == "403":
        rt = {r["object"]: r["srv"] for r in wh["reads"]}
        seen = [w for w in committed if w["commit"] < rt[w["obj"]]]
        truth = "CORRECT_ABORT" if not v_core(st_with(seen))[0] else "FALSE_ABORT"
    elif outcome == "409_rv":
        seen = [w for w in committed if (w["commit"] if arm == "A4d" else w.get("mirror_commit", math.inf)) < C]
        truth = "CORRECT_ABORT" if not v_core(st_with(seen))[0] else "FALSE_ABORT"
    else:
        before = [w for w in committed if w["commit"] < C]
        truth = "INVALID_EXECUTED" if not v_core(st_with(before))[0] else "VALID_EXECUTED"
    tr = {"trial_id": e["trial_id"], "truth_primary": truth, "outcome": outcome, "decision": decision,
          "n_writes": len(writes), "mirror_lag_ms": (writes[0].get("mirror_commit", 0) - writes[0]["commit"]) if (arm == "A4m" and writes) else None}
    return raw, audit, whlog, tr


# ----------------------------------------------------------------------------- schedules
def design_entries(x, mini=False):
    """Pre-registered design (or a mini version with n=3 per cell) as schedule entries (without ids)."""
    out = []
    n = lambda full: 3 if mini else full
    if x == "A":
        for arm in ARMS_A:
            out += [dict(arm_c=arm, client_impl="python", condition_c="no_write", delta_ms=None)] * n(N_A_NO_WRITE)
            out += [dict(arm_c=arm, client_impl="python", condition_c="uid_rebind_control", delta_ms=None)] * n(N_A_UID)
            for cond in ASYNC_CONDITIONS:
                for d in (DELTAS_A if not mini else (0.0, 2.0, 50.0)):
                    out += [dict(arm_c=arm, client_impl="python", condition_c=cond, delta_ms=d)] * n(N_A_ASYNC)
    elif x == "B":
        for arm in ARMS_B:
            for lam in LAMBDAS_B:
                out += [dict(arm_c=arm, client_impl="python", condition_c="async_scope", lambda_per_s=lam, raw_cond="poisson_scope")] * (20 if mini else N_B)
    elif x == "C":
        pid = 0
        for lat in LATENCIES_C:
            for acq in ACQ_MODES:
                for cond in C_CONDITIONS:
                    for pl in PLANNING_MODES:
                        for _ in range(n(N_C_PAIRS)):
                            pid += 1
                            order = random.Random(pid).choice(("dcji_first", "fullrefresh_first"))
                            for arm in ("A1", "A2"):
                                out.append(dict(arm_c=arm, client_impl="python", condition_c=cond, injected_latency_ms=lat,
                                                acquisition_mode=acq, planning_mode=pl, pair_id=pid, guard_order=order))
    elif x == "D":
        for arm in ARMS_D:
            for lv in LOAD_LEVELS:
                for d in DELTAS_SHORT:
                    out += [dict(arm_c=arm, client_impl="python", condition_c="async_scope", delta_ms=d, load_level=lv)] * n(N_D)
    elif x == "E":
        for arm in ARMS_E:
            for cond in ("async_scope", "async_evidence_last_literal"):
                for d in DELTAS_SHORT:
                    out += [dict(arm_c=arm, client_impl="python", condition_c=cond, delta_ms=d)] * n(N_E)
    elif x == "F":
        for arm, impl in ARMS_F:
            for d in DELTAS_SHORT:
                out += [dict(arm_c=arm, client_impl=impl, condition_c="async_scope", delta_ms=d)] * n(N_F)
    elif x == "G":
        for arm in ARMS_G:
            for cond in G_CONDITIONS:
                out += [dict(arm_c=arm, client_impl="python", condition_c=cond)] * n(N_G)
    return [dict(o) for o in out]


def build_schedule(experiments, seed, mini=False, run_id="e39-synth"):
    rng = random.Random(seed)
    trials, tid, block = [], 0, 0
    for x in experiments:
        ents = design_entries(x, mini)
        rng.shuffle(ents)
        for i, e in enumerate(ents):
            tid += 1
            if i % 50 == 0:
                block += 1
            raw_cond = e.pop("raw_cond", None) or RAW_COND[e["condition_c"]]
            ent = {"trial_id": tid, "stage": 1, "block": block, "experiment": x,
                   "arm": RAW_ARM[(e["arm_c"], e["client_impl"])], "condition": raw_cond,
                   "delta_ms": e.get("delta_ms"), "rep": i, "schedule_seed": seed,
                   "client_impl": e["client_impl"], "action_type": "netpol_create" if x == "E" else "pod_delete"}
            for k in ("lambda_per_s", "injected_latency_ms", "acquisition_mode", "planning_mode", "load_level", "pair_id", "guard_order"):
                if k in e:
                    ent[k] = e[k]
            trials.append(ent)
    return {"schema": "e39.schedule.v1", "run_id": run_id, "schedule_seed": seed, "stage": 1, "trials": trials}


def _canon(ent):
    from e39_common import canon_arm, canon_condition
    arm_c, impl = canon_arm(ent["arm"], ent.get("client_impl"))
    return dict(ent, arm_c=arm_c, client_impl=impl, condition_c=canon_condition(ent["condition"]))


def simulate(schedule, seed, P=None, ctx=None, overrides=None):
    ctx = ctx or Ctx(run_id=schedule["run_id"], seed=seed)
    rng = random.Random(seed + 1)
    raws, audit, whl, truth = [], [], [], []
    for ent in schedule["trials"]:
        e = _canon(ent)
        p = dict(P or {})
        if overrides and e["trial_id"] in overrides:
            p.update(overrides[e["trial_id"]])
        r, a, w, t = simulate_trial(ctx, e, rng, p)
        raws.append(r)
        audit += a
        whl += w
        truth.append(t)
    return raws, audit, whl, truth


def write_run(outd, schedule, raws, audit, whl, truth):
    outd = Path(outd)
    outd.mkdir(parents=True, exist_ok=True)
    (outd / "SCHEDULE.json").write_text(json.dumps(schedule, indent=1) + "\n", encoding="utf-8")
    for name, rows in (("RAW_TRIALS.jsonl", raws), ("audit.log", audit), ("webhook_log.jsonl", whl), ("TRUTH.jsonl", truth)):
        with open(outd / name, "w", encoding="utf-8", newline="\n") as fh:
            for r in rows:
                fh.write(json.dumps(r, sort_keys=True) + "\n")


def write_lock(root, schedule_path, plan_path, seed, run_id, status="LOCKED-2026-09-26T00:00:00Z"):
    """Minimal LOCK in the format analyze_e39.verify_lock reads (schedule + plan hashes)."""
    from e39_common import sha256_file
    lock = {"schema": "e39.lock.v1", "status": status, "schedule_seed": seed, "run_id": run_id,
            "design": {"schedule_seed": seed, "forbidden_schedule_seeds": [20260912, 1772720255]},
            "schedule_path": str(schedule_path), "schedule_sha256": sha256_file(schedule_path),
            "analysis_plan_path": str(plan_path), "analysis_plan_sha256": sha256_file(plan_path)}
    p = Path(root) / "protocol" / "LOCK_E39.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    return p


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True)
    ap.add_argument("--design", choices=("full", "mini"), default="mini")
    ap.add_argument("--seed", type=int, default=3926099)
    ap.add_argument("--experiments", default="A,B,C,D,E,F,G")
    a = ap.parse_args(argv)
    exps = [x.strip() for x in a.experiments.split(",") if x.strip()]
    sch = build_schedule(exps, a.seed, mini=a.design == "mini")
    raws, audit, whl, truth = simulate(sch, a.seed)
    write_run(a.out, sch, raws, audit, whl, truth)
    print("synthetic E39 run: %d trials, %d audit events, %d webhook entries -> %s" % (len(raws), len(audit), len(whl), a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
