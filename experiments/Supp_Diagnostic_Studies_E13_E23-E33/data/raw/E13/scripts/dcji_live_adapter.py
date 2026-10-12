#!/usr/bin/env python3
"""Bind frozen DCJI scenarios to live Kubernetes observations without changing the locked core."""
import copy, hashlib, json, sys, time
from pathlib import Path

E13_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = Path(__file__).resolve().parents[3]
DET = PACKAGE_ROOT / 'deterministic'
sys.path.insert(0, str(DET))
from flashir_core.dcji import derive_critical_predicates
from flashir_core.defenses import B6_dcji_gate

BINDINGS = json.loads((E13_ROOT / 'E13_MODEL_BINDINGS.json').read_text())
LOCKED_INPUTS = json.loads((DET / 'CONTROLLED_INPUTS_V8.sha256.json').read_text())

def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def _verify_locked(rel):
    p = DET / rel
    expected = LOCKED_INPUTS.get(rel)
    actual = sha256(p)
    if expected is None or actual != expected:
        raise RuntimeError(f'locked input mismatch: {rel}')
    return actual

def derive_live_plan(case_id):
    spec = BINDINGS['cases'][case_id]
    scenario_rel = spec['scenario_rel']
    scenario_hash = _verify_locked(scenario_rel)
    _verify_locked('flashir_core/dcji.py')
    _verify_locked('flashir_core/defenses.py')
    scenario = json.loads((DET / scenario_rel).read_text())
    t0 = time.perf_counter()
    plan = derive_critical_predicates(
        scenario,
        float(spec['deadline_s']),
        spec['attacker_capability'],
        spec['profile']
    )
    planning_runtime_ms = (time.perf_counter() - t0) * 1000.0
    expected = spec['expected_critical_query_ids']
    if plan.get('status') != 'EXPLOITABLE':
        raise RuntimeError(f'{case_id}: frozen DCJI plan status is {plan.get("status")}, expected EXPLOITABLE')
    if plan.get('critical_query_ids') != expected:
        raise RuntimeError(f'{case_id}: critical-query drift {plan.get("critical_query_ids")} != {expected}')
    return {
        'case_id': case_id,
        'scenario': scenario,
        'scenario_rel': scenario_rel,
        'scenario_sha256': scenario_hash,
        'deadline_s': float(spec['deadline_s']),
        'profile': spec['profile'],
        'attacker_capability': spec['attacker_capability'],
        'plan': plan,
        'planning_runtime_ms': planning_runtime_ms
    }

def _project_commit_state(case_id, scenario, decision_obs, commit_obs, deadline_s):
    state = copy.deepcopy(scenario['initial_state'])
    state['timestamp_s'] = float(deadline_s)
    changed = commit_obs != decision_obs
    if case_id == 'F1.1':
        state['facts']['api0_current_uid'] = scenario['initial_state']['facts']['api0_current_uid'] if not changed else 'LIVE_REBOUND_UID'
    elif case_id == 'F1.2':
        state['facts']['F1_2_current_uid'] = scenario['initial_state']['facts']['F1_2_current_uid'] if not changed else 'LIVE_REBOUND_UID'
    elif case_id == 'F4.1':
        state['facts']['selector_members'] = copy.deepcopy(scenario['initial_state']['facts']['selector_members']) if not changed else ['U1', 'U2']
    elif case_id == 'F4.4':
        state['facts']['scope_members'] = copy.deepcopy(scenario['initial_state']['facts']['scope_members']) if not changed else ['U1', 'U2']
    else:
        raise KeyError(case_id)
    return state, changed

def evaluate_live_commit(plan_ctx, decision_obs, commit_obs):
    scenario = plan_ctx['scenario']
    commit_state, changed = _project_commit_state(
        plan_ctx['case_id'], scenario, decision_obs, commit_obs, plan_ctx['deadline_s']
    )
    outcome = B6_dcji_gate(
        scenario,
        scenario['initial_state'],
        commit_state,
        profile=plan_ctx['profile'],
        deadline_s=plan_ctx['deadline_s'],
        attacker_capability=plan_ctx['attacker_capability'],
        critical_plan=plan_ctx['plan']
    )
    return {
        'dcji_live_decision': outcome['decision'],
        'dcji_defense_mode': outcome['defense_mode'],
        'critical_query_ids': outcome['critical_query_ids'],
        'critical_predicates': outcome['critical_predicates'],
        'query_cost_units': outcome['query_cost_units'],
        'observed_relevant_state_changed': changed,
        'modeled_commit_valid_after_refresh': outcome['decision'] == 'EXECUTE'
    }
