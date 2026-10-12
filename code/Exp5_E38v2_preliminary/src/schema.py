#!/usr/bin/env python3
"""JSON Schema for the e38v2.trial.v1 raw trial record (interface spec).

Used by the runner's --offline-selftest self-validation and by the unit tests.
The schema is strict (additionalProperties: false) at the top level and on every
nested object so records conform EXACTLY to the interface specification.
"""
from __future__ import annotations

ARMS = ('A0_native_uid', 'A1_dcji', 'A2_fullrefresh', 'A3_dcji_webhook')
CONDITIONS = ('no_write', 'async_scope', 'async_app_policy',
              'async_evidence_last_literal', 'uid_rebind_control')
DELTA_GRID_MS = (-5.0, -2.0, -1.0, 0.0, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0, 20.0, 50.0)

_int_or_null = {'type': ['integer', 'null']}
_str_or_null = {'type': ['string', 'null']}

TRIAL_SCHEMA = {
    '$schema': 'http://json-schema.org/draft-07/schema#',
    'type': 'object',
    'additionalProperties': False,
    'required': ['schema', 'run_id', 'trial_id', 'stage', 'block', 'arm', 'condition',
                 'delta_ms', 'rep', 'schedule_seed', 'fixture', 'gate', 'writer',
                 'delete', 'effect', 'webhook_request_uid', 'status', 'error'],
    'properties': {
        'schema': {'const': 'e38v2.trial.v1'},
        'run_id': {'type': 'string'},
        'trial_id': {'type': 'integer'},
        'stage': {'enum': [1, 2]},
        'block': {'type': 'integer'},
        'arm': {'enum': list(ARMS)},
        'condition': {'enum': list(CONDITIONS)},
        'delta_ms': {'type': ['number', 'null']},
        'rep': {'type': 'integer'},
        'schedule_seed': {'type': 'integer'},
        'fixture': {
            'type': 'object', 'additionalProperties': False,
            'required': ['reset_ok', 'pod_uid_decision', 'initial_values'],
            'properties': {
                'reset_ok': {'type': 'boolean'},
                'pod_uid_decision': {'type': 'string'},
                'initial_values': {'type': 'object'},
            },
        },
        'gate': {
            'type': 'object', 'additionalProperties': False,
            'required': ['decision', 't_start_mono_ns', 't_end_mono_ns', 't_end_wall_ns',
                         'reads', 'ssar_allowed'],
            'properties': {
                'decision': {'enum': ['EXECUTE', 'ABORT_REPLAN', 'UNKNOWN_COHERENCE', None]},
                't_start_mono_ns': _int_or_null,
                't_end_mono_ns': _int_or_null,
                't_end_wall_ns': _int_or_null,
                'reads': {
                    'type': 'array',
                    'items': {
                        'type': 'object', 'additionalProperties': False,
                        'required': ['object', 'resource_version', 'value',
                                     't_send_mono_ns', 't_recv_mono_ns'],
                        'properties': {
                            'object': {'type': 'string'},
                            'resource_version': {'type': ['string', 'null']},
                            'value': {'type': ['string', 'null']},
                            't_send_mono_ns': {'type': 'integer'},
                            't_recv_mono_ns': {'type': 'integer'},
                        },
                    },
                },
                'ssar_allowed': {'type': ['boolean', 'null']},
            },
        },
        'writer': {
            'oneOf': [
                {'type': 'null'},
                {
                    'type': 'object', 'additionalProperties': False,
                    'required': ['object', 'new_value', 'intended_release_mono_ns',
                                 'actual_release_mono_ns', 't_resp_mono_ns', 'http_status',
                                 'resulting_resource_version', 'audit_id'],
                    'properties': {
                        'object': {'type': 'string'},
                        'new_value': {'type': 'string'},   # interface spec: str (E38v2 integration fix)
                        'intended_release_mono_ns': {'type': 'integer'},
                        'actual_release_mono_ns': {'type': 'integer'},
                        't_resp_mono_ns': _int_or_null,
                        'http_status': _int_or_null,
                        'resulting_resource_version': _str_or_null,
                        'audit_id': _str_or_null,
                        'error': {'type': 'string'},
                    },
                },
            ],
        },
        'delete': {
            'type': 'object', 'additionalProperties': False,
            'required': ['dispatched', 't_send_mono_ns', 't_send_wall_ns', 't_resp_mono_ns',
                         'http_status', 'reason', 'audit_id', 'expected_uid',
                         'deleted_object_resource_version'],
            'properties': {
                'dispatched': {'type': 'boolean'},
                't_send_mono_ns': _int_or_null,
                't_send_wall_ns': _int_or_null,
                't_resp_mono_ns': _int_or_null,
                'http_status': _int_or_null,
                'reason': _str_or_null,
                'audit_id': _str_or_null,
                'expected_uid': {'type': 'string'},
                'deleted_object_resource_version': _str_or_null,
            },
        },
        'effect': {
            'type': 'object', 'additionalProperties': False,
            'required': ['verified', 't_verified_mono_ns'],
            'properties': {
                'verified': {'type': 'boolean'},
                't_verified_mono_ns': _int_or_null,
            },
        },
        'webhook_request_uid': _str_or_null,
        # optional, uid_rebind_control only (E38v2 integration): replacement Pod created after the gate
        'uid_rebind': {
            'type': 'object', 'additionalProperties': False,
            'required': ['replacement_pod_uid', 't_replaced_mono_ns'],
            'properties': {'replacement_pod_uid': _str_or_null, 't_replaced_mono_ns': {'type': 'integer'}},
        },
        'status': {'enum': ['COMPLETE', 'UNKNOWN', 'INFRA_ERROR']},
        'error': _str_or_null,
    },
}


def validate_record(record):
    import jsonschema
    jsonschema.validate(record, TRIAL_SCHEMA)
    # Cross-field rules the JSON Schema cannot express.
    if record['condition'] in ('no_write', 'uid_rebind_control'):
        if record['delta_ms'] is not None:
            raise ValueError('delta_ms must be null for ' + record['condition'])
        if record['writer'] is not None:
            raise ValueError('writer must be null for ' + record['condition'])
    else:
        if record['delta_ms'] is None:
            raise ValueError('async condition requires a numeric delta_ms')
    if 'uid_rebind' in record and record['condition'] != 'uid_rebind_control':
        raise ValueError('uid_rebind only for uid_rebind_control')
    return True
