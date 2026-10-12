#!/usr/bin/env python3
"""Fail-closed estimate from real previous-phase attempt duration; NOT Azure price or runtime guarantee."""
from __future__ import annotations
import argparse,json,math,statistics,sys
from collections import defaultdict
from pathlib import Path

def estimate(run,conditions,methods,reps,blocks,budget_hours):
    if not (run/'SESSION.json').is_file() or not (run/'RAW_TRIALS.jsonl').is_file():
        raise ValueError('Previous phase SESSION and RAW_TRIALS are required')
    session=json.loads((run/'SESSION.json').read_text())
    if session.get('setup_status')!='COMPLETE':raise ValueError('Previous phase setup not complete')
    rows=[json.loads(line) for line in (run/'RAW_TRIALS.jsonl').read_text().splitlines()]
    if len(rows)!=session['total_planned_trials'] or any(r.get('failed') for r in rows):
        raise ValueError('Previous phase incomplete or contains failed attempts')
    if not (run/'DESCRIPTIVE_AUDIT.json').exists() or not (run/'RAW_INTEGRITY_REPORT.json').exists():
        raise ValueError('Previous phase analysis and raw integrity reports missing')
    analysis=json.loads((run/'DESCRIPTIVE_AUDIT.json').read_text())
    raw_audit=json.loads((run/'RAW_INTEGRITY_REPORT.json').read_text())
    if analysis.get('complete_schedule') is not True or raw_audit.get('hard_accounting_errors'):
        raise ValueError('Previous phase did not pass both audit gates')
    grouped=defaultdict(list)
    for r in rows:
        t=r.get('elapsed_wall_ms')
        if not isinstance(t,(float,int)) or type(t) is bool or not math.isfinite(t) or t<=0:
            raise ValueError('Missing/invalid wall duration in previous phase')
        grouped[(r['condition'],r['method'])].append(t/3600000)
    need=[(c,m) for c in conditions for m in methods]
    missing=[x for x in need if not grouped[x]]
    if missing:raise ValueError('No empirical calibration for cells: '+repr(missing))
    # Twice the observed worst duration per cell plus a fixed operational reserve.
    per_cell=[{'condition':c,'method':m,'prior_n':len(grouped[(c,m)]),
               'max_observed_seconds':max(grouped[(c,m)])*3600,
               'planned_repetitions':reps*blocks,
               'estimated_hours_with_2x_factor':max(grouped[(c,m)])*2*reps*blocks}
              for c,m in need]
    estimate_hours=sum(x['estimated_hours_with_2x_factor'] for x in per_cell)+.25
    return {'source_run':str(run),'planned_total':len(need)*reps*blocks,
       'estimate_hours':round(estimate_hours,4),'operator_budget_hours':budget_hours,
       'within_approved_estimated_runtime':estimate_hours<=budget_hours,
       'reserve_hours':.25,'duration_factor':2,
       'warning':'Planning heuristic, NOT a guaranteed runtime or Azure billing estimate; credits and VM rate separately checked by operator.',
       'cells':per_cell}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('run_dir',type=Path);p.add_argument('--conditions',required=True)
    p.add_argument('--methods',required=True);p.add_argument('--repetitions',type=int,required=True)
    p.add_argument('--blocks',type=int,required=True);p.add_argument('--max-estimated-hours',type=float,required=True)
    p.add_argument('--output',type=Path)
    a=p.parse_args()
    if a.repetitions<=0 or a.blocks<=0 or not math.isfinite(a.max_estimated_hours) or a.max_estimated_hours<=0:
        p.error('Positive finite count and budget required')
    conditions=[x.strip() for x in a.conditions.split(',')];methods=[x.strip() for x in a.methods.split(',')]
    if any(not x for x in conditions+methods) or len(set(conditions))!=len(conditions) or len(set(methods))!=len(methods):
        p.error('Nonempty distinct conditions/methods required')
    try:r=estimate(a.run_dir,conditions,methods,a.repetitions,a.blocks,a.max_estimated_hours)
    except (ValueError,KeyError,FileNotFoundError) as exc:p.error('Cost preflight refused: '+str(exc))
    if a.output:a.output.write_text(json.dumps(r,indent=2)+'\n')
    print(json.dumps({k:v for k,v in r.items() if k!='cells'},indent=2))
    if not r['within_approved_estimated_runtime']:
        print('REFUSED: estimate exceeds operator budget; inspect and explicitly adjust design/budget before running',file=sys.stderr)
        return 2
    return 0
if __name__=='__main__':sys.exit(main())
