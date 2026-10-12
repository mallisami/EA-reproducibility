#!/usr/bin/env python3
"""E39 arms A4d / A4m: commit-bound validity binding for the Pod DELETE response action.

A4d "direct":   the validity records (evidence a..f, scope, app-policy) live as annotations on the
                target Pod (e38.evidence/a..f, e38.scope, e38.app-policy); writers patch the Pod.
A4m "mirrored": the records live in ConfigMaps (as E38v2); mirror_controller.py copies them onto the
                Pod annotations; gate + DELETE are identical to A4d.

Gate  = ONE Pod GET -> evaluate  J=(a AND b) OR (c AND d) OR (e AND f)  AND scope AND app-policy
        (strict "true"/"false" parsing, fail closed) and check uid == decision uid.
DELETE (only if the gate says EXECUTE) carries preconditions {uid, resourceVersion = RV of the gate
        read}. Any commit to the Pod after the gate read (annotation PATCH by a writer or by the mirror,
        a kubelet status update, a replacement Pod) changes the RV -> apiserver answers 409 Conflict.
        A 409 is recorded (conflict_kind 'resource_version' | 'uid') and NOT retried (E39-A policy).

Record fragments produced here follow protocol/E39_RECORD_SCHEMA.json:
  gate (e38v2 gate shape), delete (e38v2 delete shape), bound_state, delete_preconditions, mirror.
The runner logs raw facts only; CORRECT_ABORT / FALSE_ABORT etc. are assigned by the offline oracle.
"""
from __future__ import annotations

import time

from e39_kube import (ANNOTATION_KEYS, ANN_TO_CM, APP_ANN, CM_TO_ANN, CM_VALUE_KEY, EVIDENCE_ANN,
                      EVIDENCE_FLAGS, POLICY_CMS, SCOPE_ANN, APIError, mono_ns)

ARM_DIRECT = 'A4d_commit_direct'
ARM_MIRROR = 'A4m_commit_mirror'
COMMIT_ARMS = (ARM_DIRECT, ARM_MIRROR)
DISJUNCTS = (('a', 'b'), ('c', 'd'), ('e', 'f'))


# --------------------------------------------------------------------------- key helpers
def to_annotation_key(key):
    """Accept an annotation key, a ConfigMap name, a flag letter, 'scope' or 'app_policy'."""
    if key in ANNOTATION_KEYS:
        return key
    if key in CM_TO_ANN:
        return CM_TO_ANN[key]
    if key in EVIDENCE_FLAGS:
        return EVIDENCE_ANN[key]
    if key in ('scope', 'response_scope'):
        return SCOPE_ANN
    if key in ('app_policy', 'app-policy', 'application_policy'):
        return APP_ANN
    raise KeyError('unknown validity record %r' % (key,))


def fixture_annotations(overrides=None):
    """All eight validity annotations 'true', then overrides (any key form) applied as strings."""
    ann = {k: 'true' for k in ANNOTATION_KEYS}
    for k, v in (overrides or {}).items():
        ann[to_annotation_key(k)] = v if isinstance(v, str) else ('true' if v else 'false')
    return ann


def annotations_from_configmaps(cm_objs):
    """Map {cm_name: ConfigMap object} -> {annotation key: value} (what the mirror should converge to)."""
    out = {}
    for name in POLICY_CMS:
        obj = cm_objs.get(name)
        data = (obj or {}).get('data') or {}
        v = data.get(CM_VALUE_KEY[name])
        if isinstance(v, str):
            out[CM_TO_ANN[name]] = v
    return out


# --------------------------------------------------------------------------- policy (pure)
def _parse(v):
    if isinstance(v, str) and v == 'true':
        return True
    if isinstance(v, str) and v == 'false':
        return False
    return None


def evaluate_annotations(annotations):
    """Same policy and wording as webhook/policy.evaluate, over Pod annotations. Returns (allow, reason)."""
    if not isinstance(annotations, dict):
        return False, 'deny: annotations not a mapping'
    parsed, bad = {}, []
    for key in ANNOTATION_KEYS:
        raw = annotations.get(key)
        b = _parse(raw)
        if b is None:
            bad.append('%s=%s' % (key, '<missing>' if raw is None else repr(raw)[:40]))
        parsed[key] = b
    if bad:
        return False, 'deny: missing_or_unparseable ' + ','.join(bad)
    ev = {k: parsed[EVIDENCE_ANN[k]] for k in EVIDENCE_FLAGS}
    satisfied = [x + '&' + y for (x, y) in DISJUNCTS if ev[x] and ev[y]]
    j, scope_ok, app_ok = bool(satisfied), parsed[SCOPE_ANN], parsed[APP_ANN]
    if j and scope_ok and app_ok:
        return True, 'allow: J via %s; scope_allowed; app_policy_allowed' % '|'.join(satisfied)
    fails = []
    if not j:
        fails.append('J_false')
    if not scope_ok:
        fails.append('scope_not_allowed')
    if not app_ok:
        fails.append('app_policy_not_allowed')
    return False, 'deny: ' + ';'.join(fails)


# --------------------------------------------------------------------------- record fragments
def bound_state_fragment(pod_obj):
    """schema bound_state: {pod_rv_at_gate: str, annotations_at_gate: {key: value}} (validity keys only)."""
    if not isinstance(pod_obj, dict):
        return None
    md = pod_obj.get('metadata') or {}
    anns = md.get('annotations') or {}
    return {'pod_rv_at_gate': str(md.get('resourceVersion') or ''),
            'annotations_at_gate': {k: anns[k] for k in ANNOTATION_KEYS if isinstance(anns.get(k), str)}}


def delete_preconditions_fragment(uid, resource_version):
    """schema delete_preconditions: {uid: str|null, resource_version: str|null}."""
    return {'uid': uid or None, 'resource_version': (str(resource_version) if resource_version else None)}


def mirror_fragment(source_object, source_commit_rv, source_commit_wall_ns, mirror_entry):
    """schema mirror (A4m): combine the WRITER's commit facts for one ConfigMap write with the mirror
    controller's log entry that propagated it (see find_mirror_entry). mirror_entry None -> the mirror
    fields are null (mirror had not propagated the write when the record was closed)."""
    m = mirror_entry or {}
    return {'source_object': source_object,
            'source_commit_rv': None if source_commit_rv is None else str(source_commit_rv),
            'source_commit_wall_ns': source_commit_wall_ns,
            'mirror_commit_rv': m.get('mirror_commit_rv'),
            'mirror_commit_wall_ns': m.get('mirror_commit_wall_ns'),
            'mirror_audit_id': m.get('mirror_audit_id')}


def find_mirror_entry(mirror_log, source_object, source_rv):
    """First successful mirror 'update' entry that propagated source_object at RV >= source_rv
    (a later source RV also carries the value of an earlier write when events coalesce)."""
    try:
        want = int(source_rv)
    except (TypeError, ValueError):
        return None
    best = None
    for e in mirror_log or ():
        if e.get('kind') != 'update' or e.get('source_object') != source_object:
            continue
        if e.get('http_status') != 200 or e.get('skipped'):
            continue
        try:
            srv = int(e.get('source_commit_rv'))
        except (TypeError, ValueError):
            continue
        if srv >= want and (best is None or int(best['source_commit_rv']) > srv):
            best = e
    return best


def mirror_lag(fragment):
    """Derived quantities for a mirror fragment: rv_gap (commits between source and mirror commit),
    wall_lag_ms (mirror commit wall time minus writer-observed source commit wall time; both are
    response-receipt stamps: the audit log gives the exact stageTimestamps offline)."""
    if not fragment or fragment.get('mirror_commit_rv') is None:
        return {'rv_gap': None, 'wall_lag_ms': None}
    try:
        gap = int(fragment['mirror_commit_rv']) - int(fragment['source_commit_rv'])
    except (TypeError, ValueError):
        gap = None
    lag = None
    if fragment.get('mirror_commit_wall_ns') is not None and fragment.get('source_commit_wall_ns') is not None:
        lag = (fragment['mirror_commit_wall_ns'] - fragment['source_commit_wall_ns']) / 1e6
    return {'rv_gap': gap, 'wall_lag_ms': lag}


def _pod_state(obj):
    if not isinstance(obj, dict):
        return None
    md = obj.get('metadata') or {}
    st = obj.get('status') or {}
    ready = None
    for c in st.get('conditions') or []:
        if c.get('type') == 'Ready':
            ready = c.get('status') == 'True'
    cstate = None
    for cs in st.get('containerStatuses') or []:
        s = cs.get('state') or {}
        cstate = 'running' if 'running' in s else ('terminated' if 'terminated' in s else
                                                   ('waiting' if 'waiting' in s else None))
    return {'phase': st.get('phase'), 'ready': ready, 'node': (obj.get('spec') or {}).get('nodeName'),
            'resource_version': md.get('resourceVersion'), 'uid': md.get('uid'),
            'terminating': bool(md.get('deletionTimestamp')), 'container_state': cstate}


# --------------------------------------------------------------------------- gate + DELETE
def commit_gate(responder, decision_uid):
    """ONE Pod GET, then pure evaluation (outside the timed read). Returns
    {'gate': <e38v2 gate shape>, 'bound_state', 'allowed', 'reason', 'uid', 'resource_version', 'pod'}.
    gate.decision is 'EXECUTE' only if the policy holds on the annotations AND uid == decision_uid."""
    t_start = mono_ns()
    t_send = mono_ns()
    obj, _ = responder.get_pod()
    t_recv = mono_ns()
    t_end = mono_ns()
    t_end_wall = time.time_ns()
    md = (obj or {}).get('metadata') or {}
    uid, rv = md.get('uid'), md.get('resourceVersion')
    read = {'object': responder.pod_name, 'resource_version': rv, 'value': uid,
            't_send_mono_ns': t_send, 't_recv_mono_ns': t_recv}
    if obj is None:
        allowed, reason = False, 'deny: pod_missing'
    elif uid != decision_uid:
        allowed, reason = False, 'deny: uid_changed (%s != decision %s)' % (uid, decision_uid)
    elif md.get('deletionTimestamp'):
        allowed, reason = False, 'deny: pod_terminating'
    else:
        allowed, reason = evaluate_annotations(md.get('annotations') or {})
    gate = {'decision': 'EXECUTE' if allowed else 'ABORT_REPLAN', 't_start_mono_ns': t_start,
            't_end_mono_ns': t_end, 't_end_wall_ns': t_end_wall, 'reads': [read], 'ssar_allowed': None,
            'pod_state': _pod_state(obj)}
    return {'gate': gate, 'bound_state': bound_state_fragment(obj), 'allowed': allowed, 'reason': reason,
            'uid': uid, 'resource_version': rv, 'pod': obj}


def classify_conflict(status, obj):
    """409 body -> 'resource_version' | 'uid' | 'other' ; non-409 -> None."""
    if status != 409:
        return None
    msg = ((obj or {}).get('message') or '')
    # both message formats: older 'Precondition failed: ResourceVersion in precondition: ...' and
    # kube-apiserver v1.37 'the ResourceVersion in the precondition (p) does not match ... in record (c)'
    if 'ResourceVersion in precondition' in msg or 'ResourceVersion in the precondition' in msg:
        return 'resource_version'
    if 'UID in precondition' in msg or 'UID in the precondition' in msg:
        return 'uid'
    return 'other'


def commit_delete(responder, uid, resource_version):
    """DELETE with preconditions {uid, resourceVersion}. No retry on 409. Returns
    {'delete': <e38v2 delete shape>, 'delete_preconditions', 'conflict_kind', 'outcome'}."""
    pre = delete_preconditions_fragment(uid, resource_version)
    t_send = mono_ns()
    t_send_wall = time.time_ns()
    try:
        status, h, obj = responder.delete_pod(uid, resource_version=resource_version)
    except APIError as exc:
        return {'delete': {'dispatched': True, 't_send_mono_ns': t_send, 't_send_wall_ns': t_send_wall,
                           't_resp_mono_ns': None, 'http_status': None, 'reason': 'transport: %s' % exc,
                           'audit_id': None, 'expected_uid': uid or '',
                           'deleted_object_resource_version': None},
                'delete_preconditions': pre, 'conflict_kind': None, 'outcome': 'ERROR'}
    t_resp = mono_ns()
    reason = (obj or {}).get('reason') if status != 200 else None
    deleted_rv = ((obj or {}).get('metadata') or {}).get('resourceVersion') if status in (200, 202) else None
    ck = classify_conflict(status, obj)
    outcome = {200: 'EXECUTED', 202: 'EXECUTED', 409: 'CONFLICT_409', 403: 'DENIED_403',
               404: 'NOT_FOUND'}.get(status, 'ERROR')
    # E39 oracle input R4: the FULL apiserver Status.message (409: "Precondition failed: ResourceVersion in
    # precondition: <p>, ResourceVersion in object meta: <current>") is needed for 409 attribution.
    msg = (obj or {}).get('message') if status not in (200, 202) and isinstance(obj, dict) else None
    return {'delete': {'dispatched': True, 't_send_mono_ns': t_send, 't_send_wall_ns': t_send_wall,
                       't_resp_mono_ns': t_resp, 'http_status': status, 'reason': reason,
                       'audit_id': h.get('Audit-Id'), 'expected_uid': uid or '',
                       'deleted_object_resource_version': deleted_rv, 'response_message': msg},
            'delete_preconditions': pre, 'conflict_kind': ck, 'outcome': outcome}


def run_commit_bound(responder, arm, decision_uid, after_gate=None):
    """Full A4d/A4m response: gate, then (if EXECUTE) the precondition DELETE.

    after_gate: optional callable(gate_result) invoked between gate and DELETE (tests / writer arming;
    the runner's writer normally runs concurrently instead).
    Returns a dict of record fragments: gate, bound_state, delete_preconditions, delete,
    conflict_kind, outcome, gate_reason. mirror is filled by the caller for A4m (mirror_fragment).
    """
    if arm not in COMMIT_ARMS:
        raise ValueError('not a commit-bound arm: %r' % (arm,))
    g = commit_gate(responder, decision_uid)
    if after_gate is not None:
        after_gate(g)
    if g['gate']['decision'] != 'EXECUTE':
        return {'gate': g['gate'], 'bound_state': g['bound_state'],
                'delete_preconditions': delete_preconditions_fragment(decision_uid, g['resource_version']),
                'delete': {'dispatched': False, 't_send_mono_ns': None, 't_send_wall_ns': None,
                           't_resp_mono_ns': None, 'http_status': None, 'reason': None, 'audit_id': None,
                           'expected_uid': decision_uid or '', 'deleted_object_resource_version': None},
                'conflict_kind': None, 'outcome': 'GATE_ABORT', 'gate_reason': g['reason']}
    d = commit_delete(responder, decision_uid, g['resource_version'])
    return {'gate': g['gate'], 'bound_state': g['bound_state'], 'delete_preconditions': d['delete_preconditions'],
            'delete': d['delete'], 'conflict_kind': d['conflict_kind'], 'outcome': d['outcome'],
            'gate_reason': g['reason']}


# --------------------------------------------------------------------------- writers / fixture (A4d)
def write_annotation(writer, key, value, pod_uid=None, intended_release_mono_ns=None):
    """A4d writer action: merge-PATCH one validity annotation on the target Pod (optionally with a uid
    precondition so a replacement Pod is never written). Returns an e38v2 writer-shaped record
    (object = annotation key)."""
    k = to_annotation_key(key)
    rel = mono_ns()
    rec = {'object': k, 'new_value': str(value),
           'intended_release_mono_ns': intended_release_mono_ns if intended_release_mono_ns is not None else rel,
           'actual_release_mono_ns': rel, 't_resp_mono_ns': None, 'http_status': None,
           'resulting_resource_version': None, 'audit_id': None}
    try:
        status, h, obj = writer.patch_pod_annotations({k: str(value)}, uid=pod_uid)
    except APIError as exc:
        rec['error'] = repr(exc)
        return rec
    rec['t_resp_mono_ns'] = mono_ns()
    rec['http_status'] = status
    rec['resulting_resource_version'] = ((obj or {}).get('metadata') or {}).get('resourceVersion') \
        if status == 200 else None
    rec['audit_id'] = h.get('Audit-Id')
    return rec


def reset_pod_annotations(reset_client, overrides=None, pod_uid=None):
    """Restore all eight annotations on the CURRENT target Pod (reset identity). Returns (status, rv)."""
    status, _, obj = reset_client.patch_pod_annotations(fixture_annotations(overrides), uid=pod_uid)
    return status, ((obj or {}).get('metadata') or {}).get('resourceVersion')


def mirror_converged(client):
    """Read the eight ConfigMaps and the Pod; True iff every Pod annotation equals its ConfigMap value.
    Returns (converged, diff {annotation key: (cm_value, pod_value)}). For A4m fixture readiness."""
    cms = {}
    for name in POLICY_CMS:
        status, _, obj = client.get_configmap(name)
        if status != 200:
            raise APIError('GET %s HTTP %s' % (name, status))
        cms[name] = obj
    want = annotations_from_configmaps(cms)
    pod, _ = client.get_pod()
    have = (((pod or {}).get('metadata') or {}).get('annotations') or {})
    diff = {k: (v, have.get(k)) for k, v in want.items() if have.get(k) != v}
    return (pod is not None and not diff), diff


def wait_mirror_converged(client, timeout_s=10.0, poll_s=0.01):
    end = time.monotonic() + timeout_s
    polls = 0
    t0 = mono_ns()
    while True:
        ok, diff = mirror_converged(client)
        polls += 1
        if ok:
            return {'converged': True, 'polls': polls, 'wait_ms': (mono_ns() - t0) / 1e6}
        if time.monotonic() > end:
            return {'converged': False, 'polls': polls, 'wait_ms': (mono_ns() - t0) / 1e6,
                    'diff': {k: list(v) for k, v in diff.items()}}
        time.sleep(poll_s)


__all__ = ['ARM_DIRECT', 'ARM_MIRROR', 'COMMIT_ARMS', 'ANN_TO_CM', 'to_annotation_key', 'fixture_annotations',
           'annotations_from_configmaps', 'evaluate_annotations', 'bound_state_fragment',
           'delete_preconditions_fragment', 'mirror_fragment', 'find_mirror_entry', 'mirror_lag',
           'commit_gate', 'commit_delete', 'classify_conflict', 'run_commit_bound', 'write_annotation',
           'reset_pod_annotations', 'mirror_converged', 'wait_mirror_converged']
