
from pathlib import Path
import itertools, math, copy
from flashir_core.io import load_json
from flashir_core.engine import transition_allowed_for_attacker_model, can_apply_transition, apply_transition
from flashir_core.expr import commit_components
from flashir_core.dcji import search_dcji

ROOT=Path(__file__).resolve().parent
DEV=ROOT/"scenarios"/"dev"

def brute_force(s,deadline,profile):
    cap=s["attacker_capability_tier"]
    ts=[t for t in s["transitions"] if transition_allowed_for_attacker_model(t,cap)]
    best=None
    for r in range(len(ts)+1):
        for perm in itertools.permutations(ts,r):
            st=copy.deepcopy(s["initial_state"]); elapsed=0; cost=0; completed=[]; path=[]; ok=True
            for t in perm:
                if elapsed+t["duration_s"]>deadline or not can_apply_transition(t,s,st,completed):
                    ok=False; break
                st=apply_transition(t,s,st,completed)
                elapsed += t["duration_s"]; cost += t["cost_ops"]
                completed.append(t["transition_id"]); path.append(t["transition_id"])
            if not ok: continue
            comp=commit_components(s,st,profile)
            if comp["attack_goal"]:
                score=(cost,elapsed,len(path),path)
                if best is None or score < best[0]:
                    best=(score,comp)
    return best

for p in sorted(DEV.glob("*.json")):
    s=load_json(p); w=s["worlds"]["W2"]; profile=s["primary_policy"]
    brute=brute_force(s,w["commit_deadline_s"],profile)
    dcji=search_dcji(s,w["commit_deadline_s"],s["attacker_capability_tier"],profile)
    if brute is None:
        assert dcji["status"]=="NOT_EXPLOITABLE",(s["scenario_id"],dcji)
        print("PASS",s["scenario_id"],"NOT_EXPLOITABLE")
    else:
        score,_=brute
        expected=list(score[3])
        assert dcji["status"]=="EXPLOITABLE",(s["scenario_id"],dcji)
        assert (dcji["cost_ops"],dcji["elapsed_s"],len(dcji["path"]))==(score[0],score[1],score[2]),(s["scenario_id"],score,dcji)
        assert dcji["path"]==expected,(s["scenario_id"],expected,dcji["path"])
        print("PASS",s["scenario_id"],expected,"cost",score[0],"time",score[1])
print("ALL DEVELOPMENT DCJI OPTIMA MATCH EXHAUSTIVE ENUMERATION.")
