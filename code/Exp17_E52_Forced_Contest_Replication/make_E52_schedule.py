#!/usr/bin/env python3
"""E52 schedule: filter the frozen E39 stage-1 schedule to experiment-A A4d small-delta
async cells (+controls) and replicate them, to accumulate many genuine precondition
contests. Output is runner-valid e39.schedule.v1 (all required fields preserved).

Contest yield in E39 was ~42/1287 async A4d trials (~3.3%), concentrated at delta<=3ms.
To expect ~300 contests you need ~9,000 async small-delta A4d trials; set --replicate so
that (base small-delta async trials) x R ~ 9,000. This is an honest replication design,
not a timing change; contest LANDING is confirmed post-hoc by analyze_E52_contests.py.
"""
import argparse, json
SMALL_DELTAS={0.0,0.5,1.0,2.0,3.0}
ASYNC={'async_scope','async_app_policy','async_evidence_last_literal'}
def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--full', required=True, help='a frozen --make-schedule output (stage 1)')
    ap.add_argument('--replicate', type=int, default=16)
    ap.add_argument('--run-id', default='e52'); ap.add_argument('--out', required=True)
    a=ap.parse_args()
    full=json.load(open(a.full)); T=full['trials']
    keep=[t for t in T if t['experiment']=='A' and t['arm']=='A4d_commit_direct'
          and ((t['condition'] in ASYNC and t['delta_ms'] in SMALL_DELTAS)
               or t['condition'] in ('no_write','uid_rebind_control'))]
    out=[]; tid=0
    for r in range(a.replicate):
        for t in keep:
            nt=dict(t); tid+=1; nt['trial_id']=tid
            nt['rep']=t.get('rep',0)+r*1000; nt['block']=t.get('block',0)+r*1000
            nt['run_id']=a.run_id; out.append(nt)
    keys=('trial_id','stage','block','experiment','arm','condition','delta_ms','rep','schedule_seed',
          'lambda_per_s','injected_latency_ms','acquisition_mode','planning_mode','pair_id','guard_order',
          'load_level','client_impl','action_type','arrival_window_ms')
    rows=[{k:t[k] for k in keys if k in t} for t in out]
    cbe={}
    for t in rows: cbe[t['experiment']]=cbe.get(t['experiment'],0)+1
    doc={'schema':full['schema'],'run_id':a.run_id,'schedule_seed':full['schedule_seed'],'stage':1,
         'n_trials':len(rows),'counts_by_experiment':cbe,
         'note':f'E52 contest replication x{a.replicate} of A4d small-delta async cells','trials':rows}
    json.dump(doc,open(a.out,'w'))
    async_small=sum(1 for t in rows if t['condition'] in ASYNC)
    print(f"wrote {a.out}: n_trials={len(rows)} async_small={async_small} "
          f"expected_contests~{round(async_small*0.033)}")
if __name__=='__main__': main()
