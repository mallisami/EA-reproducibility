#!/usr/bin/env python3
"""JSON Schema (draft-07) for the e39.trial.v1 raw trial record written by e39_runner.py.

= protocol/E39_RECORD_SCHEMA.json (e38v2.trial.v1 + added_fields) made machine-checkable. Strict
(additionalProperties: false) at the top level and on the nested runner objects, like schema.py for
e38v2.trial.v1; the E39 sub-objects reuse e39_fragments (bound_state, delete_preconditions, mirror,
netpol). Runner-only diagnostic blocks (arrivals_meta, go, mirror_meta, load) are declared explicitly.
Cross-field rules (validate_record_e39) encode which fields are populated for which sub-experiment.
"""
from __future__ import annotations

import copy

import schema as S38
from e39_fragments import BOUND_STATE, DELETE_PRECONDITIONS, MIRROR, NETPOL

TRIAL_SCHEMA_ID = 'e39.trial.v1'
EXPERIMENTS = ('A', 'B', 'C', 'D', 'E', 'F', 'G')
ARMS = ('A0_native_uid', 'A1_dcji', 'A2_fullrefresh', 'A3_dcji_webhook', 'A4d_commit_direct',
        'A4m_commit_mirror', 'A1_dcji_go', 'A4d_commit_direct_go')
CONDITIONS = ('no_write', 'async_scope', 'async_app_policy', 'async_evidence_last_literal', 'uid_rebind_control',
              'poisson_scope', 'stable', 'ea_attack', 'out_of_model_bdf', 'in_model_ace')
ASYNC_CONDITIONS = ('async_scope', 'async_app_policy', 'async_evidence_last_literal')
GO_ARMS = ('A1_dcji_go', 'A4d_commit_direct_go')
LOAD_LEVELS = ('idle', 'moderate', 'high')

_i = {'type': 'integer'}
_in = {'type': ['integer', 'null']}
_n = {'type': 'number'}
_nn = {'type': ['number', 'null']}
_s = {'type': 'string'}
_sn = {'type': ['string', 'null']}
_b = {'type': 'boolean'}
_bn = {'type': ['boolean', 'null']}

_READ = {'type': 'object', 'additionalProperties': False,
         'required': ['object', 'resource_version', 'value', 't_send_mono_ns', 't_recv_mono_ns'],
         'properties': {'object': _s, 'resource_version': _sn, 'value': _sn, 't_send_mono_ns': _i,
                        't_recv_mono_ns': _i, 'audit_id': _sn, 'http_status': _in}}

_GATE = copy.deepcopy(S38.TRIAL_SCHEMA['properties']['gate'])
_GATE['properties']['reads'] = {'type': 'array', 'items': _READ}
_GATE = {'oneOf': [{'type': 'null'}, _GATE]}

_WRITER = copy.deepcopy(S38.TRIAL_SCHEMA['properties']['writer'])

_ARRIVAL = {'type': 'object', 'additionalProperties': False,
            'required': ['planned_offset_ms', 'actual_release_mono_ns', 't_resp_mono_ns', 'http_status',
                         'resulting_resource_version', 'audit_id', 'object'],
            'properties': {'planned_offset_ms': _n, 'intended_release_mono_ns': _i, 'actual_release_mono_ns': _i,
                           't_resp_mono_ns': _in, 'http_status': _in, 'resulting_resource_version': _sn,
                           'audit_id': _sn, 'object': _s, 'new_value': _s, 'error': _s}}

_CUT = {'type': 'object', 'additionalProperties': False,
        'required': ['object', 'new_value', 't_send_mono_ns', 't_resp_mono_ns', 'http_status',
                     'resulting_resource_version', 'audit_id'],
        'properties': {'object': _s, 'new_value': _s, 't_send_mono_ns': _i, 't_resp_mono_ns': _in,
                       'http_status': _in, 'resulting_resource_version': _sn, 'audit_id': _sn}}

_DELETE = copy.deepcopy(S38.TRIAL_SCHEMA['properties']['delete'])
_DELETE['properties'].update({'response_message': _sn, 'conflict_kind': _sn, 't_resp_is_upper_bound': _b})

_EFFECT = copy.deepcopy(S38.TRIAL_SCHEMA['properties']['effect'])
_EFFECT['properties'].update({'netpol_exists': _bn, 'netpol_resource_version': _sn, 'netpol_cleanup_status': _in})

_FIXTURE = copy.deepcopy(S38.TRIAL_SCHEMA['properties']['fixture'])
_FIXTURE['properties'].update({'pod_annotations': {'type': ['object', 'null']}, 'pod_labels': {'type': ['object', 'null']},
                               'condition_storage': _sn})
_FIXTURE = {'oneOf': [{'type': 'null'}, _FIXTURE]}

_HOST = {'type': 'object', 'additionalProperties': False, 'required': ['cpu_pct', 'free_ram_mb', 'load_throttled'],
         'properties': {'cpu_pct': _nn, 'free_ram_mb': _nn, 'load_throttled': _b}}

_ACQ = {'type': 'object', 'additionalProperties': False,
        'required': ['t_start_mono_ns', 't_plan_end_mono_ns', 't_end_mono_ns', 'refresh_ms', 'gate_ms', 'justified'],
        'properties': {'t_start_mono_ns': _i, 't_plan_end_mono_ns': _i, 't_end_mono_ns': _i, 'refresh_ms': _n,
                       'gate_ms': _n, 'justified': _b, 'flags_read': {'type': 'array', 'items': _s},
                       'n_reads': _i, 'all_reads_ok': _b, 'applied_delay_ms': _n, 'guard_only': _b,
                       'reads': {'type': 'array'}}}

_UID_REBIND = copy.deepcopy(S38.TRIAL_SCHEMA['properties']['uid_rebind'])

TRIAL_SCHEMA = {
    '$schema': 'http://json-schema.org/draft-07/schema#',
    'type': 'object',
    'additionalProperties': False,
    'required': ['schema', 'run_id', 'trial_id', 'stage', 'block', 'experiment', 'arm', 'client_impl', 'action_type',
                 'condition', 'delta_ms', 'rep', 'schedule_seed', 'lambda_per_s', 'injected_latency_ms',
                 'acquisition_mode', 'planning_mode', 'planning_ms', 'guard_order', 'pair_id', 'load_level',
                 'load_observed_rps', 'host', 'fixture', 'gate', 'writer', 'writer_arrivals', 'delete',
                 'delete_preconditions', 'bound_state', 'mirror', 'netpol', 'effect', 'go_client_version',
                 'webhook_request_uid', 'status', 'error'],
    'properties': {
        'schema': {'const': TRIAL_SCHEMA_ID},
        'run_id': _s, 'trial_id': _i, 'stage': {'enum': [1, 2]}, 'block': _i,
        'experiment': {'enum': list(EXPERIMENTS)}, 'arm': {'enum': list(ARMS)},
        'client_impl': {'enum': ['python', 'client-go']},
        'action_type': {'enum': ['pod_delete', 'netpol_create']},
        'condition': {'enum': list(CONDITIONS)}, 'delta_ms': _nn, 'rep': _i, 'schedule_seed': _i,
        'lambda_per_s': _nn, 'injected_latency_ms': _nn,
        'acquisition_mode': {'enum': ['sequential', 'parallel', None]},
        'planning_mode': {'enum': ['precomputed', 'per_incident', None]}, 'planning_ms': _nn,
        'guard_order': {'enum': ['dcji_first', 'fullrefresh_first', None]}, 'pair_id': _in,
        'load_level': {'enum': list(LOAD_LEVELS)}, 'requested_level': {'enum': list(LOAD_LEVELS) + [None]},
        'load_observed_rps': _nn, 'host': _HOST,
        'fixture': _FIXTURE, 'gate': _GATE, 'writer': _WRITER,
        'writer_arrivals': {'oneOf': [{'type': 'null'}, {'type': 'array', 'items': _ARRIVAL}]},
        'arrivals_meta': {'type': ['object', 'null']},
        'cut_writes': {'type': 'array', 'items': _CUT},
        'delete': _DELETE, 'delete_preconditions': DELETE_PRECONDITIONS, 'bound_state': BOUND_STATE,
        'mirror': MIRROR, 'mirror_meta': {'type': ['object', 'null']}, 'netpol': NETPOL, 'effect': _EFFECT,
        'acquisition': _ACQ, 'go_client_version': _sn, 'go': {'type': ['object', 'null']},
        'load': {'type': ['object', 'null']},
        'webhook_request_uid': _sn, 'uid_rebind': _UID_REBIND,
        'status': {'enum': ['COMPLETE', 'UNKNOWN', 'INFRA_ERROR']}, 'error': _sn,
    },
}


def validate_record_e39(rec):
    """jsonschema validation + cross-field rules. Raises (jsonschema.ValidationError / ValueError)."""
    import jsonschema
    jsonschema.validate(rec, TRIAL_SCHEMA)
    x, arm, cond = rec['experiment'], rec['arm'], rec['condition']
    need_delta = (x in ('D', 'E', 'F')) or (x == 'A' and cond in ASYNC_CONDITIONS)
    if need_delta and rec['delta_ms'] is None:
        raise ValueError('E39-%s %s requires a numeric delta_ms' % (x, cond))
    if not need_delta and rec['delta_ms'] is not None:
        raise ValueError('delta_ms must be null for E39-%s %s' % (x, cond))
    if (x == 'B') != (rec['lambda_per_s'] is not None):
        raise ValueError('lambda_per_s is set exactly for E39-B')
    if x == 'B' and rec['writer_arrivals'] is None and rec['status'] == 'COMPLETE':
        raise ValueError('E39-B record without writer_arrivals')
    if x != 'B' and rec['writer_arrivals'] is not None:
        raise ValueError('writer_arrivals only for E39-B')
    c_fields = ('injected_latency_ms', 'acquisition_mode', 'planning_mode', 'guard_order', 'pair_id')
    for f in c_fields:
        if (x == 'C') != (rec[f] is not None):
            raise ValueError('%s is set exactly for E39-C' % f)
    if x == 'C' and rec['status'] == 'COMPLETE' and 'acquisition' not in rec:
        raise ValueError('E39-C record without acquisition block')
    if x != 'D' and rec['load_level'] != 'idle':
        raise ValueError("non-D records carry load_level 'idle'")
    if x == 'D' and rec.get('requested_level') is None and rec['status'] == 'COMPLETE':
        raise ValueError('E39-D record without requested_level')
    if (rec['client_impl'] == 'client-go') != (arm in GO_ARMS):
        raise ValueError('client_impl must be client-go exactly for the *_go arms')
    if (x == 'E') != (rec['action_type'] == 'netpol_create'):
        raise ValueError('action_type netpol_create exactly for E39-E')
    if x == 'G' and rec['status'] == 'COMPLETE' and not rec.get('cut_writes'):
        raise ValueError('E39-G record without cut_writes')
    if x != 'G' and 'cut_writes' in rec:
        raise ValueError('cut_writes only for E39-G')
    if cond in ('no_write', 'uid_rebind_control') + ('stable', 'ea_attack', 'out_of_model_bdf', 'in_model_ace',
                                                     'poisson_scope') and rec['writer'] is not None:
        raise ValueError('writer must be null for condition ' + cond)
    if 'uid_rebind' in rec and cond != 'uid_rebind_control':
        raise ValueError('uid_rebind only for uid_rebind_control')
    if rec['mirror'] is not None and arm != 'A4m_commit_mirror':
        raise ValueError('mirror only for A4m')
    if rec['go_client_version'] is not None and arm not in GO_ARMS:
        raise ValueError('go_client_version only for client-go arms')
    return True
