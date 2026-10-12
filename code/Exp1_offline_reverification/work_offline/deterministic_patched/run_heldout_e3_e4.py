
from pathlib import Path
import json, time
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
DELTA_GRID=[0,1,2,5,10,30]
CAP_GRID=["C0","C1","C2","C3"]
TTL=2.0

def b3_applicable(s,profile):
    return s["policy_profiles"][profile]["binding"] is not True

def b5_applicable(s):
    return any(c.get("applicable") for c in s.get("native_controls",[]))

def defense_eval(s, initial, commit_state, profile, deadline, capability):
    comp=commit_components(s,commit_state,profile)
    defs=[
        ("B0",B0_oneshot,{}),
        ("B2",B2_ttlguard,{"ttl_s":TTL}),
        ("B3",B3_targetbindingguard,{}),
        ("B4",B4_fullrefresh,{}),
        ("B5",B5_nativeprecondition,{}),
        ("B6",B6_dcji_gate,{
            "deadline_s":deadline,
            "attacker_capability":capability,
            "max_expansions":100000,
            "max_subset_evals":100000,
        })
    ]
    out={}
    for did,fn,kw in defs:
        if did=="B3" and not b3_applicable(s,profile):
            continue
        if did=="B5" and not b5_applicable(s):
            continue
        t0=time.perf_counter()
        d=fn(s,initial,commit_state,profile,**kw)
        runtime=time.perf_counter()-t0
        if not d["supported"]:
            continue
        unsafe=bool(
            d["decision"]=="EXECUTE"
            and comp["authorization"]
            and comp["executable"]
            and not comp["valid"]
        )
        out[did]={
            "decision":d["decision"],
            "unsafe_commit":unsafe,
            "false_abort":bool(d["decision"]=="ABORT_REPLAN" and comp["valid"]),
            "query_cost_units":d.get("query_cost_units"),
            "defense_mode":d.get("defense_mode",""),
            "planning_runtime_s":d.get("planning_runtime_s",0.0),
            "total_runtime_s":runtime,
            "critical_query_ids":d.get("critical_query_ids",[]),
        }
    return comp,out

def main():
    existing=[OUT/name for name in ['e3_delay_sensitivity.json', 'e4_capability_sensitivity.json'] if (OUT/name).exists()]
    if existing:
        raise SystemExit("Frozen held-out results already exist; refusing overwrite. Reproduce only in a separate copy/output directory: "+", ".join(str(x) for x in existing))
    files=sorted(HELD.glob("*.json"))
    assert len(files)==96
    e3=[]; e4=[]

    for idx,p in enumerate(files,1):
        w=load_json(p)
        s=w["scenario"]; profile=s["primary_policy"]; initial=s["initial_state"]
        meta={
            "instance_id":w["instance_id"],"template_id":w["template_id"],
            "parameterization_id":w["parameterization_id"],"family":s["family"],
            "scenario_role":s["scenario_role"],"realism_tier":s["realism_tier"],
        }

        # E3 — delay sweep at scenario's frozen attacker capability.
        cap=s["attacker_capability_tier"]
        for delta in DELTA_GRID:
            t0=time.perf_counter()
            sol=search_dcji(s,delta,cap,profile,max_expansions=100000)
            sr=time.perf_counter()-t0
            row=dict(meta)
            row.update({
                "experiment":"E3","delta_s":delta,"active_capability":cap,
                "solver_status":sol["status"],"solver_path":sol.get("path"),
                "C_star":sol.get("cost_ops"),"T_star_s":sol.get("elapsed_s"),
                "solver_expanded":sol.get("expanded"),"solver_runtime_s":sr,
                "defenses":None,
            })
            if sol["status"]=="EXPLOITABLE":
                rep=replay_path(s,sol["path"],delta)
                cs=rep["state"]
                comp,defs=defense_eval(s,initial,cs,profile,delta,cap)
                assert rep["path_completed"]
                assert comp["attack_goal"]
                row.update({
                    "attack_path_completed":True,
                    "authorization_valid_at_commit":comp["authorization"],
                    "valid_at_commit":comp["valid"],
                    "actuation_executable_at_commit":comp["executable"],
                    "defenses":defs,
                })
            else:
                row.update({
                    "attack_path_completed":False,
                    "authorization_valid_at_commit":True,
                    "valid_at_commit":True,
                    "actuation_executable_at_commit":None,
                })
            e3.append(row)

        # E4 — capability sweep at frozen W2 deadline.
        delta=s["worlds"]["W2"]["commit_deadline_s"]
        for cap in CAP_GRID:
            t0=time.perf_counter()
            sol=search_dcji(s,delta,cap,profile,max_expansions=100000)
            sr=time.perf_counter()-t0
            row=dict(meta)
            row.update({
                "experiment":"E4","delta_s":delta,"active_capability":cap,
                "scenario_frozen_capability":s["attacker_capability_tier"],
                "solver_status":sol["status"],"solver_path":sol.get("path"),
                "C_star":sol.get("cost_ops"),"T_star_s":sol.get("elapsed_s"),
                "solver_expanded":sol.get("expanded"),"solver_runtime_s":sr,
                "defenses":None,
            })
            if sol["status"]=="EXPLOITABLE":
                rep=replay_path(s,sol["path"],delta)
                cs=rep["state"]
                comp,defs=defense_eval(s,initial,cs,profile,delta,cap)
                assert rep["path_completed"]
                assert comp["attack_goal"]
                row.update({
                    "attack_path_completed":True,
                    "authorization_valid_at_commit":comp["authorization"],
                    "valid_at_commit":comp["valid"],
                    "actuation_executable_at_commit":comp["executable"],
                    "defenses":defs,
                })
            else:
                row.update({
                    "attack_path_completed":False,
                    "authorization_valid_at_commit":True,
                    "valid_at_commit":True,
                    "actuation_executable_at_commit":None,
                })
            e4.append(row)

        if idx%12==0:
            print(f"processed {idx}/96 incidents")

    save_json(OUT/"e3_delay_sensitivity.json",e3)
    save_json(OUT/"e4_capability_sensitivity.json",e4)
    print("E3 rows",len(e3))
    print("E4 rows",len(e4))

if __name__=="__main__":
    main()
