#!/usr/bin/env python3
"""E38v2 ground-truth oracle (offline; reads files only, never talks to a cluster).

Inputs : RAW_TRIALS.jsonl (e38v2.trial.v1), webhook_log.jsonl (e38v2.webhook.v1),
         audit.log (audit.k8s.io/v1 Event JSON lines), optionally pod_events.jsonl (e38v2.podevent.v1,
         E38v2 RERUN). Join key: audit auditID == trial *.audit_id; webhook request_uid == trial
         webhook_request_uid (else pod_uid); pod events uid == delete.expected_uid.
Output : one row per trial with a PRIMARY (commit-order / resourceVersion), a SECONDARY (time order,
         corrected in plan v1.2) and a TERTIARY (the plan v1/v1.1 secondary rule, reported for
         transparency) outcome class, the evidence used, and flags.

Outcome classes: VALID_EXECUTED, INVALID_EXECUTED, CORRECT_ABORT, FALSE_ABORT, UID_BLOCKED,
UNKNOWN, INFRA_ERROR. Missing, conflicting or inconsistent evidence is never guessed: the trial is
classified UNKNOWN with an explicit reason.

Oracle state model (independent of the runner's own belief):
  pre-write state  = fixture.initial_values (the known fixture state after reset)
  post-write state = pre-write state with writer.object := writer.new_value
  ACCEPTED DELETE (HTTP 200/202):
     primary   : write "before use" iff writer committed RV < RV at which the Pod deletion was committed
                 (deletion RV: audit responseObject / client response; plan v1.2 fallback when BOTH are
                 missing: the DELETED watch event of that Pod UID in pod_events.jsonl)
     secondary : (plan v1.2) write "before use" iff writer ResponseComplete stageTimestamp < DELETE
                 ResponseComplete stageTimestamp (completion of the DELETE, commit proxy)
     tertiary  : (plan v1/v1.1 secondary, reported only) writer ResponseComplete stageTimestamp < DELETE
                 requestReceivedTimestamp (receipt; misspecified: the DELETE commits 10-40 ms after receipt)
     INVALID_EXECUTED iff write-before-use AND V_core(post-write) is false; else VALID_EXECUTED.
  DENIED (gate ABORT_REPLAN without dispatch, or webhook deny of the DELETE):
     the reference point is the denier's read of the written object:
     primary   : write "seen" iff writer RV <= RV returned by the denier's read of that object
     secondary : write "seen" iff writer ResponseComplete stageTimestamp < denier read time
                 (gate: that read's send time mapped mono->wall with the gate's own anchor;
                  webhook: that read's t_send_wall_ns from the webhook log; received_wall_ns only as a
                  flagged fallback for logs without per-read stamps)
  Webhook join: trial.webhook_request_uid when present, else webhook pod_uid == delete.expected_uid.
     CORRECT_ABORT iff seen AND V_core(post-write) false; else FALSE_ABORT.
  HTTP 409 on the DELETE -> UID_BLOCKED.
  gate UNKNOWN_COHERENCE (epoch fence / coherence failure, no dispatch) -> UNKNOWN (reason gate_unknown_coherence).
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter

from e38v2_common import (ARMS, ASYNC_CONDITIONS, CONDITIONS, NOT_ANALYZABLE, POLICY_OBJECTS,
                          TRIAL_SCHEMA, WEBHOOK_SCHEMA, WRITER_OBJECT, load_jsonl, norm_object,
                          parse_bool, parse_k8s_time_ns, rv_int, v_core)

ORACLE_VERSION = "e38v2.oracle.v1.2"

ID_COLS = ["run_id", "trial_id", "stage", "block", "arm", "condition", "delta_ms", "rep",
           "schedule_seed", "status"]
CLASS_COLS = ["primary_class", "primary_reason", "secondary_class", "secondary_reason", "rules_agree",
              "tertiary_class", "tertiary_reason", "rules_agree_tertiary"]
EVIDENCE_COLS = ["v_pre", "v_post", "gate_decision", "delete_dispatched", "delete_http_status",
                 "writer_object", "writer_new_value", "writer_http_status",
                 "writer_rv", "writer_rv_source", "delete_rv", "delete_rv_source", "rv_margin",
                 "decider", "decider_read_rv", "decider_read_value", "webhook_decision", "webhook_request_uid",
                 "writer_complete_ts_ns", "ref_ts_ns", "ref_ts_kind", "time_margin_us",
                 "ref_ts_tertiary_ns", "time_margin_tertiary_us",
                 "pod_watch_deleted_rv", "pod_watch_deletion_accepted_rv",
                 "expected_uid", "deleted_uid", "effect_verified",
                 "writer_audit_present", "delete_audit_present",
                 "delete_rtt_ms", "release_error_us", "realized_delta_ms"]
CSV_COLS = ID_COLS + CLASS_COLS + EVIDENCE_COLS + ["flags"]


# ----------------------------------------------------------------------------- joins
def index_audit(events):
    idx = {}
    for ev in events:
        aid = ev.get("auditID")
        if aid:
            idx.setdefault(str(aid), []).append(ev)
    return idx


class WebhookIndex(dict):
    """{request_uid: [entries]} plus .by_pod {pod_uid: [entries]}.

    E38v2 integration decision: the live runner cannot observe the AdmissionReview request uid, so
    trial.webhook_request_uid is null and the webhook entry is joined on pod_uid == delete.expected_uid
    (every trial creates a fresh target Pod, so the Pod UID identifies the trial's DELETE admission)."""

    def __init__(self):
        super().__init__()
        self.by_pod = {}


def index_webhook(entries):
    idx = WebhookIndex()
    for e in entries:
        uid = e.get("request_uid")
        if uid:
            idx.setdefault(str(uid), []).append(e)
        puid = e.get("pod_uid")
        if puid:
            idx.by_pod.setdefault(str(puid), []).append(e)
    return idx


def index_pod_events(events):
    """E38v2 RERUN (plan v1.2): {pod uid: {"deleted": [rv strings of DELETED events],
    "accepted": rv string of the first ADDED/MODIFIED/LIST event showing deletionTimestamp}}.
    Returns None when no pod-event file was supplied."""
    if events is None:
        return None
    idx = {}
    for e in events:
        uid = e.get("uid")
        if not uid:
            continue
        d = idx.setdefault(str(uid), {"deleted": [], "accepted": None})
        typ = e.get("type")
        if typ == "DELETED":
            d["deleted"].append(e.get("rv"))
        elif typ in ("ADDED", "MODIFIED", "LIST") and e.get("deletion_timestamp") and d["accepted"] is None:
            d["accepted"] = e.get("rv")
    return idx


def audit_summary(evs):
    """Summarise all audit events sharing one auditID. Never guesses: conflicts are listed."""
    s = {"present": bool(evs), "conflicts": [], "received_ns": None, "complete_ns": None,
         "code": None, "rv": None, "uid": None, "data": None, "verb": None, "name": None,
         "resource": None, "namespace": None, "has_response_complete": False}
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
                  None if is_status else md.get("uid"),
                  e.get("stageTimestamp")))
    if len(keys) > 1:
        s["conflicts"].append("multiple_ResponseComplete_differ")
        return s
    e = rc[0]
    s["code"] = (e.get("responseStatus") or {}).get("code")
    s["complete_ns"] = parse_k8s_time_ns(e.get("stageTimestamp"))
    if e.get("stageTimestamp") and s["complete_ns"] is None:
        s["conflicts"].append("stageTimestamp_unparseable")
    ro = e.get("responseObject") if isinstance(e.get("responseObject"), dict) else None
    if ro and ro.get("kind") != "Status":
        md = ro.get("metadata") or {}
        s["rv"] = md.get("resourceVersion")
        s["uid"] = md.get("uid")
        s["data"] = ro.get("data")
    return s


def resolve_rv(audit_raw, trial_raw):
    """Combine audit-derived and trial-recorded resourceVersions. Returns (int|None, source, error|None)."""
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


def _initial_value(v):
    if isinstance(v, dict):
        return _cm_value(v)
    return v


def _i(x):
    return x if isinstance(x, int) and not isinstance(x, bool) else None


# ----------------------------------------------------------------------------- per-trial
def classify_trial(t, audit_idx, webhook_idx, pod_idx=None):
    """Pure function: one RAW trial record (+ indexes) -> oracle row dict (CSV_COLS).
    pod_idx: index_pod_events(...) or None (no pod-event file)."""
    row = {c: "" for c in CSV_COLS}
    for c in ID_COLS:
        v = t.get(c)
        row[c] = "" if v is None else v
    flags = []
    gate = t.get("gate") if isinstance(t.get("gate"), dict) else {}
    writer = t.get("writer") if isinstance(t.get("writer"), dict) else None
    dl = t.get("delete") if isinstance(t.get("delete"), dict) else {}
    eff = t.get("effect") if isinstance(t.get("effect"), dict) else {}
    fx = t.get("fixture") if isinstance(t.get("fixture"), dict) else {}
    arm, cond = t.get("arm"), t.get("condition")

    # descriptive evidence available regardless of classification path
    row["gate_decision"] = gate.get("decision") or ""
    row["delete_dispatched"] = dl.get("dispatched", "")
    row["delete_http_status"] = "" if dl.get("http_status") is None else dl.get("http_status")
    row["expected_uid"] = dl.get("expected_uid") or ""
    row["effect_verified"] = eff.get("verified", "")
    ts, tr = _i(dl.get("t_send_mono_ns")), _i(dl.get("t_resp_mono_ns"))
    if ts is not None and tr is not None:
        row["delete_rtt_ms"] = (tr - ts) / 1e6
    if writer:
        ia, aa = _i(writer.get("intended_release_mono_ns")), _i(writer.get("actual_release_mono_ns"))
        if ia is not None and aa is not None:
            row["release_error_us"] = (aa - ia) / 1e3
        ge = _i(gate.get("t_end_mono_ns"))
        if aa is not None and ge is not None:
            row["realized_delta_ms"] = (aa - ge) / 1e6
        row["writer_object"] = writer.get("object") or ""
        row["writer_new_value"] = writer.get("new_value") if writer.get("new_value") is not None else ""
        row["writer_http_status"] = "" if writer.get("http_status") is None else writer.get("http_status")

    def done(pc, pr, sc=None, sr=None, tc=None, tr=None):
        sc = pc if sc is None else sc
        sr = pr if sr is None else sr
        # tertiary (plan v1/v1.1 secondary rule) differs from the secondary only for accepted DELETEs
        tc = sc if tc is None else tc
        tr = sr if tr is None else tr
        row["primary_class"], row["primary_reason"] = pc, pr
        row["secondary_class"], row["secondary_reason"] = sc, sr
        row["tertiary_class"], row["tertiary_reason"] = tc, tr
        if pc in NOT_ANALYZABLE or sc in NOT_ANALYZABLE:
            row["rules_agree"] = "NA"
        else:
            row["rules_agree"] = 1 if pc == sc else 0
        if pc in NOT_ANALYZABLE or tc in NOT_ANALYZABLE:
            row["rules_agree_tertiary"] = "NA"
        else:
            row["rules_agree_tertiary"] = 1 if pc == tc else 0
        row["flags"] = "|".join(flags)
        return row

    # ---- runner status / schema / fixture
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
    if arm not in ARMS:
        return done("UNKNOWN", "unknown_arm(%s)" % arm)
    if cond not in CONDITIONS:
        return done("UNKNOWN", "unknown_condition(%s)" % cond)
    if fx.get("reset_ok") is not True:
        return done("UNKNOWN", "fixture_reset_ok_missing")

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
    if not v_pre:
        return done("UNKNOWN", "fixture_prewrite_vcore_false")
    if cond == "async_evidence_last_literal" and (state["evidence-c"] or state["evidence-e"]):
        return done("UNKNOWN", "fixture_condition_mismatch(c_or_e_not_false)")

    # ---- writer (async conditions only)
    w_rv = w_src = w_err = None
    wa = audit_summary([])
    wobj = new_b = v_post = None
    if cond == "no_write" and writer is not None:
        return done("UNKNOWN", "unexpected_writer_in_no_write")
    if cond in ASYNC_CONDITIONS:
        if writer is None:
            return done("UNKNOWN", "writer_missing")
        wobj = norm_object(writer.get("object"))
        if wobj != WRITER_OBJECT[cond]:
            return done("UNKNOWN", "writer_object_mismatch(%s)" % writer.get("object"))
        new_b = parse_bool(writer.get("new_value"))
        if new_b is None:
            return done("UNKNOWN", "writer_value_unparseable(%s)" % writer.get("new_value"))
        hs = writer.get("http_status")
        if hs is None:
            return done("UNKNOWN", "writer_http_status_missing")
        if not (200 <= int(hs) < 300):
            return done("INFRA_ERROR", "writer_not_committed_http_%s" % hs)
        post = dict(state)
        post[wobj] = new_b
        v_post = v_core(post)[0]
        row["v_post"] = int(v_post)
        if v_post:
            flags.append("write_not_invalidating")
        if not writer.get("audit_id"):
            flags.append("writer_audit_id_missing")
        wa = audit_summary(audit_idx.get(str(writer.get("audit_id")), []) if writer.get("audit_id") else [])
        row["writer_audit_present"] = int(wa["present"])
        if not wa["present"]:
            flags.append("audit_missing_writer")
        else:
            if wa["conflicts"]:
                return done("UNKNOWN", "writer_audit_conflict(%s)" % ",".join(wa["conflicts"]))
            if not wa["has_response_complete"]:
                flags.append("writer_audit_no_ResponseComplete")
            if wa["code"] is not None and int(wa["code"]) != int(hs):
                return done("UNKNOWN", "writer_audit_status_mismatch(audit=%s,trial=%s)" % (wa["code"], hs))
            if wa["name"] and norm_object(wa["name"]) != wobj:
                return done("UNKNOWN", "writer_audit_object_mismatch(%s)" % wa["name"])
            if wa["verb"] and wa["verb"] not in ("update", "patch"):
                return done("UNKNOWN", "writer_audit_verb_mismatch(%s)" % wa["verb"])
            av = _cm_value(wa["data"])
            if av is not None and parse_bool(av) != new_b:
                return done("UNKNOWN", "writer_audit_value_mismatch(audit=%s)" % av)
        w_rv, w_src, w_err = resolve_rv(wa["rv"], writer.get("resulting_resource_version"))
        row["writer_rv"] = "" if w_rv is None else w_rv
        row["writer_rv_source"] = w_src
        row["writer_complete_ts_ns"] = "" if wa["complete_ns"] is None else wa["complete_ns"]

    # ---- path: accepted / denied (gate or webhook) / 409
    decision = gate.get("decision")
    dispatched = dl.get("dispatched") is True
    da = audit_summary([])
    wh = None
    if not dispatched:
        if decision == "UNKNOWN_COHERENCE":
            return done("UNKNOWN", "gate_unknown_coherence")
        if decision != "ABORT_REPLAN":
            return done("UNKNOWN", "not_dispatched_without_abort(%s)" % decision)
        path, decider = "deny", "gate"
    else:
        if decision in ("UNKNOWN_COHERENCE", "ABORT_REPLAN"):
            flags.append("dispatched_after_%s" % decision)
        hs = dl.get("http_status")
        if dl.get("audit_id"):
            da = audit_summary(audit_idx.get(str(dl.get("audit_id")), []))
        else:
            flags.append("delete_audit_id_missing")
        row["delete_audit_present"] = int(da["present"])
        if da["present"]:
            if da["conflicts"]:
                return done("UNKNOWN", "delete_audit_conflict(%s)" % ",".join(da["conflicts"]))
            if da["code"] is not None and hs is not None and int(da["code"]) != int(hs):
                return done("UNKNOWN", "delete_audit_status_mismatch(audit=%s,trial=%s)" % (da["code"], hs))
            if da["verb"] and da["verb"] != "delete":
                return done("UNKNOWN", "delete_audit_verb_mismatch(%s)" % da["verb"])
            if not da["has_response_complete"]:
                flags.append("delete_audit_no_ResponseComplete")
        else:
            flags.append("audit_missing_delete")
        # webhook log join: by request uid when the trial carries one, else by pod_uid == expected_uid
        wuid = t.get("webhook_request_uid")
        ents = []
        if wuid:
            ents = webhook_idx.get(str(wuid), [])
        elif dl.get("expected_uid"):
            ents = list(getattr(webhook_idx, "by_pod", {}).get(str(dl.get("expected_uid")), []))
            if ents:
                flags.append("webhook_joined_by_pod_uid")
        # Governing webhook evaluation (plan v1.1 (f), wording corrected in v1.2). kube-apiserver can
        # invoke the webhook more than once for ONE Pod DELETE request (observed: one audit event, one
        # HTTP status, several AdmissionReview uids). Calls on the already-terminating Pod are logged as
        # "not evaluated" (pod_terminating) and ignored here. The governing evaluation is chosen by the
        # outcome: accepted DELETE -> earliest evaluated allow; denied DELETE -> last evaluated deny.
        # Multiple evaluations are flagged, never guessed away. (Where the extra calls originate is
        # established from kube-apiserver logs in the rerun's separate mechanism report, not assumed here.)
        evaluated = sorted([e for e in ents if e.get("pod_terminating") is not True],
                           key=lambda e: (e.get("received_wall_ns") or 0))
        if ents:
            if arm != "A3_dcji_webhook":
                flags.append("webhook_entry_for_non_webhook_arm")
            if len(ents) > len(evaluated):
                flags.append("webhook_terminating_calls(%d)" % (len(ents) - len(evaluated)))
            if len(evaluated) > 1:
                flags.append("webhook_multiple_evaluations(%d)" % len(evaluated))
                if len({e.get("decision") for e in evaluated}) > 1:
                    flags.append("webhook_evaluations_differ")
        if arm == "A3_dcji_webhook" and not evaluated:
            flags.append("webhook_log_missing")
        if hs is None:
            return done("UNKNOWN", "delete_no_http_status")
        hs = int(hs)

        def _use(entry):
            if entry.get("schema") != WEBHOOK_SCHEMA:
                flags.append("webhook_schema_mismatch")
            row["webhook_decision"] = entry.get("decision") or ""
            row["webhook_request_uid"] = entry.get("request_uid") or ""
            if entry.get("pod_uid") and dl.get("expected_uid") and entry.get("pod_uid") != dl.get("expected_uid"):
                flags.append("webhook_pod_uid_differs_from_expected")
            return entry

        if hs == 409:
            return done("UID_BLOCKED", "uid_precondition_conflict_409")
        if hs in (200, 202):
            allows = [e for e in evaluated if e.get("decision") == "allow"]
            if evaluated and not allows:
                wh = _use(evaluated[-1])
                return done("UNKNOWN", "webhook_deny_but_delete_accepted")
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
                return done("UNKNOWN", "webhook_allow_but_delete_denied_%d" % hs)
            elif arm == "A3_dcji_webhook":
                return done("UNKNOWN", "delete_denied_%d_webhook_log_missing" % hs)
            else:
                return done("UNKNOWN", "delete_denied_%d_unattributed" % hs)
        else:
            return done("UNKNOWN", "delete_http_%d" % hs)
    row["decider"] = decider
    if gate.get("ssar_allowed") is False:
        flags.append("ssar_denied")

    # ---- uid_rebind_control positive control
    if cond == "uid_rebind_control":
        if path == "deny":
            return done("UNKNOWN", "uid_rebind_denied_not_oracle_evaluable(%s)" % decider)
        row["deleted_uid"] = da["uid"] or ""
        if eff.get("verified") is not True:
            flags.append("effect_not_verified")
        if not da["uid"]:
            return done("UNKNOWN", "deleted_uid_unavailable")
        if da["uid"] != dl.get("expected_uid"):
            return done("INVALID_EXECUTED", "uid_rebind_wrong_object_deleted")
        flags.append("uid_rebind_not_realized")
        return done("VALID_EXECUTED", "uid_rebind_expected_object_deleted")

    # ---- no_write
    if cond == "no_write":
        if path == "accept":
            if eff.get("verified") is not True:
                flags.append("effect_not_verified")
            return done("VALID_EXECUTED", "no_write_valid_state")
        return done("FALSE_ABORT", "denied_valid_state_no_write(%s)" % decider)

    # ---- async conditions
    if path == "accept":
        if eff.get("verified") is not True:
            flags.append("effect_not_verified")
        d_rv, d_src, d_err = resolve_rv(da["rv"], dl.get("deleted_object_resource_version"))
        # plan v1.2: pod-event (watch) evidence for the deletion revision. Fallback ONLY when both
        # pre-registered sources are missing (running Pod removed by the kubelet's update-triggered
        # delete while the responder's DELETE was still in its storage phase); a cross-check otherwise.
        pw = (pod_idx or {}).get(str(dl.get("expected_uid") or "")) if pod_idx is not None else None
        if pw is not None:
            row["pod_watch_deletion_accepted_rv"] = pw["accepted"] or ""
            if len(pw["deleted"]) == 1:
                row["pod_watch_deleted_rv"] = pw["deleted"][0] or ""
        if pod_idx is not None:
            pw_del = [rv_int(x) for x in (pw or {}).get("deleted", [])]
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
        row["delete_rv"] = "" if d_rv is None else d_rv
        row["delete_rv_source"] = d_src
        # primary: commit order
        if w_err:
            pc, pr = "UNKNOWN", "writer_" + w_err
        elif d_err:
            pc, pr = "UNKNOWN", "delete_" + d_err
        elif w_rv == d_rv:
            pc, pr = "UNKNOWN", "rv_tie"
        else:
            row["rv_margin"] = d_rv - w_rv
            before = w_rv < d_rv
            if before and not v_post:
                pc, pr = "INVALID_EXECUTED", "write_committed_before_delete(rv)"
            elif before:
                pc, pr = "VALID_EXECUTED", "write_before_delete_but_not_invalidating(rv)"
            else:
                pc, pr = "VALID_EXECUTED", "write_committed_after_delete(rv)"
        wc = wa["complete_ns"]
        # secondary (plan v1.2): writer ResponseComplete vs DELETE ResponseComplete (commit proxy)
        dc = da["complete_ns"]
        row["ref_ts_ns"] = "" if dc is None else dc
        row["ref_ts_kind"] = "delete_ResponseComplete_stageTimestamp"
        if wc is None:
            sc, sr = "UNKNOWN", "time_rule_writer_audit_missing"
        elif dc is None:
            sc, sr = "UNKNOWN", "time_rule_delete_audit_missing"
        elif wc == dc:
            row["time_margin_us"] = 0.0
            sc, sr = "UNKNOWN", "time_tie"
        else:
            row["time_margin_us"] = (dc - wc) / 1e3
            if wc < dc and not v_post:
                sc, sr = "INVALID_EXECUTED", "write_complete_before_delete_complete(time)"
            elif wc < dc:
                sc, sr = "VALID_EXECUTED", "write_before_delete_but_not_invalidating(time)"
            else:
                sc, sr = "VALID_EXECUTED", "write_complete_not_before_delete_complete(time)"
        # tertiary (plan v1/v1.1 secondary, reported for transparency): vs DELETE requestReceivedTimestamp
        dr = da["received_ns"]
        row["ref_ts_tertiary_ns"] = "" if dr is None else dr
        if wc is None:
            tc, tr = "UNKNOWN", "time_rule_writer_audit_missing"
        elif dr is None:
            tc, tr = "UNKNOWN", "time_rule_delete_audit_missing"
        elif wc == dr:
            row["time_margin_tertiary_us"] = 0.0
            tc, tr = "UNKNOWN", "time_tie"
        else:
            row["time_margin_tertiary_us"] = (dr - wc) / 1e3
            if wc < dr and not v_post:
                tc, tr = "INVALID_EXECUTED", "write_complete_before_delete_received(time)"
            elif wc < dr:
                tc, tr = "VALID_EXECUTED", "write_before_delete_but_not_invalidating(time)"
            else:
                tc, tr = "VALID_EXECUTED", "write_complete_not_before_delete_received(time)"
        return done(pc, pr, sc, sr, tc, tr)

    # denied (gate abort or webhook deny) with a writer
    reads = (gate.get("reads") if decider == "gate" else (wh or {}).get("reads")) or []
    match = [r for r in reads if isinstance(r, dict) and norm_object(r.get("object")) == wobj]
    if not match:
        return done("UNKNOWN", "decider_did_not_read_written_object(%s)" % decider)
    rd = match[-1]
    row["decider_read_rv"] = "" if rd.get("resource_version") is None else rd.get("resource_version")
    row["decider_read_value"] = "" if rd.get("value") is None else rd.get("value")
    rr = rv_int(rd.get("resource_version"))
    rb = parse_bool(rd.get("value"))
    if w_err:
        pc, pr = "UNKNOWN", "writer_" + w_err
    elif rr is None:
        pc, pr = "UNKNOWN", "decider_read_rv_missing_or_non_numeric"
    else:
        seen = rr >= w_rv
        expected_val = new_b if seen else state[wobj]
        if rb is not None and rb != expected_val:
            pc, pr = "UNKNOWN", "decider_read_inconsistent(rv_vs_value)"
        elif seen and not v_post:
            pc, pr = "CORRECT_ABORT", "denier_read_post_write_invalid_state(rv)"
        else:
            pc, pr = "FALSE_ABORT", ("denier_read_pre_write_valid_state(rv)" if not seen
                                     else "denier_read_post_write_but_valid(rv)")
    # secondary: time order vs denier read time
    wc = wa["complete_ns"]
    ref, kind = None, ""
    if decider == "gate":
        te_m, te_w, rs = _i(gate.get("t_end_mono_ns")), _i(gate.get("t_end_wall_ns")), _i(rd.get("t_send_mono_ns"))
        kind = "gate_read_send_wall(mapped)"
        if None not in (te_m, te_w, rs):
            ref = te_w - (te_m - rs)
    else:
        # E38v2 integration: the webhook logs per-read wall stamps; use the send time of its read of
        # the written object. Older logs without per-read stamps fall back to received_wall_ns
        # (conservative: earlier than any read) and are flagged.
        rs_w = _i(rd.get("t_send_wall_ns"))
        if rs_w is not None:
            kind = "webhook_read_send_wall_ns"
            ref = rs_w
        else:
            kind = "webhook_received_wall_ns(fallback)"
            ref = _i((wh or {}).get("received_wall_ns"))
            flags.append("webhook_read_time_missing")
    row["ref_ts_ns"] = "" if ref is None else ref
    row["ref_ts_kind"] = kind
    if wc is None:
        sc, sr = "UNKNOWN", "time_rule_writer_audit_missing"
    elif ref is None:
        sc, sr = "UNKNOWN", "time_rule_denier_time_missing"
    elif wc == ref:
        row["time_margin_us"] = 0.0
        sc, sr = "UNKNOWN", "time_tie"
    else:
        row["time_margin_us"] = (ref - wc) / 1e3
        seen_t = wc < ref
        if seen_t and not v_post:
            sc, sr = "CORRECT_ABORT", "write_complete_before_denier_read(time)"
        else:
            sc, sr = "FALSE_ABORT", "write_not_complete_before_denier_read(time)"
    return done(pc, pr, sc, sr)


def run_oracle(trials, audit_events, webhook_entries, pod_events=None):
    ai = index_audit(audit_events)
    wi = index_webhook(webhook_entries)
    pi = index_pod_events(pod_events)
    return [classify_trial(t, ai, wi, pi) for t in trials]


def load_inputs(raw_path, webhook_path, audit_path, pod_events_path=None):
    trials, e1 = load_jsonl(raw_path)
    wh, e2 = load_jsonl(webhook_path)
    au, e3 = load_jsonl(audit_path)
    if pod_events_path is None:
        return trials, wh, au, {"raw": e1, "webhook": e2, "audit": e3}
    pe, e4 = load_jsonl(pod_events_path)
    return trials, wh, au, {"raw": e1, "webhook": e2, "audit": e3, "pod_events": e4, "_pod_events": pe}


def write_csv(rows, path):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_COLS, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in CSV_COLS})


def main(argv=None):
    ap = argparse.ArgumentParser(description="E38v2 offline ground-truth oracle")
    ap.add_argument("--raw", required=True)
    ap.add_argument("--webhook", required=True, help="webhook_log.jsonl (may be empty)")
    ap.add_argument("--audit", required=True)
    ap.add_argument("--out", required=True, help="output CSV")
    ap.add_argument("--pod-events", help="pod_events.jsonl (E38v2 RERUN, plan v1.2); optional")
    a = ap.parse_args(argv)
    trials, wh, au, errs = load_inputs(a.raw, a.webhook, a.audit, a.pod_events)
    pe = errs.pop("_pod_events", None)
    for k, v in errs.items():
        if v:
            print("WARNING: %d unparseable lines in %s input (first: %s)" % (len(v), k, v[0]), file=sys.stderr)
    rows = run_oracle(trials, au, wh, pe)
    write_csv(rows, a.out)
    c = Counter(r["primary_class"] for r in rows)
    print("oracle %s: %d trials; primary classes: %s" % (ORACLE_VERSION, len(rows), dict(sorted(c.items()))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
