#!/usr/bin/env python3
from __future__ import annotations
import argparse, csv, json, math, statistics
from collections import defaultdict
from pathlib import Path

Z=1.959963984540054

def wilson(k,n):
    if n<=0: return (None,None)
    p=k/n; z2=Z*Z; den=1+z2/n
    c=(p+z2/(2*n))/den
    h=Z*math.sqrt(p*(1-p)/n+z2/(4*n*n))/den
    return max(0,c-h),min(1,c+h)

def classify(r):
    if r.get('status')!='COMPLETE': return 'INFRA_ERROR'
    g=r.get('gate') or {}
    d=r.get('delete') or {}
    if g.get('decision') not in ('EXECUTE', None): return 'UNEXPECTED_GATE_ABORT'
    st=d.get('http_status')
    if st in (200,202):
        eff=r.get('effect') or {}
        return 'VALID_EXECUTED' if eff.get('verified') else 'UNVERIFIED_EXECUTION'
    if st==409:
        msg=str(d.get('response_message') or '')
        ck=str(d.get('conflict_kind') or '')
        if 'resourceversion' in msg.lower() or 'resourceversion' in ck.lower() or ck=='resourceVersion':
            pv=r.get('post_action_validation') or {}
            if pv.get('pod_present') is True and pv.get('all_target_validity_annotations_true') is True:
                return 'FALSE_ABORT_RV'
            return 'RV_ABORT_VALIDITY_UNVERIFIED'
        return 'ABORT_409_OTHER'
    return 'OTHER'

def writer_before_delete(r):
    arr=r.get('writer_arrivals') or []
    td=(r.get('delete') or {}).get('t_resp_mono_ns')
    if td is None: return False
    return any(a.get('http_status')==200 and a.get('t_resp_mono_ns') is not None and a['t_resp_mono_ns']<=td for a in arr)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--raw',required=True); ap.add_argument('--out',required=True)
    a=ap.parse_args(); out=Path(a.out); out.mkdir(parents=True,exist_ok=True)
    rows=[]
    for line in Path(a.raw).read_text(encoding='utf-8').splitlines():
        if not line.strip(): continue
        r=json.loads(line); r['_outcome']=classify(r); r['_writer_before_delete']=writer_before_delete(r); rows.append(r)
    groups=defaultdict(list)
    for r in rows:
        key=(r['arm'],r['condition'],r.get('delta_ms'))
        groups[key].append(r)
    cell=[]
    for key,rs in sorted(groups.items(), key=lambda kv:(kv[0][0],kv[0][1],-1 if kv[0][2] is None else kv[0][2])):
        usable=[r for r in rs if r['_outcome']!='INFRA_ERROR']
        k=sum(r['_outcome']=='FALSE_ABORT_RV' for r in usable); n=len(usable); lo,hi=wilson(k,n)
        wb=sum(r['_writer_before_delete'] for r in usable)
        writer_assoc=sum(r['_outcome']=='FALSE_ABORT_RV' and r['_writer_before_delete'] for r in usable)
        bg_assoc=k-writer_assoc
        cell.append({'arm':key[0],'condition':key[1],'delta_ms':key[2],'scheduled':len(rs),'n':n,
                     'false_abort_rv':k,'p_false_abort':(k/n if n else None),'wilson_lo':lo,'wilson_hi':hi,
                     'writer_committed_before_delete':wb,'writer_associated_false_abort':writer_assoc,
                     'background_associated_false_abort':bg_assoc,
                     'valid_executed':sum(r['_outcome']=='VALID_EXECUTED' for r in usable),
                     'infra_error':sum(r['_outcome']=='INFRA_ERROR' for r in rs),
                     'other':sum(r['_outcome'] not in ('VALID_EXECUTED','FALSE_ABORT_RV','INFRA_ERROR') for r in rs)})
    with (out/'cells_E42.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=list(cell[0].keys()) if cell else ['arm']); w.writeheader(); w.writerows(cell)
    summary={'schema':'e42.analysis.v1','n_records':len(rows),'n_infra':sum(r['_outcome']=='INFRA_ERROR' for r in rows),
             'cells':cell,'headline':{},
             'interpretation_note':'Pooled benign-churn values are descriptive averages over the fixed delay grid, not production abort rates.'}
    for arm in sorted({r['arm'] for r in rows}):
        rs=[r for r in rows if r['arm']==arm and r['_outcome']!='INFRA_ERROR']
        benign=[r for r in rs if r.get('condition')=='benign_target_annotation_churn']
        nowrite=[r for r in rs if r.get('condition')=='no_write']
        kb=sum(r['_outcome']=='FALSE_ABORT_RV' for r in benign); nb=len(benign); blo,bhi=wilson(kb,nb)
        k0=sum(r['_outcome']=='FALSE_ABORT_RV' for r in nowrite); n0=len(nowrite); zlo,zhi=wilson(k0,n0)
        summary['headline'][arm]={'n':len(rs),'false_abort_rv':sum(r['_outcome']=='FALSE_ABORT_RV' for r in rs),
                                  'valid_executed':sum(r['_outcome']=='VALID_EXECUTED' for r in rs),
                                  'benign_grid':{'k_false_abort_rv':kb,'n':nb,'p':(kb/nb if nb else None),'wilson_lo':blo,'wilson_hi':bhi},
                                  'no_write':{'k_false_abort_rv':k0,'n':n0,'p':(k0/n0 if n0 else None),'wilson_lo':zlo,'wilson_hi':zhi}}
    (out/'summary_E42.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(summary['headline'],indent=2))
if __name__=='__main__': main()
