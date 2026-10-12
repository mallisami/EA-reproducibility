#!/usr/bin/env python3
from __future__ import annotations
import argparse,csv,json
from collections import defaultdict
from pathlib import Path

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--analysis-dir',required=True); ap.add_argument('--out',required=True); a=ap.parse_args()
    ad=Path(a.analysis_dir); rows=list(csv.DictReader((ad/'cells_E39.csv').open(encoding='utf-8')))
    async_rows=[]; controls=[]
    totals=defaultdict(lambda:{'n':0,'invalid':0,'correct_abort':0,'false_abort':0,'unknown':0,'rv409':0})
    for r in rows:
        if r.get('experiment')!='A': continue
        parts=r['cell'].split('/')
        arm=parts[1] if len(parts)>1 else 'unknown'; cond=parts[3] if len(parts)>3 else ''
        d=parts[4] if len(parts)>4 else ''
        z=totals[arm]
        z['n']+=int(r.get('n_primary_denominator') or 0); z['invalid']+=int(r.get('k_invalid_executed') or 0)
        z['correct_abort']+=int(r.get('n_CORRECT_ABORT') or 0); z['false_abort']+=int(r.get('n_FALSE_ABORT') or 0)
        z['unknown']+=int(r.get('n_UNKNOWN') or 0); z['rv409']+=int(r.get('n_rv_precondition_409') or 0)
        item={'arm':arm,'condition':cond,'delta_ms':None if d=='NA' else float(d),'n':int(r.get('n_primary_denominator') or 0),
              'k_invalid':int(r.get('k_invalid_executed') or 0),'p_hat':None if r.get('p_hat') in ('','None') else float(r['p_hat']),
              'wilson_lo':None if not r.get('wilson_lo') else float(r['wilson_lo']),'wilson_hi':None if not r.get('wilson_hi') else float(r['wilson_hi']),
              'sec_k':int(r.get('sec_k') or 0),'sec_n':int(r.get('sec_n') or 0),'rv409':int(r.get('n_rv_precondition_409') or 0)}
        (async_rows if cond.startswith('async_') else controls).append(item)
    res=json.loads((ad/'results_E39.json').read_text(encoding='utf-8'))
    exp=(res.get('E39_A') or {}).get('exposure_window') or []
    avail=(res.get('E39_A') or {}).get('availability_cost') or []
    out={'schema':'e41.summary.v1','run_id':res.get('run_id'),'primary_totals_by_arm':dict(totals),
         'exposure_window':exp,'availability_cost':avail,
         'primary_secondary_agreement':(res.get('E39_A') or {}).get('agreement_secondary'),
         'n_async_cells':len(async_rows),'async_cells':async_rows,'controls':controls,
         'interpretation_rule':'Report all valid outcomes. Exact timing need not match E39; this experiment evaluates deployment-level qualitative transportability.'}
    Path(a.out).write_text(json.dumps(out,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'run_id':out['run_id'],'primary_totals_by_arm':out['primary_totals_by_arm']},indent=2))
if __name__=='__main__': main()
