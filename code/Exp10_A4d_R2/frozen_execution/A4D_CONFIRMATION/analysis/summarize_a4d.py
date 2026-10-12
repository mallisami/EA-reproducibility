#!/usr/bin/env python3
from __future__ import annotations
import argparse,csv,json
from pathlib import Path
p=argparse.ArgumentParser(); p.add_argument('--analysis-dir',required=True); p.add_argument('--out',required=True); a=p.parse_args(); ad=Path(a.analysis_dir)
rows=list(csv.DictReader((ad/'cells_E39.csv').open(encoding='utf-8'))); cells=[]; totals={'n':0,'invalid':0,'correct_abort':0,'false_abort':0,'unknown':0,'rv409':0}
for r in rows:
    if r.get('experiment')!='A' or '/A4d_commit_direct/' not in r.get('cell',''): continue
    parts=r['cell'].split('/'); cond=parts[3] if len(parts)>3 else ''; d=parts[4] if len(parts)>4 else ''
    item={'condition':cond,'delta_ms':None if d=='NA' else float(d),'n':int(r.get('n_primary_denominator') or 0),'k_invalid':int(r.get('k_invalid_executed') or 0),'p_hat':None if r.get('p_hat') in ('','None') else float(r['p_hat']),'wilson_lo':None if not r.get('wilson_lo') else float(r['wilson_lo']),'wilson_hi':None if not r.get('wilson_hi') else float(r['wilson_hi']),'correct_abort':int(r.get('n_CORRECT_ABORT') or 0),'false_abort':int(r.get('n_FALSE_ABORT') or 0),'unknown':int(r.get('n_UNKNOWN') or 0),'rv409':int(r.get('n_rv_precondition_409') or 0),'sec_k':int(r.get('sec_k') or 0),'sec_n':int(r.get('sec_n') or 0)}
    cells.append(item)
    for key,col in [('n','n_primary_denominator'),('invalid','k_invalid_executed'),('correct_abort','n_CORRECT_ABORT'),('false_abort','n_FALSE_ABORT'),('unknown','n_UNKNOWN'),('rv409','n_rv_precondition_409')]: totals[key]+=int(r.get(col) or 0)
res=json.loads((ad/'results_E39.json').read_text(encoding='utf-8')); ea=(res.get('E39_A') or {})
out={'schema':'a4d.confirmation.summary.v1','run_id':res.get('run_id'),'totals':totals,'cells':cells,'exposure_window':ea.get('exposure_window'),'availability_cost':ea.get('availability_cost'),'primary_secondary_agreement':ea.get('agreement_secondary'),'interpretation':'All scientific outcomes are reported. Audit-evidence completeness is a separate instrumentation gate and never depends on outcome direction.'}
Path(a.out).write_text(json.dumps(out,indent=2)+'\n',encoding='utf-8'); print(json.dumps({'run_id':out['run_id'],'totals':totals},indent=2))
