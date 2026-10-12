
import time
from .expr import commit_components, eval_expr
from .refresh import (
    policy_predicates, refreshable_query_ids, refresh_queries,
    query_cost, stale_query_ids, query_id_for_predicate
)
from .dcji import derive_critical_predicates

def B0_oneshot(scenario,initial_state,commit_state,profile="balanced",**kwargs):
    return {"defense_id":"B0","supported":True,"decision":"EXECUTE","query_cost_units":0.0,
            "refreshed_predicates":[],"critical_predicates":[],"critical_query_ids":[],
            "defense_mode":"oneshot","planning_runtime_s":0.0,"reason":"No commit-time revalidation."}


def B2_ttlguard(scenario,initial_state,commit_state,profile="balanced",ttl_s=2.0,**kwargs):
    qids=stale_query_ids(scenario,profile,float(commit_state.get("timestamp_s",0)),ttl_s)
    hybrid,pids=refresh_queries(scenario,initial_state,commit_state,qids,profile)
    comp=commit_components(scenario,hybrid,profile)
    return {"defense_id":"B2","supported":True,"decision":"EXECUTE" if comp["valid"] else "ABORT_REPLAN",
            "query_cost_units":query_cost(scenario,qids),"refreshed_predicates":pids,
            "critical_predicates":[],"critical_query_ids":[],"defense_mode":f"ttl_{ttl_s:g}s",
            "planning_runtime_s":0.0,"reason":"Refresh physical state queries whose cached age exceeds TTL."}

def B3_targetbindingguard(scenario,initial_state,commit_state,profile="balanced",**kwargs):
    bexpr=scenario["policy_profiles"][profile]["binding"]
    qids=sorted({
        query_id_for_predicate(scenario,pid)
        for pid in policy_predicates(bexpr)
        if float(scenario["refresh_model"]["query_costs"][query_id_for_predicate(scenario,pid)])>0
    })
    hybrid,pids=refresh_queries(scenario,initial_state,commit_state,qids,profile)
    ok=eval_expr(bexpr,scenario,hybrid)
    return {"defense_id":"B3","supported":True,"decision":"EXECUTE" if ok else "ABORT_REPLAN",
            "query_cost_units":query_cost(scenario,qids),"refreshed_predicates":pids,
            "critical_predicates":[],"critical_query_ids":[],"defense_mode":"binding_only",
            "planning_runtime_s":0.0,"reason":"Refresh only physical state read(s) needed by target binding."}

B3_targetversionguard=B3_targetbindingguard

def B4_fullrefresh(scenario,initial_state,commit_state,profile="balanced",**kwargs):
    qids=sorted(refreshable_query_ids(scenario,profile))
    hybrid,pids=refresh_queries(scenario,initial_state,commit_state,qids,profile)
    comp=commit_components(scenario,hybrid,profile)
    return {"defense_id":"B4","supported":True,"decision":"EXECUTE" if comp["valid"] else "ABORT_REPLAN",
            "query_cost_units":query_cost(scenario,qids),"refreshed_predicates":pids,
            "critical_predicates":[],"critical_query_ids":[],"defense_mode":"full_refresh",
            "planning_runtime_s":0.0,"reason":"Refresh every dynamic physical state query used by the commit policy."}

def B5_nativeprecondition(scenario,initial_state,commit_state,profile="balanced",**kwargs):
    controls=[c for c in scenario.get("native_controls",[]) if c.get("applicable")]
    if not controls:
        return {"defense_id":"B5","supported":False,"decision":None,"query_cost_units":None,
                "refreshed_predicates":[],"critical_predicates":[],"critical_query_ids":[],
                "defense_mode":"not_applicable","planning_runtime_s":0.0,
                "reason":"No native atomic platform precondition is defined."}
    pids=sorted(set(pid for c in controls for pid in c["predicate_ids"]))
    ok=all(eval_expr({"predicate":pid},scenario,commit_state) for pid in pids)
    cost=sum(float(c["normalized_cost_units"]) for c in controls)
    return {"defense_id":"B5","supported":True,"decision":"EXECUTE" if ok else "ABORT_REPLAN",
            "query_cost_units":cost,"refreshed_predicates":[],
            "critical_predicates":[],"critical_query_ids":[],"defense_mode":"native_atomic_precondition",
            "planning_runtime_s":0.0,
            "reason":"Atomic server-side UID/resource-version style commit precondition."}

def B6_dcji_gate(
    scenario,initial_state,commit_state,profile="balanced",deadline_s=None,
    attacker_capability=None,max_expansions=100000,max_subset_evals=100000,critical_plan=None,**kwargs
):
    if deadline_s is None: deadline_s=float(commit_state.get("timestamp_s",0))
    t0=time.perf_counter()
    plan=critical_plan or derive_critical_predicates(
        scenario,deadline_s,attacker_capability,profile,max_expansions,max_subset_evals
    )
    planning_runtime=time.perf_counter()-t0 if critical_plan is None else 0.0

    if plan["status"]=="NOT_EXPLOITABLE":
        return {"defense_id":"B6","supported":True,"decision":"EXECUTE","query_cost_units":0.0,
                "refreshed_predicates":[],"critical_predicates":[],"critical_query_ids":[],
                "defense_mode":"dcji_no_feasible_attack","planning_runtime_s":planning_runtime,
                "attack_goal_state_count":0,"reason":"No modeled effectful EA-TOCTOU state is reachable before commit."}

    if plan["status"]!="EXPLOITABLE":
        fb=B4_fullrefresh(scenario,initial_state,commit_state,profile)
        fb.update({"defense_id":"B6","defense_mode":"dcji_fallback_full_refresh",
                   "critical_predicates":[],"critical_query_ids":[],
                   "planning_runtime_s":planning_runtime,"attack_goal_state_count":plan.get("goal_count"),
                   "reason":f"Selective planning returned {plan['status']}; fail-safe FullRefresh fallback."})
        return fb

    qids=plan["critical_query_ids"]
    hybrid,pids=refresh_queries(scenario,initial_state,commit_state,qids,profile)
    comp=commit_components(scenario,hybrid,profile)
    return {"defense_id":"B6","supported":True,"decision":"EXECUTE" if comp["valid"] else "ABORT_REPLAN",
            "query_cost_units":query_cost(scenario,qids),"refreshed_predicates":pids,
            "critical_predicates":plan["critical_predicates"],"critical_query_ids":qids,
            "defense_mode":"dcji_selective","planning_runtime_s":planning_runtime,
            "attack_goal_state_count":plan["goal_count"],"candidate_query_ids":plan["candidate_query_ids"],
            "reason":"Minimum-cost physical query certificate covering all modeled effectful attack states reachable through the deadline."}

DETERMINISTIC_DEFENSES={"B0":B0_oneshot,"B2":B2_ttlguard,
                        "B3":B3_targetbindingguard,"B4":B4_fullrefresh,
                        "B5":B5_nativeprecondition,"B6":B6_dcji_gate}
