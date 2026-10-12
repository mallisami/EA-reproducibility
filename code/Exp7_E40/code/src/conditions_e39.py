#!/usr/bin/env python3
"""E39-G condition setup: live out-of-model mutation vs in-model control.

  out_of_model_bdf : evidence b, d, f set to "false" BEFORE the gate. DCJI's frozen certificate
                     {a,c,e,uid,scope} does not include b/d/f (the model says they cannot change;
                     their values come from the planning-time snapshot), so a DCJI gate still sees
                     a, c, e true and EXECUTEs although J = (a&b)|(c&d)|(e&f) is false -> invalid.
                     FullRefresh reads all eight records and aborts.
  in_model_ace     : evidence a, c, e set to "false" before the gate (control): J false as well,
                     but DCJI reads a/c/e and aborts.
Both conditions make the policy INVALID before the gate; scope and app-policy stay "true".

Storage:
  'configmap'  : PATCH the evidence-<x> ConfigMaps (A1/A2/A3 and A4m -- for A4m the mirror copies them)
  'annotation' : ONE merge-PATCH of the target Pod annotations e38.evidence/<x> (A4d source of truth)
The setup is done by the RESET identity (fixture preparation, not a writer race), after the fixture
reset AND after the responder's planning snapshot (the cached values DCJI trusts for uncertified
records) and before the gate; the write records carry Audit-IDs and RVs so the oracle sees them.
If the snapshot were taken after the cut, DCJI would see b/d/f false in its cache and the
out-of-model case would not be exercised.
"""
from __future__ import annotations

from e39_kube import (ANNOTATION_KEYS, CM_TO_ANN, EVIDENCE_ANN, OBJ_APP, OBJ_SCOPE, POLICY_CMS, APIError,
                      mono_ns)

OUT_OF_MODEL = 'out_of_model_bdf'
IN_MODEL = 'in_model_ace'
G_CONDITIONS = (OUT_OF_MODEL, IN_MODEL)
CUT_FLAGS = {OUT_OF_MODEL: ('b', 'd', 'f'), IN_MODEL: ('a', 'c', 'e')}
STORAGE_CONFIGMAP = 'configmap'
STORAGE_ANNOTATION = 'annotation'
STORAGES = (STORAGE_CONFIGMAP, STORAGE_ANNOTATION)


def _check(condition, storage=STORAGE_CONFIGMAP):
    if condition not in CUT_FLAGS:
        raise ValueError('unknown E39-G condition %r' % (condition,))
    if storage not in STORAGES:
        raise ValueError('unknown storage %r' % (storage,))


def condition_values(condition):
    """Full intended pre-gate state keyed by ConfigMap name (all eight policy records)."""
    _check(condition)
    vals = {name: 'true' for name in POLICY_CMS}
    for k in CUT_FLAGS[condition]:
        vals['evidence-' + k] = 'false'
    return vals


def condition_annotations(condition):
    """Same state keyed by Pod annotation key."""
    return {CM_TO_ANN[n]: v for n, v in condition_values(condition).items()}


def initial_values(condition, storage):
    """For the record's fixture.initial_values (keys = the storage's object names)."""
    _check(condition, storage)
    return condition_values(condition) if storage == STORAGE_CONFIGMAP else condition_annotations(condition)


def policy_valid(values_by_cm):
    """Ground-truth validity over ConfigMap-keyed values (strict 'true')."""
    t = lambda n: values_by_cm.get(n) == 'true'  # noqa: E731
    j = (t('evidence-a') and t('evidence-b')) or (t('evidence-c') and t('evidence-d')) \
        or (t('evidence-e') and t('evidence-f'))
    return bool(j and t(OBJ_SCOPE) and t(OBJ_APP))


def expected_outcome(condition, arm):
    """Design expectation (NOT ground truth): whether the arm's gate is expected to EXECUTE.
    DCJI-based arms re-read only the certificate {a,c,e,uid,scope} (e38v2_model); FullRefresh and the
    commit-bound arms evaluate all eight records."""
    _check(condition)
    dcji_like = arm in ('A1_dcji', 'A3_dcji_webhook', 'A1_dcji_go')
    if condition == OUT_OF_MODEL:
        return 'EXECUTE' if dcji_like else 'ABORT'
    return 'ABORT'


def apply_condition(reset_client, condition, storage, pod_uid=None):
    """Write the cut values before the gate. Returns a list of write records
    {object, new_value, t_send_mono_ns, t_resp_mono_ns, http_status, resulting_resource_version, audit_id}.
    Raises APIError if any write does not return 200."""
    _check(condition, storage)
    out = []
    if storage == STORAGE_CONFIGMAP:
        for k in CUT_FLAGS[condition]:
            name = 'evidence-' + k
            t0 = mono_ns()
            status, h, obj = reset_client.patch_configmap(name, {'value': 'false'})
            out.append({'object': name, 'new_value': 'false', 't_send_mono_ns': t0, 't_resp_mono_ns': mono_ns(),
                        'http_status': status,
                        'resulting_resource_version': ((obj or {}).get('metadata') or {}).get('resourceVersion'),
                        'audit_id': h.get('Audit-Id')})
            if status != 200:
                raise APIError('condition %s: PATCH %s HTTP %s' % (condition, name, status))
    else:
        anns = {EVIDENCE_ANN[k]: 'false' for k in CUT_FLAGS[condition]}
        t0 = mono_ns()
        status, h, obj = reset_client.patch_pod_annotations(anns, uid=pod_uid)
        out.append({'object': 'pod-annotations:' + ','.join(sorted(anns)), 'new_value': 'false',
                    't_send_mono_ns': t0, 't_resp_mono_ns': mono_ns(), 'http_status': status,
                    'resulting_resource_version': ((obj or {}).get('metadata') or {}).get('resourceVersion'),
                    'audit_id': h.get('Audit-Id')})
        if status != 200:
            raise APIError('condition %s: PATCH pod annotations HTTP %s' % (condition, status))
    return out


def observe(client, storage):
    """Read back the eight records from the given storage; returns ConfigMap-keyed values."""
    if storage == STORAGE_CONFIGMAP:
        vals = {}
        for name in POLICY_CMS:
            status, _, obj = client.get_configmap(name)
            if status != 200:
                raise APIError('GET %s HTTP %s' % (name, status))
            data = (obj or {}).get('data') or {}
            vals[name] = data.get('value', data.get('allowed'))
        return vals
    pod, _ = client.get_pod()
    anns = (((pod or {}).get('metadata') or {}).get('annotations') or {})
    return {n: anns.get(CM_TO_ANN[n]) for n in POLICY_CMS}


def verify_condition(client, condition, storage):
    """(ok, observed): ok iff the storage holds exactly the intended pre-gate state."""
    obs = observe(client, storage)
    return obs == condition_values(condition), obs


__all__ = ['OUT_OF_MODEL', 'IN_MODEL', 'G_CONDITIONS', 'CUT_FLAGS', 'STORAGE_CONFIGMAP', 'STORAGE_ANNOTATION',
           'condition_values', 'condition_annotations', 'initial_values', 'policy_valid', 'expected_outcome',
           'apply_condition', 'observe', 'verify_condition', 'ANNOTATION_KEYS']
