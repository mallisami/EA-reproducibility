#!/usr/bin/env python3
"""Validate that E41 probe operations are represented in K3s audit logs and check the sequential cross-object RV/time diagnostic."""
from __future__ import annotations
import argparse, json
from datetime import datetime
from pathlib import Path

def tns(s):
    d=datetime.strptime(s.replace('Z','+0000'),'%Y-%m-%dT%H:%M:%S.%f%z')
    return int(d.timestamp())*10**9+d.microsecond*1000

def main():
    p=argparse.ArgumentParser(); p.add_argument('--audit',required=True); p.add_argument('--probe',required=True); p.add_argument('--out',required=True); a=p.parse_args()
    ev=[json.loads(x) for x in Path(a.audit).read_text(encoding='utf-8').splitlines() if x.strip()]
    probe=json.loads(Path(a.probe).read_text()); by={}
    for e in ev: by.setdefault(e.get('auditID'),[]).append(e)
    join=[]; miss=rv_mis=0; ordered=[]
    for o in probe.get('ops',[]):
        aid=o.get('audit_id')
        if not aid or o['op']=='create_pod': continue
        es=[e for e in by.get(aid,[]) if e.get('stage')=='ResponseComplete']
        if len(es)!=1:
            miss+=1; join.append({'op':o['op'],'audit_id':aid,'matches':len(es)}); continue
        e=es[0]; arv=((e.get('responseObject') or {}).get('metadata') or {}).get('resourceVersion')
        if o.get('status') in (200,201) and o.get('rv') and str(arv)!=str(o['rv']): rv_mis+=1
        if o['op']=='order_seq' and o.get('status')==200 and arv and str(arv).isdigit():
            ordered.append((int(arv),tns(e['stageTimestamp']),o.get('kind')))
    ordered.sort()
    discord=sum(1 for x,y in zip(ordered,ordered[1:]) if x[1]>y[1])
    kinds=sorted({x[2] for x in ordered})
    ns='e39-'+probe['run_id']
    mine=[e for e in ev if (e.get('objectRef') or {}).get('namespace')==ns]
    stages=sorted({e.get('stage') for e in mine})
    d409=[e for e in mine if e.get('verb')=='delete' and (e.get('responseStatus') or {}).get('code')==409 and e.get('stage')=='ResponseComplete']
    checks={
        'api_preflight_pass':bool(probe.get('ALL_API_PASS')),
        'audit_join_all':miss==0 and rv_mis==0,
        'sequential_cross_object_rv_time_nondecreasing':len(ordered)>=90 and discord==0 and set(kinds)=={'configmap','pod'},
        'delete_409_message_present':any('ResourceVersion in the precondition' in ((e.get('responseStatus') or {}).get('message') or '') for e in d409),
        'no_RequestReceived':'RequestReceived' not in stages,
    }
    out={'schema':'e41.preflight.audit.v1','run_id':probe['run_id'],'checks':checks,'PASS':all(checks.values()),
         'audit_events_total':len(ev),'events_in_run_namespace':len(mine),'audit_join':{'missing':miss,'rv_mismatch':rv_mis,'sample':join[:10]},
         'sequential_order':{'n':len(ordered),'adjacent_discordant':discord,'kinds':kinds},
         'concurrent_order_note':'Concurrent probe writes are retained for diagnostics but are not a pass/fail criterion because response-completion timestamps need not serialize concurrent response latency.'}
    Path(a.out).write_text(json.dumps(out,indent=2)+'\n')
    print(json.dumps(out,indent=2))
    return 0 if out['PASS'] else 4
if __name__=='__main__': raise SystemExit(main())
