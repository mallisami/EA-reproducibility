
from pathlib import Path
import json, copy, time
from flashir_core.io import load_json, save_json
from flashir_core.dcji import search_dcji
from flashir_core.engine import replay_path
from flashir_core.expr import commit_components
from flashir_core.defenses import (
    B0_oneshot, B2_ttlguard, B3_targetbindingguard,
    B4_fullrefresh, B5_nativeprecondition, B6_dcji_gate
)

ROOT=Path(__file__).resolve().parent
HELD=ROOT/"instances"/"heldout"
OUT=ROOT/"results"/"heldout"
LAGS=[0,0.5,1,2,5]
TTL=2.0

def b3_applicable(s,profile):
    return s["policy_profiles"][profile]["binding"] is not True

def b5_applicable(s):
    return any(c.get("applicable") for c in s.get("native_controls",[]))

def telemetry_state(s,path,commit_time,lag):
    cutoff=max(0.0,commit_time-lag)
    rep=replay_path(s,path,cutoff)
    st=copy.deepcopy(rep["state"])
    # Values are from the lagged snapshot, but the defense is making its decision at real t_e.
    st["timestamp_s"]=commit_time
    return st, cutoff, rep["completed_ids"]

def main():
    existing=[OUT/name for name in ['e11_telemetry_lag.json'] if (OUT/name).exists()]
    if existing:
        raise SystemExit("Frozen held-out results already exist; refusing overwrite. Reproduce only in a separate copy/output directory: "+", ".join(str(x) for x in existing))
    files=sorted(HELD.glob("*.json"))
    assert len(files)==96
    rows=[]

    # Full-model optimal actual attack per template.
    fullsol={}
    for pth in sorted((ROOT/"scenarios"/"heldout").glob("*.json")):
        s=load_json(pth)
        prof=s["primary_policy"]
        deadline=s["worlds"]["W2"]["commit_deadline_s"]
        sol=search_dcji(s,deadline,s["attacker_capability_tier"],prof,max_expansions=100000)
        fullsol[s["scenario_id"]]=sol

    for idx,pth in enumerate(files,1):
        wrapper=load_json(pth)
        s=wrapper["scenario"]; prof=s["primary_policy"]; initial=s["initial_state"]
        tid=wrapper["template_id"]; deadline=s["worlds"]["W2"]["commit_deadline_s"]
        sol=fullsol[tid]

        if s["scenario_role"]=="positive_ea_toctou":
            assert sol["status"]=="EXPLOITABLE"
            path=sol["path"]
            actual_rep=replay_path(s,path,deadline)
            actual_state=actual_rep["state"]
            actual_comp=commit_components(s,actual_state,prof)
            assert actual_rep["path_completed"] and actual_comp["attack_goal"]
        else:
            assert sol["status"]=="NOT_EXPLOITABLE"
            path=[]
            actual_rep=replay_path(s,[],deadline)
            actual_state=actual_rep["state"]
            actual_comp=commit_components(s,actual_state,prof)
            assert actual_comp["valid"]

        for lag in LAGS:
            observed_state,cutoff,observed_completed=telemetry_state(s,path,deadline,lag)
            observed_comp=commit_components(s,observed_state,prof)

            defs=[
                ("B0",B0_oneshot,initial,{}),
                ("B2",B2_ttlguard,observed_state,{"ttl_s":TTL}),
                ("B3",B3_targetbindingguard,observed_state,{}),
                ("B4",B4_fullrefresh,observed_state,{}),
                # B5 is an atomic server-side commit condition and therefore observes actual commit state.
                ("B5",B5_nativeprecondition,actual_state,{}),
                ("B6",B6_dcji_gate,observed_state,{
                    "deadline_s":deadline,
                    "attacker_capability":s["attacker_capability_tier"],
                    "max_expansions":100000,
                    "max_subset_evals":100000,
                }),
            ]

            for did,fn,seen_state,kw in defs:
                if did=="B3" and not b3_applicable(s,prof): continue
                if did=="B5" and not b5_applicable(s): continue
                t0=time.perf_counter()
                d=fn(s,initial,seen_state,prof,**kw)
                rt=time.perf_counter()-t0
                if not d["supported"]: continue

                # Consequence is judged against the true actual commit state, never the lagged observation.
                unsafe=bool(
                    d["decision"]=="EXECUTE"
                    and actual_comp["authorization"]
                    and actual_comp["executable"]
                    and not actual_comp["valid"]
                )
                false_abort=bool(d["decision"]=="ABORT_REPLAN" and actual_comp["valid"])

                rows.append({
                    "experiment":"E11",
                    "instance_id":wrapper["instance_id"],
                    "template_id":tid,
                    "parameterization_id":wrapper["parameterization_id"],
                    "family":s["family"],
                    "scenario_role":s["scenario_role"],
                    "lag_s":lag,
                    "commit_time_s":deadline,
                    "observation_cutoff_s":cutoff,
                    "actual_attack_path":path,
                    "actual_attack_time_s":sol.get("elapsed_s"),
                    "observed_completed_transition_ids":observed_completed,
                    "actual_valid_at_commit":actual_comp["valid"],
                    "observed_validity":observed_comp["valid"],
                    "actual_attack_goal":actual_comp["attack_goal"],
                    "defense_id":did,
                    "defense_mode":d.get("defense_mode",""),
                    "decision":d["decision"],
                    "unsafe_commit":unsafe,
                    "false_abort":false_abort,
                    "query_cost_units":d.get("query_cost_units"),
                    "critical_query_ids":d.get("critical_query_ids",[]),
                    "planning_runtime_s":d.get("planning_runtime_s",0.0),
                    "total_runtime_s":rt,
                    "telemetry_stale_relative_to_attack":bool(
                        s["scenario_role"]=="positive_ea_toctou"
                        and observed_comp["valid"]
                        and not actual_comp["valid"]
                    ),
                })

        if idx%12==0:
            print(f"processed {idx}/96 incidents")

    save_json(OUT/"e11_telemetry_lag.json",rows)
    print("E11 rows",len(rows))

if __name__=="__main__":
    main()
