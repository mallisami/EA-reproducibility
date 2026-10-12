
from pathlib import Path
import json, copy, hashlib, random, time
from flashir_core.io import load_json, save_json
from flashir_core.dcji import search_dcji
from flashir_core.engine import replay_path, transition_allowed_for_attacker_model
from flashir_core.expr import commit_components
from flashir_core.defenses import B4_fullrefresh, B6_dcji_gate

ROOT=Path(__file__).resolve().parent
HELD=ROOT/"instances"/"heldout"
OUT=Path(__import__("os").environ["DCJI_OUT"])  # redirected (offline reanalysis copy)
HIDE=[0.10,0.20,0.30,0.50]
REPS=20
MASTER=20260905

def seed_for(template_id,p,rep):
    raw=f"{MASTER}|{template_id}|{p:.2f}|{rep}".encode()
    return int(hashlib.sha256(raw).hexdigest()[:16],16)

def mask_scenario(full,p,rep):
    s=copy.deepcopy(full)
    cap=s["attacker_capability_tier"]
    candidates=[
        t["transition_id"] for t in s["transitions"]
        if transition_allowed_for_attacker_model(t,cap)
    ]
    rng=random.Random(seed_for(s["scenario_id"],p,rep))
    hidden=sorted([tid for tid in candidates if rng.random() < p])
    kept=[]
    for t in s["transitions"]:
        if t["transition_id"] not in hidden:
            kept.append(t)
    s["transitions"]=kept
    return s,candidates,hidden

def main():
    existing=[OUT/name for name in ['e10_incomplete_transition_model.json'] if (OUT/name).exists()]
    if existing:
        raise SystemExit("Frozen held-out results already exist; refusing overwrite. Reproduce only in a separate copy/output directory: "+", ".join(str(x) for x in existing))
    files=sorted(HELD.glob("*.json"))
    assert len(files)==96
    rows=[]

    # Full-model actual attack path is fixed per template because P1-P4 share causal core.
    full_solution={}
    for pth in sorted((ROOT/"scenarios"/"heldout").glob("*.json")):
        s=load_json(pth)
        prof=s["primary_policy"]
        deadline=s["worlds"]["W2"]["commit_deadline_s"]
        sol=search_dcji(s,deadline,s["attacker_capability_tier"],prof,max_expansions=100000)
        full_solution[s["scenario_id"]]=sol

    for idx,pth in enumerate(files,1):
        wrapper=load_json(pth)
        full=wrapper["scenario"]
        prof=full["primary_policy"]
        tid=wrapper["template_id"]
        deadline=full["worlds"]["W2"]["commit_deadline_s"]
        sol_full=full_solution[tid]

        # For positive cases the actual attacker executes the full-model optimal path.
        # F5.6 remains the negative control and has no exploit path.
        if full["scenario_role"]=="positive_ea_toctou":
            assert sol_full["status"]=="EXPLOITABLE"
            actual_path=sol_full["path"]
            actual_rep=replay_path(full,actual_path,deadline)
            actual_state=actual_rep["state"]
            actual_comp=commit_components(full,actual_state,prof)
            assert actual_rep["path_completed"] and actual_comp["attack_goal"]
        else:
            assert sol_full["status"]=="NOT_EXPLOITABLE"
            actual_path=[]
            actual_rep=replay_path(full,[],deadline)
            actual_state=actual_rep["state"]
            actual_comp=commit_components(full,actual_state,prof)
            assert actual_comp["valid"]

        for hp in HIDE:
            for rep in range(1,REPS+1):
                masked,candidates,hidden=mask_scenario(full,hp,rep)

                # B6 plans with incomplete model but observes actual commit state if it queries.
                t0=time.perf_counter()
                b6=B6_dcji_gate(
                    masked,full["initial_state"],actual_state,prof,
                    deadline_s=deadline,
                    attacker_capability=full["attacker_capability_tier"],
                    max_expansions=100000,max_subset_evals=100000
                )
                rt=time.perf_counter()-t0

                # Strong reference unaffected by attacker-model completeness.
                b4=B4_fullrefresh(full,full["initial_state"],actual_state,prof)

                b6_unsafe=bool(
                    b6["decision"]=="EXECUTE"
                    and actual_comp["authorization"]
                    and actual_comp["executable"]
                    and not actual_comp["valid"]
                )
                b4_unsafe=bool(
                    b4["decision"]=="EXECUTE"
                    and actual_comp["authorization"]
                    and actual_comp["executable"]
                    and not actual_comp["valid"]
                )

                actual_set=set(actual_path)
                hidden_set=set(hidden)
                rows.append({
                    "experiment":"E10",
                    "instance_id":wrapper["instance_id"],
                    "template_id":tid,
                    "parameterization_id":wrapper["parameterization_id"],
                    "family":full["family"],
                    "scenario_role":full["scenario_role"],
                    "hide_probability":hp,
                    "mask_replicate":rep,
                    "mask_seed":seed_for(tid,hp,rep),
                    "attacker_model_candidate_transition_ids":candidates,
                    "hidden_transition_ids":hidden,
                    "hidden_transition_count":len(hidden),
                    "candidate_transition_count":len(candidates),
                    "actual_full_model_status":sol_full["status"],
                    "actual_attack_path":actual_path,
                    "actual_attack_cost_ops":sol_full.get("cost_ops"),
                    "actual_attack_time_s":sol_full.get("elapsed_s"),
                    "actual_path_hidden_transition_count":len(actual_set & hidden_set),
                    "actual_path_fully_visible_to_B6":len(actual_set & hidden_set)==0,
                    "actual_attack_goal":actual_comp["attack_goal"],
                    "actual_valid_at_commit":actual_comp["valid"],
                    "actual_authorization_valid":actual_comp["authorization"],
                    "actual_executable":actual_comp["executable"],
                    "B6_decision":b6["decision"],
                    "B6_unsafe_commit":b6_unsafe,
                    "B6_query_cost_units":b6["query_cost_units"],
                    "B6_defense_mode":b6["defense_mode"],
                    "B6_planning_runtime_s":b6.get("planning_runtime_s",0.0),
                    "B6_total_runtime_s":rt,
                    "B6_critical_query_ids":b6.get("critical_query_ids",[]),
                    "B6_attack_goal_state_count":b6.get("attack_goal_state_count"),
                    "B4_decision":b4["decision"],
                    "B4_unsafe_commit":b4_unsafe,
                    "B4_query_cost_units":b4["query_cost_units"],
                })

        if idx%12==0:
            print(f"processed {idx}/96 incidents")

    save_json(OUT/"e10_incomplete_transition_model.json",rows)
    print("E10 rows",len(rows))

if __name__=="__main__":
    main()
