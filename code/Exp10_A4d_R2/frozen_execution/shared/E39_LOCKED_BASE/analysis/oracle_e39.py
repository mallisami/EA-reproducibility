#!/usr/bin/env python3
"""E39 ground-truth oracle (offline; reads files only, never talks to a cluster).

Extends the E38v2 confirmatory-rerun oracle (base_e38v2r/oracle.py, e38v2.oracle.v1.2) to:
  * A4d  commit-bound DIRECT: policy values are Pod annotations; writers PATCH the Pod; the DELETE carries
         preconditions {uid, resourceVersion = Pod RV read at the gate} -> HTTP 409 on any later Pod write.
  * A4m  commit-bound MIRRORED: writers PATCH ConfigMaps (source of truth); a mirror controller copies them
         to Pod annotations (mirror PATCH); gate/DELETE as A4d. Mirror lag (RV and time) is measured.
  * DELETE with resourceVersion precondition: a 409 whose message names the resourceVersion precondition
         is a DENIAL by the precondition ("rv_precondition" decider); the Pod revision at the check is the
         'ResourceVersion in object meta' of the apiserver message.
  * NetworkPolicy CREATE action (E39-E): action commit RV = created object's RV (audit / client).
  * Poisson multi-arrival writers (E39-B) and pre-gate out-of-model cuts (E39-G): several writes per trial.
    The true state at the action commit is the fixture state with EVERY write committed before the action
    applied in commit (RV) order.

Outcome classes (unchanged): VALID_EXECUTED, INVALID_EXECUTED, CORRECT_ABORT, FALSE_ABORT, UID_BLOCKED,
UNKNOWN, INFRA_ERROR; E39-C latency trials are classified by the same rules (descriptive only).

PRIMARY (commit order, resourceVersion):
  accepted action  : state(action) = S0 + {writes w : rv(w) < rv(action commit)}; any rv(w) == rv(action)
                     -> UNKNOWN rv_tie. INVALID_EXECUTED iff V_core(state(action)) is false.
                     (A4m: rv(w) is the SOURCE ConfigMap commit - the mirror is not the truth.)
  denial           : decider = gate (ABORT_REPLAN), webhook (4xx + deny log entry) or rv_precondition (409).
                     A write is SEEN iff its representation RV <= the RV of the decider's read of that object
                     (representation: ConfigMap write RV; A4d Pod-annotation write RV; A4m mirror PATCH RV when
                     the decider reads the Pod). rv_precondition: the read is the Pod at revision Y (current RV
                     of the apiserver message). Objects the decider did not read: seen iff representation RV <=
                     the decider's largest read RV (flag decider_unread_object).
                     state(seen) = S0 + seen writes; CORRECT_ABORT iff V_core(state(seen)) false, else FALSE_ABORT.
SECONDARY (time order, plan v1.2 semantics):
  accepted action  : writes whose ResponseComplete stageTimestamp < action ResponseComplete stageTimestamp.
  denial           : representation ResponseComplete < decider read time (gate: read send mapped mono->wall;
                     webhook: read t_send_wall_ns; rv_precondition: DELETE ResponseComplete stageTimestamp).
TERTIARY (plan v1/v1.1, transparency only): accepted action vs action requestReceivedTimestamp.
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter

from e39_common import (ASYNC_CONDITIONS, C_CONDITIONS, G_CONDITIONS, G_CUT_OBJECTS, NO_WRITE,
                        POLICY_OBJECTS, RV_PRECONDITION_ARMS, TRIAL_SCHEMA, UID_REBIND, WEBHOOK_SCHEMAS,
                        WRITER_OBJECT, canon_acq, canon_action, canon_arm, canon_condition, canon_load,
                        canon_planning, load_jsonl, norm_float, norm_object, parse_bool,
                        parse_k8s_time_ns, parse_precondition_message, rv_int, v_core)

ORACLE_VERSION = "e39.oracle.v1"
NOT_ANALYZABLE = ("UNKNOWN", "INFRA_ERROR")
OUTCOMES = ("VALID_EXECUTED", "INVALID_EXECUTED", "CORRECT_ABORT", "FALSE_ABORT",
            "UID_BLOCKED", "UNKNOWN", "INFRA_ERROR")
FAIL_CLOSED_REASON = "gate_unknown_coherence"

ID_COLS = ["run_id", "trial_id", "experiment", "stage", "block", "arm", "condition", "delta_ms", "rep",
           "schedule_seed", "status", "arm_c", "client_impl", "condition_c", "lambda_per_s",
           "injected_latency_ms", "acquisition_mode", "planning_mode", "load_level", "load_level_effective",
           "load_throttled", "action_type", "pair_id", "guard_order", "go_client_version", "load_observed_rps"]
CLASS_COLS = ["primary_class", "primary_reason", "secondary_class", "secondary_reason", "rules_agree",
              "tertiary_class", "tertiary_reason", "rules_agree_tertiary"]
EVIDENCE_COLS = ["v_pre", "v_post", "gate_decision", "action_dispatched", "action_http_status", "decider",
                 "n_writes", "n_writes_committed", "n_writes_before_action", "first_write_rv", "writer_object",
                 "writer_rv_source", "action_rv", "action_rv_source", "rv_margin", "precondition_rv",
                 "conflict_kind", "conflict_current_rv", "decider_read_rv", "webhook_decision",
                 "writer_complete_ts_ns", "ref_ts_ns", "ref_ts_kind", "time_margin_us", "ref_ts_tertiary_ns",
                 "mirror_source_rv", "mirror_rv", "mirror_lag_rv", "mirror_lag_ms", "mirror_before_action",
                 "source_before_action", "expected_uid", "deleted_uid", "effect_verified",
                 "writer_audit_present", "action_audit_present",
                 "delete_rtt_ms", "gate_ms", "refresh_ms", "planning_ms", "e2e_ms", "e2e_kind",
                 "gate_end_to_send_ms", "release_error_us", "realized_delta_ms",
                 "host_cpu_pct", "free_ram_mb", "pod_watch_deleted_rv", "c_justified", "load_level_recorded", "conflict_attribution"]
CSV_COLS = ID_COLS + CLASS_COLS + EVIDENCE_COLS + ["flags"]


# ----------------------------------------------------------------------------- joins
def index_audit(events):
    """{auditID: [events]} plus idx["__pod_rv__"] = {resourceVersion: (user, verb, subresource)} of every
    successful Pod write (used to attribute a 409 to the Pod change that caused it: writer, mirror, kubelet
    status update, ...)."""
    idx = {"__pod_rv__": {}}
    for ev in events:
        aid = ev.get("auditID")
        if aid:
            idx.setdefault(str(aid), []).append(ev)
        ref = ev.get("objectRef") or {}
        ro = ev.get("responseObject") if isinstance(ev.get("responseObject"), dict) else {}
        if ev.get("stage") == "ResponseComplete" and ref.get("resource") == "pods" and ro.get("kind") == "Pod" \
                and ev.get("verb") in ("update", "patch"):
            rv = (ro.get("metadata") or {}).get("resourceVersion")
            if rv is not None:
                idx["__pod_rv__"][str(rv)] = ((ev.get("user") or {}).get("username") or "", ev.get("verb"),
                                              ref.get("subresource") or "")
    return idx


def attribute_pod_change(audit_idx, rv):
    """Who committed Pod revision rv (the 'ResourceVersion in object meta' of a 409)."""
    hit = (audit_idx.get("__pod_rv__") or {}).get(str(rv)) if rv is not None else None
    if not hit:
        return "unattributed(no_audit_event)"
    user, verb, sub = hit
    u = user.lower()
    if u.startswith("system:node:") or sub == "status":
        return "kubelet_status_update"
    if "mirror" in u:
        return "mirror_controller"
    if "writer" in u:
        return "writer"
    if "reset" in u:
        return "reset_identity"
    return "other(%s)" % user


class WebhookIndex(dict):
    """{request_uid: [entries]} + .by_pod {pod_uid: [...]} + .by_name {object name: [...]}."""

    def __init__(self):
        super().__init__()
        self.by_pod = {}
        self.by_name = {}


def index_webhook(entries):
    idx = WebhookIndex()
    for e in entries:
        uid = e.get("request_uid")
        if uid:
            idx.setdefault(str(uid), []).append(e)
        puid = e.get("pod_uid") or e.get("target_pod_uid")
        if puid:
            idx.by_pod.setdefault(str(puid), []).append(e)
        nm = e.get("object_name") or e.get("netpol_name")
        if nm:
            idx.by_name.setdefault(str(nm), []).append(e)
    return idx


def index_pod_events(events):
    """plan v1.2 (i): {pod uid: {"deleted": [rv of DELETED events]}}; None when no file was supplied."""
    if events is None:
        return None
    idx = {}
    for e in events:
        uid = e.get("uid")
        if not uid:
            continue
        d = idx.setdefault(str(uid), {"deleted": []})
        if e.get("type") == "DELETED":
            d["deleted"].append(e.get("rv"))
    return idx


def audit_summary(evs):
    """Summarise all audit events sharing one auditID; conflicts are listed, never guessed."""
    s = {"present": bool(evs), "conflicts": [], "received_ns": None, "complete_ns": None,
         "code": None, "rv": None, "uid": None, "data": None, "annotations": None, "verb": None,
         "name": None, "resource": None, "namespace": None, "message": None, "has_response_complete": False}
    if not evs:
        return s
    rrts = {e.get("requestReceivedTimestamp") for e in evs if e.get("requestReceivedTimestamp")}
    if len(rrts) > 1:
        s["conflicts"].append("requestReceivedTimestamp_differs")
    elif rrts:
        s["received_ns"] = parse_k8s_time_ns(next(iter(rrts)))
        if s["received_ns"] is None:
            s["conflicts"].append("requestReceivedTimestamp_unparseable")
    verbs = {e.get("verb") for e in evs if e.get("verb")}
    if len(verbs) > 1:
        s["conflicts"].append("verb_differs")
    elif verbs:
        s["verb"] = next(iter(verbs))
    refs = {(((e.get("objectRef") or {}).get("resource")), ((e.get("objectRef") or {}).get("namespace")),
             ((e.get("objectRef") or {}).get("name"))) for e in evs if e.get("objectRef")}
    if len(refs) > 1:
        s["conflicts"].append("objectRef_differs")
    elif refs:
        s["resource"], s["namespace"], s["name"] = next(iter(refs))
    rc = [e for e in evs if e.get("stage") == "ResponseComplete"]
    if not rc:
        return s
    s["has_response_complete"] = True
    keys = set()
    for e in rc:
        ro = e.get("responseObject") if isinstance(e.get("responseObject"), dict) else None
        md = (ro or {}).get("metadata") or {}
        is_status = (ro or {}).get("kind") == "Status"
        keys.add(((e.get("responseStatus") or {}).get("code"),
                  None if is_status else md.get("resourceVersion"),
                  None if is_status else md.get("uid"), e.get("stageTimestamp")))
    if len(keys) > 1:
        s["conflicts"].append("multiple_ResponseComplete_differ")
        return s
    e = rc[0]
    rs = e.get("responseStatus") or {}
    s["code"] = rs.get("code")
    s["message"] = rs.get("message")
    s["complete_ns"] = parse_k8s_time_ns(e.get("stageTimestamp"))
    if e.get("stageTimestamp") and s["complete_ns"] is None:
        s["conflicts"].append("stageTimestamp_unparseable")
    ro = e.get("responseObject") if isinstance(e.get("responseObject"), dict) else None
    if ro and ro.get("kind") == "Status" and not s["message"]:
        s["message"] = ro.get("message")
    if ro and ro.get("kind") != "Status":
        md = ro.get("metadata") or {}
        s["rv"] = md.get("resourceVersion")
        s["uid"] = md.get("uid")
        s["data"] = ro.get("data")
        s["annotations"] = md.get("annotations")
        s["deletion_timestamp"] = md.get("deletionTimestamp")
        s["deletion_grace_period_seconds"] = md.get("deletionGracePeriodSeconds")
    return s


def resolve_rv(audit_raw, trial_raw):
    """Combine audit-derived and trial-recorded resourceVersions -> (int|None, source, error|None)."""
    a_i, t_i = rv_int(audit_raw), rv_int(trial_raw)
    if audit_raw is not None and a_i is None:
        return None, "audit", "rv_not_numeric_audit(%s)" % audit_raw
    if trial_raw is not None and t_i is None:
        return None, "trial", "rv_not_numeric_trial(%s)" % trial_raw
    if a_i is not None and t_i is not None:
        if a_i != t_i:
            return None, "audit+trial", "rv_conflict_audit_%d_trial_%d" % (a_i, t_i)
        return a_i, "audit+trial", None
    if a_i is not None:
        return a_i, "audit", None
    if t_i is not None:
        return t_i, "trial", None
    return None, "", "rv_missing"


def _cm_value(data):
    if not isinstance(data, dict):
        return None
    for k in ("value", "allowed"):
        if k in data:
            return data[k]
    return None


def _annotation_value(annotations, obj):
    if not isinstance(annotations, dict):
        return None
    for k, v in annotations.items():
        if norm_object(k) == obj:
            return v
    return None


def _initial_value(v):
    if isinstance(v, dict):
        return _cm_value(v)
    return v


def _i(x):
    return x if isinstance(x, int) and not isinstance(x, bool) else None


def _first(d, *keys):
    if not isinstance(d, dict):
        return None
    for k in keys:
        if d.get(k) is not None:
            return d.get(k)
    return None


# ----------------------------------------------------------------------------- normalisation (schema adapter)
def _norm_write(w, role):
    """One write record (writer / arrival / cut) -> canonical dict. Field aliases cover the E38v2 writer
    record and the E39 extensions (writer_arrivals, cut writes)."""
    mir = w.get("mirror") if isinstance(w.get("mirror"), dict) else None
    raw_obj = _first(w, "object", "annotation", "key", "configmap")
    objs = None
    if isinstance(raw_obj, str) and raw_obj.startswith("pod-annotations:"):
        # runner conditions_e39.apply_condition (annotation storage): ONE Pod PATCH setting several keys
        objs = [norm_object(k) for k in raw_obj.split(":", 1)[1].split(",") if k.strip()]
    return {"role": role,
            "objects": objs,
            "object": objs[0] if objs else norm_object(raw_obj),
            "object_raw": raw_obj,
            "new_value": _first(w, "new_value", "value"),
            "http_status": _first(w, "http_status", "status_code"),
            "audit_id": _first(w, "audit_id"),
            "rv_trial": _first(w, "resulting_resource_version", "resource_version", "rv"),
            "target": (_first(w, "target_resource", "resource", "target") or ""),
            "intended": _i(_first(w, "intended_release_mono_ns")),
            "actual": _i(_first(w, "actual_release_mono_ns")),
            "t_resp": _i(_first(w, "t_resp_mono_ns")),
            "mirror": mir}


def normalize_trial(t):
    """Raw e39.trial.v1 record -> canonical dict used by classify_trial. Never raises on missing fields."""
    exp = t.get("experiment")
    if isinstance(exp, str):
        exp = exp.strip().upper().replace("E39-", "").replace("E39_", "").replace("E39", "") or None
    arm_c, impl = canon_arm(t.get("arm"), t.get("client_impl"))
    load = t.get("load") if isinstance(t.get("load"), dict) else {}
    guard = t.get("load_guard") if isinstance(t.get("load_guard"), dict) else {}
    host = t.get("host") if isinstance(t.get("host"), dict) else {}
    n = {"experiment": exp, "arm_c": arm_c, "client_impl": impl,
         "condition_c": canon_condition(t.get("condition")),
         "delta_ms": norm_float(t.get("delta_ms")),
         "lambda_per_s": norm_float(t.get("lambda_per_s")),
         "injected_latency_ms": norm_float(t.get("injected_latency_ms")),
         "acquisition_mode": canon_acq(t.get("acquisition_mode")),
         "planning_mode": canon_planning(t.get("planning_mode")),
         "load_level": canon_load(t.get("load_level")),
         "load_level_effective": canon_load(_first(t, "load_level_effective") or load.get("effective_level")
                                            or guard.get("effective_level") or t.get("load_level")),
         "load_throttled": bool(_first(t, "load_throttled") or load.get("throttled") or guard.get("throttled")
                                or guard.get("dropped_to_moderate") or host.get("load_throttled")),
         "load_observed_rps": _first(t, "load_observed_rps"),
         "action_type": canon_action(t.get("action_type")),
         "pair_id": t.get("pair_id"), "guard_order": t.get("guard_order"),
         "host_cpu_pct": _first(t, "host_cpu_pct") if _first(t, "host_cpu_pct") is not None else host.get("cpu_pct"),
         "free_ram_mb": _first(t, "free_ram_mb") if _first(t, "free_ram_mb") is not None else host.get("free_ram_mb")}
    # E39-D load guard. The runner's LoadProcess.snapshot() reports load_level = the EFFECTIVE (possibly
    # throttled) level and requested_level = the nominal one. Nominal (the randomised assignment, ITT) comes
    # from an explicit nominal field if present, else from the record's load_level; analyze_e39 finally
    # overrides it with the SCHEDULE's load_level (authoritative). Effective: explicit field, else the
    # record's load_level when a nominal field exists, else high -> moderate when throttled (spec rule).
    n["load_level_recorded"] = canon_load(t.get("load_level"))
    nom = canon_load(_first(t, "load_level_nominal", "requested_level", "load_level_requested")
                     or load.get("requested_level"))
    explicit_eff = _first(t, "load_level_effective") or load.get("effective_level") or guard.get("effective_level")
    if nom is not None:
        n["load_level"] = nom
        if not explicit_eff:
            n["load_level_effective"] = n["load_level_recorded"]
    elif n["load_throttled"] and n["load_level"] == "high" and not explicit_eff:
        n["load_level_effective"] = "moderate"
    # action (pod DELETE or NetworkPolicy CREATE); E39_RECORD_SCHEMA: E uses trial.netpol
    act = None
    keys = ("netpol", "action", "create", "delete") if n["action_type"] == "netpol_create" else ("action", "delete")
    for k in keys:
        if isinstance(t.get(k), dict):
            act = t[k]
            break
    act = act or {}
    pre = act.get("preconditions") if isinstance(act.get("preconditions"), dict) else {}
    tpre = t.get("delete_preconditions") if isinstance(t.get("delete_preconditions"), dict) else {}
    if not pre and tpre:
        pre = tpre
    disp = act.get("dispatched")
    if disp is None and n["action_type"] == "netpol_create" and act:
        disp = act.get("t_send_mono_ns") is not None or act.get("http_status") is not None
    n["action"] = {
        "dispatched": disp,
        "http_status": act.get("http_status"),
        "audit_id": act.get("audit_id"),
        "expected_uid": act.get("expected_uid") or pre.get("uid"),
        "committed_rv": _first(act, "deleted_object_resource_version", "created_resource_version",
                               "committed_resource_version", "resulting_resource_version"),
        "precondition_rv": _first(act, "precondition_resource_version") or _first(pre, "resourceVersion",
                                                                                  "resource_version"),
        "t_send": _i(act.get("t_send_mono_ns")), "t_resp": _i(act.get("t_resp_mono_ns")),
        "message": _first(act, "response_message", "message", "reason_message", "reason"),
        "object_name": _first(act, "object_name", "name", "netpol_name"),
    }
    # writes
    writes = []
    if isinstance(t.get("writer"), dict):
        writes.append(_norm_write(t["writer"], "writer"))
    cond_c = n["condition_c"]
    for k in ("writer_arrivals", "arrivals"):
        if isinstance(t.get(k), list):
            role = "cut" if cond_c in G_CONDITIONS else "arrival"
            for w in t[k]:
                if not isinstance(w, dict):
                    continue
                nw = _norm_write(w, role)
                # E39_RECORD_SCHEMA arrivals carry no new_value: the pre-registered invalidating value of the
                # condition is used (and cross-checked against the audit responseObject when present).
                if nw["new_value"] is None:
                    if role == "arrival" and cond_c in WRITER_OBJECT:
                        nw["new_value"] = "not allowed" if WRITER_OBJECT[cond_c] == "response-scope" else "false"
                        nw["value_from_design"] = True
                    elif role == "cut":
                        nw["new_value"] = "false"
                        nw["value_from_design"] = True
                if nw["object"] not in POLICY_OBJECTS and role == "arrival" and cond_c in WRITER_OBJECT:
                    # A4d arrivals PATCH the Pod: the record's object may be the Pod name (annotation key
                    # implied by the condition); the audit responseObject annotation cross-checks the value
                    nw["object_inferred_from"] = nw["object_raw"]
                    nw["object"] = WRITER_OBJECT[cond_c]
                writes.append(nw)
    cut = t.get("cut")
    for src in ([cut.get("writes")] if isinstance(cut, dict) else []) + \
            [t.get(k) for k in ("cut_writes", "pre_gate_writes", "condition_writes", "setup_writes", "g_cut_writes")]:
        if isinstance(src, list):
            writes += [_norm_write(w, "cut") for w in src if isinstance(w, dict)]
    # mirror records (A4m): trial.mirror dict or list; attach by source RV / audit id / single
    mir = t.get("mirror")
    mirs = [mir] if isinstance(mir, dict) else ([m for m in mir if isinstance(m, dict)] if isinstance(mir, list) else [])
    n["mirror_records"] = mirs
    for m in mirs:
        tgt = None
        for w in writes:
            if w["mirror"] is not None:
                continue
            if m.get("source_audit_id") and m.get("source_audit_id") == w["audit_id"]:
                tgt = w
                break
            msrc = _first(m, "source_commit_rv", "source_rv")
            if msrc is not None and rv_int(msrc) is not None and rv_int(msrc) == rv_int(w["rv_trial"]):
                tgt = w
                break
        if tgt is None and len(writes) == 1 and writes[0]["mirror"] is None:
            tgt = writes[0]
        if tgt is not None:
            tgt["mirror"] = m
    n["writes"] = writes
    gate = dict(t.get("gate")) if isinstance(t.get("gate"), dict) else {}
    # A4d/A4m: the gate's single Pod read is recorded as bound_state {pod_rv_at_gate, annotations_at_gate}
    bs = t.get("bound_state")
    if isinstance(bs, dict) and bs.get("annotations_at_gate"):
        reads = list(gate.get("reads") or [])
        if not any(isinstance(r, dict) and isinstance(r.get("annotations"), dict) for r in reads):
            # the runner's commit gate records its Pod GET as reads[0] = {object: <pod name>,
            # resource_version: <pod rv>, value: <uid>, t_send_mono_ns, ...}: match by RV
            pod_reads = [r for r in reads if isinstance(r, dict) and r.get("resource_version") is not None
                         and str(r.get("resource_version")) == str(bs.get("pod_rv_at_gate"))]
            if not pod_reads and len(reads) == 1 and isinstance(reads[0], dict):
                pod_reads = reads
            ts = pod_reads[-1].get("t_send_mono_ns") if pod_reads else None
            reads.append({"object": "pod", "resource_version": bs.get("pod_rv_at_gate"),
                          "annotations": bs.get("annotations_at_gate"), "t_send_mono_ns": ts,
                          "from_bound_state": True})
            gate["reads"] = reads
    if isinstance(bs, dict) and n["action"]["precondition_rv"] is None and bs.get("pod_rv_at_gate") is not None:
        n["action"]["precondition_rv_gate"] = bs.get("pod_rv_at_gate")
    n["gate"] = gate
    pl = dict(t.get("planning")) if isinstance(t.get("planning"), dict) else {}
    if pl.get("planning_ms") is None and t.get("planning_ms") is not None:
        pl["planning_ms"] = t.get("planning_ms")
    n["planning"] = pl
    return n


def _expand_reads(reads):
    """Gate/webhook reads -> list of per-object reads. A Pod read carrying annotations (A4d/A4m gate) is
    expanded into one read per annotated policy object, all at the Pod's resourceVersion."""
    out = []
    for r in reads or []:
        if not isinstance(r, dict):
            continue
        ann = r.get("annotations")
        if isinstance(ann, dict) and ann:
            for k, v in ann.items():
                o = norm_object(k)
                if o in POLICY_OBJECTS:
                    out.append(dict(r, object=o, value=v, via_pod=True))
            continue
        o = norm_object(r.get("object"))
        out.append(dict(r, object=o))
    return out


# ----------------------------------------------------------------------------- per-trial
def classify_trial(t, audit_idx, webhook_idx, pod_idx=None):
    """Pure function: one raw E39 trial record (+ indexes) -> oracle row dict (CSV_COLS)."""
    row = {c: "" for c in CSV_COLS}
    for c in ("run_id", "trial_id", "stage", "block", "arm", "condition", "delta_ms", "rep", "schedule_seed", "status"):
        v = t.get(c)
        row[c] = "" if v is None else v
    n = normalize_trial(t)
    for c in ("experiment", "arm_c", "client_impl", "condition_c", "lambda_per_s", "injected_latency_ms",
              "acquisition_mode", "planning_mode", "load_level", "load_level_effective", "action_type",
              "pair_id", "guard_order", "host_cpu_pct", "free_ram_mb", "load_observed_rps", "load_level_recorded"):
        row[c] = "" if n.get(c) is None else n[c]
    row["load_throttled"] = int(bool(n["load_throttled"]))
    row["go_client_version"] = t.get("go_client_version") or ""
    flags = []
    gate, act, writes = n["gate"], n["action"], n["writes"]
    fx = t.get("fixture") if isinstance(t.get("fixture"), dict) else {}
    eff = t.get("effect") if isinstance(t.get("effect"), dict) else {}
    exp, arm, cond, action_type = n["experiment"], n["arm_c"], n["condition_c"], n["action_type"]

    # ---- descriptive evidence (timings) regardless of classification path
    row["gate_decision"] = gate.get("decision") or ""
    row["action_dispatched"] = "" if act["dispatched"] is None else act["dispatched"]
    row["action_http_status"] = "" if act["http_status"] is None else act["http_status"]
    row["expected_uid"] = act["expected_uid"] or ""
    row["effect_verified"] = eff.get("verified", "")
    row["n_writes"] = len(writes)
    ts, tr = act["t_send"], act["t_resp"]
    if ts is not None and tr is not None:
        row["delete_rtt_ms"] = (tr - ts) / 1e6
    gs, ge = _i(gate.get("t_start_mono_ns")), _i(gate.get("t_end_mono_ns"))
    if gs is not None and ge is not None:
        row["gate_ms"] = (ge - gs) / 1e6
    rf = gate.get("refresh_ms")
    if rf is None and _i(gate.get("refresh_t_start_mono_ns")) is not None and _i(gate.get("refresh_t_end_mono_ns")) is not None:
        rf = (gate["refresh_t_end_mono_ns"] - gate["refresh_t_start_mono_ns"]) / 1e6
    if rf is None:
        # refresh latency = first ConfigMap/Pod read sent -> last read received (gate.reads)
        rr = [r for r in (gate.get("reads") or []) if isinstance(r, dict)
              and _i(r.get("t_send_mono_ns")) is not None and _i(r.get("t_recv_mono_ns")) is not None]
        if rr:
            rf = (max(r["t_recv_mono_ns"] for r in rr) - min(r["t_send_mono_ns"] for r in rr)) / 1e6
    if rf is not None:
        row["refresh_ms"] = float(rf)
    pl = n["planning"]
    ps, pe = _i(pl.get("t_start_mono_ns")), _i(pl.get("t_end_mono_ns"))
    p_ms = None
    if pl.get("planning_ms") is not None:
        p_ms = float(pl["planning_ms"])
    elif ps is not None and pe is not None:
        p_ms = (pe - ps) / 1e6
    if p_ms is not None:
        row["planning_ms"] = p_ms
    # end-to-end decision-to-action latency INCLUDING planning: planning start (or gate start + measured
    # planning_ms when only the duration is recorded) -> action send (or gate decision when not dispatched)
    if ps is not None:
        start, add, lab = ps, 0.0, "planning_start"
    elif gs is not None:
        start, add, lab = gs, (p_ms or 0.0), ("planning_ms+gate_start" if p_ms is not None else "gate_start")
    else:
        start = None
    if start is not None:
        if act["dispatched"] is True and ts is not None:
            row["e2e_ms"], kind = (ts - start) / 1e6 + add, "to_action_send"
            row["e2e_kind"] = lab + ":" + kind
        elif ge is not None:
            row["e2e_ms"], kind = (ge - start) / 1e6 + add, "to_gate_decision(no_dispatch)"
            row["e2e_kind"] = lab + ":" + kind
    # E39-C paired acquisition driver (runner harness_delay_proxy.PairedAcquisitionDriver): the guard is
    # recorded in trial.acquisition {t_start_mono_ns (before planning), t_plan_end_mono_ns, t_end_mono_ns,
    # refresh_ms (reads only), gate_ms (planning + reads), justified}; these take precedence.
    acq = t.get("acquisition") if isinstance(t.get("acquisition"), dict) else None
    if acq is not None:
        if acq.get("refresh_ms") is not None:
            row["refresh_ms"] = float(acq["refresh_ms"])
        if acq.get("gate_ms") is not None:
            row["gate_ms"] = float(acq["gate_ms"])
        a_s = _i(acq.get("t_start_mono_ns"))
        if act["dispatched"] is True and ts is not None and a_s is not None:
            row["e2e_ms"], row["e2e_kind"] = (ts - a_s) / 1e6, "acquisition_start(incl_planning):to_action_send"
        elif acq.get("gate_ms") is not None:
            row["e2e_ms"], row["e2e_kind"] = float(acq["gate_ms"]), "acquisition_guard_only(incl_planning)"
        if acq.get("justified") is not None:
            row["c_justified"] = int(bool(acq.get("justified")))
    if ts is not None and ge is not None:
        row["gate_end_to_send_ms"] = (ts - ge) / 1e6
    if len(writes) == 1:
        w0 = writes[0]
        if w0["intended"] is not None and w0["actual"] is not None:
            row["release_error_us"] = (w0["actual"] - w0["intended"]) / 1e3
        if w0["actual"] is not None and ge is not None:
            row["realized_delta_ms"] = (w0["actual"] - ge) / 1e6
    if writes:
        row["writer_object"] = ",".join(sorted({w["object"] or "?" for w in writes}))

    def done(pc, pr, sc=None, sr=None, tc=None, trr=None):
        sc = pc if sc is None else sc
        sr = pr if sr is None else sr
        tc = sc if tc is None else tc
        trr = sr if trr is None else trr
        row["primary_class"], row["primary_reason"] = pc, pr
        row["secondary_class"], row["secondary_reason"] = sc, sr
        row["tertiary_class"], row["tertiary_reason"] = tc, trr
        row["rules_agree"] = "NA" if (pc in NOT_ANALYZABLE or sc in NOT_ANALYZABLE) else (1 if pc == sc else 0)
        row["rules_agree_tertiary"] = "NA" if (pc in NOT_ANALYZABLE or tc in NOT_ANALYZABLE) else (1 if pc == tc else 0)
        row["flags"] = "|".join(flags)
        return row

    # ---- status / schema / design identity
    if t.get("schema") != TRIAL_SCHEMA:
        return done("UNKNOWN", "schema_mismatch(%s)" % t.get("schema"))
    status = t.get("status")
    if status == "INFRA_ERROR":
        return done("INFRA_ERROR", "runner_status_INFRA_ERROR" + (": %s" % t["error"] if t.get("error") else ""))
    if fx.get("reset_ok") is False:
        return done("INFRA_ERROR", "fixture_reset_failed")
    if status == "UNKNOWN":
        return done("UNKNOWN", "runner_status_UNKNOWN" + (": %s" % t["error"] if t.get("error") else ""))
    if status != "COMPLETE":
        return done("UNKNOWN", "invalid_status(%s)" % status)
    if exp not in ("A", "B", "C", "D", "E", "F", "G"):
        return done("UNKNOWN", "unknown_experiment(%s)" % t.get("experiment"))
    if arm is None:
        return done("UNKNOWN", "unknown_arm(%s)" % t.get("arm"))
    if cond is None:
        return done("UNKNOWN", "unknown_condition(%s)" % t.get("condition"))
    if action_type is None:
        return done("UNKNOWN", "unknown_action_type(%s)" % t.get("action_type"))
    if fx.get("reset_ok") is not True:
        return done("UNKNOWN", "fixture_reset_ok_missing")
    if action_type == "netpol_create" and arm in RV_PRECONDITION_ARMS:
        return done("UNKNOWN", "a4_not_applicable_to_create")

    # ---- fixture state S0
    iv = fx.get("initial_values")
    if not isinstance(iv, dict):
        return done("UNKNOWN", "initial_values_missing")
    state, bad, dup = {}, [], []
    for k, v in iv.items():
        o = norm_object(k)
        if o not in POLICY_OBJECTS:
            continue
        b = parse_bool(_initial_value(v))
        if b is None:
            bad.append(o)
            continue
        if o in state and state[o] != b:
            dup.append(o)
        state[o] = b
    if bad:
        return done("UNKNOWN", "initial_value_unparseable(%s)" % ",".join(sorted(bad)))
    if dup:
        return done("UNKNOWN", "initial_value_conflict(%s)" % ",".join(sorted(dup)))
    v_pre, missing = v_core(state)
    if missing:
        return done("UNKNOWN", "initial_state_incomplete(%s)" % ",".join(missing))
    row["v_pre"] = int(v_pre)
    fixture_cut = False
    if not v_pre and cond in G_CONDITIONS:
        # E39-G: the runner records fixture.initial_values as the intended PRE-GATE state, i.e. with the
        # cut applied (runner conditions_e39.initial_values). Accepted only if exactly the cut objects are
        # false and restoring them gives a valid state. With cut writes recorded, S0 := restored state and
        # the cut writes are applied by commit order as usual; without them the cut counts as committed
        # before every read and before the action (pseudo-writes at revision < any real revision).
        cut = set(G_CUT_OBJECTS[cond])
        falses = {o for o, b in state.items() if b is False}
        restored = dict(state)
        for o in cut:
            restored[o] = True
        if falses == cut and v_core(restored)[0]:
            if writes:
                flags.append("fixture_state_recorded_post_cut")
            else:
                fixture_cut = True
                flags.append("pre_gate_cut_in_fixture")
            state = restored
            v_pre = True
            row["v_pre"] = 1
        else:
            return done("UNKNOWN", "fixture_cut_mismatch")
    if not v_pre:
        return done("UNKNOWN", "fixture_prewrite_vcore_false")
    if cond == "async_evidence_last_literal" and (state["evidence-c"] or state["evidence-e"]):
        return done("UNKNOWN", "fixture_condition_mismatch(c_or_e_not_false)")

    # ---- design-level expectations on the writes
    if cond in (NO_WRITE, UID_REBIND, "stable") and writes:
        return done("UNKNOWN", "unexpected_writes_in_%s" % cond)
    if exp == "B":
        bad_o = [w["object_raw"] for w in writes if w["object"] != WRITER_OBJECT.get(cond)]
        if bad_o:
            return done("UNKNOWN", "arrival_object_mismatch(%s)" % bad_o[0])
    elif cond in ASYNC_CONDITIONS:
        if len(writes) != 1:
            return done("UNKNOWN", "writer_missing" if not writes else "multiple_writers(%d)" % len(writes))
        if writes[0]["object"] != WRITER_OBJECT[cond]:
            return done("UNKNOWN", "writer_object_mismatch(%s)" % writes[0]["object_raw"])
    elif cond in G_CONDITIONS and not fixture_cut:
        want = set(G_CUT_OBJECTS[cond])
        got = [o for w in writes for o in (w["objects"] or [w["object"]])]
        if sorted(got) != sorted(want):
            return done("UNKNOWN", "cut_objects_mismatch(%s)" % ",".join(sorted(map(str, got))))
        if any(parse_bool(w["new_value"]) is not False for w in writes):
            return done("UNKNOWN", "cut_value_not_false")

    # ---- per-write verification against the audit log
    pod_target = arm == "A4d"
    committed = []
    gone404 = []
    terminating = []
    for i, w in enumerate(writes):
        nb = parse_bool(w["new_value"])
        if nb is None:
            return done("UNKNOWN", "writer_value_unparseable(%s)" % w["new_value"])
        hs = w["http_status"]
        if hs is None:
            return done("UNKNOWN", "writer_http_status_missing" if w["role"] == "writer" else "arrival_http_status_missing")
        hs = int(hs)
        if not (200 <= hs < 300):
            if hs == 404 and pod_target:
                # A4d writes PATCH the target Pod: 404 = the Pod no longer exists, nothing was committed.
                # Only consistent with an ACCEPTED DELETE (checked below, after the action path is known).
                flags.append("%s_404_pod_gone" % w["role"])
                gone404.append(w)
                continue
            return done("INFRA_ERROR", "%s_not_committed_http_%s" % (w["role"], hs))
        wa = audit_summary(audit_idx.get(str(w["audit_id"]), []) if w["audit_id"] else [])
        if not w["audit_id"]:
            flags.append("%s_audit_id_missing" % w["role"])
        if not wa["present"]:
            flags.append("audit_missing_%s" % w["role"])
        else:
            if wa["conflicts"]:
                return done("UNKNOWN", "writer_audit_conflict(%s)" % ",".join(wa["conflicts"]))
            if wa["code"] is not None and int(wa["code"]) != hs:
                return done("UNKNOWN", "writer_audit_status_mismatch(audit=%s,trial=%s)" % (wa["code"], hs))
            if wa["verb"] and wa["verb"] not in ("update", "patch"):
                return done("UNKNOWN", "writer_audit_verb_mismatch(%s)" % wa["verb"])
            want_res = "pods" if pod_target else "configmaps"
            if wa["resource"] and wa["resource"] != want_res:
                return done("UNKNOWN", "writer_audit_resource_mismatch(%s)" % wa["resource"])
            if wa["resource"] == "configmaps" and wa["name"] and norm_object(wa["name"]) != w["object"]:
                return done("UNKNOWN", "writer_audit_object_mismatch(%s)" % wa["name"])
            for o in (w["objects"] or [w["object"]]):
                av = _annotation_value(wa["annotations"], o) if wa["resource"] == "pods" else _cm_value(wa["data"])
                if av is not None and parse_bool(av) != nb:
                    return done("UNKNOWN", "writer_audit_value_mismatch(audit=%s)" % av)
        if w.get("object_inferred_from") is not None and "arrival_object_inferred" not in flags:
            flags.append("arrival_object_inferred")
        if pod_target and wa["present"] and wa.get("deletion_timestamp"):
            # plan 2.3 (f), pre-LOCK: the PATCH was applied to a Pod ALREADY marked for deletion (grace 0) by the
            # accepted DELETE; kube-apiserver then removes the object during that update ("deletion triggered by
            # update") and the annotation is never persisted. The returned resourceVersion is the DELETE's own
            # deletion-mark revision, not a commit of this write -> not committed (checked below: action accepted).
            flags.append("%s_on_terminating_pod" % w["role"])
            terminating.append(w)
            continue
        rv, src, err = resolve_rv(wa["rv"], w["rv_trial"])
        if err:
            return done("UNKNOWN", "writer_" + err)
        if w["objects"] and len(w["objects"]) > 1:
            for o in w["objects"][1:]:
                committed.append({"i": i, "object": o, "value": nb, "rv": rv, "src": src,
                                  "complete_ns": wa["complete_ns"], "role": w["role"], "mirror_rv": None,
                                  "mirror_complete_ns": None, "mirror_state": None, "multi": True})
        cw = {"i": i, "object": w["object"], "value": nb, "rv": rv, "src": src, "complete_ns": wa["complete_ns"],
              "role": w["role"], "mirror_rv": None, "mirror_complete_ns": None, "mirror_state": None}
        if arm == "A4m":
            m = w["mirror"]
            if m is None:
                cw["mirror_state"] = "record_missing"
            else:
                mrv_raw = _first(m, "mirror_commit_rv", "mirror_rv", "mirror_resource_version")
                if mrv_raw is None:
                    cw["mirror_state"] = "not_mirrored"
                else:
                    ma = audit_summary(audit_idx.get(str(m.get("mirror_audit_id")), []) if m.get("mirror_audit_id") else [])
                    mrv, _, merr = resolve_rv(ma["rv"], mrv_raw)
                    if merr:
                        return done("UNKNOWN", "mirror_" + merr)
                    srv = rv_int(_first(m, "source_commit_rv", "source_rv"))
                    if srv is not None and srv != rv:
                        return done("UNKNOWN", "mirror_source_rv_mismatch(%s!=%s)" % (srv, rv))
                    if mrv <= rv:
                        return done("UNKNOWN", "mirror_rv_not_after_source")
                    cw["mirror_rv"] = mrv
                    tm = ma["complete_ns"] if ma["complete_ns"] is not None else _i(_first(m, "mirror_commit_wall_ns", "t_mirror_commit_wall_ns", "t_mirror_commit"))
                    tsrc = wa["complete_ns"] if wa["complete_ns"] is not None else _i(_first(m, "source_commit_wall_ns", "t_source_commit_wall_ns", "t_source_commit"))
                    cw["mirror_complete_ns"] = tm
                    cw["mirror_state"] = "mirrored"
                    if cw["complete_ns"] is None and tsrc is not None:
                        flags.append("source_time_from_mirror_record")
                        cw["complete_ns_mirror_record"] = tsrc
                    cw["mirror_lag_ms"] = ((tm - tsrc) / 1e6) if (tm is not None and tsrc is not None) else None
        committed.append(cw)
    if fixture_cut:
        # pseudo-writes at revision 0 / time 0: committed before any read and any action
        for j, o in enumerate(sorted(G_CUT_OBJECTS[cond])):
            committed.append({"i": -1 - j, "object": o, "value": False, "rv": -1 - j, "src": "fixture",
                              "complete_ns": -1 - j, "role": "cut_fixture", "mirror_rv": -1 - j,
                              "mirror_complete_ns": -1 - j, "mirror_state": "mirrored"})
    committed.sort(key=lambda c: (c["rv"], c["i"]))
    # Same resourceVersion from DIFFERENT writes: a merge-PATCH that changes nothing is a no-op on a real
    # apiserver (returns 200 with the unchanged object and RV). Identical (object, value) -> one commit;
    # anything else sharing an RV is inconsistent -> UNKNOWN.
    dedup, seen_rv = [], {}
    for c in committed:
        prev = seen_rv.get(c["rv"])
        if prev is None or prev["i"] == c["i"]:
            seen_rv.setdefault(c["rv"], c)
            dedup.append(c)
            continue
        same = [d for d in dedup if d["rv"] == c["rv"] and d["object"] == c["object"] and d["value"] == c["value"]]
        if not same:
            return done("UNKNOWN", "writes_share_rv")
        if "noop_patch_same_rv" not in flags:
            flags.append("noop_patch_same_rv")
    committed = dedup
    row["n_writes_committed"] = len(committed)
    if committed:
        row["first_write_rv"] = committed[0]["rv"]
        row["writer_rv_source"] = committed[0]["src"]
        row["writer_complete_ts_ns"] = "" if committed[0]["complete_ns"] is None else committed[0]["complete_ns"]
        row["writer_audit_present"] = int(committed[0]["complete_ns"] is not None)
        if arm == "A4m":
            c0 = committed[0]
            row["mirror_source_rv"] = c0["rv"]
            if c0["mirror_rv"] is not None:
                row["mirror_rv"] = c0["mirror_rv"]
                row["mirror_lag_rv"] = c0["mirror_rv"] - c0["rv"]
                if c0.get("mirror_lag_ms") is not None:
                    row["mirror_lag_ms"] = c0["mirror_lag_ms"]
            else:
                flags.append("mirror_" + str(c0["mirror_state"]))
    post = dict(state)
    for c in committed:
        post[c["object"]] = c["value"]
    v_post = v_core(post)[0]
    if committed:
        row["v_post"] = int(v_post)
        if v_post:
            flags.append("write_not_invalidating")

    def state_with(cs):
        s = dict(state)
        for c in sorted(cs, key=lambda c: c["rv"]):
            s[c["object"]] = c["value"]
        return s

    # ---- path: accepted / denied (gate, webhook, rv precondition) / uid 409
    decision = gate.get("decision")
    dispatched = act["dispatched"] is True
    da = audit_summary([])
    wh = None
    conflict = None
    ok_codes = (200, 202) if action_type == "pod_delete" else (200, 201)
    want_verb = "delete" if action_type == "pod_delete" else "create"
    want_res = "pods" if action_type == "pod_delete" else "networkpolicies"
    if not dispatched:
        if decision == "UNKNOWN_COHERENCE":
            return done("UNKNOWN", FAIL_CLOSED_REASON)
        if decision != "ABORT_REPLAN":
            return done("UNKNOWN", "not_dispatched_without_abort(%s)" % decision)
        path, decider = "deny", "gate"
    else:
        if decision in ("UNKNOWN_COHERENCE", "ABORT_REPLAN"):
            flags.append("dispatched_after_%s" % decision)
        hs = act["http_status"]
        if act["audit_id"]:
            da = audit_summary(audit_idx.get(str(act["audit_id"]), []))
        else:
            flags.append("action_audit_id_missing")
        row["action_audit_present"] = int(da["present"])
        if da["present"]:
            if da["conflicts"]:
                return done("UNKNOWN", "action_audit_conflict(%s)" % ",".join(da["conflicts"]))
            if da["code"] is not None and hs is not None and int(da["code"]) != int(hs):
                return done("UNKNOWN", "action_audit_status_mismatch(audit=%s,trial=%s)" % (da["code"], hs))
            if da["verb"] and da["verb"] != want_verb:
                return done("UNKNOWN", "action_audit_verb_mismatch(%s)" % da["verb"])
            if da["resource"] and da["resource"] != want_res:
                return done("UNKNOWN", "action_audit_resource_mismatch(%s)" % da["resource"])
            if not da["has_response_complete"]:
                flags.append("action_audit_no_ResponseComplete")
        else:
            flags.append("audit_missing_action")
        # webhook join: request uid, else target pod uid, else created object name
        ents = []
        wuid = t.get("webhook_request_uid")
        if wuid:
            ents = webhook_idx.get(str(wuid), [])
        else:
            if act["expected_uid"]:
                ents = list(getattr(webhook_idx, "by_pod", {}).get(str(act["expected_uid"]), []))
                if ents:
                    flags.append("webhook_joined_by_pod_uid")
            if not ents and act["object_name"]:
                ents = list(getattr(webhook_idx, "by_name", {}).get(str(act["object_name"]), []))
                if ents:
                    flags.append("webhook_joined_by_object_name")
        evaluated = sorted([e for e in ents if e.get("pod_terminating") is not True],
                           key=lambda e: (e.get("received_wall_ns") or 0))
        if ents:
            if arm != "A3":
                flags.append("webhook_entry_for_non_webhook_arm")
            if len(ents) > len(evaluated):
                flags.append("webhook_terminating_calls(%d)" % (len(ents) - len(evaluated)))
            if len(evaluated) > 1:
                flags.append("webhook_multiple_evaluations(%d)" % len(evaluated))
                if len({e.get("decision") for e in evaluated}) > 1:
                    flags.append("webhook_evaluations_differ")
        if arm == "A3" and not evaluated:
            flags.append("webhook_log_missing")
        if hs is None:
            return done("UNKNOWN", "action_no_http_status")
        hs = int(hs)

        def _use(entry):
            if entry.get("schema") not in WEBHOOK_SCHEMAS:
                flags.append("webhook_schema_mismatch")
            row["webhook_decision"] = entry.get("decision") or ""
            return entry

        if hs == 409:
            if action_type != "pod_delete":
                return done("UNKNOWN", "create_conflict_409")
            msg = da["message"] or act["message"]
            conflict = parse_precondition_message(msg)
            row["conflict_kind"] = conflict["kind"] or ""
            row["conflict_current_rv"] = conflict["current"] or ""
            if conflict["kind"] == "uid" or (conflict["kind"] is None and arm not in RV_PRECONDITION_ARMS):
                if conflict["kind"] is None:
                    flags.append("conflict_message_unparsed")
                return done("UID_BLOCKED", "uid_precondition_conflict_409")
            if conflict["kind"] is None and cond == UID_REBIND:
                flags.append("conflict_message_unparsed")
                return done("UID_BLOCKED", "uid_precondition_conflict_409(assumed_uid_rebind)")
            if arm not in RV_PRECONDITION_ARMS:
                return done("UNKNOWN", "rv_precondition_conflict_in_non_a4_arm")
            if cond == UID_REBIND:
                # UID is checked before resourceVersion; a rebind with an RV-conflict message is anomalous
                return done("UNKNOWN", "uid_rebind_rv_conflict")
            path, decider = "deny", "rv_precondition"
            row["conflict_attribution"] = attribute_pod_change(audit_idx, conflict["current"])
        elif hs in ok_codes:
            allows = [e for e in evaluated if e.get("decision") == "allow"]
            if evaluated and not allows:
                wh = _use(evaluated[-1])
                return done("UNKNOWN", "webhook_deny_but_action_accepted")
            if allows:
                wh = _use(allows[0])
                if evaluated[0] is not allows[0]:
                    flags.append("webhook_first_evaluation_not_governing")
            path, decider = "accept", ""
        elif 400 <= hs < 500 and hs != 404:
            denies = [e for e in evaluated if e.get("decision") == "deny"]
            if denies:
                wh = _use(denies[-1])
                path, decider = "deny", "webhook"
            elif evaluated:
                wh = _use(evaluated[-1])
                return done("UNKNOWN", "webhook_allow_but_action_denied_%d" % hs)
            elif arm == "A3":
                return done("UNKNOWN", "action_denied_%d_webhook_log_missing" % hs)
            else:
                return done("UNKNOWN", "action_denied_%d_unattributed" % hs)
        else:
            return done("UNKNOWN", "action_http_%d" % hs)
    row["decider"] = decider
    if terminating and path != "accept":
        return done("UNKNOWN", "write_on_terminating_pod_but_action_not_accepted")
    if gone404:
        if path != "accept":
            return done("INFRA_ERROR", "writer_404_but_action_not_accepted")
        for w in gone404:
            ga = audit_summary(audit_idx.get(str(w["audit_id"]), []) if w["audit_id"] else [])
            if ga["complete_ns"] is not None and da["received_ns"] is not None and ga["complete_ns"] < da["received_ns"]:
                return done("UNKNOWN", "writer_404_before_action_received")
    if gate.get("ssar_allowed") is False:
        flags.append("ssar_denied")
    pre_rv = rv_int(act["precondition_rv"])
    row["precondition_rv"] = "" if pre_rv is None else pre_rv
    if arm in RV_PRECONDITION_ARMS and dispatched and act["precondition_rv"] is None:
        flags.append("rv_precondition_missing")

    # ---- uid_rebind_control positive control (unchanged)
    if cond == UID_REBIND:
        if path == "deny":
            return done("UNKNOWN", "uid_rebind_denied_not_oracle_evaluable(%s)" % decider)
        row["deleted_uid"] = da["uid"] or ""
        if not da["uid"]:
            return done("UNKNOWN", "deleted_uid_unavailable")
        if da["uid"] != act["expected_uid"]:
            return done("INVALID_EXECUTED", "uid_rebind_wrong_object_deleted")
        flags.append("uid_rebind_not_realized")
        return done("VALID_EXECUTED", "uid_rebind_expected_object_deleted")

    # ================================================================ accepted action
    if path == "accept":
        if eff.get("verified") is not True:
            flags.append("effect_not_verified")
        d_rv, d_src, d_err = resolve_rv(da["rv"], act["committed_rv"])
        if action_type == "pod_delete" and pod_idx is not None:
            pw = pod_idx.get(str(act["expected_uid"] or ""))
            pw_del = [rv_int(x) for x in (pw or {}).get("deleted", [])]
            if len(pw_del) == 1 and pw_del[0] is not None:
                row["pod_watch_deleted_rv"] = pw_del[0]
            if d_err == "rv_missing":
                if len(pw_del) == 1 and pw_del[0] is not None:
                    d_rv, d_src, d_err = pw_del[0], "pod_watch_deleted", None
                    flags.append("delete_rv_from_pod_watch")
                elif len(pw_del) > 1:
                    d_err = "rv_missing(pod_watch_multiple_deleted_events)"
                else:
                    d_err = "rv_missing(pod_watch_no_deleted_event)"
            elif d_err is None and len(pw_del) == 1 and pw_del[0] is not None and pw_del[0] != d_rv:
                d_rv, d_err = None, "rv_conflict_pod_watch_%s_vs_%s" % (pw_del[0], d_src)
        row["action_rv"] = "" if d_rv is None else d_rv
        row["action_rv_source"] = d_src
        if not committed:
            pc, pr = ("VALID_EXECUTED", "no_write_valid_state") if cond in (NO_WRITE, "stable") else \
                     ("VALID_EXECUTED", "write_not_committed_pod_gone(404)" if gone404 else "no_committed_write")
            if d_err:
                flags.append("action_" + d_err)
            return done(pc, pr)
        # primary: commit order
        if d_err:
            pc, pr = "UNKNOWN", "action_" + d_err
        elif any(c["rv"] == d_rv for c in committed):
            pc, pr = "UNKNOWN", "rv_tie"
        else:
            before = [c for c in committed if c["rv"] < d_rv]
            row["n_writes_before_action"] = len(before)
            row["rv_margin"] = d_rv - committed[0]["rv"]
            row["source_before_action"] = int(bool(before))
            if arm == "A4m" and committed[0]["mirror_rv"] is not None:
                row["mirror_before_action"] = int(committed[0]["mirror_rv"] < d_rv)
                if before and committed[0]["mirror_rv"] > d_rv:
                    flags.append("mirror_lag_exposure")
            v_use = v_core(state_with(before))[0]
            if before and not v_use:
                pc, pr = "INVALID_EXECUTED", "write_committed_before_action(rv)"
            elif before:
                pc, pr = "VALID_EXECUTED", "write_before_action_but_not_invalidating(rv)"
            else:
                pc, pr = "VALID_EXECUTED", "write_committed_after_action(rv)"
        # secondary: ResponseComplete order (plan v1.2)
        dc = da["complete_ns"]
        row["ref_ts_ns"] = "" if dc is None else dc
        row["ref_ts_kind"] = "action_ResponseComplete_stageTimestamp"
        wct = [c["complete_ns"] for c in committed]
        if any(x is None for x in wct):
            sc, sr = "UNKNOWN", "time_rule_writer_audit_missing"
        elif dc is None:
            sc, sr = "UNKNOWN", "time_rule_action_audit_missing"
        elif any(x == dc for x in wct):
            row["time_margin_us"] = 0.0
            sc, sr = "UNKNOWN", "time_tie"
        else:
            row["time_margin_us"] = (dc - wct[0]) / 1e3
            bt = [c for c in committed if c["complete_ns"] < dc]
            vt = v_core(state_with(bt))[0]
            if bt and not vt:
                sc, sr = "INVALID_EXECUTED", "write_complete_before_action_complete(time)"
            elif bt:
                sc, sr = "VALID_EXECUTED", "write_before_action_but_not_invalidating(time)"
            else:
                sc, sr = "VALID_EXECUTED", "write_complete_not_before_action_complete(time)"
        # tertiary: vs action requestReceivedTimestamp (plan v1/v1.1 rule, transparency only)
        dr = da["received_ns"]
        row["ref_ts_tertiary_ns"] = "" if dr is None else dr
        if any(x is None for x in wct):
            tc, trr = "UNKNOWN", "time_rule_writer_audit_missing"
        elif dr is None:
            tc, trr = "UNKNOWN", "time_rule_action_audit_missing"
        elif any(x == dr for x in wct):
            tc, trr = "UNKNOWN", "time_tie"
        else:
            bt = [c for c in committed if c["complete_ns"] < dr]
            vt = v_core(state_with(bt))[0]
            if bt and not vt:
                tc, trr = "INVALID_EXECUTED", "write_complete_before_action_received(time)"
            elif bt:
                tc, trr = "VALID_EXECUTED", "write_before_action_but_not_invalidating(time)"
            else:
                tc, trr = "VALID_EXECUTED", "write_complete_not_before_action_received(time)"
        return done(pc, pr, sc, sr, tc, trr)

    # ================================================================ denial
    if not committed:
        if decider == "rv_precondition":
            flags.append("conflict_from_other_pod_update")
        return done("FALSE_ABORT", "denied_valid_state_no_write(%s)" % decider)

    reads_pod = decider == "rv_precondition" or (decider == "gate" and arm in RV_PRECONDITION_ARMS)

    def repr_rv(c):
        if arm == "A4m" and reads_pod:
            return c["mirror_rv"]
        return c["rv"]

    def repr_time(c):
        if arm == "A4m" and reads_pod:
            return c["mirror_complete_ns"]
        return c["complete_ns"]

    if arm == "A4m" and reads_pod and any(c["mirror_state"] == "record_missing" for c in committed):
        return done("UNKNOWN", "mirror_record_missing")

    # the decider's reads
    if decider == "rv_precondition":
        y = rv_int(conflict["current"]) if conflict else None
        reads = [] if y is None else [{"object": o, "resource_version": str(y)} for o in POLICY_OBJECTS]
        row["decider_read_rv"] = "" if y is None else y
    else:
        raw_reads = gate.get("reads") if decider == "gate" else (wh or {}).get("reads")
        reads = _expand_reads(raw_reads)
    # primary
    pc = pr = None
    reads = [r for r in reads if r.get("object") in POLICY_OBJECTS]
    read_rvs = [rv_int(r.get("resource_version")) for r in reads]
    if decider == "rv_precondition" and not reads:
        pc, pr = "UNKNOWN", "precondition_current_rv_unavailable"
    elif any(x is None for x in read_rvs) or not reads:
        pc, pr = "UNKNOWN", "decider_read_rv_missing_or_non_numeric"
    else:
        r_star = max(read_rvs)
        seen = []
        for c in committed:
            rr = repr_rv(c)
            match = [r for r in reads if r.get("object") == c["object"]]
            if match:
                ref = rv_int(match[-1].get("resource_version"))
            else:
                ref = r_star
                flags.append("decider_unread_object(%s)" % c["object"])
            c["_seen"] = rr is not None and rr <= ref
            if c["_seen"]:
                seen.append(c)
        # read-value consistency (only objects the decider actually read)
        st_seen = state_with(seen)
        incons = False
        for c in committed:
            match = [r for r in reads if r.get("object") == c["object"] and r.get("value") is not None]
            if match:
                rb = parse_bool(match[-1].get("value"))
                if rb is not None and rb != st_seen[c["object"]]:
                    incons = True
        wread = [r for r in reads if r.get("object") == committed[0]["object"]]
        if wread and decider != "rv_precondition":
            row["decider_read_rv"] = wread[-1].get("resource_version") or ""
        if incons:
            pc, pr = "UNKNOWN", "decider_read_inconsistent(rv_vs_value)"
        elif not v_core(st_seen)[0]:
            pc, pr = "CORRECT_ABORT", "denier_read_post_write_invalid_state(rv)"
        else:
            if decider == "rv_precondition":
                flags.append("conflict_from_other_pod_update")
            pc, pr = "FALSE_ABORT", ("denier_read_pre_write_valid_state(rv)" if not seen
                                     else "denier_read_post_write_but_valid(rv)")
    # secondary: time order vs the decider's read time
    ref, kind = None, ""
    if decider == "gate":
        te_m, te_w = _i(gate.get("t_end_mono_ns")), _i(gate.get("t_end_wall_ns"))
        kind = "gate_read_send_wall(mapped)"

        def ref_for(obj):
            m = [r for r in reads if r.get("object") == obj and _i(r.get("t_send_mono_ns")) is not None]
            rs = _i(m[-1].get("t_send_mono_ns")) if m else None
            if rs is None:
                allr = [_i(r.get("t_send_mono_ns")) for r in reads if _i(r.get("t_send_mono_ns")) is not None]
                rs = max(allr) if allr else None
            return None if None in (te_m, te_w, rs) else te_w - (te_m - rs)
    elif decider == "webhook":
        kind = "webhook_read_send_wall_ns"

        def ref_for(obj):
            m = [r for r in reads if r.get("object") == obj and _i(r.get("t_send_wall_ns")) is not None]
            if m:
                return _i(m[-1].get("t_send_wall_ns"))
            flags.append("webhook_read_time_missing")
            return _i((wh or {}).get("received_wall_ns"))
    else:
        kind = "action_ResponseComplete_stageTimestamp(precondition)"

        def ref_for(obj):
            return da["complete_ns"]
    row["ref_ts_kind"] = kind
    refs = {c["object"]: ref_for(c["object"]) for c in committed}
    r0 = refs[committed[0]["object"]]
    row["ref_ts_ns"] = "" if r0 is None else r0
    times = [repr_time(c) for c in committed]
    if any(x is None for x, c in zip(times, committed) if not (arm == "A4m" and reads_pod and c["mirror_state"] == "not_mirrored")):
        sc, sr = "UNKNOWN", "time_rule_writer_audit_missing"
    elif any(v is None for v in refs.values()):
        sc, sr = "UNKNOWN", "time_rule_denier_time_missing"
    elif any(x is not None and x == refs[c["object"]] for x, c in zip(times, committed)):
        row["time_margin_us"] = 0.0
        sc, sr = "UNKNOWN", "time_tie"
    else:
        if times[0] is not None:
            row["time_margin_us"] = (r0 - times[0]) / 1e3
        seen_t = [c for x, c in zip(times, committed) if x is not None and x < refs[c["object"]]]
        if seen_t and not v_core(state_with(seen_t))[0]:
            sc, sr = "CORRECT_ABORT", "write_complete_before_denier_read(time)"
        else:
            sc, sr = "FALSE_ABORT", "write_not_complete_before_denier_read(time)"
    return done(pc, pr, sc, sr)


def run_oracle(trials, audit_events, webhook_entries, pod_events=None):
    ai = index_audit(audit_events)
    wi = index_webhook(webhook_entries)
    pi = index_pod_events(pod_events)
    return [classify_trial(t, ai, wi, pi) for t in trials]


def write_csv(rows, path):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_COLS, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in CSV_COLS})


def main(argv=None):
    ap = argparse.ArgumentParser(description="E39 offline ground-truth oracle")
    ap.add_argument("--raw", required=True)
    ap.add_argument("--webhook", required=True, help="webhook_log.jsonl (may be empty)")
    ap.add_argument("--audit", required=True)
    ap.add_argument("--pod-events", help="pod_events.jsonl (optional, plan v1.2 (i))")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    trials, e1 = load_jsonl(a.raw)
    wh, e2 = load_jsonl(a.webhook)
    au, e3 = load_jsonl(a.audit)
    pe = None
    if a.pod_events:
        pe, e4 = load_jsonl(a.pod_events)
        if e4:
            print("WARNING: %d unparseable pod-event lines" % len(e4), file=sys.stderr)
    for k, v in (("raw", e1), ("webhook", e2), ("audit", e3)):
        if v:
            print("WARNING: %d unparseable lines in %s (first: %s)" % (len(v), k, v[0]), file=sys.stderr)
    rows = run_oracle(trials, au, wh, pe)
    write_csv(rows, a.out)
    c = Counter(r["primary_class"] for r in rows)
    print("oracle %s: %d trials; primary classes: %s" % (ORACLE_VERSION, len(rows), dict(sorted(c.items()))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
