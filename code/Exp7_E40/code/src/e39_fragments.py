#!/usr/bin/env python3
"""JSON Schemas (draft-07) for the E39 record fragments produced by arms_commit / action_netpol /
mirror_controller, transcribed from protocol/E39_RECORD_SCHEMA.json (added_fields):

  bound_state          object|null  {pod_rv_at_gate: str, annotations_at_gate: {key: value}}
  delete_preconditions {uid: str|null, resource_version: str|null}
  mirror               object|null  {source_object, source_commit_rv, source_commit_wall_ns,
                                     mirror_commit_rv, mirror_commit_wall_ns, mirror_audit_id}
  netpol               object|null  {name, t_send_mono_ns, t_resp_mono_ns, http_status, audit_id,
                                     created_resource_version}  (+ optional diagnostics)
plus the mirror controller's own JSONL line (e39.mirror.v1, kind=update).
The integration step can embed these sub-schemas into the full e39.trial.v1 schema.
"""
from __future__ import annotations

from e39_kube import ANNOTATION_KEYS, POLICY_CMS

_s = {'type': 'string'}
_sn = {'type': ['string', 'null']}
_in = {'type': ['integer', 'null']}

BOUND_STATE = {
    'oneOf': [
        {'type': 'null'},
        {'type': 'object', 'additionalProperties': False,
         'required': ['pod_rv_at_gate', 'annotations_at_gate'],
         'properties': {
             'pod_rv_at_gate': _s,
             'annotations_at_gate': {'type': 'object', 'additionalProperties': False,
                                     'properties': {k: _s for k in ANNOTATION_KEYS}}}},
    ]}

DELETE_PRECONDITIONS = {
    'type': 'object', 'additionalProperties': False, 'required': ['uid', 'resource_version'],
    'properties': {'uid': _sn, 'resource_version': _sn}}

MIRROR = {
    'oneOf': [
        {'type': 'null'},
        {'type': 'object', 'additionalProperties': False,
         'required': ['source_object', 'source_commit_rv', 'source_commit_wall_ns', 'mirror_commit_rv',
                      'mirror_commit_wall_ns', 'mirror_audit_id'],
         'properties': {'source_object': {'enum': list(POLICY_CMS)}, 'source_commit_rv': _sn,
                        'source_commit_wall_ns': _in, 'mirror_commit_rv': _sn, 'mirror_commit_wall_ns': _in,
                        'mirror_audit_id': _sn}},
    ]}

NETPOL = {
    'oneOf': [
        {'type': 'null'},
        {'type': 'object', 'additionalProperties': False,
         'required': ['name', 't_send_mono_ns', 't_resp_mono_ns', 'http_status', 'audit_id',
                      'created_resource_version'],
         'properties': {'name': {'type': 'string', 'pattern': '^e40-isolate-'}, 't_send_mono_ns': _in,
                        't_resp_mono_ns': _in, 'http_status': _in, 'audit_id': _sn,
                        'created_resource_version': _sn,
                        't_send_wall_ns': _in, 'reason': _sn, 'created_uid': _sn}},
    ]}

MIRROR_LOG_UPDATE = {
    'type': 'object', 'additionalProperties': False,
    'required': ['schema', 'kind', 'seq', 'identity', 't_wall_ns', 't_mono_ns', 'source_object', 'source_value',
                 'source_commit_rv', 'event_type', 'via', 't_event_recv_wall_ns', 't_event_recv_mono_ns',
                 'annotation_key', 'pod_uid', 'pod_rv_before', 't_patch_send_wall_ns', 't_patch_send_mono_ns',
                 'http_status', 'mirror_commit_rv', 'mirror_commit_wall_ns', 'mirror_commit_mono_ns',
                 'mirror_audit_id', 'skipped', 'error'],
    'properties': {'schema': {'const': 'e39.mirror.v1'}, 'kind': {'const': 'update'}, 'seq': {'type': 'integer'},
                   'identity': _s, 't_wall_ns': {'type': 'integer'}, 't_mono_ns': {'type': 'integer'},
                   'source_object': {'enum': list(POLICY_CMS)}, 'source_value': _s, 'source_commit_rv': _sn,
                   'event_type': {'enum': ['ADDED', 'MODIFIED', 'LIST']}, 'via': {'enum': ['watch', 'resync']},
                   't_event_recv_wall_ns': {'type': 'integer'}, 't_event_recv_mono_ns': {'type': 'integer'},
                   'annotation_key': {'enum': list(ANNOTATION_KEYS)}, 'pod_uid': _sn, 'pod_rv_before': _sn,
                   't_patch_send_wall_ns': _in, 't_patch_send_mono_ns': _in, 'http_status': _in,
                   'mirror_commit_rv': _sn, 'mirror_commit_wall_ns': _in, 'mirror_commit_mono_ns': _in,
                   'mirror_audit_id': _sn, 'skipped': {'type': 'boolean'}, 'error': _sn}}

FRAGMENTS = {'bound_state': BOUND_STATE, 'delete_preconditions': DELETE_PRECONDITIONS, 'mirror': MIRROR,
             'netpol': NETPOL, 'mirror_log_update': MIRROR_LOG_UPDATE}


def validate_fragment(name, value):
    import jsonschema
    jsonschema.validate(value, dict(FRAGMENTS[name], **{'$schema': 'http://json-schema.org/draft-07/schema#'}))
    return True
