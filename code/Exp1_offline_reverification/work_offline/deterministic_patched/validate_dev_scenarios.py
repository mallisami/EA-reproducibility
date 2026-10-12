
from pathlib import Path
import json, copy, itertools, sys
import jsonschema

ROOT=Path(__file__).resolve().parent
SD=ROOT/"schemas"; DEV=ROOT/"scenarios"/"dev"
def load(p): return json.loads(Path(p).read_text(encoding="utf-8"))

def get(o,p):
    cur=o
    for t in p.strip("/").split("/"):
        if not t: continue
        t=t.replace("~1","/").replace("~0","~")
        cur=cur[int(t)] if isinstance(cur,list) else cur[t]
    return cur

def setv(o,p,v):
    parts=p.strip("/").split("/"); cur=o
    for t in parts[:-1]:
        t=t.replace("~1","/").replace("~0","~")
        cur=cur[int(t)] if isinstance(cur,list) else cur[t]
    last=parts[-1].replace("~1","/").replace("~0","~")
    if isinstance(cur,list): cur[int(last)]=v
    else: cur[last]=v

def xp(e):
    if isinstance(e,bool): return set()
    if "predicate" in e: return {e["predicate"]}
    if "all" in e:
        z=set()
        for x in e["all"]: z|=xp(x)
        return z
    if "any" in e:
        z=set()
        for x in e["any"]: z|=xp(x)
        return z
    if "not" in e: return xp(e["not"])
    return set()

def hasnot(e):
    if isinstance(e,bool): return False
    if "not" in e: return True
    if "all" in e: return any(hasnot(x) for x in e["all"])
    if "any" in e: return any(hasnot(x) for x in e["any"])
    return False

def ve(e,known):
    if isinstance(e,bool): return
    if not isinstance(e,dict) or len(e)!=1: raise ValueError(e)
    k,v=next(iter(e.items()))
    if k=="predicate":
        if v not in known: raise ValueError(f"unknown predicate {v}")
    elif k in ("all","any"):
        if not v: raise ValueError(f"empty {k}")
        for x in v: ve(x,known)
    elif k=="not": ve(v,known)
    else: raise ValueError(k)

def ep(pid,s,st):
    p=s["predicates"][pid]; a=get(st,p["path"]); b=p["value"]
    if p["op"]=="eq": return a==b
    if p["op"]=="ne": return a!=b
    if p["op"]=="contains": return b in a
    if p["op"]=="subseteq": return set(a).issubset(set(b))
    raise ValueError(p["op"])

def ee(e,s,st):
    if isinstance(e,bool): return e
    if "predicate" in e: return ep(e["predicate"],s,st)
    if "all" in e: return all(ee(x,s,st) for x in e["all"])
    if "any" in e: return any(ee(x,s,st) for x in e["any"])
    if "not" in e: return not ee(e["not"],s,st)
    raise ValueError(e)

def ea(e,a):
    if isinstance(e,bool): return e
    if "predicate" in e: return a[e["predicate"]]
    if "all" in e: return all(ea(x,a) for x in e["all"])
    if "any" in e: return any(ea(x,a) for x in e["any"])
    if "not" in e: return not ea(e["not"],a)
    raise ValueError(e)

def bases(e):
    if hasnot(e): raise ValueError("NOT unsupported in unsigned basis form")
    p=sorted(xp(e)); sats=set()
    for bits in itertools.product([False,True],repeat=len(p)):
        a=dict(zip(p,bits))
        if ea(e,a): sats.add(frozenset(k for k,v in a.items() if v))
    return {x for x in sats if not any(y<x for y in sats)}

def world(s,w):
    st=copy.deepcopy(s["initial_state"]); tm={t["transition_id"]:t for t in s["transitions"]}
    elapsed=0; done=[]
    for tid in w["transition_path"]:
        t=tm[tid]
        if any(p not in done for p in t.get("causal_parent_ids",[])): break
        if elapsed+t["duration_s"]>w["commit_deadline_s"]: break
        if not ee(t.get("preconditions",True),s,st): break
        elapsed+=t["duration_s"]
        for ef in t["effects"]: setv(st,ef["path"],copy.deepcopy(ef["value"]))
        done.append(tid)
    st["timestamp_s"]=w["commit_deadline_s"]
    return st,done

def comp(s,st,prof):
    p=s["policy_profiles"][prof]
    au=ep(s["action"]["authorization"]["predicate"],s,st)
    j=ee(p["justification"],s,st); b=ee(p["binding"],s,st); g=ee(p["scope"],s,st)
    x=ee(s["action"]["execution_precondition"],s,st)
    return au,j,b,g,x

def acyclic(ids,pm):
    visiting=set(); done=set()
    def dfs(n):
        if n in visiting: raise ValueError(f"cycle {n}")
        if n in done: return
        visiting.add(n)
        for p in pm.get(n,[]): dfs(p)
        visiting.remove(n); done.add(n)
    for n in ids: dfs(n)

def rooted(tid,tm,memo):
    if tid in memo: return memo[tid]
    t=tm[tid]
    if t["actor"]=="attacker":
        memo[tid]=True; return True
    ps=t.get("causal_parent_ids",[])
    ans=bool(ps) and any(rooted(p,tm,memo) for p in ps)
    memo[tid]=ans; return ans

def main():
    ss=load(SD/"scenario.schema.json"); sts=load(SD/"state.schema.json"); ts=load(SD/"transition.schema.json")
    fail=[]
    for path in sorted(DEV.glob("*.json")):
        s=load(path); sid=s["scenario_id"]
        try:
            jsonschema.validate(s,ss); jsonschema.validate(s["initial_state"],sts)
            for t in s["transitions"]: jsonschema.validate(t,ts)
        except Exception as e:
            fail.append(f"{sid}: schema {e}"); continue

        known=set(s["predicates"])
        try:
            ve({"predicate":s["action"]["authorization"]["predicate"]},known)
            ve(s["action"]["execution_precondition"],known)
            for prof,p in s["policy_profiles"].items():
                for k in ("justification","binding","scope"): ve(p[k],known)
                if hasnot(p["justification"]): raise ValueError(f"{prof}: NOT in justification")
            for t in s["transitions"]: ve(t.get("preconditions",True),known)
        except Exception as e:
            fail.append(f"{sid}: expression {e}"); continue

        ids=[t["transition_id"] for t in s["transitions"]]
        if len(ids)!=len(set(ids)): fail.append(f"{sid}: duplicate transition IDs")
        tm={t["transition_id"]:t for t in s["transitions"]}
        try: acyclic(set(ids),{k:v.get("causal_parent_ids",[]) for k,v in tm.items()})
        except Exception as e: fail.append(f"{sid}: {e}")

        memo={}
        for t in s["transitions"]:
            if t["actor"]=="environment" and t.get("adversarially_induced") and not rooted(t["transition_id"],tm,memo):
                fail.append(f"{sid}: {t['transition_id']} has no attacker root")
            if t["actor"]=="attacker" and not t.get("adversarially_induced"):
                fail.append(f"{sid}: attacker transition not adversarial {t['transition_id']}")
            if any(e["path"]=="/facts/action_authorized" for e in t["effects"]):
                fail.append(f"{sid}: authorization mutated by {t['transition_id']}")

        material=set()
        for p in s["policy_profiles"].values():
            material |= xp(p["justification"])|xp(p["binding"])|xp(p["scope"])
        material |= xp(s["action"]["execution_precondition"])
        material.discard(s["action"]["authorization"]["predicate"])
        rm=s["refresh_model"]
        for pid in material:
            qid=rm["predicate_query_ids"].get(pid)
            if qid is None: fail.append(f"{sid}: no query map for {pid}")
            elif qid not in rm["query_costs"]: fail.append(f"{sid}: no cost for {qid}")
            elif qid not in rm["query_observed_at_s"]: fail.append(f"{sid}: no observation time for {qid}")

        for prof,p in s["policy_profiles"].items():
            try: en=bases(p["justification"])
            except Exception as e:
                fail.append(f"{sid}/{prof}: {e}"); continue
            dec={frozenset(x) for x in s["justification_bases_by_policy"][prof]}
            if en!=dec: fail.append(f"{sid}/{prof}: basis mismatch")

        for wn in ("W0","W1","W2","W3"):
            w=s["worlds"][wn]; st,done=world(s,w); generated={}
            for prof in s["policy_profiles"]:
                au,j,b,g,x=comp(s,st,prof)
                if not au: fail.append(f"{sid} {wn}/{prof}: auth invalid")
                generated[prof]="EXECUTE" if au and j and b and g else "ABORT_REPLAN"
            if generated!=w["expected_commit_by_policy"]: fail.append(f"{sid} {wn}: policy oracle drift")
            full=done==w["transition_path"]
            au,j,b,g,x=comp(s,st,s["primary_policy"]); valid=au and j and b and g
            if wn=="W2" and (not full or valid or not x): fail.append(f"{sid}: invalid W2")
            if wn=="W3" and (full or not valid): fail.append(f"{sid}: invalid W3")

        print(f"PASS {sid}: v2.2.1 strict audit validation")
    if fail:
        print("\\nFAILURES"); [print(" -",x) for x in fail]; sys.exit(1)
    print("\\nALL SIX DEVELOPMENT SCENARIOS PASSED V2.2.1 STRICT AUDIT VALIDATION.")
if __name__=="__main__": main()
