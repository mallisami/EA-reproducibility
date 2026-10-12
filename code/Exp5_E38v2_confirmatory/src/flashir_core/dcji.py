
import copy, heapq, itertools, json, warnings
from fractions import Fraction
from .engine import transition_allowed_for_attacker_model, can_apply_transition, apply_transition
from .expr import commit_components, eval_predicate
from .refresh import (material_predicates, query_id_for_predicate, query_cost, refresh_queries,
                      predicates_for_queries, refreshable_query_ids, exact_query_cost, exact_cost_value)

def _state_key(state,completed):
    semantic=copy.deepcopy(state)
    semantic.pop("timestamp_s",None)
    return json.dumps({"state":semantic,"completed":sorted(completed)},sort_keys=True,separators=(",",":"))

def _goal_state_key(state):
    semantic=copy.deepcopy(state)
    semantic.pop("timestamp_s",None)
    return json.dumps(semantic,sort_keys=True,separators=(",",":"))

def _candidates(scenario,capability):
    return [t for t in scenario["transitions"] if transition_allowed_for_attacker_model(t,capability)]

def search_dcji(scenario,deadline_s,attacker_capability=None,profile="balanced",max_expansions=100000):
    attacker_capability=attacker_capability or scenario["attacker_capability_tier"]
    start=copy.deepcopy(scenario["initial_state"])
    sc=commit_components(scenario,start,profile)
    if not sc["valid"] or not sc["authorization"]:
        return {"status":"INVALID_INITIAL_STATE","path":[],"cost_ops":0,"elapsed_s":0.0,"expanded":0,"goal_components":sc}

    candidates=_candidates(scenario,attacker_capability)
    counter=itertools.count()
    pq=[(0,0.0,0,next(counter),start,tuple(),tuple())]
    best={}; expanded=0

    while pq:
        cost,elapsed,steps,_,state,completed_t,path_t=heapq.heappop(pq)
        completed=list(completed_t); path=list(path_t)
        key=_state_key(state,completed); score=(cost,elapsed,steps)
        if key in best and best[key] <= score: continue
        best[key]=score; expanded+=1

        comp=commit_components(scenario,state,profile)
        if not comp["authorization"]: continue
        if comp["attack_goal"]:
            return {"status":"EXPLOITABLE","path":path,"cost_ops":cost,"elapsed_s":elapsed,"expanded":expanded,"goal_components":comp}
        if expanded>=max_expansions:
            return {"status":"UNKNOWN_TIMEOUT","path":None,"cost_ops":None,"elapsed_s":None,"expanded":expanded,"goal_components":None}

        for t in candidates:
            tid=t["transition_id"]
            if tid in completed: continue
            ne=elapsed+t["duration_s"]
            if ne>deadline_s: continue
            if not can_apply_transition(t,scenario,state,completed): continue
            nxt=apply_transition(t,scenario,state,completed)
            heapq.heappush(pq,(cost+t["cost_ops"],ne,steps+1,next(counter),nxt,tuple(completed+[tid]),tuple(path+[tid])))

    return {"status":"NOT_EXPLOITABLE","path":None,"cost_ops":None,"elapsed_s":None,"expanded":expanded,"goal_components":None}

def enumerate_attack_goals(scenario,deadline_s,attacker_capability=None,profile="balanced",max_expansions=100000):
    """
    Enumerate ALL unique effectful EA-TOCTOU states reachable through the deadline.
    Traversal continues after an early goal state because the attacker may keep
    changing the world before actual commit time.
    """
    attacker_capability=attacker_capability or scenario["attacker_capability_tier"]
    start=copy.deepcopy(scenario["initial_state"])
    sc=commit_components(scenario,start,profile)
    if not sc["valid"] or not sc["authorization"]:
        return {"status":"INVALID_INITIAL_STATE","goals":[],"expanded":0}

    candidates=_candidates(scenario,attacker_capability)
    stack=[(start,0.0,0,tuple(),tuple())]
    seen=set(); goals={}; expanded=0

    while stack:
        state,elapsed,cost,completed_t,path_t=stack.pop()
        completed=list(completed_t); path=list(path_t)
        key=_state_key(state,completed)
        if key in seen: continue
        seen.add(key); expanded+=1

        comp=commit_components(scenario,state,profile)
        if not comp["authorization"]: continue
        if comp["attack_goal"]:
            gkey=_goal_state_key(state)
            rec={"state":state,"path":path,"elapsed_s":elapsed,"cost_ops":cost,"components":comp}
            old=goals.get(gkey)
            if old is None or (cost,elapsed,len(path)) < (old["cost_ops"],old["elapsed_s"],len(old["path"])):
                goals[gkey]=rec
            # Continue traversal.

        if expanded>=max_expansions:
            return {"status":"UNKNOWN_TIMEOUT","goals":list(goals.values()),"expanded":expanded}

        for t in candidates:
            tid=t["transition_id"]
            if tid in completed: continue
            ne=elapsed+t["duration_s"]
            if ne>deadline_s: continue
            if not can_apply_transition(t,scenario,state,completed): continue
            nxt=apply_transition(t,scenario,state,completed)
            stack.append((nxt,ne,cost+t["cost_ops"],tuple(completed+[tid]),tuple(path+[tid])))

    return {"status":"EXPLOITABLE" if goals else "NOT_EXPLOITABLE","goals":list(goals.values()),"expanded":expanded}

def changed_material_queries(scenario,initial_state,goal_state,profile="balanced"):
    qids=set()
    for pid in sorted(material_predicates(scenario,profile)):
        qid=query_id_for_predicate(scenario,pid)
        if float(scenario["refresh_model"]["query_costs"][qid]) <= 0:
            continue
        if eval_predicate(pid,scenario,initial_state) != eval_predicate(pid,scenario,goal_state):
            qids.add(qid)
    return sorted(qids)

# ---------------------------------------------------------------------------
# PATCH (offline reanalysis, C2/C7): lazy best-first subset enumeration.
#
# The locked implementation materialised and sorted all 2^n subsets BEFORE the
# max_subset_evals cap was consulted, so the cap bounded neither time nor memory.
# iter_subsets_in_certificate_order() yields subsets lazily in exactly the same
# total order the locked code used -- (cost, cardinality, lexicographic tuple of
# query IDs in code-point order) -- using a heap over the classic "extend / replace
# last" successor tree on indices sorted by (cost, query_id).
#
# Correctness (requires NON-NEGATIVE costs; checked):
#   * every non-empty index set {i1<...<ik} has exactly one parent (extend if
#     ik == i(k-1)+1, else replace-last from ik-1), so each subset is generated once;
#   * children have a STRICTLY larger key than their parent: "extend" adds a
#     non-negative cost and one element (cardinality +1); "replace last i by i+1"
#     does not decrease cost (indices sorted by cost) and, at equal cost, replaces an
#     ID by a code-point-larger ID, which makes the sorted ID tuple lexicographically
#     larger at equal cardinality.
#   Popping a heap over a tree with monotone keys therefore yields the global sorted
#   order. The heap never holds more than evals+1 entries, so max_subset_evals now
#   bounds both time and memory.
# Costs are exact Fractions of the decimal literal (no float-summation order effects).
# ---------------------------------------------------------------------------
def iter_subsets_in_certificate_order(query_ids,cost_of):
    """Yield (exact_cost:Fraction, subset:tuple[str]) in (cost, |subset|, subset) order.
    query_ids: iterable of distinct query IDs; cost_of: dict qid -> Fraction (>=0)."""
    ids=sorted(set(query_ids))                       # explicit code-point order (C7)
    for q in ids:
        if cost_of[q] < 0:
            raise ValueError(f"negative query cost for {q}; lazy enumeration requires costs >= 0")
    order=sorted(ids,key=lambda q:(cost_of[q],q))
    yield Fraction(0),()
    if not order:
        return
    def key(idx,c):
        combo=tuple(sorted(order[i] for i in idx))
        return (c,len(combo),combo)
    heap=[(key((0,),cost_of[order[0]]),(0,))]
    while heap:
        (c,_,combo),idx=heapq.heappop(heap)
        yield c,combo
        last=idx[-1]
        if last+1<len(order):
            nxt=last+1
            ext=idx+(nxt,)
            heapq.heappush(heap,(key(ext,c+cost_of[order[nxt]]),ext))
            rep=idx[:-1]+(nxt,)
            heapq.heappush(heap,(key(rep,c-cost_of[order[last]]+cost_of[order[nxt]]),rep))

def _full_refresh_rejects_all_goals(scenario,initial,goals,profile):
    """PATCH (C1): True iff refreshing EVERY refreshable (cost>0) material query makes the
    commit policy reject every enumerated goal state."""
    qids=sorted(refreshable_query_ids(scenario,profile))
    for goal in goals:
        hybrid,_=refresh_queries(scenario,initial,goal["state"],qids,profile)
        if commit_components(scenario,hybrid,profile)["valid"]:
            return False
    return True

def derive_critical_predicates(
    scenario,deadline_s,attacker_capability=None,profile="balanced",
    max_expansions=100000,max_subset_evals=100000
):
    enum=enumerate_attack_goals(scenario,deadline_s,attacker_capability,profile,max_expansions)
    if enum["status"]=="UNKNOWN_TIMEOUT":
        # PATCH (C1): goal set is incomplete, so no refresh set can be PROVEN to cover it.
        return {"status":"UNKNOWN_TIMEOUT","critical_query_ids":None,"critical_predicates":None,
                "query_cost_units":None,"goal_count":len(enum["goals"]),"candidate_query_ids":None,
                "expanded":enum["expanded"],"subset_evals":0,
                "goal_enumeration_complete":False,"full_refresh_rejects_all_goals":False}
    if not enum["goals"]:
        return {"status":"NOT_EXPLOITABLE","critical_query_ids":[],"critical_predicates":[],
                "query_cost_units":0.0,"goal_count":0,"candidate_query_ids":[],
                "expanded":enum["expanded"],"subset_evals":0}

    initial=scenario["initial_state"]
    candidate=set(); per_goal=[]
    for goal in enum["goals"]:
        qs=changed_material_queries(scenario,initial,goal["state"],profile)
        per_goal.append(qs); candidate.update(qs)
    candidates=sorted(candidate)

    # PATCH (C2): lazy generation; the cap is enforced while generating.
    cost_of={q:exact_cost_value(scenario["refresh_model"]["query_costs"][q]) for q in candidates}
    evals=0
    for cost,combo in iter_subsets_in_certificate_order(candidates,cost_of):
        evals+=1
        if evals>max_subset_evals:
            return {"status":"UNKNOWN_SUBSET_TIMEOUT","critical_query_ids":None,"critical_predicates":None,
                    "query_cost_units":None,"goal_count":len(enum["goals"]),"candidate_query_ids":candidates,
                    "expanded":enum["expanded"],"subset_evals":evals-1,
                    "goal_enumeration_complete":True,
                    "full_refresh_rejects_all_goals":_full_refresh_rejects_all_goals(scenario,initial,enum["goals"],profile)}
        good=True
        for goal in enum["goals"]:
            hybrid,_=refresh_queries(scenario,initial,goal["state"],combo,profile)
            if commit_components(scenario,hybrid,profile)["valid"]:
                good=False; break
        if good:
            return {"status":"EXPLOITABLE","critical_query_ids":list(combo),
                    "critical_predicates":predicates_for_queries(scenario,combo,profile),
                    "query_cost_units":float(cost),"goal_count":len(enum["goals"]),
                    "candidate_query_ids":candidates,"per_goal_changed_queries":per_goal,
                    "expanded":enum["expanded"],"subset_evals":evals}

    # PATCH (C1): no subset of the (changed, cost>0) candidate queries rejects every goal.
    # Record explicitly whether FullRefresh would; B6 must NOT fall back to it otherwise.
    return {"status":"NO_SELECTIVE_CERTIFICATE","critical_query_ids":None,"critical_predicates":None,
            "query_cost_units":None,"goal_count":len(enum["goals"]),"candidate_query_ids":candidates,
            "expanded":enum["expanded"],"subset_evals":evals,
            "goal_enumeration_complete":True,
            "full_refresh_rejects_all_goals":_full_refresh_rejects_all_goals(scenario,initial,enum["goals"],profile)}

# ---------------------------------------------------------------------------
# PATCH (validator, optional): transitions that write state observable ONLY through
# zero-cost ("immutable") queries. Such writes can invalidate justification in a way
# no refresh can detect; with the C1 patch B6 then aborts, but the scenario model is
# almost certainly mis-specified, so we surface it.
# ---------------------------------------------------------------------------
def _paths_overlap(a,b):
    a=a.rstrip("/"); b=b.rstrip("/")
    return a==b or a.startswith(b+"/") or b.startswith(a+"/")

def zero_cost_observability_violations(scenario,profile="balanced"):
    costs=scenario["refresh_model"]["query_costs"]
    mats=sorted(material_predicates(scenario,profile))
    out=[]
    for t in scenario["transitions"]:
        for e in t.get("effects",[]):
            readers=[pid for pid in mats if _paths_overlap(scenario["predicates"][pid]["path"],e["path"])]
            if not readers:
                continue
            qids=sorted({query_id_for_predicate(scenario,pid) for pid in readers})
            if all(exact_cost_value(costs[q])<=0 for q in qids):
                out.append({"transition_id":t["transition_id"],"path":e["path"],
                            "material_predicates":readers,"query_ids":qids})
    return out

def validate_refresh_observability(scenario,profile="balanced",mode="warn"):
    """mode: 'off' | 'warn' (warnings.warn, returns violations) | 'reject' (raises ValueError)."""
    if mode=="off":
        return []
    v=zero_cost_observability_violations(scenario,profile)
    if v:
        msg=(f"scenario {scenario.get('scenario_id','?')}: {len(v)} transition effect(s) write material "
             f"state observable only through zero-cost queries: {v}")
        if mode=="reject":
            raise ValueError(msg)
        warnings.warn(msg)
    return v
