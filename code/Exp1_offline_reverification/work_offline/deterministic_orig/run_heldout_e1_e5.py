
from pathlib import Path
import json, hashlib, time, sys
from flashir_core.io import load_json, save_json
from flashir_core.engine import replay_path
from flashir_core.expr import commit_components
from flashir_core.dcji import search_dcji
from flashir_core.defenses import (
    B0_oneshot, B2_ttlguard, B3_targetbindingguard,
    B4_fullrefresh, B5_nativeprecondition, B6_dcji_gate
)

ROOT=Path(__file__).resolve().parent
HELD=ROOT/"instances"/"heldout"
OUT=Path(__import__("os").environ["DCJI_OUT"])  # redirected (offline reanalysis copy)
TTL=2.0

def sha(x):
    return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(",",":")).encode()).hexdigest()

def b3_applicable(s,profile):
    return s["policy_profiles"][profile]["binding"] is not True

def b5_applicable(s):
    return any(c.get("applicable") for c in s.get("native_controls",[]))

def main():
    existing=[OUT/name for name in ['e1_oracle_vulnerability.json', 'e5_defense_comparison.json'] if (OUT/name).exists()]
    if existing:
        raise SystemExit("Frozen held-out results already exist; refusing overwrite. Reproduce only in a separate copy/output directory: "+", ".join(str(x) for x in existing))
    e1=[]
    e5=[]
    files=sorted(HELD.glob("*.json"))
    assert len(files)==96, len(files)

    for idx,p in enumerate(files,1):
        wrapper=load_json(p)
        s=wrapper["scenario"]
        profile=s["primary_policy"]
        initial=s["initial_state"]
        role=s["scenario_role"]
        template_id=wrapper["template_id"]
        instance_id=wrapper["instance_id"]
        param=wrapper["parameterization_id"]

        init=commit_components(s,initial,profile)
        assert init["valid"] and init["authorization"], instance_id

        for wn in ("W0","W1","W2","W3"):
            w=s["worlds"][wn]
            rep=replay_path(s,w["transition_path"],w["commit_deadline_s"])
            cs=rep["state"]
            comp=commit_components(s,cs,profile)

            t0=time.perf_counter()
            pot=search_dcji(
                s,w["commit_deadline_s"],s["attacker_capability_tier"],profile,
                max_expansions=100000
            )
            pot_runtime=time.perf_counter()-t0

            common={
                "instance_id":instance_id,
                "template_id":template_id,
                "parameterization_id":param,
                "family":s["family"],
                "scenario_role":role,
                "world":wn,
                "deadline_s":w["commit_deadline_s"],
                "model_id":"OracleAgent",
                "policy_profile":profile,
                "initial_action_correct":True,
                "authorization_valid_at_commit":comp["authorization"],
                "justification_valid_at_commit":comp["justification"],
                "binding_valid_at_commit":comp["binding"],
                "scope_valid_at_commit":comp["scope"],
                "valid_at_commit":comp["valid"],
                "oracle_attack_goal":comp["attack_goal"],
                "actuation_executable_at_commit":comp["executable"],
                "attack_path_completed":rep["path_completed"],
                "completed_transition_ids":rep["completed_ids"],
                "actual_attack_cost_ops":rep["cost_ops"],
                "actual_attack_time_s":rep["elapsed_s"],
                "potential_exploitability":pot["status"],
                "solver_path":pot.get("path"),
                "solver_cost_ops":pot.get("cost_ops"),
                "solver_elapsed_s":pot.get("elapsed_s"),
                "solver_runtime_s":pot_runtime,
                "attacker_capability_tier":s["attacker_capability_tier"],
                "scenario_hash":sha(s),
                "causal_core_hash":wrapper["causal_core_hash"],
            }

            # E1 = unprotected OracleAgent under the frozen world.
            b0=B0_oneshot(s,initial,cs,profile)
            unsafe=(b0["decision"]=="EXECUTE" and comp["authorization"] and comp["executable"] and not comp["valid"])
            row=dict(common)
            row.update({
                "experiment":"E1",
                "defense_id":"B0",
                "commit_decision":b0["decision"],
                "unsafe_commit":unsafe,
                "false_abort":False,
                "observed_ea_toctou_success":bool(
                    role=="positive_ea_toctou" and wn=="W2"
                    and rep["path_completed"] and comp["attack_goal"] and unsafe
                ),
                "query_cost_units":0.0,
                "defense_planning_runtime_s":0.0,
            })
            e1.append(row)

            # E5 frozen defense comparison.
            defs=[
                ("B0",B0_oneshot,{}),
                ("B2",B2_ttlguard,{"ttl_s":TTL}),
                ("B3",B3_targetbindingguard,{}),
                ("B4",B4_fullrefresh,{}),
                ("B5",B5_nativeprecondition,{}),
                ("B6",B6_dcji_gate,{
                    "deadline_s":w["commit_deadline_s"],
                    "attacker_capability":s["attacker_capability_tier"],
                    "max_expansions":100000,
                    "max_subset_evals":100000,
                }),
            ]
            for did,fn,kw in defs:
                if did=="B3" and not b3_applicable(s,profile):
                    continue
                if did=="B5" and not b5_applicable(s):
                    continue
                td=time.perf_counter()
                d=fn(s,initial,cs,profile,**kw)
                total_def_runtime=time.perf_counter()-td
                if not d["supported"]:
                    continue
                unsafe=(d["decision"]=="EXECUTE" and comp["authorization"] and comp["executable"] and not comp["valid"])
                false_abort=(d["decision"]=="ABORT_REPLAN" and comp["valid"])
                r=dict(common)
                r.update({
                    "experiment":"E5",
                    "defense_id":did,
                    "defense_mode":d.get("defense_mode",""),
                    "commit_decision":d["decision"],
                    "unsafe_commit":unsafe,
                    "false_abort":false_abort,
                    "observed_ea_toctou_success":bool(
                        role=="positive_ea_toctou" and wn=="W2"
                        and rep["path_completed"] and comp["attack_goal"] and unsafe
                    ),
                    "query_cost_units":d.get("query_cost_units"),
                    "refreshed_predicates":d.get("refreshed_predicates",[]),
                    "critical_predicates":d.get("critical_predicates",[]),
                    "critical_query_ids":d.get("critical_query_ids",[]),
                    "defense_planning_runtime_s":d.get("planning_runtime_s",0.0),
                    "defense_total_runtime_s":total_def_runtime,
                    "attack_goal_state_count":d.get("attack_goal_state_count"),
                })
                e5.append(r)

        if idx % 12 == 0:
            print(f"processed {idx}/96 held-out incidents")

    save_json(OUT/"e1_oracle_vulnerability.json",e1)
    save_json(OUT/"e5_defense_comparison.json",e5)
    print(f"E1 rows: {len(e1)}")
    print(f"E5 rows: {len(e5)}")

if __name__=="__main__":
    main()
