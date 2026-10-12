
import copy, heapq, itertools, json
from .engine import transition_allowed_for_attacker_model, can_apply_transition, apply_transition
from .expr import commit_components, eval_predicate
from .refresh import material_predicates, query_id_for_predicate, query_cost, refresh_queries, predicates_for_queries

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

def derive_critical_predicates(
    scenario,deadline_s,attacker_capability=None,profile="balanced",
    max_expansions=100000,max_subset_evals=100000
):
    enum=enumerate_attack_goals(scenario,deadline_s,attacker_capability,profile,max_expansions)
    if enum["status"]=="UNKNOWN_TIMEOUT":
        return {"status":"UNKNOWN_TIMEOUT","critical_query_ids":None,"critical_predicates":None,
                "query_cost_units":None,"goal_count":len(enum["goals"]),"candidate_query_ids":None,
                "expanded":enum["expanded"],"subset_evals":0}
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

    combos=[]
    for r in range(len(candidates)+1):
        for combo in itertools.combinations(candidates,r):
            combos.append((query_cost(scenario,combo),len(combo),combo))
    combos.sort(key=lambda x:(x[0],x[1],x[2]))

    evals=0
    for cost,_,combo in combos:
        evals+=1
        if evals>max_subset_evals:
            return {"status":"UNKNOWN_SUBSET_TIMEOUT","critical_query_ids":None,"critical_predicates":None,
                    "query_cost_units":None,"goal_count":len(enum["goals"]),"candidate_query_ids":candidates,
                    "expanded":enum["expanded"],"subset_evals":evals-1}
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

    return {"status":"NO_SELECTIVE_CERTIFICATE","critical_query_ids":None,"critical_predicates":None,
            "query_cost_units":None,"goal_count":len(enum["goals"]),"candidate_query_ids":candidates,
            "expanded":enum["expanded"],"subset_evals":evals}
