#!/usr/bin/env python3
"""Exploratory fixed-N precision aid from a SEPARATE pilot; NOT automatic approval."""
import argparse,collections,json,math,statistics
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('pilot_dir',type=Path)
p.add_argument('--halfwidth-ms',type=float,required=True,help='Predeclared two-sided 95% mean difference CI half-width')
a=p.parse_args()
if a.halfwidth_ms<=0:raise SystemExit('Positive precision target required')
session=json.loads((a.pilot_dir/'SESSION.json').read_text())
if session['phase']!='pilot':raise SystemExit('REFUSED: calibration can only use PILOT data')
rows=[json.loads(x) for x in (a.pilot_dir/'RAW_TRIALS.jsonl').read_text().splitlines()]
by=collections.defaultdict(dict)
for r in rows:by[(r['condition'],r['block_id'],r['rep_index'])][r['method']]=r
pairs=collections.defaultdict(list)
for (condition,block,rep),item in by.items():
    if {'DCJI','FullRefresh'} <= item.keys():
        d=item['DCJI'];f=item['FullRefresh']
        if not d.get('failed') and not f.get('failed') and d.get('guard',{}).get('refresh_latency_ms') is not None and f.get('guard',{}).get('refresh_latency_ms') is not None:
            pairs[condition].append(d['guard']['refresh_latency_ms']-f['guard']['refresh_latency_ms'])
output=[]
for cond,values in sorted(pairs.items()):
    if len(values)<3:output.append({'condition':cond,'warning':'Insufficient calibration pairs'});continue
    sd=statistics.stdev(values);est=max(1,math.ceil((1.96*sd/a.halfwidth_ms)**2))
    output.append({'condition':cond,'pilot_pairs':len(values),'pilot_sd_ms':sd,
      'normal_approx_pairs_for_halfwidth':est,
      'caveat':'Preliminary conditional-on-host iid calculation; account for temporal block dependence, cell multiplicity and failures before freezing sample size.'})
result={'status':'SAMPLE_SIZE_PROPOSAL_NOT_LOCK','precision_halfwidth_ms':a.halfwidth_ms,
 'host_population_inference':False,'cells':output}
(a.pilot_dir/'PILOT_PRECISION_PROPOSAL.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
