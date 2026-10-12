
from pathlib import Path
import json, copy, itertools, sys
import jsonschema

ROOT=Path(__file__).resolve().parent
SCHEMA=ROOT/"schemas"
DIRS=[ROOT/"scenarios/dev",ROOT/"scenarios/heldout"]

def load(p): return json.loads(Path(p).read_text(encoding="utf-8"))

def get(o,p):
    cur=o
    for t in p.strip("/").split("/"):
        if not t: continue
        cur=cur[int(t)] if isinstance(cur,list) else cur[t]
    return cur

def setv(o,p,v):
    parts=p.strip("/").split("/"); cur=o
    for t in parts[:-1]:
        cur=cur[int(t)] if isinstance(cur,list) else cur[t]
    last=parts[-1]
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
    ps=sorted(xp(e)); sats=set()
    for bits in itertools.product([False,True],repeat=len(ps)):
        a=dict(zip(ps,bits))
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
        elapsed += t["duration_s"]
        for ef in t["effects"]: setv(st,ef["path"],copy.deepcopy(ef["value"]))
        done.append(tid)
    st["timestamp_s"]=w["commit_deadline_s"]
    return st,done

def rooted(tid,tm,memo):
    if tid in memo: return memo[tid]
    t=tm[tid]
    if t["actor"]=="attacker":
        memo[tid]=True; return True
    ps=t.get("causal_parent_ids",[])
    ans=bool(ps) and any(rooted(p,tm,memo) for p in ps)
    memo[tid]=ans; return ans

def main():
    ss=load(SCHEMA/"scenario.schema.json"); sts=load(SCHEMA/"state.schema.json"); ts=load(SCHEMA/"transition.schema.json")
    files=[p for d in DIRS for p in sorted(d.glob("*.json"))]
    fail=[]
    ids=[]
    for path in files:
        s=load(path); sid=s["scenario_id"]; ids.append(sid)
        try:
            jsonschema.validate(s,ss); jsonschema.validate(s["initial_state"],sts)
            for t in s["transitions"]: jsonschema.validate(t,ts)
        except Exception as e:
            fail.append(f"{sid}: schema {e}"); continue

        if bool(s["development"]) != ("dev" in path.parts):
            fail.append(f"{sid}: development flag/path mismatch")

        # Transition IDs unique and adversarial environment transitions attacker-rooted.
        tids=[t["transition_id"] for t in s["transitions"]]
        if len(tids)!=len(set(tids)): fail.append(f"{sid}: duplicate transition ids")
        tm={t["transition_id"]:t for t in s["transitions"]}; memo={}
        for t in s["transitions"]:
            if t["actor"]=="environment" and t.get("adversarially_induced") and not rooted(t["transition_id"],tm,memo):
                fail.append(f"{sid}: adversarial environment transition lacks attacker root")

        # Authorization may not be mutated.
        for t in s["transitions"]:
            if any(e["path"]=="/facts/action_authorized" for e in t["effects"]):
                fail.append(f"{sid}: authorization mutation")

        # Exact bases and complete query map for all profiles.
        material=set()
        for prof,p in s["policy_profiles"].items():
            try: en=bases(p["justification"])
            except Exception as e:
                fail.append(f"{sid}/{prof}: {e}"); continue
            dec={frozenset(x) for x in s["justification_bases_by_policy"][prof]}
            if en!=dec: fail.append(f"{sid}/{prof}: basis mismatch")
            material |= xp(p["justification"])|xp(p["binding"])|xp(p["scope"])
        material |= xp(s["action"]["execution_precondition"])
        material.discard(s["action"]["authorization"]["predicate"])
        rm=s["refresh_model"]
        for pid in material:
            qid=rm["predicate_query_ids"].get(pid)
            if qid is None or qid not in rm["query_costs"] or qid not in rm["query_observed_at_s"]:
                fail.append(f"{sid}: incomplete query mapping for {pid}")

        # W0-W3 oracle integrity.
        for wn,w in s["worlds"].items():
            st,done=world(s,w); out={}
            for prof,p in s["policy_profiles"].items():
                auth=ep(s["action"]["authorization"]["predicate"],s,st)
                valid=auth and ee(p["justification"],s,st) and ee(p["binding"],s,st) and ee(p["scope"],s,st)
                out[prof]="EXECUTE" if valid else "ABORT_REPLAN"
            if out!=w["expected_commit_by_policy"]: fail.append(f"{sid} {wn}: oracle drift")
            if out["balanced"]!=w["expected_commit"]: fail.append(f"{sid} {wn}: primary drift")

            full=done==w["transition_path"]
            if wn in ("W2","W3") and full!=w["expected_attack_path_completed"]:
                fail.append(f"{sid} {wn}: path completion mismatch")

            exec_ok=ee(s["action"]["execution_precondition"],s,st)
            if s["scenario_role"]=="positive_ea_toctou" and wn=="W2":
                if out["balanced"]!="ABORT_REPLAN" or not full or not exec_ok:
                    fail.append(f"{sid}: invalid positive W2")
            if s["scenario_role"]=="negative_exploitability_control" and wn=="W2":
                if out["balanced"]!="EXECUTE" or not full:
                    fail.append(f"{sid}: negative control unexpectedly exploitable")
            if wn=="W3" and out["balanced"]!="EXECUTE":
                fail.append(f"{sid}: invalid W3")

        print(f"PASS {sid}: benchmark structural/oracle validation")

    if len(ids)!=30: fail.append(f"template count {len(ids)} != 30")
    if len(set(ids))!=30: fail.append("duplicate scenario IDs")
    dev=sum(1 for p in files if "dev" in p.parts); held=sum(1 for p in files if "heldout" in p.parts)
    if dev!=6 or held!=24: fail.append(f"split mismatch dev={dev} heldout={held}")

    if fail:
        print("\\nFAILURES"); [print(" -",x) for x in fail]; sys.exit(1)
    print("\\nALL 30 TEMPLATES PASSED BENCHMARK STRUCTURAL/ORACLE VALIDATION.")
    print("Split: 6 development + 24 held-out.")

if __name__=="__main__": main()
