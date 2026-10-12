
from pathlib import Path
import json, hashlib, copy, sys, jsonschema

ROOT=Path(__file__).resolve().parent
INST=ROOT/"instances"

def load(p): return json.loads(Path(p).read_text(encoding="utf-8"))
def sha(o): return hashlib.sha256(json.dumps(o,sort_keys=True,separators=(",",":")).encode()).hexdigest()

def causal_core(s):
    return {
        "scenario_id":s["scenario_id"],"family":s["family"],"scenario_role":s["scenario_role"],
        "action":s["action"],"attacker_capability_tier":s["attacker_capability_tier"],
        "predicates":s["predicates"],"policy_profiles":s["policy_profiles"],
        "primary_policy":s["primary_policy"],"justification_bases_by_policy":s["justification_bases_by_policy"],
        "transitions":s["transitions"],"worlds":s["worlds"],
        "native_controls":s["native_controls"],"refresh_model":s["refresh_model"]
    }

def get(o,p):
    cur=o
    for t in p.strip("/").split("/"):
        if not t: continue
        cur=cur[int(t)] if isinstance(cur,list) else cur[t]
    return cur
def setv(o,p,v):
    parts=p.strip("/").split("/"); cur=o
    for t in parts[:-1]: cur=cur[int(t)] if isinstance(cur,list) else cur[t]
    last=parts[-1]
    if isinstance(cur,list): cur[int(last)]=v
    else: cur[last]=v
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
def replay(s,w):
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
    return st,done

ischema=load(ROOT/"schemas/instance.schema.json")
sschema=load(ROOT/"schemas/scenario.schema.json")
files=sorted((INST/"development").glob("*.json"))+sorted((INST/"heldout").glob("*.json"))
fail=[]
for p in files:
    x=load(p); s=x["scenario"]
    try:
        jsonschema.validate(x,ischema); jsonschema.validate(s,sschema)
    except Exception as e:
        fail.append(f"{x.get('instance_id',p.name)} schema: {e}"); continue
    if sha(causal_core(s)) != x["causal_core_hash"]:
        fail.append(f"{x['instance_id']}: causal core hash mismatch")
    for wn,w in s["worlds"].items():
        st,_=replay(s,w); out={}
        for prof,pol in s["policy_profiles"].items():
            auth=ep(s["action"]["authorization"]["predicate"],s,st)
            valid=auth and ee(pol["justification"],s,st) and ee(pol["binding"],s,st) and ee(pol["scope"],s,st)
            out[prof]="EXECUTE" if valid else "ABORT_REPLAN"
        if out != w["expected_commit_by_policy"]:
            fail.append(f"{x['instance_id']} {wn}: oracle drift")
    print("PASS",x["instance_id"])
if len(files)!=120: fail.append(f"instance count={len(files)}")
if fail:
    print("\\nFAILURES"); [print(" -",f) for f in fail]; sys.exit(1)
print("\\nALL 120 MATERIALIZED INCIDENTS PASSED CAUSAL-CORE AND ORACLE VALIDATION.")
print("24 development + 96 held-out.")
