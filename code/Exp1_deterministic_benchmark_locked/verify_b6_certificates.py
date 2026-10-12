
from pathlib import Path
import itertools
from flashir_core.io import load_json
from flashir_core.dcji import enumerate_attack_goals, derive_critical_predicates
from flashir_core.refresh import refresh_queries, query_cost
from flashir_core.expr import commit_components

ROOT=Path(__file__).resolve().parent
for p in sorted((ROOT/"scenarios/dev").glob("*.json")):
    s=load_json(p); prof=s["primary_policy"]; d=s["worlds"]["W2"]["commit_deadline_s"]
    e=enumerate_attack_goals(s,d,s["attacker_capability_tier"],prof)
    plan=derive_critical_predicates(s,d,s["attacker_capability_tier"],prof)
    assert plan["status"]=="EXPLOITABLE",(s["scenario_id"],plan)
    qs=plan["critical_query_ids"]
    for g in e["goals"]:
        h,_=refresh_queries(s,s["initial_state"],g["state"],qs,prof)
        assert not commit_components(s,h,prof)["valid"],(s["scenario_id"],qs,g["path"])
    cand=plan["candidate_query_ids"]; best=None
    for r in range(len(cand)+1):
        for combo in itertools.combinations(cand,r):
            good=True
            for g in e["goals"]:
                h,_=refresh_queries(s,s["initial_state"],g["state"],combo,prof)
                if commit_components(s,h,prof)["valid"]:
                    good=False; break
            if good:
                score=(query_cost(s,combo),len(combo),combo)
                if best is None or score<best: best=score
    assert best is not None
    assert (plan["query_cost_units"],len(qs),tuple(qs))==(best[0],best[1],best[2]),(s["scenario_id"],plan,best)
    print("PASS",s["scenario_id"],"goals",len(e["goals"]),"Q",qs,"cost",plan["query_cost_units"])
print("ALL B6 CERTIFICATES COVER ALL REACHABLE GOAL STATES AND ARE EXACT MINIMUM-COST.")
