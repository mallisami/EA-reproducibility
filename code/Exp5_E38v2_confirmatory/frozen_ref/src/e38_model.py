"""E38 prospectively proposed model. This is NOT an empirical result.

Uses the unchanged, copied v8.1.1 flashir_core planner and its E37 F5 physical
scenario as an explicitly versioned extension for current target UID and scope.
Application-level policy authorization is a separately mandatory query, not an
attacker goal for the locked engine. The Kubernetes SSAR is a separate API POST.
"""
from __future__ import annotations
import copy
import json
from pathlib import Path
from flashir_core.dcji import derive_critical_predicates
from e38_independent_offline_oracle import State, asymmetric_goals, enumerate_exact_minimum

ROOT=Path(__file__).resolve().parents[1]
FLAGS=('a','b','c','d','e','f')
F5=json.loads((ROOT/'source_provenance/E37_F5_PHYS_ORIGINAL.json').read_text())

def construct_model():
    model=copy.deepcopy(F5)
    model['scenario_id']='E38.INTEGRATED.PROSPECTIVE.V1'
    model['title']='Six physical evidence observations with explicit UID and scope'
    model['action']['action_type']='delete_bound_pod'
    model['action']['target_reference']='pod/e38-target'
    model['initial_state']['facts'].update(current_uid='UID_ORIGINAL',scope_ok=True)
    model['predicates'].update({
        'p_binding':{'path':'/facts/current_uid','op':'eq','value':'UID_ORIGINAL','evidence_class':'B',
                     'description':'Current pod UID still refers to original decision-time target'},
        'p_scope':{'path':'/facts/scope_ok','op':'eq','value':True,'evidence_class':'L',
                   'description':'Application response scope remains identical to approved singleton'},
    })
    model['policy_profiles']['balanced']['binding']={'predicate':'p_binding'}
    model['policy_profiles']['balanced']['scope']={'predicate':'p_scope'}
    for tid,path,val in [('uid_rebind','/facts/current_uid','UID_REPLACEMENT'),
                         ('scope_drift','/facts/scope_ok',False)]:
        model['transitions'].append({'transition_id':tid,'title':tid,'actor':'attacker',
          'capability_tier':'C2','duration_s':0.6,'cost_ops':1,'preconditions':True,
          'effects':[{'op':'set','path':path,'value':val}], 'adversarially_induced':True,
          'causal_parent_ids':[]})
    for pid,name in [('p_binding','current_uid'),('p_scope','scope_ok')]:
        qid=f'query:/facts/{name}'
        model['refresh_model']['query_costs'][qid]=1.0
        model['refresh_model']['predicate_query_ids'][pid]=qid
        model['refresh_model']['query_observed_at_s'][qid]=0.0
    return model

PREDICATE_TO_PHYSICAL={'flag_a':'a','flag_b':'b','flag_c':'c','flag_d':'d',
  'flag_e':'e','flag_f':'f','p_binding':'uid','p_scope':'scope'}

def frozen_planner_certificate():
    model=construct_model()
    result=derive_critical_predicates(model,5.0,'C2','balanced')
    if result['status']!='EXPLOITABLE':
        raise RuntimeError('Existing planner did not return exploitable goal certificate: '+str(result))
    selected=set(PREDICATE_TO_PHYSICAL[p] for p in result['critical_predicates'])
    independent=enumerate_exact_minimum(State(),asymmetric_goals())
    if len(independent)!=1 or selected!=set(independent[0]):
        raise RuntimeError('Independent exact certificate disagrees with locked planner')
    expected={'a','c','e','uid','scope'}
    if selected!=expected:raise RuntimeError('Unreviewed certificate change')
    return {'model':model,'plan':result,'observations':tuple(x for x in ('a','b','c','d','e','f','uid','scope') if x in selected),
            'independent_exact_minima':[list(a) for a in independent]}


def model_policy(evidence,uid_matches,scope_safe,app_policy_allowed,rbac_allowed,ready=True):
    """Study-specific model truth, independent of flashir_core commit_components."""
    def bit(k):
        v=evidence.get(k)
        if type(v) is not bool:raise ValueError('unknown evidence '+k)
        return v
    j=(bit('a') and bit('b')) or (bit('c') and bit('d')) or (bit('e') and bit('f'))
    eligible=all(type(v) is bool for v in (uid_matches,scope_safe,app_policy_allowed,rbac_allowed,ready))
    if not eligible:raise ValueError('unknown policy component')
    decision='EXECUTE' if (j and uid_matches and scope_safe and app_policy_allowed and rbac_allowed and ready) else 'ABORT_REPLAN'
    return {'justification':j,'same_binding':uid_matches,'scope_safe':scope_safe,
        'app_policy_allowed':app_policy_allowed,'rbac_allowed':rbac_allowed,'ready':ready,
        'decision':decision}


def make_hybrid(cached,measured):
    out={k:cached[k] for k in FLAGS}
    for k in FLAGS:
        if k in measured:out[k]=measured[k]
    return out
