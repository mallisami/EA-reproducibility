#!/usr/bin/env python3
"""E38v2 model: horizon-parameterised planner certificate and validity policy.

Copied and adapted from the frozen ``e38_model.py`` (frozen_ref is never edited).
Changes vs frozen:
  * FIX (b): ``frozen_planner_certificate`` takes ``horizon`` (default 30 s, from
    SESSION config) instead of a hard-coded 5.0; it asserts the certificate is
    identical to {a,c,e,uid,scope} at 5/10/30/60 s AND at the requested horizon,
    failing closed otherwise. The E37 F5 physical scenario JSON is loaded from a
    configurable path and its sha256 is returned so SESSION.json can record it.
  * Adds ``v_core`` / ``core_decision`` implementing the E38v2 validity policy
    V_core = J AND scope_ok AND app_policy_allowed with J=(a&b)|(c&d)|(e&f).

This is a prospective model, NOT an empirical result.
"""
from __future__ import annotations
import copy, hashlib, json
from pathlib import Path
from flashir_core.dcji import derive_critical_predicates
from e38_independent_offline_oracle import State, asymmetric_goals, enumerate_exact_minimum

FLAGS = ('a', 'b', 'c', 'd', 'e', 'f')
EXPECTED_CERTIFICATE = frozenset({'a', 'c', 'e', 'uid', 'scope'})
CERTIFICATE_INVARIANT_HORIZONS = (5.0, 10.0, 30.0, 60.0)

PREDICATE_TO_PHYSICAL = {'flag_a': 'a', 'flag_b': 'b', 'flag_c': 'c', 'flag_d': 'd',
                         'flag_e': 'e', 'flag_f': 'f', 'p_binding': 'uid', 'p_scope': 'scope'}

POD_NAME = 'e40-target'


def sha256_file(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_f5(f5_path) -> dict:
    return json.loads(Path(f5_path).read_text())


def construct_model(f5: dict) -> dict:
    """Deterministically extend the E37 F5 physical scenario for E38v2."""
    model = copy.deepcopy(f5)
    model['scenario_id'] = 'E38v2.INTEGRATED.PROSPECTIVE.V1'
    model['title'] = 'Six physical evidence observations with explicit UID and scope (E38v2)'
    model['action']['action_type'] = 'delete_bound_pod'
    model['action']['target_reference'] = 'pod/' + POD_NAME
    model['initial_state']['facts'].update(current_uid='UID_ORIGINAL', scope_ok=True)
    model['predicates'].update({
        'p_binding': {'path': '/facts/current_uid', 'op': 'eq', 'value': 'UID_ORIGINAL',
                      'evidence_class': 'B', 'description': 'Current pod UID still refers to original target'},
        'p_scope': {'path': '/facts/scope_ok', 'op': 'eq', 'value': True,
                    'evidence_class': 'L', 'description': 'Response scope remains identical to approved singleton'},
    })
    model['policy_profiles']['balanced']['binding'] = {'predicate': 'p_binding'}
    model['policy_profiles']['balanced']['scope'] = {'predicate': 'p_scope'}
    for tid, path, val in [('uid_rebind', '/facts/current_uid', 'UID_REPLACEMENT'),
                           ('scope_drift', '/facts/scope_ok', False)]:
        model['transitions'].append({'transition_id': tid, 'title': tid, 'actor': 'attacker',
            'capability_tier': 'C2', 'duration_s': 0.6, 'cost_ops': 1, 'preconditions': True,
            'effects': [{'op': 'set', 'path': path, 'value': val}],
            'adversarially_induced': True, 'causal_parent_ids': []})
    for pid, name in [('p_binding', 'current_uid'), ('p_scope', 'scope_ok')]:
        qid = f'query:/facts/{name}'
        model['refresh_model']['query_costs'][qid] = 1.0
        model['refresh_model']['predicate_query_ids'][pid] = qid
        model['refresh_model']['query_observed_at_s'][qid] = 0.0
    return model


def _certificate_at(f5: dict, horizon: float) -> frozenset:
    result = derive_critical_predicates(construct_model(f5), float(horizon), 'C2', 'balanced')
    if result['status'] != 'EXPLOITABLE':
        raise RuntimeError('Planner did not return an exploitable certificate at horizon '
                           + repr(horizon) + ': ' + str(result))
    return frozenset(PREDICATE_TO_PHYSICAL[p] for p in result['critical_predicates']), result


def frozen_planner_certificate(horizon: float = 30.0, f5_path=None):
    """FIX (b): horizon-parameterised, fail-closed certificate.

    Verifies the certificate equals {a,c,e,uid,scope} at 5/10/30/60 s and at the
    requested horizon, and that it agrees with the independent exhaustive minimum.
    Any deviation raises (fail closed).
    """
    if f5_path is None:
        raise ValueError('f5_path is required (configurable per FIX (b))')
    horizon = float(horizon)
    if horizon <= 0:
        raise ValueError('horizon must be positive')
    f5 = load_f5(f5_path)

    # Invariance assertion across the reviewed horizons plus the requested one.
    horizons = tuple(sorted(set(CERTIFICATE_INVARIANT_HORIZONS) | {horizon}))
    selected_at = {}
    result_at_horizon = None
    for h in horizons:
        sel, result = _certificate_at(f5, h)
        selected_at[h] = sel
        if sel != EXPECTED_CERTIFICATE:
            raise RuntimeError(f'Certificate at {h}s is {sorted(sel)}, expected '
                               f'{sorted(EXPECTED_CERTIFICATE)} (fail closed)')
        if h == horizon:
            result_at_horizon = result
    distinct = set(selected_at.values())
    if len(distinct) != 1:
        raise RuntimeError('Certificate is NOT identical across horizons: '
                           + str({h: sorted(s) for h, s in selected_at.items()}) + ' (fail closed)')

    independent = enumerate_exact_minimum(State(), asymmetric_goals())
    if len(independent) != 1 or EXPECTED_CERTIFICATE != frozenset(independent[0]):
        raise RuntimeError('Independent exact certificate disagrees with planner (fail closed)')

    selected = EXPECTED_CERTIFICATE
    observations = tuple(x for x in ('a', 'b', 'c', 'd', 'e', 'f', 'uid', 'scope') if x in selected)
    return {
        'horizon_s': horizon,
        'observations': observations,
        'certificate': sorted(selected),
        'plan': result_at_horizon,
        'independent_exact_minima': [list(a) for a in independent],
        'invariant_horizons_checked': list(horizons),
        'f5_path': str(f5_path),
        'f5_sha256': sha256_file(f5_path),
    }


# --- E38v2 validity policy (identical in client gate, webhook and oracle) ---

def justification(evidence: dict) -> bool:
    def bit(k):
        v = evidence.get(k)
        if type(v) is not bool:
            raise ValueError('unknown evidence ' + repr(k))
        return v
    return (bit('a') and bit('b')) or (bit('c') and bit('d')) or (bit('e') and bit('f'))


def v_core(evidence: dict, scope_ok: bool, app_policy_allowed: bool) -> bool:
    """V_core = J AND scope_ok AND app_policy_allowed (uid is a separate DELETE precondition)."""
    if type(scope_ok) is not bool or type(app_policy_allowed) is not bool:
        raise ValueError('scope_ok and app_policy_allowed must be bool')
    return justification(evidence) and scope_ok and app_policy_allowed


def core_decision(evidence: dict, scope_ok: bool, app_policy_allowed: bool,
                  uid_match: bool, ssar_allowed: bool, coherent: bool) -> str:
    """Client-gate decision for DCJI / FullRefresh (fail closed on incoherence)."""
    if not coherent:
        return 'UNKNOWN_COHERENCE'
    ok = v_core(evidence, scope_ok, app_policy_allowed) and bool(uid_match) and bool(ssar_allowed)
    return 'EXECUTE' if ok else 'ABORT_REPLAN'


def make_hybrid(cached: dict, measured: dict) -> dict:
    out = {k: cached[k] for k in FLAGS}
    for k in FLAGS:
        if k in measured:
            out[k] = measured[k]
    return out
