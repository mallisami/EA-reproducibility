
import copy
from fractions import Fraction
from .expr import pointer_get, pointer_set

def policy_predicates(expr):
    if isinstance(expr,bool): return set()
    if "predicate" in expr: return {expr["predicate"]}
    if "all" in expr:
        out=set()
        for x in expr["all"]: out |= policy_predicates(x)
        return out
    if "any" in expr:
        out=set()
        for x in expr["any"]: out |= policy_predicates(x)
        return out
    if "not" in expr: return policy_predicates(expr["not"])
    raise ValueError(expr)

def material_predicates(scenario,profile="balanced",include_execution=True):
    pol=scenario["policy_profiles"][profile]
    pids=set()
    for key in ("justification","binding","scope"):
        pids |= policy_predicates(pol[key])
    if include_execution:
        pids |= policy_predicates(scenario["action"]["execution_precondition"])
    pids.discard(scenario["action"]["authorization"]["predicate"])
    return pids

def query_id_for_predicate(scenario,pid):
    return scenario["refresh_model"]["predicate_query_ids"][pid]

# PATCH (C7): exact, order-independent cost arithmetic. A cost literal is converted
# through its shortest decimal repr (str(float)) into a Fraction, so e.g. 0.1+0.2 == 0.3
# exactly and the result no longer depends on set-iteration (hash) order.
def exact_cost_value(v):
    if isinstance(v,Fraction): return v
    if isinstance(v,int): return Fraction(v)
    return Fraction(str(float(v)))

def exact_query_cost(scenario,query_ids):
    costs=scenario["refresh_model"]["query_costs"]
    return sum((exact_cost_value(costs[q]) for q in sorted(set(query_ids))),Fraction(0))

def query_cost(scenario,query_ids):
    ids=set(query_ids)
    if not ids:
        return 0          # locked code returned int 0 for the empty set (sum of empty generator)
    return float(exact_query_cost(scenario,ids))

def refreshable_query_ids(scenario,profile="balanced"):
    out=set()
    for pid in material_predicates(scenario,profile):
        qid=query_id_for_predicate(scenario,pid)
        if float(scenario["refresh_model"]["query_costs"][qid]) > 0:
            out.add(qid)
    return out

def predicates_for_queries(scenario,query_ids,profile="balanced"):
    qset=set(query_ids)
    return sorted(
        pid for pid in material_predicates(scenario,profile)
        if query_id_for_predicate(scenario,pid) in qset
    )

def refresh_queries(scenario,cached_state,current_state,query_ids,profile="balanced"):
    hybrid=copy.deepcopy(cached_state)
    pids=predicates_for_queries(scenario,query_ids,profile)
    copied=set()
    for pid in pids:
        path=scenario["predicates"][pid]["path"]
        if path in copied: continue
        pointer_set(hybrid,path,copy.deepcopy(pointer_get(current_state,path)))
        copied.add(path)
    hybrid["timestamp_s"]=current_state.get("timestamp_s",hybrid.get("timestamp_s",0))
    return hybrid,pids

def refresh_predicates(scenario,cached_state,current_state,predicate_ids,profile="balanced"):
    qids={query_id_for_predicate(scenario,pid) for pid in predicate_ids}
    return refresh_queries(scenario,cached_state,current_state,qids,profile)

def stale_query_ids(scenario,profile,commit_time_s,ttl_s):
    observed=scenario["refresh_model"]["query_observed_at_s"]
    stale=[]
    for qid in sorted(refreshable_query_ids(scenario,profile)):
        if commit_time_s - float(observed.get(qid,0)) > ttl_s:
            stale.append(qid)
    return stale
