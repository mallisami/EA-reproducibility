#!/usr/bin/env python3
"""E39-E alternative response action: CREATE a NetworkPolicy that isolates the target Pod.

Policy shape (fixed): podSelector.matchLabels = {e40-trial: <per-trial value>} (the target Pod is
created with that label), policyTypes [Ingress, Egress], no ingress/egress rules -> all traffic to and
from the selected Pod is denied once a NetworkPolicy-enforcing CNI programs it. E39-E measures the
API-level effect only (object committed); dataplane enforcement is not measured.

A3 (webhook) trials label the NetworkPolicy with e40-webhook=enforce so the VWC objectSelector
(applied to the NEW object for CREATE) routes it to the validating webhook.

CREATE has no preconditions -> commit-bound arms (A4) are structurally not applicable to this action.

Record fragment "netpol" (protocol/E39_RECORD_SCHEMA.json):
  {name, t_send_mono_ns, t_resp_mono_ns, http_status, audit_id, created_resource_version}
  plus optional diagnostics: t_send_wall_ns, reason, created_uid.
"""
from __future__ import annotations

import hashlib
import re
import time

from e39_kube import NETPOL_PREFIX, TRIAL_LABEL_KEY, WEBHOOK_LABEL, APIError, mono_ns

ACTION_TYPE = 'netpol_create'
_LABEL_RE = re.compile(r'^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$')


def trial_label_value(run_id, trial_id):
    """DNS-label-safe per-trial selector value (<= 63 chars), stable for (run_id, trial_id)."""
    raw = ('%s-t%s' % (run_id, trial_id)).lower()
    val = re.sub(r'[^a-z0-9-]', '-', raw).strip('-')
    if len(val) > 63 or not _LABEL_RE.match(val):
        val = 't%s-%s' % (trial_id, hashlib.sha256(raw.encode()).hexdigest()[:16])
    return val[:63].strip('-')


def netpol_name(trial_id, run_id=None):
    suffix = str(trial_id) if run_id is None else trial_label_value(run_id, trial_id)
    name = (NETPOL_PREFIX + suffix)[:63].rstrip('-')
    return name


def target_pod_labels(label_value):
    """Extra label(s) the target Pod must carry so the isolation policy selects it."""
    return {TRIAL_LABEL_KEY: label_value}


def build_isolation_netpol(namespace, name, label_value, webhook=False):
    labels = {'e40-managed': 'true', TRIAL_LABEL_KEY: label_value}
    if webhook:
        labels[WEBHOOK_LABEL[0]] = WEBHOOK_LABEL[1]
    return {'apiVersion': 'networking.k8s.io/v1', 'kind': 'NetworkPolicy',
            'metadata': {'name': name, 'namespace': namespace, 'labels': labels},
            'spec': {'podSelector': {'matchLabels': {TRIAL_LABEL_KEY: label_value}},
                     'policyTypes': ['Ingress', 'Egress']}}


def create_isolation_netpol(responder, name, label_value, webhook=False):
    """Send the CREATE once (no retry). Returns the schema 'netpol' fragment."""
    body = build_isolation_netpol(responder.ns, name, label_value, webhook=webhook)
    frag = {'name': name, 't_send_mono_ns': None, 't_resp_mono_ns': None, 'http_status': None,
            'audit_id': None, 'created_resource_version': None, 't_send_wall_ns': None, 'reason': None,
            'created_uid': None}
    frag['t_send_wall_ns'] = time.time_ns()
    frag['t_send_mono_ns'] = mono_ns()
    try:
        status, h, obj = responder.create_netpol(body)
    except APIError as exc:
        frag['reason'] = 'transport: %s' % exc
        return frag
    frag['t_resp_mono_ns'] = mono_ns()
    frag['http_status'] = status
    frag['audit_id'] = h.get('Audit-Id')
    md = (obj or {}).get('metadata') or {}
    if status in (200, 201):
        frag['created_resource_version'] = md.get('resourceVersion')
        frag['created_uid'] = md.get('uid')
    else:
        frag['reason'] = (obj or {}).get('reason')
    return frag


def netpol_effect(client, name):
    """API-level effect check after the action: does the NetworkPolicy object exist?"""
    t = mono_ns()
    status, _, obj = client.get_netpol(name)
    return {'exists': status == 200, 't_checked_mono_ns': t,
            'resource_version': ((obj or {}).get('metadata') or {}).get('resourceVersion') if status == 200 else None}


def cleanup_isolation_netpol(reset_client, name):
    """Reset identity removes the trial's isolation policy (404 = already absent)."""
    status, _, _ = reset_client.delete_netpol(name)
    if status not in (200, 202, 404):
        raise APIError('netpol cleanup %s HTTP %s' % (name, status))
    return status
